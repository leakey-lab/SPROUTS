"""
Data caching and interval calculation utilities for Dash visualizations.

The cache is built once from the DataProcessor's DataFrame in ``__init__`` /
``_cache_data``. The DataFrame MUST NOT be mutated after a DataCache is
constructed; if the underlying data changes, rebuild via ``refresh()`` (or
create a new DataCache) so memoized interval data is not stale.

The cache is metric-agnostic (AR-15): it aggregates ``data_processor.value_column``
("Length (mm)" for root length, "Area (mm²)" for root area).

Depth intervals are fixed bins of ``interval_size`` positions (L1-L10 -> 10,
L11-L20 -> 20, ...). One interval table maps each interval end to an
``IntervalStats`` dict::

    {"avg", "min", "max", "std", "count", "interval_start", "interval_end"}

A table runs contiguously from the first to the last measured interval; bins in
between with no rows have ``count == 0`` and NaN statistics. ``std`` is the
sample standard deviation (NaN for a single row). Every statistic equals, bit
for bit, what ``pandas.Series.mean()/min()/max()/std()`` returns for the bin's
rows in CSV order, which is how the charts always computed them.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from app.config import LENGTH_VALUE_COLUMN

logger = logging.getLogger(__name__)

#: Statistics of one depth interval (see the module docstring for the keys).
IntervalStats = dict[str, Any]
#: Interval end position -> statistics of that interval.
IntervalTable = dict[int, IntervalStats]


def interval_ends(positions: pd.Series, interval_size: int) -> pd.Series:
    """Map each position onto the end of its fixed interval (1-10 -> 10, 11-20 -> 20, ...).

    The arithmetic runs in int64 so deep positions never overflow a narrow
    integer dtype (PV-01: int8 turned L121-L127 into an empty chart).
    """
    pos = positions.astype("int64")
    return ((pos - 1) // interval_size + 1) * interval_size


def _bin_statistics(values: np.ndarray) -> tuple[float, float, float, float]:
    """(mean, min, max, sample std) of one bin, as pandas Series reductions compute them.

    Mirrors ``pandas.core.nanops`` without bottleneck: mean = pairwise ``sum`` / n;
    variance is two-pass (``sum((mean - x)**2) / (n - 1)``). A groupby
    aggregation (Kahan/Welford) can differ in the last bit, which flips a
    2-decimal hover value when the exact mean ends in 5 (10.445: 10.44 vs 10.45).
    """
    count = float(values.size)
    mean = values.sum(dtype=np.float64) / count
    if values.size > 1:
        variance = ((mean - values) ** 2).sum(dtype=np.float64) / (count - 1.0)
        std = math.sqrt(variance)
    else:
        std = math.nan
    return float(mean), float(values.min()), float(values.max()), std


def _interval_stats(
    end: int,
    interval_size: int,
    count: int = 0,
    mean: float = math.nan,
    low: float = math.nan,
    high: float = math.nan,
    std: float = math.nan,
) -> IntervalStats:
    """One ``IntervalStats`` dict; the defaults describe an empty bin."""
    return {
        "avg": mean,
        "min": low,
        "max": high,
        "std": std,
        "count": count,
        "interval_start": end - interval_size + 1,
        "interval_end": end,
    }


def _contiguous(bins: IntervalTable, interval_size: int) -> IntervalTable:
    """Fill the gaps between the first and last measured bins with empty bins."""
    return {
        end: bins[end] if end in bins else _interval_stats(end, interval_size)
        for end in range(min(bins), max(bins) + 1, interval_size)
    }


def merge_nearby_dates(
    tube_counts: Mapping[pd.Timestamp, int] | pd.Series, window_days: int = 3
) -> dict[pd.Timestamp, pd.Timestamp]:
    """Map each measurement date onto the representative date of its campaign (F-034).

    Deterministic greedy merge: dates are visited in ascending order; each
    not-yet-assigned date anchors a ``+-window_days`` window over the dates not
    assigned yet, and the whole window maps to its date with the most measured
    tubes (the earliest one on a tie).

    Args:
        tube_counts: number of measured tubes per date.
        window_days: half-width of the merge window in days.
    """
    counts = pd.Series(tube_counts, dtype="int64").sort_index()
    window = pd.Timedelta(days=window_days)
    mapping: dict[pd.Timestamp, pd.Timestamp] = {}
    for anchor in counts.index:
        if anchor in mapping:
            continue
        nearby = counts[
            (counts.index >= anchor - window)
            & (counts.index <= anchor + window)
            & ~counts.index.isin(list(mapping))
        ]
        representative = nearby.idxmax()  # first maximum = earliest date (sorted index)
        for date in nearby.index:
            mapping[date] = representative
    return mapping


def _copy_table(table: IntervalTable) -> IntervalTable:
    """Copy a table so callers cannot mutate the memoized one (values are scalars)."""
    return {end: dict(stats) for end, stats in table.items()}


class DataCache:
    """Manages cached data for Dash visualizations."""

    def __init__(self, data_processor: Any, value_column: str | None = None):
        """
        Initialize the data cache.

        Args:
            data_processor: A MetricDataProcessor (length or area) with loaded data.
            value_column: Metric column to aggregate. Defaults to
                ``data_processor.value_column`` (falls back to "Length (mm)").
        """
        self.data_processor = data_processor
        self.value_column: str = value_column or getattr(
            data_processor, "value_column", LENGTH_VALUE_COLUMN
        )
        self.tubes: tuple[int, ...] | None = None
        self.dates: tuple[pd.Timestamp, ...] | None = None
        self.tube_date_groups: pd.Series | None = None
        #: Deprecated: never computed any more (nothing used it, PV-17); kept for callers.
        self.position_groups = None
        #: Bumped by every (re)build; lets dependent memos detect a refresh().
        self.generation = 0
        # Per-instance memos (not @lru_cache, which would pin the instance and
        # share results across instances, F-012). Keyed by interval size (and
        # tube); each value maps date -> IntervalTable.
        self._tube_tables: dict[tuple[Any, int], dict[pd.Timestamp, IntervalTable]] = {}
        self._field_tables: dict[int, dict[pd.Timestamp, IntervalTable]] = {}
        self._tube_totals: dict[Any, pd.Series] | None = None
        self._availability: dict[tuple[Any, pd.Timestamp], bool] | None = None
        self._field_growth: dict[int, pd.DataFrame] = {}
        self._cache_data()

    def _cache_data(self) -> None:
        """Cache frequently used data to improve performance."""
        self.tubes = tuple(sorted(self.data_processor.get_unique_tubes()))
        self.dates = tuple(sorted(self.data_processor.get_unique_dates()))

        df = self.data_processor.df
        self.tube_date_groups = df.groupby(["Tube", "Date"])[self.value_column].sum()
        # Invalidate everything derived from the previous frame.
        self._tube_tables.clear()
        self._field_tables.clear()
        self._tube_totals = None
        self._availability = None
        self._field_growth.clear()
        self.generation += 1

    def refresh(self) -> None:
        """Rebuild all cached structures from the (possibly changed) DataFrame.

        Call this if ``data_processor.df`` is replaced/mutated so callers do not
        observe stale interval data.
        """
        self._cache_data()

    def get_interval_data(self, tube: int, date: pd.Timestamp, interval_size: int = 10) -> dict:
        """
        Get cached interval data for a specific tube and date with standardized intervals.
        Returns data in fixed intervals (1-10, 11-20, etc.) regardless of starting position.

        A copy is returned so callers cannot mutate the cached structure.

        Args:
            tube: Tube number
            date: Date timestamp
            interval_size: Size of each interval (default 10)

        Returns:
            dict: Interval statistics keyed by interval end position ({} if unmeasured)
        """
        key = (tube, interval_size)
        tables = self._tube_tables.get(key)
        if tables is None:
            df = self.data_processor.df
            tables = self._build_tables(df[df["Tube"] == tube], interval_size)
            self._tube_tables[key] = tables
        return _copy_table(tables.get(pd.Timestamp(date), {}))

    def get_field_interval_data(self, date: pd.Timestamp, interval_size: int = 10) -> dict:
        """Interval statistics for one date pooled over EVERY tube (the field average).

        Same table shape as :meth:`get_interval_data`; ``{}`` if nothing was
        measured on ``date``. A copy is returned.
        """
        tables = self._field_tables.get(interval_size)
        if tables is None:
            tables = self._build_tables(self.data_processor.df, interval_size)
            self._field_tables[interval_size] = tables
        return _copy_table(tables.get(pd.Timestamp(date), {}))

    def get_tube_totals(self, tube: int) -> pd.Series:
        """Total metric per date for one tube (a date-sorted copy; empty if unmeasured).

        The same sums as ``tube_date_groups``, so the time view and the stacked
        bars agree to the last bit.
        """
        if self._tube_totals is None:
            self._tube_totals = {
                t: totals.droplevel(0) for t, totals in self.tube_date_groups.groupby(level=0)
            }
        totals = self._tube_totals.get(tube)
        if totals is None:
            return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="Date"))
        return totals.copy()

    def get_field_growth(self, merge_window_days: int = 3) -> pd.DataFrame:
        """Field average of the per-tube totals over time (the "Field Average" series).

        Dates within ``merge_window_days`` are merged first (see
        :func:`merge_nearby_dates`); a tube measured on several merged dates
        contributes the mean of its totals, so re-measured tubes are not double
        counted (F-034).

        Returns:
            A date-sorted copy with columns ``Date``, ``mean`` and ``std`` (sample
            standard deviation across tubes) and ``count`` (number of tubes).
        """
        field = self._field_growth.get(merge_window_days)
        if field is None:
            totals = self.tube_date_groups.rename("total").reset_index()
            mapping = merge_nearby_dates(totals.groupby("Date").size(), merge_window_days)
            totals["Date"] = totals["Date"].map(mapping)
            per_tube = totals.groupby(["Date", "Tube"])["total"].mean()
            field = per_tube.groupby(level="Date").agg(["mean", "std", "count"]).reset_index()
            self._field_growth[merge_window_days] = field
        return field.copy()

    def get_tube_date_availability(self) -> dict:
        """Which tube/date combinations have at least one row.

        Returns:
            dict: {(tube, date): has_data_bool} over every tube x date. The dict is
            shared (memoized until ``refresh()``); treat it as read-only.
        """
        if self._availability is None:
            present = set(self.tube_date_groups.index)
            self._availability = {
                (tube, date): (tube, date) in present for tube in self.tubes for date in self.dates
            }
        return self._availability

    def _build_tables(
        self, rows: pd.DataFrame, interval_size: int
    ) -> dict[pd.Timestamp, IntervalTable]:
        """Interval tables for every date in ``rows`` from ONE stable sort (PV-17).

        Replaces a boolean-filter pass per interval (O(intervals x rows) for
        every tube/date) with a stable sort by (date, interval end): each bin is
        then a contiguous slice, still in CSV row order, reduced exactly like a
        pandas Series (see :func:`_bin_statistics`).
        """
        if rows.empty:
            return {}
        date_codes, date_values = pd.factorize(rows["Date"], sort=True)
        ends = interval_ends(rows["Position"], interval_size).to_numpy()
        order = np.lexsort((ends, date_codes))  # stable: keeps CSV order inside a bin
        values = rows[self.value_column].to_numpy(dtype=np.float64)[order]
        date_codes, ends = date_codes[order], ends[order]
        breaks = np.flatnonzero((np.diff(date_codes) != 0) | (np.diff(ends) != 0)) + 1
        starts = np.concatenate(([0], breaks))
        stops = np.concatenate((breaks, [len(values)]))

        measured: dict[pd.Timestamp, IntervalTable] = {}
        for start, stop in zip(starts.tolist(), stops.tolist(), strict=True):
            end = int(ends[start])
            mean, low, high, std = _bin_statistics(values[start:stop])
            bins = measured.setdefault(pd.Timestamp(date_values[date_codes[start]]), {})
            bins[end] = _interval_stats(end, interval_size, stop - start, mean, low, high, std)
        return {date: _contiguous(bins, interval_size) for date, bins in measured.items()}

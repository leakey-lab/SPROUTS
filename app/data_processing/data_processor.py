"""Load a root-metric CSV (length or area) into a typed DataFrame for the dashboards."""

import logging
from dataclasses import dataclass

import pandas as pd

from app.config import LENGTH_VALUE_COLUMN
from app.naming import parse_image_name

logger = logging.getLogger(__name__)

#: Columns every chart row needs besides the metric itself.
KEY_COLUMNS = ("Date", "Tube", "Position")
#: Image-name convention the Measure step reads (see app.naming.parse_image_name).
NAME_FORMAT_HINT = "<field>_T<tube>_L<position>_YYYY.MM.DD_HHMMSS_..."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _join(parts: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


@dataclass(frozen=True)
class RowExclusions:
    """Rows of a metric CSV that the dashboards leave out, by cause (FR-15).

    Attributes:
        failed: measurements that failed (non-empty ``Error`` cell).
        bad_name: image names ``app.naming`` cannot read a tube, position and
            date from (not in :data:`NAME_FORMAT_HINT` form, e.g. ``IMG_0001``).
        unreadable: other rows with a missing or non-numeric field or value.
    """

    failed: int = 0
    bad_name: int = 0
    unreadable: int = 0

    @classmethod
    def from_mask(
        cls,
        excluded: pd.Series,
        errors: pd.Series | None = None,
        names: pd.Series | None = None,
    ) -> "RowExclusions":
        """Classify the ``excluded`` rows (boolean mask) using their Error and Image cells."""
        if not excluded.any():
            return cls()
        rest = excluded
        failed = 0
        if errors is not None:
            has_error = errors.notna() & errors.astype(str).str.strip().ne("")
            failed = int((excluded & has_error).sum())
            rest = excluded & ~has_error
        bad_name = 0
        if names is not None:
            bad_name = sum(
                not parse_image_name(str(name)).is_complete for name in names[rest].tolist()
            )
        return cls(failed=failed, bad_name=bad_name, unreadable=int(rest.sum()) - bad_name)

    @property
    def total(self) -> int:
        """Number of rows left out of the charts."""
        return self.failed + self.bad_name + self.unreadable

    def message(self) -> str | None:
        """One user-facing sentence explaining the exclusions (``None`` if none)."""
        if not self.total:
            return None
        parts = []
        if self.failed:
            parts.append(
                f"{_plural(self.failed, 'failed measurement')} (see the CSV's Error column)"
            )
        if self.bad_name:
            parts.append(
                f"{_plural(self.bad_name, 'image name')} not in the {NAME_FORMAT_HINT} format"
            )
        if self.unreadable:
            parts.append(
                f"{_plural(self.unreadable, 'row')} with a missing or non-numeric tube, "
                "position, date or value"
            )
        verb = "is" if self.total == 1 else "are"
        return f"{_plural(self.total, 'row')} {verb} not shown: {_join(parts)}."


class MetricDataProcessor:
    """
    Loads and prepares a root-metric CSV (length or area) for visualization.

    The length and area pipelines previously had two near-identical processor
    classes; they now share this base, parameterized only by the metric column
    and its display label/unit.

    After loading, ``df`` holds one row per measured image with ``Date``
    (datetime64), ``Tube``/``Position`` (int64) and ``value_column`` (float64);
    rows missing any of them are dropped and counted in ``exclusions``.
    """

    #: name of the numeric metric column in the CSV (e.g. "Length (mm)")
    value_column = LENGTH_VALUE_COLUMN
    #: human-readable metric name used in chart titles/hover (e.g. "Length")
    metric_label = "Length"
    #: display unit of ``value_column`` (e.g. "mm")
    unit = "mm"

    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.exclusions = RowExclusions()
        self._load_problem: str | None = None  # why the file could not be used at all
        self.df = self._load_and_prepare_data()
        self._compute_unique_caches()

    @property
    def dropped_rows(self) -> int:
        """Rows left out of ``df`` because a key field or the value was unreadable."""
        return self.exclusions.total

    def empty_reason(self) -> str | None:
        """Why ``df`` has no row to chart (one user-facing sentence); None when it has rows."""
        if not self.df.empty:
            return None
        if self._load_problem:
            return self._load_problem
        if self.exclusions.total:
            return f"Nothing to chart: {self.exclusions.message()}"
        return "The CSV has no measurement rows."

    def _compute_unique_caches(self):
        """Compute the sorted unique lookups once instead of on every accessor call."""
        self._unique_tubes = sorted(self.df["Tube"].unique()) if "Tube" in self.df.columns else []
        self._unique_dates = sorted(self.df["Date"].unique()) if "Date" in self.df.columns else []
        self._unique_positions = (
            sorted(self.df["Position"].unique()) if "Position" in self.df.columns else []
        )
        self._unique_treatments = (
            sorted(self.df["Treatment"].astype(str).unique())
            if "Treatment" in self.df.columns
            else []
        )
        self._unique_genotypes = (
            sorted(self.df["Genotype"].astype(str).unique())
            if "Genotype" in self.df.columns
            else []
        )

    def _read_csv(self):
        """Read the metric CSV; writers emit UTF-8 (see write_metric_csv)."""
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return pd.read_csv(self.csv_path, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return pd.read_csv(self.csv_path)

    def _resolve_value_column(self, df):
        """Map the metric column when legacy encodings mangled the header."""
        if self.value_column in df.columns:
            return df
        for col in df.columns:
            if col.replace("\ufeff", "").replace("Â", "") == self.value_column:
                return df.rename(columns={col: self.value_column})
        # e.g. "Area (mm^2)" for "Area (mm²)"; only this processor's own metric,
        # so a length dashboard never charts an area column as length.
        same_metric = [c for c in df.columns if c.startswith(self.metric_label) and "mm" in c]
        if len(same_metric) == 1:
            return df.rename(columns={same_metric[0]: self.value_column})
        return df

    def _load_and_prepare_data(self):
        """Read, type and filter the CSV; returns an empty frame on any failure."""
        try:
            df = self._read_csv()
            df = self._resolve_value_column(df)
            if self.value_column not in df.columns:
                logger.error(
                    "CSV %s missing metric column %r (columns: %s)",
                    self.csv_path,
                    self.value_column,
                    list(df.columns),
                )
                self._load_problem = f"The CSV has no {self.value_column!r} column."
                return pd.DataFrame()
            missing = [column for column in KEY_COLUMNS if column not in df.columns]
            if missing:
                logger.error("CSV %s missing key column(s) %s", self.csv_path, missing)
                names = ", ".join(repr(column) for column in missing)
                self._load_problem = f"The CSV has no {names} column{'s' * (len(missing) > 1)}."
                return pd.DataFrame()
            df["Date"] = pd.to_datetime(df["Date"], format="%Y.%m.%d", errors="coerce")
            # No integer downcast: int8 positions overflow in the depth-interval
            # arithmetic (124 -> interval end 130 wraps to -126, PV-01).
            df["Tube"] = pd.to_numeric(df["Tube"], errors="coerce")
            df["Position"] = pd.to_numeric(df["Position"], errors="coerce")
            df[self.value_column] = pd.to_numeric(df[self.value_column], errors="coerce").astype(
                "float64"
            )
            required = [*KEY_COLUMNS, self.value_column]
            self.exclusions = RowExclusions.from_mask(
                df[required].isna().any(axis=1), df.get("Error"), df.get("Image")
            )
            if self.exclusions.total:
                logger.warning("%s: %s", self.csv_path, self.exclusions.message())
            df.dropna(subset=required, inplace=True)
            df["Tube"] = df["Tube"].astype("int64")
            df["Position"] = df["Position"].astype("int64")
            return df
        except Exception as exc:
            logger.exception("Failed to load CSV %s", self.csv_path)
            self._load_problem = f"The CSV could not be read: {exc}"
            return pd.DataFrame()

    def get_unique_tubes(self):
        """Sorted tube numbers present in ``df``."""
        return self._unique_tubes

    def get_unique_dates(self):
        """Sorted measurement dates (Timestamps) present in ``df``."""
        return self._unique_dates

    def get_unique_positions(self):
        """Sorted depth positions (L numbers) present in ``df``."""
        return self._unique_positions

    def get_unique_treatments(self):
        """Sorted treatment labels, or ``[]`` when the CSV has no Treatment column."""
        return self._unique_treatments

    def get_unique_genotypes(self):
        """Sorted genotype labels, or ``[]`` when the CSV has no Genotype column."""
        return self._unique_genotypes


class DataProcessor(MetricDataProcessor):
    """Root-length data processor."""

    value_column = LENGTH_VALUE_COLUMN
    metric_label = "Length"
    unit = "mm"

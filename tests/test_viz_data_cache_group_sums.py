"""DataCache group totals and means are Kahan-compensated, as pandas computed them before 3.1.

pandas 3.1 sums the rows of a frame sorted by its group keys with ``np.add.reduceat``,
without compensation, so tube x date totals moved in the last digit (an export read
``297.53999999999996`` for ``297.54``). ``group_sum`` / ``group_mean`` run the steps of
pandas' Cython ``group_sum`` / ``group_mean`` kernels on every pandas: the kernels are the
oracle here, and they are what pandas 2.x / 3.0 ran for every groupby sum and mean.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.visualization.dash_data_cache import (
    DataCache,
    _kahan_group_reduce,
    group_mean,
    group_sum,
)

libgroupby = pytest.importorskip("pandas._libs.groupby")


def _kernel(values, codes, ngroups, *, mean):
    out = np.zeros((ngroups, 1))
    counts = np.zeros(ngroups, dtype=np.int64)
    column = np.ascontiguousarray(values, dtype=np.float64).reshape(-1, 1)
    if mean:
        libgroupby.group_mean(out, counts, column, codes, min_count=-1)
    else:
        libgroupby.group_sum(out, counts, column, codes, None, min_count=0)
    return out[:, 0]


@pytest.mark.parametrize("mean", [False, True], ids=["sum", "mean"])
def test_the_reduction_equals_pandas_cython_kernel_bit_for_bit(mean):
    rng = np.random.default_rng(7)
    for trial in range(120):
        n, ngroups = int(rng.integers(0, 300)), int(rng.integers(1, 30))
        values = np.round(rng.random(n) * rng.choice([1, 100, 1e6]), int(rng.integers(0, 4)))
        if n and trial % 3 == 0:
            values[rng.random(n) < 0.15] = np.nan  # skipped, as the kernels skip them
        if n and trial % 7 == 0:
            values[rng.integers(0, n)] = np.inf
        low = -1 if trial % 5 == 0 else 0  # -1: a row in no group
        codes = rng.integers(low, ngroups, n).astype(np.intp)
        ours = _kahan_group_reduce(values, codes, ngroups, mean=mean)
        np.testing.assert_array_equal(ours, _kernel(values, codes, ngroups, mean=mean))


def test_a_key_sorted_frame_keeps_the_compensated_totals():
    # Ten 0.1 mm in one tube and date: an uncompensated sum gives 0.9999999999999999.
    frame = pd.DataFrame(
        {
            "Tube": [1] * 10 + [2],
            "Date": pd.to_datetime(["2024-07-18"] * 11).astype("datetime64[ns]"),
            "v": [0.1] * 10 + [2.5],
        }
    )
    totals = group_sum(frame["v"], [frame["Tube"], frame["Date"]])
    assert totals.tolist() == [1.0, 2.5]
    assert group_mean(frame["v"], [frame["Tube"], frame["Date"]]).tolist() == [0.1, 2.5]


def test_the_result_is_shaped_as_pandas_groupby():
    rng = np.random.default_rng(3)
    n = 500
    frame = pd.DataFrame(
        {
            "Tube": rng.integers(1, 20, n),
            "Date": (pd.Timestamp("2024-07-01") + pd.to_timedelta(rng.integers(0, 6, n), "D")),
            "Length (mm)": np.round(rng.random(n) * 50, 2),
        }
    )
    frame["Date"] = frame["Date"].astype("datetime64[ns]")
    reference = frame.groupby(["Tube", "Date"])["Length (mm)"].sum()
    ours = group_sum(frame["Length (mm)"], [frame["Tube"], frame["Date"]])
    pd.testing.assert_index_equal(ours.index, reference.index)
    assert (ours.name, ours.dtype) == (reference.name, reference.dtype)
    level = group_mean(ours, level="Date")
    pd.testing.assert_index_equal(level.index, reference.groupby(level="Date").mean().index)


def test_an_unsorted_frame_gives_what_pandas_gives():
    # On rows not sorted by their keys every pandas runs the Cython kernels.
    rng = np.random.default_rng(11)
    n = 2000
    frame = pd.DataFrame(
        {"Tube": rng.integers(1, 40, n), "Pos": rng.integers(1, 9, n), "v": rng.random(n)}
    )
    for reduce, ours in (("sum", group_sum), ("mean", group_mean)):
        reference = getattr(frame.groupby(["Tube", "Pos"])["v"], reduce)()
        result = ours(frame["v"], [frame["Tube"], frame["Pos"]])
        np.testing.assert_array_equal(result.to_numpy(), reference.to_numpy())


def test_the_cache_totals_and_field_means_are_compensated():
    class _Processor:
        value_column = "Length (mm)"

        def __init__(self, df):
            self.df = df

        def get_unique_tubes(self):
            return sorted(self.df["Tube"].unique())

        def get_unique_dates(self):
            return sorted(self.df["Date"].unique())

    date = pd.Timestamp("2024-07-18")
    df = pd.DataFrame(
        {
            "Date": pd.Series([date] * 21, dtype="datetime64[ns]"),
            "Tube": [1] * 10 + [2] * 10 + [3],
            "Position": list(range(1, 11)) * 2 + [1],
            "Length (mm)": [0.1] * 20 + [0.7],
        }
    )
    cache = DataCache(_Processor(df))
    assert cache.tube_date_groups.tolist() == [1.0, 1.0, 0.7]
    field = cache.get_field_growth()
    assert field["mean"].tolist() == [group_mean(cache.tube_date_groups, level="Date").iloc[0]]
    assert list(field.columns) == ["Date", "mean", "std", "count"]

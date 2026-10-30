"""DataCache aggregates for either metric (AR-15) and its interval tables (PV-01/PV-17).

The fixture is small enough to derive every expected value by hand:

    tube  position  date  value
    1     5         A     2.0     \\
    1     7         A     4.0      > tube 1, date A: L1-10 = {2, 4}, L11-20 empty, L21-30 = {10}
    1     25        A     10.0    /
    2     12        A     6.0     \\ tube 2, date A: L11-20 = {6, 8}
    2     18        A     8.0     /
    1     3         B     1.0     \\ tube 1, date B: L1-10 = {1}, L11-20 = {3}
    1     14        B     3.0     /
    2     9         B     5.0       tube 2, date B: L1-10 = {5}
"""

import csv
import math

import pandas as pd
import pytest

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.data_processing.data_processor_area import DataProcessorArea
from app.visualization.dash_data_cache import DataCache

A = pd.Timestamp("2024-07-18")
B = pd.Timestamp("2024-08-07")
ROWS = [
    (1, 5, "2024.07.18", 2.0),
    (1, 7, "2024.07.18", 4.0),
    (1, 25, "2024.07.18", 10.0),
    (2, 12, "2024.07.18", 6.0),
    (2, 18, "2024.07.18", 8.0),
    (1, 3, "2024.08.07", 1.0),
    (1, 14, "2024.08.07", 3.0),
    (2, 9, "2024.08.07", 5.0),
]
SQRT2 = math.sqrt(2.0)


def write_metric_csv(path, headers, rows=ROWS):
    """Write ``rows`` (tube, position, date, value) with the app's CSV headers."""
    value_column = headers[5]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for tube, position, date, value in rows:
            writer.writerow(
                {
                    "Image": f"CAM_T{tube:03d}_L{position:03d}_{date}_120000_001",
                    "Tube": tube,
                    "Position": position,
                    "Date": date,
                    "Time": "12:00:00",
                    value_column: value,
                    "Error": "",
                }
            )
    return str(path)


@pytest.fixture(params=["length", "area"])
def processor(request, tmp_path):
    if request.param == "length":
        return DataProcessor(write_metric_csv(tmp_path / "root_lengths.csv", LENGTH_CSV_HEADERS))
    return DataProcessorArea(write_metric_csv(tmp_path / "root_areas.csv", AREA_CSV_HEADERS))


def test_tube_date_totals_sum_the_metric_column(processor):
    cache = DataCache(processor)

    assert cache.tubes == (1, 2)
    assert cache.dates == (A, B)
    assert cache.tube_date_groups.to_dict() == {
        (1, A): 16.0,
        (2, A): 14.0,
        (1, B): 4.0,
        (2, B): 5.0,
    }


def test_interval_table_is_contiguous_with_empty_bins(processor):
    table = DataCache(processor).get_interval_data(1, A, 10)

    assert sorted(table) == [10, 20, 30]
    assert table[10] == {
        "avg": pytest.approx(3.0),
        "min": pytest.approx(2.0),
        "max": pytest.approx(4.0),
        "std": pytest.approx(SQRT2),
        "count": 2,
        "interval_start": 1,
        "interval_end": 10,
    }
    assert table[20]["count"] == 0
    assert math.isnan(table[20]["avg"]) and math.isnan(table[20]["std"])
    assert (table[20]["interval_start"], table[20]["interval_end"]) == (11, 20)
    assert table[30]["avg"] == pytest.approx(10.0)
    assert table[30]["count"] == 1
    assert math.isnan(table[30]["std"])  # sample std of one value


def test_interval_table_for_missing_pair_is_empty(processor):
    assert DataCache(processor).get_interval_data(3, A, 10) == {}


def test_interval_table_is_a_copy(processor):
    cache = DataCache(processor)
    cache.get_interval_data(1, A, 10)[10]["avg"] = -1.0

    assert cache.get_interval_data(1, A, 10)[10]["avg"] == pytest.approx(3.0)


def test_field_intervals_pool_every_tube_of_a_date(processor):
    field = DataCache(processor).get_field_interval_data(A, 10)

    assert sorted(field) == [10, 20, 30]
    assert field[10]["avg"] == pytest.approx(3.0)
    assert field[10]["std"] == pytest.approx(SQRT2)
    assert field[20]["avg"] == pytest.approx(7.0)  # tube 2's L12/L18
    assert field[20]["std"] == pytest.approx(SQRT2)
    assert field[20]["count"] == 2
    assert field[30]["avg"] == pytest.approx(10.0)
    assert math.isnan(field[30]["std"])
    assert (field[20]["interval_start"], field[20]["interval_end"]) == (11, 20)


def test_field_intervals_for_unmeasured_date_are_empty(processor):
    assert DataCache(processor).get_field_interval_data(pd.Timestamp("2030-01-01"), 10) == {}


# A real bin (2024 EF tube 6, 2024-08-07, L70..L61 in CSV row order) whose exact mean
# is 10.445: pandas' Series.mean() (numpy pairwise sum) gives 10.444999999999999 ->
# "10.44", while a groupby mean (Kahan sum) gives 10.445 -> "10.45". The charts
# always showed the Series result, so the cache must reproduce it bit for bit.
ROUNDING_BIN = [18.87, 36.43, 35.55, 4.37, 0.0, 0.21, 0.0, 0.0, 0.0, 9.02]


def test_interval_statistics_match_pandas_series_reductions_exactly(tmp_path):
    rows = [(6, 70 - i, "2024.08.07", value) for i, value in enumerate(ROUNDING_BIN)]
    cache = DataCache(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, rows)))
    reference = pd.Series(ROUNDING_BIN)

    for stats in (
        cache.get_interval_data(6, B, 10)[70],
        cache.get_field_interval_data(B, 10)[70],
    ):
        assert stats["avg"] == reference.mean()  # bitwise, not approx
        assert stats["std"] == reference.std()
        assert f"{stats['avg']:.2f}" == "10.44"


def test_availability_marks_measured_tube_dates(tmp_path):
    rows = [r for r in ROWS if not (r[0] == 2 and r[2] == "2024.08.07")]
    cache = DataCache(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, rows)))

    assert cache.get_tube_date_availability() == {
        (1, A): True,
        (1, B): True,
        (2, A): True,
        (2, B): False,
    }


def test_refresh_drops_memoized_intervals(tmp_path):
    processor = DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS))
    cache = DataCache(processor)
    assert cache.get_interval_data(2, B, 10)[10]["avg"] == pytest.approx(5.0)

    processor.df.loc[processor.df["Tube"] == 2, "Length (mm)"] = 50.0
    cache.refresh()

    assert cache.get_interval_data(2, B, 10)[10]["avg"] == pytest.approx(50.0)
    assert cache.tube_date_groups[(2, B)] == pytest.approx(50.0)

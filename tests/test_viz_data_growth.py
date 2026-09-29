"""Growth-over-time aggregates served from DataCache, and the per-instance figure memo (PV-17).

Field-average semantics (F-034) are pinned by hand: measurement dates within +-3 days
merge greedily from the earliest unassigned date; the representative date is the one
with the most measured tubes (earliest on ties); a tube measured on two merged dates
contributes the MEAN of its two totals.
"""

import math

import pandas as pd
import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.visualization.dash_data_cache import DataCache, merge_nearby_dates
from app.visualization.dash_visualizations import DashVisualizations
from tests.test_viz_data_cache import write_metric_csv

D18, D19, D21, D24 = (pd.Timestamp(f"2024-07-{d}") for d in (18, 19, 21, 24))
AUG = pd.Timestamp("2024-08-07")
ROWS = [
    (1, 5, "2024.07.18", 10.0),
    (1, 5, "2024.07.19", 20.0),  # tube 1 re-measured the next day
    (2, 5, "2024.07.19", 30.0),
    (3, 5, "2024.07.19", 40.0),
    (1, 5, "2024.08.07", 5.0),
    (2, 5, "2024.08.07", 7.0),
]


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


@pytest.fixture
def charts(tmp_path):
    return _Charts(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, ROWS)))


def test_merge_picks_the_date_with_most_tubes():
    assert merge_nearby_dates({D18: 1, D19: 3, AUG: 2}) == {D18: D19, D19: D19, AUG: AUG}


def test_merge_breaks_ties_toward_the_earliest_date():
    assert merge_nearby_dates({D19: 2, D18: 2}) == {D18: D18, D19: D18}


def test_merge_windows_are_anchored_on_the_earliest_unassigned_date():
    # 07-24 is within 3 days of 07-21 but not of the 07-18 anchor
    assert merge_nearby_dates({D18: 1, D21: 1, D24: 1}) == {D18: D18, D21: D18, D24: D24}


def test_field_growth_averages_a_remeasured_tube(charts):
    field = charts.data_cache.get_field_growth()

    assert list(field["Date"]) == [D19, AUG]
    assert list(field["mean"]) == pytest.approx([85.0 / 3.0, 6.0])  # (15 + 30 + 40) / 3
    assert list(field["std"]) == pytest.approx([math.sqrt(475.0 / 3.0), math.sqrt(2.0)])
    assert list(field["count"]) == [3, 2]


def test_time_view_merges_dates_only_for_the_field_average(charts):
    fig = charts.show_growth_over_time([1], True, False, True)

    traces = {trace.name: trace for trace in fig.data}
    assert set(traces) == {"Field Average", "Tube 1"}
    assert list(pd.to_datetime(traces["Field Average"].x)) == [D19, AUG]
    assert list(pd.to_datetime(traces["Tube 1"].x)) == [D18, D19, AUG]
    assert list(traces["Tube 1"].y) == [10.0, 20.0, 5.0]


def test_time_view_without_field_statistics_or_tubes_is_empty(charts):
    assert charts.show_growth_over_time([], False, False, True).data == ()


def test_identical_requests_reuse_the_built_figure(charts):
    first = charts.show_growth_over_time([1, 2], True, True, True)

    assert charts.show_growth_over_time([1, 2], True, True, True) is first
    assert charts.show_growth_over_time([1, 2], True, True, False) is not first
    assert charts.create_faceted_depth_profile([1]) is charts.create_faceted_depth_profile([1])
    assert charts.show_growth_lines(1) is charts.show_growth_lines(1)


def test_refresh_invalidates_memoized_figures(charts):
    before = charts.show_growth_over_time([2], False, False, True)
    df = charts.data_processor.df
    df.loc[df["Tube"] == 2, "Length (mm)"] = 100.0

    charts.data_cache.refresh()
    after = charts.show_growth_over_time([2], False, False, True)

    assert list(before.data[0].y) == [30.0, 7.0]
    assert list(after.data[0].y) == [100.0, 100.0]

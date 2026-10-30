"""AR-15: the length dashboard's figure builders serve either metric.

DashVisualizations reads the metric column, label and unit from its processor,
so the same builders chart root length ("Length", mm) and root area ("Area", mm²).
Values come from the hand-derived fixture in test_viz_data_cache (tube 1 date A:
L1-10 = {2, 4}, L11-20 empty, L21-30 = {10}; tube 2 date A: L11-20 = {6, 8}; tube 1
date B: L1-10 = {1}, L11-20 = {3}; tube 2 date B: L1-10 = {5}).
"""

import math

import pandas as pd
import pytest

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.data_processing.data_processor_area import DataProcessorArea
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_visualizations import DashVisualizations
from tests.test_viz_data_cache import write_metric_csv

METRICS = {
    "length": (DataProcessor, LENGTH_CSV_HEADERS, "Length", "mm"),
    "area": (DataProcessorArea, AREA_CSV_HEADERS, "Area", "mm²"),
}


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


@pytest.fixture(params=sorted(METRICS))
def metric(request, tmp_path):
    processor_cls, headers, label, unit = METRICS[request.param]
    charts = _Charts(processor_cls(write_metric_csv(tmp_path / "m.csv", headers)))
    return charts, label, unit


def test_stacked_chart_totals_per_tube_and_date(metric):
    charts, label, unit = metric
    fig = charts.create_stacked_bar_chart()

    assert fig.layout.title.text == f"Root {label} by Tube and Date"
    assert fig.layout.yaxis.title.text == f"Total {label} ({unit})"
    bars = {trace.name: list(trace.y) for trace in fig.data}
    assert bars == {"2024-07-18": [16.0, 14.0], "2024-08-07": [4.0, 5.0]}
    assert f"{label}\u2002<b>%{{y:.2f}} {unit}</b>" in fig.data[0].hovertemplate


def test_growth_over_time_per_tube_and_field_average(metric):
    charts, label, unit = metric
    fig = charts.show_growth_over_time([1, 2], True, True, True)

    assert fig.layout.yaxis.title.text == f"Total {label} ({unit})"
    traces = {trace.name: trace for trace in fig.data}
    assert list(traces["Tube 1"].y) == [16.0, 4.0]
    assert list(traces["Tube 2"].y) == [14.0, 5.0]
    assert f"Total {label}\u2002<b>%{{y:.2f}} {unit}</b>" in traces["Tube 1"].hovertemplate
    field = traces["Field Average"]
    assert list(field.y) == pytest.approx([15.0, 4.5])  # mean of the tube totals per date
    assert [row[0] for row in field.customdata] == pytest.approx([math.sqrt(2), math.sqrt(0.5)])
    assert f"Avg {label}\u2002<b>%{{y:.2f}} {unit}</b>" in field.hovertemplate


def test_growth_lines_average_each_depth_interval(metric):
    charts, label, unit = metric
    fig = charts.show_growth_lines(1)

    assert fig.layout.xaxis.title.text == f"Root {label} ({unit})"
    first = next(trace for trace in fig.data if trace.name == "2024-07-18")
    assert list(first.y) == [10, 20, 30]
    assert first.x[0] == pytest.approx(3.0)
    assert math.isnan(first.x[1])  # L11-20 was not measured on this date
    assert first.x[2] == pytest.approx(10.0)
    assert f"{unit}</b>" in first.text[0] and "Average" in first.text[0]


def test_faceted_profile_pairs_field_and_tube_bars(metric):
    charts, label, unit = metric
    fig = charts.create_faceted_depth_profile([1, 2], [pd.Timestamp("2024-07-18")])

    tube = [t for t in fig.data if t.name == "Tube"]
    field = [t for t in fig.data if t.name == "Field Avg"]
    assert [list(t.y) for t in tube] == [[10, 30], [20]]
    assert [list(t.x) for t in tube] == [pytest.approx([3.0, 10.0]), pytest.approx([7.0])]
    assert [list(t.x) for t in field] == [pytest.approx([-3.0, -10.0]), pytest.approx([-7.0])]
    assert [list(t.customdata[0]) for t in tube] == [[1, 10, 2], [11, 20, 2]]
    assert f"Avg {label}: %{{x:.2f}} {unit}" in tube[0].hovertemplate
    assert f"Avg {label}: %{{customdata[2]:.2f}} {unit}" in field[0].hovertemplate

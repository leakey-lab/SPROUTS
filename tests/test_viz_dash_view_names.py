"""O27 / D7: both dashboards name their views, titles and legends with one set of words,
and a measurement date has one colour (a place on the blue ramp) in every view."""

from __future__ import annotations

import pytest

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.data_processing.data_processor_area import DataProcessorArea
from app.visualization import theme
from app.visualization.dash_app_area import AREA_SHELL, DashAppArea
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_length_layout import LENGTH_SHELL
from app.visualization.dash_visualizations import DashVisualizations
from tests.test_viz_data_cache import ROWS, write_metric_csv


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


@pytest.fixture
def length(tmp_path):
    return _Charts(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS)))


@pytest.fixture
def area(tmp_path):
    return DashAppArea(
        DataProcessorArea(write_metric_csv(tmp_path / "a.csv", AREA_CSV_HEADERS)), str(tmp_path)
    )


def test_both_dashboards_label_their_views_with_the_same_words():
    length = {v.value: v.label for v in LENGTH_SHELL.views}
    area = {v.value: v.label for v in AREA_SHELL.views}
    assert length == {
        "stacked": "Totals by Tube",
        "time": "Growth Over Time",
        "lines": "Depth Profile",
        "faceted": "Depth Profile Grid",
    }
    assert area == {
        "stacked": "Totals by Tube",
        "time": "Growth Over Time",
        "profile": "Depth Profile",
    }
    assert length["stacked"] == area["stacked"]
    assert length[LENGTH_SHELL.tube_view] == area[AREA_SHELL.tube_view]


def test_chart_titles_use_one_scheme(length, area):
    assert length.create_stacked_bar_chart().layout.title.text == "Root Length by Tube and Date"
    assert area.create_stacked_bar_chart().layout.title.text == "Root Area by Tube and Date"
    time = length.show_growth_over_time([1], True, True, True)
    assert time.layout.title.text == "Root Length Growth Over Time"
    assert area.show_growth_over_time().layout.title.text == "Root Area Growth Over Time"
    assert length.show_growth_lines(1).layout.title.text == "Root Length Depth Profile - Tube 1"
    assert area.show_area_profile(1).layout.title.text == "Root Area Depth Profile - Tube 1"
    faceted = length.create_faceted_depth_profile([1, 2])
    assert faceted.layout.title.text == (
        "Depth Profile Grid - Field Average (Left) vs Tube (Right)"
    )


def test_legend_title_and_depth_axes_agree(length, area):
    for fig in (
        length.create_stacked_bar_chart(),
        length.show_growth_lines(1),
        area.create_stacked_bar_chart(),
        area.show_area_profile(1),
    ):
        assert fig.layout.legend.title.text == "Measurement date"
    assert length.show_growth_lines(1).layout.yaxis.title.text == "Position (L)"
    assert area.show_area_profile(1).layout.xaxis.title.text == "Position (L)"


def test_hover_dates_are_iso(length):
    for fig in (
        length.create_stacked_bar_chart(),
        length.show_growth_over_time([1, 2], True, True),
    ):
        assert "%b" not in fig.to_json()


def _two_tube_rows(extra_dates=()):
    """ROWS (dates A, B) plus tube-1-only rows on later dates; tube 2 misses the first date."""
    rows = [row for row in ROWS if not (row[0] == 2 and row[2] == "2024.07.18")]
    rows += [(1, 5, date, 1.0) for date in extra_dates]
    return rows


def test_a_date_has_one_colour_in_every_view_and_every_tube(tmp_path):
    rows = _two_tube_rows(["2024.09.01"])
    length = _Charts(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, rows)))
    area = DashAppArea(
        DataProcessorArea(write_metric_csv(tmp_path / "a.csv", AREA_CSV_HEADERS, rows)),
        str(tmp_path),
    )
    for dashboard in (length, area):
        cache = dashboard.data_cache
        ramp = theme.date_colors(len(cache.dates))
        names = [d.strftime("%Y-%m-%d") for d in cache.dates]
        stacked = {t.name: t.marker.color for t in dashboard.create_stacked_bar_chart().data}
        assert stacked == dict(zip(names, ramp, strict=True))
    ramp = theme.date_colors(len(length.data_cache.dates))
    names = [d.strftime("%Y-%m-%d") for d in length.data_cache.dates]
    lines = {t.name: t.line.color for t in length.show_growth_lines(1).data}
    assert lines == dict(zip(names, ramp, strict=True))
    profile = {
        tube: {t.name: t.marker.color for t in area.show_area_profile(tube).data} for tube in (1, 2)
    }
    assert set(profile[2]) == {"2024-08-07"}  # tube 2 has no row on the first and last date
    assert profile[1] == dict(zip(names, ramp, strict=True))
    assert profile[2]["2024-08-07"] == profile[1]["2024-08-07"] == ramp[1]


def test_more_than_13_dates_stay_on_the_ramp(tmp_path):
    days = [f"2024.09.{day:02d}" for day in range(1, 16)]
    rows = [(1, 5, day, 1.0) for day in days]
    charts = _Charts(DataProcessor(write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, rows)))
    colours = [t.marker.color for t in charts.create_stacked_bar_chart().data]
    assert len(colours) == 15 and set(colours) <= set(theme.date_colors(15))
    assert colours[0] != colours[-1]

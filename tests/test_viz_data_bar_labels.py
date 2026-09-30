"""viz-data: bar value labels only where they can be read (PKG-VIZ, V2).

Every bar of the stacked and profile charts carried its value as a text label.
With the owner's ~170 tubes those labels shrink to unreadable specks, and each
one costs Plotly a text measurement: on a 46k-row synthetic trial the stacked
chart's first render took 795 ms with labels and 314 ms without (median of 5,
QtWebEngine). Labels are kept when at most ``theme.MAX_LABELLED_BARS`` bars share
the axis; the values stay in the hover either way.
"""

from __future__ import annotations

import os

import pytest

from app.visualization import theme
from tests.viz_dash_helpers import AREA_COLUMN, LENGTH_COLUMN, write_metric_csv


def _rows(tubes: int, positions: int = 1, dates=("2024.07.18",)):
    return tuple(
        (f"T{t:03d}_L{p:03d}_{d}", t, p, d, float(t + p))
        for t in range(1, tubes + 1)
        for p in range(1, positions + 1)
        for d in dates
    )


def _length_charts(tmp_path, rows):
    from app.data_processing.data_processor import DataProcessor
    from app.visualization.dash_app import DashApp
    from tests.viz_dash_helpers import FakeImageManager

    csv = write_metric_csv(os.path.join(tmp_path, "root_lengths.csv"), LENGTH_COLUMN, rows)
    return DashApp(DataProcessor(csv), str(tmp_path), FakeImageManager())


def _area_charts(tmp_path, rows):
    from app.data_processing.data_processor_area import DataProcessorArea
    from app.visualization.dash_app_area import DashAppArea

    csv = write_metric_csv(os.path.join(tmp_path, "root_areas.csv"), AREA_COLUMN, rows)
    return DashAppArea(DataProcessorArea(csv), str(tmp_path))


def _labelled(figure) -> bool:
    bars = [trace for trace in figure.data if trace.type == "bar"]
    assert bars
    shown = {trace.textposition != "none" and trace.text is not None for trace in bars}
    assert len(shown) == 1, "all bars of a chart are labelled alike"
    return shown.pop()


@pytest.mark.parametrize("build", [_length_charts, _area_charts], ids=["length", "area"])
def test_stacked_bars_keep_their_labels_for_a_few_tubes(tmp_path, build):
    figure = build(tmp_path, _rows(theme.MAX_LABELLED_BARS)).create_stacked_bar_chart()
    assert _labelled(figure)
    assert figure.data[0].text[0] == "2.00"  # tube 1, L1: 1 + 1


@pytest.mark.parametrize("build", [_length_charts, _area_charts], ids=["length", "area"])
def test_stacked_bars_drop_labels_that_cannot_be_read(tmp_path, build):
    figure = build(tmp_path, _rows(theme.MAX_LABELLED_BARS + 1)).create_stacked_bar_chart()
    assert not _labelled(figure)


def test_area_profile_counts_every_bar_of_a_group(tmp_path):
    # 16 positions x 2 dates = 32 bars side by side: too narrow for labels.
    dates = ("2024.07.18", "2024.08.07")
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few = _area_charts(tmp_path / "few", _rows(1, positions=15, dates=dates[:1]))
    many = _area_charts(tmp_path / "many", _rows(1, positions=16, dates=dates))

    assert _labelled(few.show_area_profile(1))
    assert not _labelled(many.show_area_profile(1))

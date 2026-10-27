"""The metrics bar never shows a value that is not a number (KNOWN_ISSUES #7).

A Measure CSV edited to hold ``nan`` (or ``inf``) showed "nan mm" in the LENGTH cell. The
bar shows measured values read-only and computes nothing, so such a value is shown as
the dash in the failure colour, with a tooltip that says the CSV holds no valid number.
"""

from __future__ import annotations

import pytest

from app.gui.metrics_bar import MetricsBar
from app.gui.widgets import tokens
from tests.qt_teardown import delete_widgets

LENGTHS = "D:/p/skeletons/root_lengths.csv"
AREAS = "D:/p/mask/root_areas.csv"


@pytest.fixture
def bar(app):
    made = [MetricsBar()]
    yield made[0]
    delete_widgets(made, close=False)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_length_that_is_not_a_number_shows_a_dash(bar, bad):
    bar.set_metrics(length=bad, area=16.41, source=LENGTHS, area_source=AREAS)

    assert bar._length_value.text() == "—"
    assert not bar._length_cell.isHidden()
    assert tokens.DANGER in bar._length_value.styleSheet()
    tip = bar._length_cell.toolTip()
    assert "root_lengths.csv" in tip and "no valid number" in tip
    assert bar._area_value.text() == "16.41 mm²"  # the other value is untouched


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_an_area_that_is_not_a_number_shows_a_dash(bar, bad):
    bar.set_metrics(length=18.77, area=bad, source=LENGTHS, area_source=AREAS)

    assert bar._area_value.text() == "—"
    assert not bar._area_cell.isHidden()
    assert "nan" not in bar._area_value.text() and "inf" not in bar._area_value.text()
    tip = bar._area_cell.toolTip()
    assert "root_areas.csv" in tip and "no valid number" in tip
    assert bar._length_value.text() == "18.77 mm"


def test_a_value_that_is_not_a_number_is_never_flagged_outdated(bar):
    bar.set_metrics(length=float("nan"), stale=True, source=LENGTHS)

    assert bar._length_value.property("stale") is False
    assert "Measure again" not in bar._length_cell.toolTip()


def test_a_valid_value_afterwards_shows_normally(bar):
    bar.set_metrics(length=float("nan"), area=float("nan"), source=LENGTHS)
    bar.set_metrics(length=18.77, area=16.41, source=LENGTHS)

    assert bar._length_value.text() == "18.77 mm"
    assert tokens.DANGER not in bar._length_value.styleSheet()
    assert "no valid number" not in bar._length_cell.toolTip()
    assert bar._area_value.text() == "16.41 mm²"

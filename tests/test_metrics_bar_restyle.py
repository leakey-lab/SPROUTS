"""A metric value is restyled only when its colour changes.

Every Library click sets the LENGTH and AREA values again; each ``setStyleSheet`` re-polishes
its label, so the bar restyled two labels per click even when their colour stayed the same.
The style sheet is now set only when it changes; the label's colour and its ``stale``
property are the same as before.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel

from app.gui import metrics_bar
from app.gui.metrics_bar import MetricsBar
from app.gui.widgets import tokens
from tests.qt_teardown import delete_widgets


@pytest.fixture
def bar(app):
    made = MetricsBar()
    yield made
    delete_widgets([made])


@pytest.fixture
def restyled(monkeypatch):
    """The value labels given a style sheet, in order."""
    seen: list[QLabel] = []
    real = QLabel.setStyleSheet

    def spy(label, qss):
        seen.append(label)
        real(label, qss)

    monkeypatch.setattr(QLabel, "setStyleSheet", spy)
    return seen


def test_the_same_colour_is_not_set_again(bar, restyled):
    bar.set_metrics(length=12.5, area=3.25, status="Mask · Skeleton", name="img")
    first = list(restyled)
    restyled.clear()
    bar.set_metrics(length=13.0, area=4.0, status="Mask · Skeleton", name="other")

    assert first == [] or set(first) <= {bar._length_value, bar._area_value}
    assert restyled == []  # values change, colours do not
    assert bar._length_value.text() == "13.00 mm"
    assert bar._length_value.styleSheet() == metrics_bar._value_qss(tokens.ACCENT)
    assert bar._area_value.styleSheet() == metrics_bar._value_qss(tokens.TEXT)


def test_a_colour_change_restyles_the_value(bar, restyled):
    bar.set_metrics(length=12.5, area=3.25, name="img")
    restyled.clear()

    bar.set_metrics(length=12.5, area=3.25, name="img", stale=True, area_stale=False)
    assert restyled == [bar._length_value]
    assert bar._length_value.styleSheet() == metrics_bar._value_qss(tokens.WARN)
    assert bar._length_value.property("stale") is True

    restyled.clear()
    bar.set_metrics(length=12.5, area=3.25, name="img")
    assert restyled == [bar._length_value]
    assert bar._length_value.styleSheet() == metrics_bar._value_qss(tokens.ACCENT)
    assert bar._length_value.property("stale") is False


def test_a_failed_value_and_back_restyles_both_ways(bar, restyled):
    bar.set_metrics(length=12.5, name="img")
    restyled.clear()
    bar.set_metrics(length=None, name="img", error="unreadable")
    assert bar._length_value.text() == "failed"
    assert bar._length_value.styleSheet() == metrics_bar._value_qss(tokens.DANGER)
    bar.set_metrics(length=12.5, name="img")
    assert bar._length_value.styleSheet() == metrics_bar._value_qss(tokens.ACCENT)
    assert restyled == [bar._length_value, bar._length_value]

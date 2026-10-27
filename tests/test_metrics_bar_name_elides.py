"""The metrics bar's image-name cell must elide, not set the window's minimum width.

Review finding (UI F5 / integrator note): once measured length and area were shown, the
never-shortened image name made the bar need ~850 px, so the whole window refused to
shrink below 1058 px.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from app.gui.metrics_bar import MetricsBar, _ElidedLabel

pytestmark = pytest.mark.usefixtures("app")

LONG_NAME = "24EFCAM1_T001_L011_2024.07.18_111618_001_SMM_with_a_very_long_suffix_rep2"


def _bar_with_everything() -> MetricsBar:
    bar = MetricsBar()
    bar.set_metrics(length=123.45, area=67.89, status="Skeleton ready", name=LONG_NAME)
    return bar


def test_long_name_does_not_widen_the_bar_minimum():
    bar = _bar_with_everything()
    short = MetricsBar()
    short.set_metrics(length=123.45, area=67.89, status="Skeleton ready", name="img")
    # The long name may cost its elided stub (the label's MIN_WIDTH in place of the short
    # name's own width, whatever the font) but never its full width.
    stub = max(0, _ElidedLabel.MIN_WIDTH - short._name_value.minimumSizeHint().width())
    assert bar.minimumSizeHint().width() <= short.minimumSizeHint().width() + stub


def test_name_keeps_full_text_and_tooltip_when_elided():
    bar = _bar_with_everything()
    bar.resize(520, MetricsBar.HEIGHT)
    bar.show()
    QApplication.processEvents()
    assert bar._name_value.text() == LONG_NAME
    assert bar._name_value.toolTip() == LONG_NAME
    bar.close()

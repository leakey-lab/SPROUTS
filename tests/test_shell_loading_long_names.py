"""A long folder name neither clips the loading card nor widens the window (R-SHELL-B).

Wave 1 started naming the folder being loaded on the loading card and on the empty state.
The card is 360 px wide and its name line was a plain QLabel: a 67-character export folder
name was cut mid-letter at the card's edge ("…T096_raw_e"). On the empty state an
unbreakable name set the label's minimum width (997 px for 161 characters), which the
window's minimum width then followed. Both now elide the middle; the tooltip keeps the name.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.gui.empty_state import EmptyStateWidget  # noqa: E402
from app.gui.loading_overlay import LoadingOverlay  # noqa: E402

LONG = "Minirhizotron_EnergyFarm_2024_all_tubes_T001-T096_raw_exports_CI600"
HUGE = "x" * 40 + "_" + "y" * 120


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def card(app):
    host = QWidget()
    host.resize(1280, 800)
    host.show()
    overlay = LoadingOverlay(host)
    yield overlay
    host.close()
    host.deleteLater()


@pytest.mark.parametrize("name", [LONG, HUGE])
def test_the_card_elides_a_long_folder_name_instead_of_cutting_it(card, name):
    card.set_filename(name, tooltip=rf"D:\data\{name}")
    card.start("Loading images")
    QApplication.processEvents()
    label = card._filename_label
    assert label.text() == name  # the full name stays the label's text
    assert label.toolTip() == rf"D:\data\{name}"
    assert label.minimumSizeHint().width() <= label.width()
    shown = label.elided_text()
    assert "…" in shown and shown != name
    assert label.fontMetrics().horizontalAdvance(shown) <= label.contentsRect().width()
    assert shown.startswith(name[:6]) and shown.endswith(name[-4:])  # head and tail kept
    assert card.width() == 360


def test_a_short_name_is_shown_whole(card):
    card.set_filename("trial_2024_field_B")
    card.start("Loading images")
    QApplication.processEvents()
    assert card._filename_label.elided_text() == "trial_2024_field_B"


def test_a_huge_folder_name_does_not_widen_the_empty_state(app):
    empty = EmptyStateWidget(lambda: None)
    try:
        before = empty.minimumSizeHint().width()
        empty.set_loading(HUGE)
        assert empty.minimumSizeHint().width() <= max(before, 480)
        assert empty.subtitle_label.toolTip() == HUGE
        assert "…" in empty.subtitle_label.text()
        empty.set_loading("trial_2024_field_B")
        assert empty.subtitle_label.text() == "trial_2024_field_B"
        empty.set_loading(None)
        assert empty.subtitle_label.text() == EmptyStateWidget.SUBTITLE
        assert empty.subtitle_label.toolTip() == ""
    finally:
        empty.deleteLater()

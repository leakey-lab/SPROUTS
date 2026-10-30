"""The folder-loading card sits below the top bar and says what it loads (H6).

Before (1280 x 800): the card was placed 18 px below the window's top edge, over the
stage buttons, and its two detail lines were never filled, so ~50 px of the card stayed
blank under the progress bar.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication, QWidget

from app.gui.loading_overlay import LoadingOverlay
from tests.qt_teardown import delete_widgets
from tests.window_support import build_window, quiet_window


def test_empty_detail_lines_take_no_room(app):
    host = QWidget()
    host.resize(800, 600)
    card = LoadingOverlay(host)
    card.start("Loading images")
    QApplication.processEvents()
    bare = card.sizeHint().height()
    assert card._filename_label.isHidden() and card._count_label.isHidden()

    card.set_filename("project_2024")
    QApplication.processEvents()
    assert not card._filename_label.isHidden()
    assert card.sizeHint().height() > bare
    card.set_filename("")
    assert card._filename_label.isHidden()
    host.deleteLater()


def test_the_card_keeps_below_a_top_offset(app):
    host = QWidget()
    host.resize(800, 600)
    card = LoadingOverlay(host, top_offset=lambda: 46)
    card.start("Loading images")
    assert card.y() >= 46
    host.deleteLater()


def test_the_detail_lines_sit_in_the_card_under_the_bar(app):
    """The lines join the card's own column (a stray layout would leave them a window of
    their own, outside the card)."""
    host = QWidget()
    host.resize(800, 600)
    card = LoadingOverlay(host)
    card.start("Loading images")
    card.set_filename("project_2024", "D:/data/project_2024")
    card.set_count(3, 9)
    QApplication.processEvents()
    frame = card.findChild(QWidget, "progFrame")
    for label in (card._filename_label, card._count_label):
        assert not label.isWindow() and label.parentWidget() is frame
        assert frame.rect().contains(label.geometry())
        assert label.geometry().top() >= card._bar.geometry().bottom()
    host.deleteLater()


@pytest.fixture
def window(app, tmp_path, no_prompts):
    made = [build_window(tmp_path / "s.ini", show=False)]
    made[0].show()
    yield made[0]
    made[0].loading_overlay.hide()
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def test_opening_a_folder_shows_its_name_below_the_top_bar(window, tmp_path, monkeypatch):
    mw = window
    folder = tmp_path / "trial_2024_field_B"
    folder.mkdir()
    monkeypatch.setattr(mw.image_manager, "load_images", lambda _folder: None)  # scan pending
    mw.open_path(str(folder))
    QApplication.processEvents()

    card = mw.loading_overlay
    assert card.isVisible()
    assert card._filename_label.text() == "trial_2024_field_B"
    assert card._filename_label.toolTip() == str(folder)
    topbar = QRect(mw.ribbon.mapTo(mw, QPoint(0, 0)), mw.ribbon.size())
    assert not card.geometry().intersects(topbar)


def test_the_empty_state_says_loading_while_the_first_folder_loads(window, tmp_path, monkeypatch):
    mw = window
    empty = mw.empty_state
    assert empty.title_label.text() == "No images loaded" and not empty.load_button.isHidden()
    folder = tmp_path / "trial_2024_field_B"
    folder.mkdir()
    monkeypatch.setattr(mw.image_manager, "load_images", lambda _folder: None)  # scan pending
    mw.open_path(str(folder))
    QApplication.processEvents()

    assert mw.display_page_stack.currentWidget() is empty
    assert empty.title_label.text() == "Loading images…"
    assert empty.subtitle_label.text() == "trial_2024_field_B"
    assert empty.load_button.isHidden()  # no "Load Images" under the loading card

    mw.on_loading_empty(str(folder))  # nothing there: back to the invitation
    assert empty.title_label.text() == "No images loaded"
    assert not empty.load_button.isHidden()

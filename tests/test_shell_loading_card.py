"""The folder-loading card sits below the top bar and says what it loads (H6).

Before (1280 x 800): the card was placed 18 px below the window's top edge, over the
stage buttons, and its two detail lines were never filled, so ~50 px of the card stayed
blank under the progress bar.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui.loading_overlay import LoadingOverlay  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    """Widgets a test deleteLater()s are destroyed before the next test (never at exit)."""
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    mw = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    mw.resize(1280, 800)
    mw.show()
    yield mw
    mw.loading_overlay.hide()
    mw.close()
    mw.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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

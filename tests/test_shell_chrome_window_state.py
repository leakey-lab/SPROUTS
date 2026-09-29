"""WindowStateStore: geometry, splitter and last-folder persistence (UXS-04 step 5)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSettings, QSize, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSplitter  # noqa: E402

from app.gui.window_state import WindowStateStore, initial_geometry  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "state.ini"), QSettings.Format.IniFormat)


def _window():
    win = QMainWindow()
    split = QSplitter(Qt.Orientation.Horizontal)
    split.addWidget(QLabel("a"))
    split.addWidget(QLabel("b"))
    win.setCentralWidget(split)
    return win, split


def test_initial_geometry_is_90_percent_and_centred():
    rect = initial_geometry(QRect(0, 0, 1000, 800), QSize(400, 300))
    assert rect.size() == QSize(900, 720)
    assert rect.center() == QRect(0, 0, 1000, 800).center()


def test_initial_geometry_clamps_to_minimum_and_stays_on_screen():
    avail = QRect(10, 20, 1093, 614)
    rect = initial_geometry(avail, QSize(900, 600))
    assert rect.width() >= 900 and rect.height() >= 600
    assert avail.contains(rect)


def test_first_run_uses_the_default_geometry(app, settings):
    win, split = _window()
    store = WindowStateStore(settings)
    assert store.restore(win, split) is False
    avail = win.screen().availableGeometry()
    assert avail.contains(win.frameGeometry()) or win.width() <= avail.width()


def test_round_trip(app, settings):
    win, split = _window()
    win.resize(700, 500)  # inside the 800x600 offscreen screen
    win.show()
    split.setSizes([250, 450])
    app.processEvents()
    sizes = split.sizes()
    store = WindowStateStore(settings)
    store.set_last_folder("D:/data/field")
    store.save(win, split)
    win.close()

    again, split2 = _window()
    fresh = WindowStateStore(QSettings(settings.fileName(), QSettings.Format.IniFormat))
    assert fresh.restore(again, split2) is True
    again.show()
    app.processEvents()
    assert again.size() == QSize(700, 500)
    assert split2.sizes() == sizes
    assert fresh.last_folder() == "D:/data/field"
    again.close()


def test_last_folder_defaults_to_empty(settings):
    assert WindowStateStore(settings).last_folder() == ""


def test_corrupt_state_is_ignored(app, settings):
    settings.setValue("window/geometry", b"garbage")
    settings.setValue("window/body_splitter", b"garbage")
    win, split = _window()
    assert WindowStateStore(settings).restore(win, split) is False

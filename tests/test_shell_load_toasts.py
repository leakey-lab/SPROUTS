"""Opening folders again does not stack "Loaded N images" toasts (W13 part 2).

Each load showed a new toast, so opening a folder twice (or two folders in a row) stacked
identical toasts. The load's feedback is one keyed toast now: a newer load updates it in
place, and a failed load replaces it.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tests.qt_teardown import flush_deleted  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def window(app, tmp_path):
    from app.gui.main_window import MainWindow

    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.show()
    yield made
    made.close()
    made.deleteLater()
    del made
    flush_deleted()
    QApplication.processEvents()


def _project(folder, stems=STEMS):
    folder.mkdir(parents=True, exist_ok=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    for stem in stems:
        assert image.save(str(folder / f"{stem}.jpg"))
    return folder


def _open(window, folder, count) -> None:
    window.open_path(str(folder))
    end = time.monotonic() + 5
    while time.monotonic() < end:
        QApplication.processEvents()
        if len(window.image_manager.images) == count and window._image_leaves():
            break
    QApplication.processEvents()


def _load_toasts(window) -> list[str]:
    return [t.message() for t in window.toasts.toasts() if "Loaded" in t.message()]


def test_opening_the_same_folder_twice_shows_one_loaded_toast(window, tmp_path):
    folder = _project(tmp_path / "p")
    _open(window, folder, 3)
    _open(window, folder, 3)

    assert _load_toasts(window) == ["Loaded 3 images"]


def test_a_second_folder_updates_the_loaded_toast_in_place(window, tmp_path):
    _open(window, _project(tmp_path / "p"), 3)
    _open(window, _project(tmp_path / "q", STEMS[:1]), 1)

    assert _load_toasts(window) == ["Loaded 1 image"]

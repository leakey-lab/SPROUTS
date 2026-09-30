"""After a folder opens, the library tree has the keyboard (review finding).

Opening a folder hid the Welcome screen's focused button, so Qt handed the keyboard to the
first widget of the focus chain: the Library stage button. Its focus ring stayed while any
other stage was selected (two stages looked highlighted), and the arrow keys moved between
the stage buttons instead of browsing the images. The tree takes the keyboard once the
folder is shown, so Down steps into the library right away.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
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
    made.activateWindow()
    assert QTest.qWaitForWindowActive(made, 3000)
    yield made
    made.close()
    made.deleteLater()
    del made
    flush_deleted()
    QApplication.processEvents()


def _open(window, folder) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    for stem in STEMS:
        assert image.save(str(folder / f"{stem}.jpg"))
    window.open_path(str(folder))
    end = time.monotonic() + 5
    while time.monotonic() < end and not window._image_leaves():
        QApplication.processEvents()
    for _ in range(3):
        QApplication.processEvents()


def test_the_tree_has_the_keyboard_once_a_folder_opens(window, tmp_path):
    _open(window, tmp_path / "p")

    assert QApplication.focusWidget() is window.file_list
    assert not window.ribbon_buttons["Library"].hasFocus()


def test_down_then_steps_into_the_library(window, tmp_path):
    _open(window, tmp_path / "p")
    window.file_list.expandAll()

    names = []
    for _ in range(5):  # through the field, tube and date rows to the images
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Down)
        item = window.file_list.currentItem()
        names.append(item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None)

    assert STEMS[0] in names  # the arrows browse the images, not the stage buttons
    assert QApplication.focusWidget() is window.file_list

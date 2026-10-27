"""After a folder opens, the library tree has the keyboard (review finding).

Opening a folder hid the Welcome screen's focused button, so Qt handed the keyboard to the
first widget of the focus chain: the Library stage button. Its focus ring stayed while any
other stage was selected (two stages looked highlighted), and the arrow keys moved between
the stage buttons instead of browsing the images. The tree takes the keyboard once the
folder is shown, so Down steps into the library right away.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.window_support import build_window, open_folder, quiet_window, write_photos

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini", show=False)]
    made[0].show()
    made[0].activateWindow()
    assert QTest.qWaitForWindowActive(made[0], 3000)
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _open(window, folder) -> None:
    open_folder(window, write_photos(folder, STEMS), len(STEMS))
    settle()


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

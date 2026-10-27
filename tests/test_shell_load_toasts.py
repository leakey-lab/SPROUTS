"""Opening folders again does not stack "Loaded N images" toasts (W13 part 2).

Each load showed a new toast, so opening a folder twice (or two folders in a row) stacked
identical toasts. The load's feedback is one keyed toast now: a newer load updates it in
place, and a failed load replaces it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from tests.qt_teardown import delete_widgets
from tests.window_support import build_window, open_folder, quiet_window, write_photos

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini", show=False)]
    made[0].show()
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _open(window, folder, count) -> None:
    open_folder(window, folder, count)
    QApplication.processEvents()


def _load_toasts(window) -> list[str]:
    return [t.message() for t in window.toasts.toasts() if "Loaded" in t.message()]


def test_opening_the_same_folder_twice_shows_one_loaded_toast(window, tmp_path):
    folder = write_photos(tmp_path / "p", STEMS)
    _open(window, folder, 3)
    _open(window, folder, 3)

    assert _load_toasts(window) == ["Loaded 3 images"]


def test_a_second_folder_updates_the_loaded_toast_in_place(window, tmp_path):
    _open(window, write_photos(tmp_path / "p", STEMS), 3)
    _open(window, write_photos(tmp_path / "q", STEMS[:1]), 1)

    assert _load_toasts(window) == ["Loaded 1 image"]

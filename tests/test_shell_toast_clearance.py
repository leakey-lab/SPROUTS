"""Toasts keep clear of the bottom chrome at every window size (KNOWN_ISSUES #5, H1).

Before: the stack only kept above the statusline and a Correct dock, so on the Library
page the toasts covered the metrics bar (at 1280 x 800 the "Loaded 40 images" toast sat on
the AREA / FOV / STATUS cells), and in an editor they could cover the zoom cluster. The
"Loaded N images" toast is shown while the empty state is still up, so it must also move
when the display page (with its metrics bar) replaces it.

Offscreen; a tmp_path project of small photos; every window has its own INI settings.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication

from app.gui.panels import Panel
from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.window_support import build_window, open_folder, quiet_window, write_photos

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]
LONG = "Mask saved for 24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"
SIZES = [(1280, 800), (1366, 768), (1024, 640)]

pytestmark = pytest.mark.usefixtures("no_prompts", "no_temp_sweep")


@pytest.fixture
def make_window(app, tmp_path):
    made = []

    def make(width: int, height: int):
        made.append(build_window(tmp_path / f"settings_{len(made)}.ini", size=(width, height)))
        return made[-1]

    yield make
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def project(root) -> str:
    return str(write_photos(root / "project", STEMS, size=(64, 48)))


def load(window, folder: str) -> None:
    open_folder(window, folder, len(STEMS))
    settle()


def rect_in(window, widget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def assert_clear(window, *chrome) -> None:
    """Every live toast is inside the window and overlaps none of ``chrome``."""
    toasts = window.toasts.toasts()
    assert toasts
    for toast in toasts:
        box = toast.geometry()
        assert window.rect().contains(box), (box, window.rect())
        for widget in chrome:
            assert widget.isVisible()
            assert not box.intersects(rect_in(window, widget)), (toast.message(), widget)


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_toasts_stay_clear_of_the_metrics_bar_on_the_library_page(
    make_window, tmp_path, width, height
):
    window = make_window(width, height)
    load(window, project(tmp_path))
    item = window.library_index.leaves()[0]
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    window.notify("Loaded 3 images")
    window.notify(LONG)
    settle()

    assert_clear(window, window.metrics_bar, window.statusline)


def test_the_load_toast_moves_off_the_metrics_bar_once_the_display_page_shows(
    make_window, tmp_path
):
    window = make_window(1280, 800)
    assert window.display_page_stack.currentIndex() == 1  # the empty state, no metrics bar

    load(window, project(tmp_path))  # "Loaded 3 images" is shown before the page flips

    assert window.metrics_bar.isVisible()
    assert [t.message() for t in window.toasts.toasts()] == ["Loaded 3 images"]
    assert_clear(window, window.metrics_bar, window.statusline)


@pytest.mark.parametrize(("width", "height"), [(1280, 800), (1024, 640)])
def test_toasts_stay_clear_of_an_editors_dock_and_zoom_cluster(
    make_window, tmp_path, width, height
):
    window = make_window(width, height)
    load(window, project(tmp_path))
    item = window.library_index.leaves()[0]
    window.file_list.setCurrentItem(item)
    window.on_tree_item_clicked(item, 0)
    window.ribbon_buttons["Correct"].click()
    settle()
    assert window.right_panel.currentIndex() == Panel.CORRECT
    editor = window.correct_editor
    window.notify(LONG)
    settle()

    assert_clear(window, editor.dock, editor.zoom_cluster, window.statusline)


def test_toasts_follow_a_window_resize(make_window, tmp_path):
    window = make_window(1366, 768)
    load(window, project(tmp_path))
    window.notify(LONG)
    settle()

    window.resize(1024, 640)
    settle()

    assert_clear(window, window.metrics_bar, window.statusline)

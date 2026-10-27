"""Back on the Library page, the display shows the library's selection (review finding).

The Correct page moves the library selection when they page (PgDn) or open a
clicked row, and the metrics bar follows the selection on every stage, but the display page
kept its last image: back in the Library (or Generate / Measure) the photo of one image sat
over the name and measured values of another. The display page now shows the selected image
whenever it comes back on screen, with the metrics bar read again (a skeleton correction
saved meanwhile shows too).
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.gui.panels import Panel
from tests.correct_support import same_path
from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.test_correct_main_window import STEMS, enter, new_project, open_project, select
from tests.window_support import build_window, quiet_window

USER = Qt.ItemDataRole.UserRole

pytestmark = pytest.mark.usefixtures("window_guards")


@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini")]
    project = new_project(tmp_path)
    open_project(made[0], project)
    made[0].project = project
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _selected(window) -> str:
    return window.file_list.currentItem().data(0, USER)


@pytest.mark.parametrize("back", ["Library", "Measure", "Generate"])
def test_after_paging_in_the_editor_the_display_shows_the_selected_image(window, back):
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor
    editor.navigate(1)  # PgDn: saves nothing (clean), opens image 1, moves the selection
    settle()
    assert same_path(editor.current_image_path, window.project.image(1))
    assert _selected(window) == STEMS[1]

    enter(window, back)

    assert window.right_panel.currentIndex() == Panel.DISPLAY
    assert window.display_controller.current_name == STEMS[1]
    assert window.metrics_bar._name_value.text() == STEMS[1]


def test_back_from_an_editor_on_the_same_image_reads_the_metrics_again(window, tmp_path):
    select(window, 1)
    enter(window, "Correct")
    calls = []
    real = window.metrics_bar.set_metrics
    window.metrics_bar.set_metrics = lambda *a, **k: calls.append(k) or real(*a, **k)

    enter(window, "Library")

    assert window.display_controller.current_name == STEMS[1]
    assert calls and calls[-1]["name"] == STEMS[1]

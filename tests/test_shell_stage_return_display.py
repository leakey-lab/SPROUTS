"""Back on the Library page, the display shows the library's selection (review finding).

The Correct and Trace GT pages move the library selection when they page (PgDn) or open a
clicked row, and the metrics bar follows the selection on every stage, but the display page
kept its last image: back in the Library (or Generate / Measure) the photo of one image sat
over the name and measured values of another. The display page now shows the selected image
whenever it comes back on screen, with the metrics bar read again (a skeleton correction
saved meanwhile shows too).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import canvas_gl  # noqa: E402
from app.gui.correct.layer import SKELETON  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import qapp  # noqa: E402
from tests.test_correct_main_window import (  # noqa: E402
    STEMS,
    enter,
    new_project,
    open_project,
    same_path,
    select,
    settle,
)

USER = Qt.ItemDataRole.UserRole


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    from app.gui import image_manager

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: pytest.fail("dialog"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def window(app, tmp_path):
    from app.gui.main_window import MainWindow

    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.show()
    settle()
    project = new_project(tmp_path)
    open_project(made, project)
    made.project = project
    yield made
    for editor in made._editors().values():
        editor.maybe_save_changes = lambda: True
    made.correct_editor.layers[SKELETON].cancel_scheduled_render()
    made.close()
    made.deleteLater()
    del made, editor
    flush_deleted()
    QApplication.processEvents()


def _selected(window) -> str:
    return window.file_list.currentItem().data(0, USER)


@pytest.mark.parametrize("stage", ["Correct", "Trace GT"])
@pytest.mark.parametrize("back", ["Library", "Measure", "Generate"])
def test_after_paging_in_an_editor_the_display_shows_the_selected_image(window, stage, back):
    select(window, 0)
    enter(window, stage)
    editor = window._editors()[window.right_panel.currentIndex()]
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

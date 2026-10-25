"""The Library shows a skeleton correction where one exists (W17, display only).

``skeleton_corrections/<stem>_skeleton.png`` is what the Correct stage saves. Measure does not
read it (FR-03: root length is measured from the generated ``skeletons/<stem>_fake.png``), but
the Library's Overlay and Side by side drew the generated skeleton even when a correction
existed, and nothing said an image had one. Now they draw the correction, the metrics bar
says "Skeleton corrected" (the tooltip: which file is drawn, and that Measure reads the
generated one), and the library row's skeleton mark is a diamond instead of a dot. The
correction state is found by the folder scan and follows the Correct stage's saves without
connecting its ``skeleton_saved`` signal (FR-03 pins that nothing listens to it).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, QSize, Qt  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_layers.skeleton import SkeletonLayerItem  # noqa: E402
from app.gui.correct.layer import SKELETON  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import H, W, draw_line, qapp, write_skeleton  # noqa: E402
from tests.test_correct_main_window import (  # noqa: E402
    STEMS,
    enter,
    leaf,
    new_project,
    open_project,
    select,
    settle,
)


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
    yield made
    for editor in made._editors().values():
        editor.maybe_save_changes = lambda: True
    made.correct_editor.layers[SKELETON].cancel_scheduled_render()
    made.close()
    made.deleteLater()
    del made, editor
    flush_deleted()
    QApplication.processEvents()


def _corrected_project(root):
    """Image 0: a generated skeleton on row 110 and a correction on row 40."""
    project = new_project(root)
    write_skeleton(project.correction(0), row=40)
    return project


def _overlay_rows(window) -> set[int]:
    """Rows of the image that the Library's skeleton overlay draws."""
    items = [
        item for item in window.display_area.scene().items() if isinstance(item, SkeletonLayerItem)
    ]
    assert len(items) == 1
    mask = items[0]._mask
    assert mask is not None and mask.shape == (H, W)
    return set(np.nonzero(mask.any(axis=1))[0].tolist())


def _overlay_mode(window) -> None:
    window.view_mode_combo.setCurrentIndex(1)  # Overlay
    settle()


# ============================================================================== display
def test_the_overlay_draws_the_correction_when_one_exists(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    _overlay_mode(window)

    select(window, 0)

    assert window.display_controller.current_fake_image is not None
    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(0))
    )
    assert _overlay_rows(window) == {40}


def test_without_a_correction_the_overlay_draws_the_generated_skeleton(window, tmp_path):
    project = new_project(tmp_path)
    open_project(window, project)
    _overlay_mode(window)

    select(window, 0)

    assert _overlay_rows(window) == {110}


def test_side_by_side_shows_the_correction_too(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    window.view_mode_combo.setCurrentIndex(2)  # Side by side
    settle()

    select(window, 0)

    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(0))
    )


# ======================================================================== what says so
def test_the_metrics_bar_says_skeleton_corrected_and_what_is_measured(window, tmp_path):
    open_project(window, _corrected_project(tmp_path))

    select(window, 0)

    bar = window.metrics_bar
    assert bar._status_value.text() == "Skeleton corrected"
    tip = bar._status_cell.toolTip()
    assert "skeleton_corrections" in tip and "generated" in tip
    select(window, 1)
    assert bar._status_value.text() == "No mask"
    assert bar._status_cell.toolTip() == ""


def test_the_library_row_marks_a_corrected_skeleton(window, tmp_path):
    open_project(window, _corrected_project(tmp_path))
    index = window.library_index

    assert window.image_manager.has_correction(STEMS[0])
    assert not window.image_manager.has_correction(STEMS[1])
    assert index.status_of(STEMS[0]) == (True, True)  # (mask, generated skeleton) unchanged
    assert index.corrected_of(STEMS[0]) is True
    assert "corrected" in index.tooltip_for(STEMS[0]).lower()
    assert "corrected" not in index.tooltip_for(STEMS[1]).lower()
    delegate = index._delegate
    size = QSize(22, 12)
    plain = delegate.status_icon(True, True).pixmap(size, QIcon.Mode.Normal).toImage()
    marked = delegate.status_icon(True, True, True).pixmap(size, QIcon.Mode.Normal).toImage()
    assert plain != marked
    assert leaf(window, 0) is not None


# ================================================================ follows the Correct saves
def test_a_correction_saved_in_correct_reaches_the_library(window, tmp_path):
    project = new_project(tmp_path)
    open_project(window, project)
    select(window, 1)
    enter(window, "Correct")
    editor = window.correct_editor
    assert not window.image_manager.has_correction(STEMS[1])

    draw_line(editor)
    assert editor.save()
    settle()

    assert os.path.isfile(project.correction(1))
    assert window.image_manager.has_correction(STEMS[1])
    assert window.library_index.corrected_of(STEMS[1]) is True
    # FR-03 unchanged: nothing listens to skeleton_saved, and it is not a generated skeleton.
    assert editor.receivers(editor.skeleton_saved) == 0
    assert not window.image_manager.has_skeleton(STEMS[1])

    enter(window, "Library")
    _overlay_mode(window)
    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(1))
    )
    assert window.metrics_bar._status_value.text() == "Skeleton corrected"


def test_a_correction_edited_back_to_the_generated_one_leaves_the_library(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor
    assert window.library_index.corrected_of(STEMS[0]) is True

    editor.layers[SKELETON].load_file(project.generated(0))
    editor.session.forget(SKELETON)  # Load clears the layer's own history
    assert editor.save()
    settle()

    assert not os.path.isfile(project.correction(0))
    assert not window.image_manager.has_correction(STEMS[0])
    assert window.library_index.corrected_of(STEMS[0]) is False


def test_a_legacy_no_op_correction_leaves_the_library_when_the_generated_one_is_saved(
    window, tmp_path
):
    project = new_project(tmp_path)
    write_skeleton(project.correction(0))  # an old Ctrl+S: equal to the generated skeleton
    open_project(window, project)
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor
    assert window.library_index.corrected_of(STEMS[0]) is True

    editor.layers[SKELETON].load_file(project.generated(0))
    editor.set_active_layer(SKELETON)
    assert editor.save()
    settle()
    assert not os.path.isfile(project.correction(0))
    # Nothing was unsaved, so no dirty change re-reads the disk while Correct is shown;
    # the Library reads it again when it is back on screen.
    enter(window, "Library")

    assert not window.image_manager.has_correction(STEMS[0])
    assert window.library_index.corrected_of(STEMS[0]) is False

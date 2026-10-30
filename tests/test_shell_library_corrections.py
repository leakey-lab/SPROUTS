"""The Library shows a GT skeleton where one exists (W17).

``gt/skeletons/<stem>.png`` is what the Correct stage saves (WG-T3; ``skeleton_corrections/``
is retired), and Measure reads it first where it is valid (O1, WG-T8). The Library's Overlay
and Side by side drew the generated skeleton even when a saved edit existed, and nothing said
an image had one. Now they draw the GT skeleton, the metrics bar's status says
"Skeleton: GT" (the badge's words; the tooltip: which file is drawn), and the library row's
skeleton mark is a diamond instead of a dot. The GT state is found by the folder scan and
follows the Correct stage's saves without connecting its ``skeleton_saved`` signal (nothing
listens to it: the editor's ``dirty_changed`` carries the save).
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.gui.canvas_layers.skeleton import SkeletonLayerItem
from app.gui.correct.layer import SKELETON
from tests.correct_support import H, W, draw_line, write_skeleton
from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.test_correct_main_window import STEMS, enter, leaf, new_project, open_project, select
from tests.window_support import build_window, quiet_window

pytestmark = pytest.mark.usefixtures("window_guards")


@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini")]
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _corrected_project(root):
    """Image 0: a generated skeleton on row 110 and a GT skeleton on row 40."""
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
def test_the_overlay_draws_the_gt_skeleton_when_one_exists(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    _overlay_mode(window)

    select(window, 0)

    assert window.display_controller.current_fake_image is not None
    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(0))
    )
    assert _overlay_rows(window) == {40}


def test_without_a_gt_skeleton_the_overlay_draws_the_generated_skeleton(window, tmp_path):
    project = new_project(tmp_path)
    open_project(window, project)
    _overlay_mode(window)

    select(window, 0)

    assert _overlay_rows(window) == {110}


def test_side_by_side_shows_the_gt_skeleton_too(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    window.view_mode_combo.setCurrentIndex(2)  # Side by side
    settle()

    select(window, 0)

    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(0))
    )


# ======================================================================== what says so
def test_the_metrics_bar_says_skeleton_gt_and_what_is_drawn(window, tmp_path):
    open_project(window, _corrected_project(tmp_path))

    select(window, 0)

    bar = window.metrics_bar
    assert bar._status_value.text() == "Mask: prediction · Skeleton: GT · No classes"
    tip = bar._status_cell.toolTip()
    assert "gt/skeletons/" in tip and "prediction (skeletons/)" in tip
    select(window, 1)
    assert bar._status_value.text() == "No mask · No skeleton · No classes"
    assert bar._status_cell.toolTip() == ""


def test_the_library_row_marks_a_gt_skeleton(window, tmp_path):
    open_project(window, _corrected_project(tmp_path))
    index = window.library_index

    assert window.image_manager.has_gt_skeleton(STEMS[0])
    assert not window.image_manager.has_gt_skeleton(STEMS[1])
    assert index.status_of(STEMS[0]) == ("prediction", "gt", False)
    assert index.status_of(STEMS[1]) == ("none", "none", False)
    assert "Skeleton: GT" in index.tooltip_for(STEMS[0])
    assert "Skeleton: GT" not in index.tooltip_for(STEMS[1])
    delegate = index._delegate
    size = QSize(32, 12)
    plain = (
        delegate.status_icon("prediction", "prediction", False)
        .pixmap(size, QIcon.Mode.Normal)
        .toImage()
    )
    marked = (
        delegate.status_icon("prediction", "gt", False).pixmap(size, QIcon.Mode.Normal).toImage()
    )
    assert plain != marked
    assert leaf(window, 0) is not None


# ================================================================ follows the Correct saves
def test_a_skeleton_saved_in_correct_reaches_the_library(window, tmp_path):
    project = new_project(tmp_path)
    open_project(window, project)
    select(window, 1)
    enter(window, "Correct")
    editor = window.correct_editor
    assert not window.image_manager.has_gt_skeleton(STEMS[1])

    draw_line(editor)
    assert editor.save()
    settle()

    assert os.path.isfile(project.correction(1))
    assert window.image_manager.has_gt_skeleton(STEMS[1])
    assert window.library_index.status_of(STEMS[1])[1] == "gt"
    # The window hears skeleton_saved once (WG-T11), and it is not a generated skeleton.
    assert editor.receivers(editor.skeleton_saved) == 1
    assert not window.image_manager.has_skeleton(STEMS[1])

    enter(window, "Library")
    _overlay_mode(window)
    assert os.path.normcase(window.display_controller.current_fake_image) == os.path.normcase(
        os.path.normpath(project.correction(1))
    )
    assert window.metrics_bar._status_value.text() == "No mask · Skeleton: GT · No classes"


def test_a_gt_skeleton_edited_back_to_the_prediction_leaves_the_library(window, tmp_path):
    project = _corrected_project(tmp_path)
    open_project(window, project)
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor
    assert window.library_index.status_of(STEMS[0])[1] == "gt"

    editor.layers[SKELETON].load_file(project.generated(0))
    editor.session.forget(SKELETON)  # Load clears the layer's own history
    assert editor.save()
    settle()

    assert not os.path.isfile(project.correction(0))
    assert not window.image_manager.has_gt_skeleton(STEMS[0])
    assert window.library_index.status_of(STEMS[0])[1] == "prediction"

"""The Correct page's skeleton layer reads GT first and writes GT only (WG-T3).

The layer opens ``gt/skeletons/<stem>.png`` when it exists, else the model's prediction
``skeletons/<stem>_fake.png``; a save writes the GT file and never the prediction, and no
``skeleton_corrections/`` folder is made any more (it is retired: no migration, owner
2026-10-25). A GT skeleton of another size opens resampled to the photo and saves at the
photo's size. "Load prediction" puts the prediction back as one undoable action, and a later
Save removes the GT file (W1-T3's rule). The Library mark and the Library overlay follow the
GT skeleton.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.gui.correct.layer import SKELETON, LayerStatus  # noqa: E402
from app.gui.skeleton_document import GT, PREDICTION, SkeletonFiles  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    H,
    W,
    click,
    draw_line,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    raster,
    record,
    write_skeleton,
)
from tests.test_correct_main_window import (  # noqa: E402
    STEMS,
    enter,
    open_project,
    select,
    settle,
)
from tests.test_correct_main_window import new_project as new_window_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    from app.gui import image_manager

    # A loaded project schedules its stale-temp sweep 2 s later (R8): a no-op here.
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    return new_project(tmp_path)


@pytest.fixture
def window_with_project(app, tmp_path):
    """A MainWindow (own INI settings) with a project of four photos open; image 0 has a
    model mask and a generated skeleton."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow
    from tests.qt_teardown import flush_deleted

    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    window.resize(1280, 800)
    window.show()
    settle()
    project = new_window_project(tmp_path)
    open_project(window, project)
    yield window, project
    for editor in window._editors().values():
        editor.maybe_save_changes = lambda: True
    window.correct_editor.layers[SKELETON].cancel_scheduled_render()
    window.close()
    window.deleteLater()
    del window
    flush_deleted()
    QApplication.processEvents()


def file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def png_size(path: str) -> tuple[int, int]:
    """``(w, h)`` of the image file at ``path``."""
    image = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    return int(image.shape[1]), int(image.shape[0])


def same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(
        os.path.abspath(b)
    )


# ========================================================================= where it writes
def test_a_skeleton_edit_saves_to_gt_skeletons_and_never_creates_skeleton_corrections(
    make_editor, project
):
    before = file_bytes(project.generated())
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)

    draw_line(editor, (40, 60), (150, 70))
    assert editor.save() is True

    layout = ProjectLayout(project.folder)
    assert os.path.isfile(layout.gt_skeleton_path(project.a))
    assert not os.path.exists(os.path.join(project.folder, "skeleton_corrections"))
    assert file_bytes(project.generated()) == before
    status = editor.layers[SKELETON].status()
    assert status == LayerStatus("clean", "GT", layout.gt_skeleton_path(project.a))


def test_the_skeleton_opens_the_gt_file_first(project):
    files = SkeletonFiles(project.a, project.folder)
    assert files.start_file() == (files.generated_path(), PREDICTION)

    write_skeleton(files.gt_path, row=40)

    assert files.start_file() == (files.gt_path, GT)
    assert files.gt_path == ProjectLayout(project.folder).gt_skeleton_path(project.a)
    assert (GT, PREDICTION) == ("gt", "prediction")


def test_a_gt_skeleton_of_another_size_opens_resampled_and_saves_at_the_photo_size(
    project, make_editor
):
    gt_path = ProjectLayout(project.folder).gt_skeleton_path(project.a)
    write_skeleton(gt_path, row=60, w=341, h=256)  # not the photo's 200 x 150

    editor = make_editor(project.a, project.folder)
    layer = editor.layers[SKELETON]
    assert layer.document.model.mask.shape == (H, W) and layer.status().label == "GT"

    editor.set_active_layer(SKELETON)
    draw_line(editor, (40, 60), (150, 70))
    assert editor.save() is True
    assert png_size(gt_path) == (W, H)


def test_load_prediction_on_the_skeleton_is_undoable_and_save_removes_the_gt(make_editor, project):
    gt_path = ProjectLayout(project.folder).gt_skeleton_path(project.a)
    write_skeleton(gt_path, row=40)
    editor = make_editor(project.a, project.folder)
    signals = {"skeleton_cleared": record(editor.skeleton_cleared)}
    editor.set_active_layer(SKELETON)
    gt_rows = set(np.nonzero(raster(editor).any(axis=1))[0].tolist())
    assert editor.controls.prediction_button.isEnabled()
    tip = editor.controls.prediction_button.toolTip()
    assert tip == "Load the prediction into the skeleton (undoable)"

    editor.load_prediction()

    assert editor.session.can_undo()
    assert set(np.nonzero(raster(editor).any(axis=1))[0].tolist()) == {110}
    editor.undo()
    assert set(np.nonzero(raster(editor).any(axis=1))[0].tolist()) == gt_rows
    editor.redo()

    assert editor.save() is True
    assert not os.path.exists(gt_path)
    assert signals["skeleton_cleared"] == [project.a]
    assert os.path.isfile(project.generated())
    status = editor.layers[SKELETON].status()
    assert status == LayerStatus("none", "Prediction", "Not corrected yet")


def test_load_prediction_is_off_on_the_skeleton_without_a_prediction(make_editor, project):
    editor = make_editor(project.b, project.folder)  # b has no generated skeleton
    editor.set_active_layer(SKELETON)

    assert not editor.controls.prediction_button.isEnabled()
    editor.load_prediction()  # does nothing
    assert not editor.session.can_undo() and not editor.has_unsaved_changes()


def test_an_unreadable_prediction_says_so_and_changes_nothing(make_editor, project):
    with open(project.generated(), "wb") as fh:
        fh.write(b"not a png")  # the skeleton opens empty
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    notices = record(editor.notice)
    before = raster(editor).copy()

    editor.load_prediction()

    assert notices == [("Could not read skeleton a_fake.png", "danger")]
    assert np.array_equal(raster(editor), before)
    assert not editor.session.can_undo() and not editor.has_unsaved_changes()


def test_load_prediction_of_the_shown_prediction_keeps_the_draft_and_says_so(make_editor, project):
    editor = make_editor(project.a, project.folder)  # opened from its prediction
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 60))
    click(editor, (150, 70))  # a two-point draft, not finished
    skeleton = editor.layers[SKELETON]
    assert skeleton.draft_state().points == 2
    notices = record(editor.notice)
    revision = skeleton.document.model.revision

    editor.load_prediction()

    assert notices == [("Nothing to load: the skeleton is the prediction already", "info")]
    assert skeleton.draft_state().points == 2  # the draft stays
    assert skeleton.document.model.revision == revision and not editor.session.can_undo()


# ============================================================================ the Library
def test_the_library_follows_a_skeleton_save_and_its_removal(window_with_project):
    window, project = window_with_project
    stem = STEMS[0]
    layout = ProjectLayout(project.folder)
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor
    assert not window.image_manager.has_gt_skeleton(stem)

    editor.set_active_layer(SKELETON)
    draw_line(editor, (40, 60), (150, 70))
    assert editor.save()
    settle()

    assert window.image_manager.has_gt_skeleton(stem)
    assert window.image_manager.gt_skeleton_path_for(stem) == layout.gt_skeleton_path(stem)
    assert window.library_index.corrected_of(stem) is True

    editor.load_prediction()
    assert editor.save()
    settle()

    assert not os.path.exists(layout.gt_skeleton_path(stem))
    assert not window.image_manager.has_gt_skeleton(stem)
    assert window.image_manager.gt_skeleton_path_for(stem) is None
    assert window.library_index.corrected_of(stem) is False
    enter(window, "Library")
    window.view_mode_combo.setCurrentIndex(1)  # Overlay
    settle()
    assert same(window.display_controller.current_fake_image, project.generated(0))


def test_the_overlay_draws_the_gt_skeleton(window_with_project):
    window, project = window_with_project
    stem = STEMS[0]
    layout = ProjectLayout(project.folder)
    write_skeleton(layout.gt_skeleton_path(stem), row=40)
    window.image_manager.set_gt(stem, "skeleton", True)  # as the folder scan finds it
    window.view_mode_combo.setCurrentIndex(1)  # Overlay
    settle()

    select(window, 0)

    assert same(window.display_controller._current_skeleton_path(), layout.gt_skeleton_path(stem))


def test_the_overlay_draws_the_prediction_without_a_gt_skeleton(window_with_project):
    window, project = window_with_project
    window.view_mode_combo.setCurrentIndex(1)  # Overlay
    settle()

    select(window, 0)

    assert same(window.display_controller._current_skeleton_path(), project.generated(0))


# ============================================================================= the layout
def test_the_layout_has_no_corrections_folder_any_more():
    for member in (
        "corrections_dir",
        "corrected_skeleton_path",
        "find_corrected_skeleton",
        "scan_corrected_skeletons",
        "scan_corrected_stems",
    ):
        assert not hasattr(ProjectLayout, member), member
    assert len(ProjectLayout("x").output_dirs) == 8
    from app import project_layout

    assert not hasattr(project_layout, "CORRECTED_SKELETON_SUFFIX")

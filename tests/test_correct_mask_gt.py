"""The Correct page's mask layer reads GT first and writes GT only (WG-T2).

The layer opens ``gt/mask/<stem>.png`` when it exists, else the model's prediction
``mask/<stem>.png``; a save writes the GT file and never the prediction. A person's saved
edit of a prediction becomes GT (O26); a layer without edits writes nothing (O2); Clear and
Save writes an empty GT mask, so Measure gives 0 for it (O9); a mask edited back to equal its
prediction removes the GT file; "Load prediction" puts the prediction back as one undoable
action.
"""

from __future__ import annotations

import logging
import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.config import AREA_VALUE_COLUMN  # noqa: E402
from app.gt import files as gt_files  # noqa: E402
from app.gui.correct import mask_document  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON, LayerStatus  # noqa: E402
from app.gui.correct.mask_document import MaskDocument  # noqa: E402
from app.gui.correct.read_back import mask_read_back  # noqa: E402
from app.inference import metrics  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.correct_goldens_support import describe_mask_file, png_colour_type  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    H,
    W,
    answer_prompts,
    box_plane,
    bump,
    editor_factory,
    install_guards,
    new_project,
    paint,
    plane,
    qapp,
    record,
    write_image,
    write_mask,
)
from tests.test_correct_editor_paging import read_ahead_done, ring  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    return new_project(tmp_path)


def gt_mask(project, name: str = "a") -> str:
    """Where the GT mask of image ``name`` lives."""
    return ProjectLayout(project.folder).gt_mask_path(getattr(project, name))


def one_block() -> np.ndarray:
    """A GT plane that differs from the prediction (``box_plane``)."""
    block = np.zeros((H, W), np.uint8)
    block[90:120, 20:60] = 1
    return block


def read_gt(path: str) -> np.ndarray:
    """The 0 / 1 plane of a saved GT mask (white on transparent)."""
    return (imread_unicode(path, cv2.IMREAD_UNCHANGED)[:, :, 3] == 255).astype(np.uint8)


def file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def stamps_of(folder: str) -> dict[str, tuple[int, int]]:
    """Every file under ``folder``: relative path -> (mtime_ns, size)."""
    found = {}
    for root, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            stat = os.stat(path)
            found[os.path.relpath(path, folder)] = (stat.st_mtime_ns, stat.st_size)
    return found


def signals_of(editor) -> dict[str, list]:
    return {
        "mask_saved": record(editor.mask_saved),
        "mask_cleared": record(editor.mask_cleared),
        "notices": record(editor.notice),
    }


# ======================================================================== where it writes
def test_the_source_names_are_the_gt_modules():
    assert (mask_document.SOURCE_GT, mask_document.SOURCE_PREDICTION) == (
        gt_files.SOURCE_GT,
        gt_files.SOURCE_PREDICTION,
    )


def test_a_mask_edit_saves_to_gt_mask_and_never_touches_the_prediction(make_editor, project):
    before = file_bytes(project.mask())
    editor = make_editor(project.a, project.folder)

    paint(editor, (20, 100), (80, 110))
    assert editor.save() is True

    gt = gt_mask(project)
    assert os.path.isfile(gt) and png_colour_type(gt) == 6
    assert file_bytes(project.mask()) == before
    assert not os.path.exists(ProjectLayout(project.folder).gt_path(project.a))  # no labels
    assert editor.layers[MASK].status() == LayerStatus("clean", "GT", "gt/mask/a.png")
    assert editor.layers[MASK].document.source == "gt"


def test_the_layer_opens_the_gt_mask_when_one_exists_else_the_prediction(make_editor, project):
    editor = make_editor(project.a, project.folder)
    layer = editor.layers[MASK]
    assert layer.document.source == "prediction" and layer.status().label == "Prediction"
    assert layer.status() == LayerStatus(
        "none",
        "Prediction",
        "mask/a.png — the model's output. Save after an edit writes gt/mask/a.png",
    )

    write_mask_file(gt_mask(project), one_block() > 0)
    editor.load_image(project.a, project.folder)  # the files changed: the layer reopens

    assert layer.document.source == "gt" and (plane(editor) == one_block()).all()
    assert layer.status() == LayerStatus("clean", "GT", "gt/mask/a.png")


def test_an_image_without_any_mask_opens_empty_with_no_source(make_editor, project):
    editor = make_editor(project.b, project.folder)

    assert editor.layers[MASK].document.source is None
    assert editor.layers[MASK].status() == LayerStatus("none", "No mask")
    assert editor.layers[MASK].document.prediction() is None


# ======================================================================= the save rules
def test_clear_and_save_writes_an_empty_gt_mask(make_editor, project):  # O9
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)

    editor.clear_active_layer()
    assert editor.save() is True

    gt = gt_mask(project)
    assert describe_mask_file(gt)["on_count"] == 0
    assert metrics.area_row("a", gt)[AREA_VALUE_COLUMN] == 0.0
    assert os.path.isfile(project.mask())  # the prediction stays
    assert seen["mask_saved"] == [project.a] and seen["mask_cleared"] == []
    assert ("Empty mask saved as GT for a.png: root area 0", "info") in seen["notices"]
    assert editor.layers[MASK].status() == LayerStatus("clean", "GT", "gt/mask/a.png")


def test_a_mask_equal_to_the_prediction_removes_the_gt_file(make_editor, project):  # O2
    gt = gt_mask(project)
    write_mask_file(gt, one_block() > 0)
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)

    editor.load_prediction()
    assert editor.layers[MASK].is_dirty() and editor.save() is True

    assert not os.path.exists(gt) and os.path.isfile(project.mask())
    assert seen["mask_cleared"] == [project.a] and seen["mask_saved"] == []
    assert ("Mask equals the prediction: removed gt/mask/a.png", "info") in seen["notices"]
    assert editor.layers[MASK].status().label == "Prediction"
    assert editor.layers[MASK].document.source == "prediction"


def test_save_with_nothing_unsaved_writes_nothing_and_says_so(make_editor, project):
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)
    before = stamps_of(project.folder)

    assert editor.save() is False

    assert stamps_of(project.folder) == before
    assert ("Nothing unsaved", "info") in seen["notices"]
    assert seen["mask_saved"] == seen["mask_cleared"] == []


# ======================================================================= Load prediction
def test_load_prediction_is_one_undo_step_and_is_off_without_a_prediction(make_editor, project):
    write_mask_file(gt_mask(project), one_block() > 0)
    editor = make_editor(project.a, project.folder)
    button = editor.controls.prediction_button
    assert button.isEnabled()
    assert button.toolTip() == "Load the prediction into the mask (undoable)"

    editor.load_prediction()

    assert (plane(editor) == box_plane()).all() and editor.session.can_undo()
    assert editor.layers[MASK].is_dirty()
    editor.undo()
    assert (plane(editor) == one_block()).all() and not editor.layers[MASK].is_dirty()
    assert not editor.session.can_undo()

    editor.load_image(project.b, project.folder)  # no prediction for b
    assert not button.isEnabled()
    editor.load_prediction()  # does nothing
    assert not plane(editor).any() and not editor.session.can_undo()


def test_load_prediction_is_off_on_the_skeleton_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)

    editor.set_active_layer(SKELETON)

    assert not editor.controls.prediction_button.isEnabled()
    editor.set_active_layer(MASK)
    assert editor.controls.prediction_button.isEnabled()


def test_the_dock_button_loads_the_prediction(make_editor, project):
    write_mask_file(gt_mask(project), one_block() > 0)
    editor = make_editor(project.a, project.folder)

    editor.controls.prediction_button.click()

    assert (plane(editor) == box_plane()).all()


def test_load_prediction_when_it_is_shown_already_changes_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)

    assert editor.layers[MASK].load_prediction() is False

    assert not editor.session.can_undo() and not editor.layers[MASK].is_dirty()


# ======================================================================== Review Focus
def test_an_unreadable_or_wrong_size_gt_mask_is_not_shown_and_kept(
    make_editor, project, monkeypatch
):  # Review Focus 1: W1-T2's rule on the GT path
    gt = gt_mask(project)
    write_mask_file(gt, np.ones((60, 80), bool))  # 80 x 60; the photo is 200 x 150
    before = file_bytes(gt)
    editor = make_editor(project.a, project.folder)
    layer = editor.layers[MASK]

    assert layer.document.source == "gt" and layer.status().label == "Mask not shown"
    assert not plane(editor).any()  # the prediction is not shown in its place
    assert layer.unloaded_label() == "gt/mask/a.png"
    assert editor.save() is False and file_bytes(gt) == before

    paint(editor, (20, 100), (80, 110))
    answers = answer_prompts(monkeypatch).will(CANCEL)  # "Keep it"
    assert editor.save() is False and file_bytes(gt) == before
    assert len(answers.seen) == 1


def test_an_unshown_prediction_names_no_file_to_replace(make_editor, project):
    with open(project.mask(), "wb") as fh:
        fh.write(b"not a png")  # an unreadable prediction
    editor = make_editor(project.a, project.folder)
    layer = editor.layers[MASK]

    assert layer.document.source == "prediction" and layer.status().label == "Mask not shown"
    paint(editor, (20, 100), (80, 110))
    assert layer.unloaded_label() == ""  # a save never replaces the prediction
    assert editor.save() is True and os.path.isfile(gt_mask(project))


def test_a_save_that_cannot_create_gt_keeps_the_prediction_and_reports(
    make_editor, project
):  # Review Focus 2
    before = file_bytes(project.mask())
    open(os.path.join(project.folder, "gt"), "w").close()  # a file where gt/ should be
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)

    paint(editor, (20, 100), (80, 110))
    assert editor.save() is False  # the guard fails the test on any prompt

    assert editor.layers[MASK].status().label == "Not saved"
    assert file_bytes(project.mask()) == before and editor.layers[MASK].is_dirty()
    assert [kind for _message, kind in seen["notices"]] == ["danger"]


# ========================================================================== read ahead
def test_read_ahead_reads_the_gt_mask_first_and_stamps_cover_both(project):
    read = MaskDocument.read(project.a, (W, H))

    assert read.source == "prediction" and read.prediction_plane is not None
    assert read.gt_path.endswith(os.path.join("gt", "mask", "a.png"))
    assert read.prediction_path is not None and read.prediction_path.endswith("a.png")
    doc = MaskDocument()
    doc.open(project.a, (W, H), read)
    assert len(doc.file_stamps()) == 1 + 6
    assert doc.write_stamps() == (doc.file_stamps()[0],)
    assert doc.file_stamps()[0] == (read.gt_path, None)


def test_a_gt_read_keeps_the_prediction_for_load_and_compare(project):
    write_mask_file(gt_mask(project), one_block() > 0)

    read = MaskDocument.read(project.a, (W, H))

    assert read.source == "gt" and (read.plane == one_block()).all()
    assert (read.prediction_plane == box_plane()).all()
    assert read.nbytes == read.plane.nbytes + read.prediction_plane.nbytes


# ============================================================================ fix round 1
def test_a_prediction_rewritten_while_editing_is_not_taken_for_the_gt(make_editor, project):
    write_mask_file(gt_mask(project), one_block() > 0)
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)
    editor.load_prediction()  # the plane is the prediction P as opened
    newer = np.zeros((H, W), np.uint8)
    newer[100:140, 120:180] = 1
    write_mask(project.mask(), newer)  # a Generate run rewrites mask/a.png meanwhile
    bump(project.mask())
    rewritten = file_bytes(project.mask())

    assert editor.save() is True

    saved = describe_mask_file(gt_mask(project))
    assert saved["exists"] and saved["on_count"] == int(box_plane().sum())
    assert (read_gt(gt_mask(project)) == box_plane()).all()  # P's pixels, as shown
    assert file_bytes(project.mask()) == rewritten  # the prediction is untouched
    assert seen["mask_saved"] == [project.a] and seen["mask_cleared"] == []
    assert ("Saved mask · a.png", "success") in seen["notices"]


def test_a_prediction_that_cannot_be_shown_disables_load_prediction_and_says_so(
    make_editor, project
):
    with open(project.mask(), "wb") as fh:
        fh.write(b"not a png")  # unreadable
    editor = make_editor(project.a, project.folder)

    button = editor.controls.prediction_button
    assert not button.isEnabled()
    assert button.toolTip() == "The prediction for this image could not be shown"


def stretched_prediction(project) -> np.ndarray:
    """Replace the prediction by a 60 x 80 one (the photo is H x W); returns the plane it
    must open as: every pixel stretched with nearest neighbour."""
    small = np.zeros((60, 80), np.uint8)
    small[20:40, 10:50] = 1
    write_mask(project.mask(), small)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


def test_load_prediction_works_over_a_gt_shown_above_a_prediction_of_another_size(
    make_editor, project
):  # O8: the stretched plane is the prediction
    expected = stretched_prediction(project)
    write_mask_file(gt_mask(project), one_block() > 0)
    editor = make_editor(project.a, project.folder)
    button = editor.controls.prediction_button

    assert button.isEnabled()
    assert button.toolTip() == "Load the prediction into the mask (undoable)"
    assert editor.layers[MASK].document.source == "gt"

    editor.load_prediction()

    assert (plane(editor) == expected).all() and editor.layers[MASK].is_dirty()
    editor.undo()
    assert (plane(editor) == one_block()).all()


def test_load_prediction_then_save_removes_the_gt_over_a_stretched_prediction(make_editor, project):
    stretched_prediction(project)
    gt = gt_mask(project)
    write_mask_file(gt, one_block() > 0)
    prediction_bytes = file_bytes(project.mask())
    editor = make_editor(project.a, project.folder)
    seen = signals_of(editor)

    editor.load_prediction()
    assert editor.save() is True

    assert not os.path.exists(gt) and file_bytes(project.mask()) == prediction_bytes
    assert seen["mask_cleared"] == [project.a] and seen["mask_saved"] == []
    assert editor.layers[MASK].document.source == "prediction"


def test_file_labels_are_relative_to_the_project_folder(make_editor, tmp_path):
    folder = str(tmp_path / "project")
    photo = write_image(os.path.join(folder, "tube1", "a.png"))
    write_mask(os.path.join(folder, "tube1", "mask", "a.png"), box_plane())
    gt = os.path.join(folder, "tube1", "gt", "mask", "a.png")
    editor = make_editor(photo, folder)
    layer = editor.layers[MASK]
    assert layer.status().tooltip == (
        "tube1/mask/a.png — the model's output. Save after an edit writes tube1/gt/mask/a.png"
    )

    write_mask_file(gt, one_block() > 0)
    editor.load_image(photo, folder)
    assert layer.status().tooltip == "tube1/gt/mask/a.png"
    seen = signals_of(editor)
    editor.load_prediction()
    assert editor.save() is True

    message = ("Mask equals the prediction: removed tube1/gt/mask/a.png", "info")
    assert message in seen["notices"] and not os.path.exists(gt)


def test_an_open_looks_the_gt_mask_up_once(project, monkeypatch):
    calls = []
    find = ProjectLayout.find_gt_mask
    monkeypatch.setattr(
        ProjectLayout, "find_gt_mask", lambda self, stem: calls.append(stem) or find(self, stem)
    )

    MaskDocument().open(project.a, (W, H))

    assert len(calls) == 1


def test_a_hand_back_after_load_prediction_and_save_says_the_stretch_again(make_editor, project):
    stretched_prediction(project)
    write_mask_file(gt_mask(project), one_block() > 0)  # a GT is shown: no stretch notice
    editor = make_editor(project.a, project.folder)
    document = editor.layers[MASK].document
    assert document.resized_from is None and document.prediction_resized_from == (80, 60)
    editor.load_prediction()
    assert editor.save() is True and not os.path.exists(gt_mask(project))

    handed = mask_read_back(document, (W, H), document.file_stamps())

    assert document.resized_from == (80, 60) and handed.resized_from == (80, 60)
    fresh = MaskDocument()
    notices = fresh.open(project.a, (W, H))
    assert notices and MaskDocument().open(project.a, (W, H), handed) == notices


def test_a_prediction_of_another_size_under_a_gt_mask_logs_no_warning(project, caplog):
    write_mask_file(gt_mask(project), one_block() > 0)
    write_mask(project.mask(), np.ones((60, 80), np.uint8))

    with caplog.at_level(logging.DEBUG, logger="app.gui.correct.mask_files"):
        read = MaskDocument.read(project.a, (W, H))

    assert read.source == "gt" and read.prediction_plane.shape == (H, W)  # stretched (O8)
    records = [r for r in caplog.records if r.name == "app.gui.correct.mask_files"]
    assert records and all(r.levelno == logging.DEBUG for r in records)


# ============================================================================ fix round 2
def test_a_prediction_rewritten_before_a_hand_back_is_read_again_on_return(make_editor, project):
    write_mask_file(gt_mask(project), one_block() > 0)  # GT G over the prediction P
    third = write_image(os.path.join(project.folder, "c.png"), value=30)
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    newer = np.zeros((H, W), np.uint8)
    newer[100:140, 120:180] = 1
    write_mask(project.mask(), newer)  # a Generate run rewrites mask/a.png: P'
    bump(project.mask())
    paint(editor, (20, 100), (80, 110))
    assert editor.save() is True  # the GT is written (the stamps check sees P')

    editor.load_image(project.b, project.folder)  # a is handed back to the read-ahead
    read_ahead_done(editor)
    editor.load_image(project.a, project.folder)

    assert (editor.layers[MASK].document.prediction() == newer).all()  # P', not P
    paint(editor, (20, 60), (80, 70))
    assert editor.save() is True
    editor.load_prediction()
    assert (plane(editor) == newer).all()  # the prediction shown is the one on disk
    assert editor.save() is True
    gt = gt_mask(project)
    assert not os.path.exists(gt)  # removed against the prediction that was shown
    shown = (imread_unicode(project.mask(), cv2.IMREAD_GRAYSCALE) > 200).astype(np.uint8)
    assert (shown == plane(editor)).all()


def test_a_prediction_deleted_while_editing_writes_the_gt(make_editor, project):
    write_mask_file(gt_mask(project), one_block() > 0)
    editor = make_editor(project.a, project.folder)
    editor.load_prediction()  # the plane is the prediction P as opened
    os.remove(project.mask())  # the prediction goes meanwhile

    assert editor.save() is True

    assert (read_gt(gt_mask(project)) == box_plane()).all()  # written, not removed
    assert not os.path.exists(project.mask())  # the prediction path is left alone

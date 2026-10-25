"""MaskDocument: the Qt-free mask of one image in the Correct workspace (C13).

Today's load and save (``MaskTracingInterface._open_document`` / ``save_mask``) moved,
plus what the workspace adds: dirty as a pixel comparison with the saved state, an
in-memory revert, a failed-save status and the pinned file stamps. It opens the GT mask
``gt/mask/<stem>.png`` first, else the prediction ``mask/<stem>.png``, and saves GT only
(WG-T2). Plane edits are driven through ``document.history`` followed by ``touch()``, as
the mask layer does after every commit, undo and redo.
"""

from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np
import pytest

from app.gui.correct import mask_document
from app.gui.correct.layer import LayerStatus, Notice
from app.gui.correct.mask_document import MaskDocument, MaskSaveResult
from app.gui.correct.read_back import mask_read_back
from app.inference import metrics
from app.io_utils import imread_unicode, imwrite_unicode
from app.masks import write_mask_file
from tests.correct_goldens_support import (
    SYNTH_STEM,
    describe_mask_file,
    load_goldens,
    make_synth_project,
    png_colour_type,
    sha,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STEM = "plot_T001_L001_2024.07.18_111530_001_SMM"
SIZE = (80, 60)  # (w, h)
NOT_SHOWN_SIZE = (
    "Saved mask size does not match the image: it is not shown and is kept unless you save over it"
)
NOT_SHOWN_UNREADABLE = (
    "Saved mask could not be read: it is not shown and is kept unless you save over it"
)
PREDICTION_NOT_SHOWN_UNREADABLE = "The prediction could not be read: it is not shown"
GT_LABEL = f"gt/mask/{STEM}.png"  # the "GT" status tooltip: relative to the project folder
MASK_EXTENSIONS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")  # lookup order
SYNTH_CASES = ("synth_model_mask_and_skeleton", "synth_blank_png")


def _image(tmp_path) -> str:
    """The image path (the document never decodes the image: the editor passes its size)."""
    return os.path.join(str(tmp_path), f"{STEM}.jpg")


def _mask_path(tmp_path, ext: str = ".png") -> str:
    """The prediction (``mask/``) of the image."""
    return os.path.normpath(os.path.join(str(tmp_path), "mask", f"{STEM}{ext}"))


def _gt_path(tmp_path) -> str:
    """The GT mask (``gt/mask/``) of the image: the one file a save writes."""
    return os.path.normpath(os.path.join(str(tmp_path), "gt", "mask", f"{STEM}.png"))


def _prediction_status() -> LayerStatus:
    tip = f"mask/{STEM}.png — the model's output. Save after an edit writes gt/mask/{STEM}.png"
    return LayerStatus("none", "Prediction", tip)


def _write(path: str, pixels: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    imwrite_unicode(path, pixels, durable=False)


def _model_mask(tmp_path) -> np.ndarray:
    """A saved 8-bit grey model mask; returns the expected 0/1 plane."""
    grey = np.zeros((60, 80), np.uint8)
    grey[0, :4] = (150, 200, 201, 255)  # the threshold is "above 200"
    grey[10:20, 30:40] = 255
    _write(_mask_path(tmp_path), grey)
    plane = np.zeros((60, 80), np.uint8)
    plane[0, 2:4] = 1
    plane[10:20, 30:40] = 1
    return plane


def _paint(doc: MaskDocument, rect: tuple[int, int, int, int], value: int = 1) -> None:
    """Commit ``value`` over ``rect`` as one action, then ``touch()`` (a stroke commit)."""
    x, y, w, h = rect
    doc.history.commit(rect, np.full((h, w), value, np.uint8))
    doc.touch()


def _boom(message: str):
    def raiser(*_args, **_kwargs):
        raise OSError(message)

    return raiser


def test_importing_it_loads_no_qt():
    code = (
        "import sys; import app.gui.correct.mask_document; "
        "qt = [m for m in sys.modules if m.split('.')[0] == 'PyQt6']; "
        "assert not qt, qt"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------------ open


def test_opens_empty_without_a_saved_mask(tmp_path):
    doc = MaskDocument()
    assert not doc.is_open() and doc.image_path == ""

    assert doc.open(_image(tmp_path), SIZE) == []

    assert doc.is_open() and doc.image_path == _image(tmp_path)
    plane = doc.history.plane
    assert plane.shape == (60, 80) and plane.dtype == np.uint8 and not plane.any()
    assert not doc.has_saved_file and not doc.is_dirty()
    assert not doc.history.can_undo
    assert doc.status() == LayerStatus("none", "No mask")
    assert doc.source is None and doc.prediction() is None
    assert doc.gt_path == _gt_path(tmp_path) and doc.prediction_path is None


def test_opens_a_model_mask_thresholded_above_200(tmp_path):
    expected = _model_mask(tmp_path)
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == []

    assert np.array_equal(doc.history.plane, expected)
    assert doc.has_saved_file and not doc.is_dirty()
    assert doc.source == "prediction" and doc.prediction_path == _mask_path(tmp_path)
    assert np.array_equal(doc.prediction(), expected)
    assert doc.prediction() is not doc.history.plane  # the plane is edited in place
    assert doc.status() == _prediction_status()


def test_opens_the_gt_mask_first_thresholded_above_200(tmp_path):
    prediction = _model_mask(tmp_path)
    grey = np.zeros((60, 80), np.uint8)
    grey[1, :3] = (200, 201, 255)
    _write(_gt_path(tmp_path), grey)
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == []

    expected = np.zeros((60, 80), np.uint8)
    expected[1, 1:3] = 1
    assert np.array_equal(doc.history.plane, expected)
    assert np.array_equal(doc.prediction(), prediction)
    assert doc.source == "gt" and doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("clean", "GT", GT_LABEL)


@pytest.mark.parametrize("case", SYNTH_CASES)
def test_opens_the_synthetic_mask_as_the_goldens_saw_it(tmp_path, case):
    _, image = make_synth_project(str(tmp_path), case)
    doc = MaskDocument()

    assert doc.open(image, (640, 480)) == []

    golden = load_goldens()["cases"][case]["mask"]["per_op_plane_sha256"]
    assert sha(doc.history.plane) == golden[0]  # op 0 only picks the brush
    assert doc.has_saved_file == (case == "synth_model_mask_and_skeleton")


def _bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _assert_not_shown_and_kept(doc: MaskDocument, path: str, text: str, before: bytes) -> None:
    """Empty, clean, flagged; status says so; a save writes and removes nothing."""
    assert not doc.history.plane.any() and doc.unloaded_file() and not doc.is_dirty()
    assert not doc.has_saved_file
    assert doc.status() == LayerStatus("error", "Mask not shown", text)

    assert doc.save() == MaskSaveResult(ok=True, kept=True)

    assert _bytes(path) == before  # nothing written or removed
    assert doc.unloaded_file() and doc.status() == LayerStatus("error", "Mask not shown", text)


def test_a_mask_of_another_size_is_not_shown_and_is_kept(tmp_path):
    _write(_gt_path(tmp_path), np.full((60, 80), 255, np.uint8))
    before = _bytes(_gt_path(tmp_path))
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), (100, 50)) == [Notice(NOT_SHOWN_SIZE, "warn")]

    assert doc.history.plane.shape == (50, 100) and doc.source == "gt"
    _assert_not_shown_and_kept(doc, _gt_path(tmp_path), NOT_SHOWN_SIZE, before)


def test_an_undecodable_mask_is_not_shown_says_so_and_is_kept(tmp_path):
    os.makedirs(tmp_path / "gt" / "mask")
    with open(_gt_path(tmp_path), "wb") as fh:
        fh.write(b"not a png")
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == [Notice(NOT_SHOWN_UNREADABLE, "warn")]

    assert doc.history.plane.shape == (60, 80)
    _assert_not_shown_and_kept(doc, _gt_path(tmp_path), NOT_SHOWN_UNREADABLE, b"not a png")


def test_an_unreadable_prediction_is_not_shown_and_never_written(tmp_path):
    os.makedirs(os.path.dirname(_mask_path(tmp_path)))
    with open(_mask_path(tmp_path), "wb") as fh:
        fh.write(b"not a png")
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == [Notice(PREDICTION_NOT_SHOWN_UNREADABLE, "warn")]

    assert doc.source == "prediction" and doc.prediction() is None
    _assert_not_shown_and_kept(
        doc, _mask_path(tmp_path), PREDICTION_NOT_SHOWN_UNREADABLE, b"not a png"
    )
    _paint(doc, (10, 10, 5, 5))
    assert doc.save().path == _gt_path(tmp_path) and _bytes(_mask_path(tmp_path)) == b"not a png"


def test_a_half_size_prediction_opens_stretched_exactly(tmp_path):
    src = np.zeros((30, 40), np.uint8)
    src[5:10, 8:20] = 255
    _write(_mask_path(tmp_path), src)
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), (80, 60)) == [
        Notice(
            "Mask is 40 x 30; shown at the photo's 80 x 60. Saving stores it at the photo's size.",
            "info",
        )
    ]

    plane = doc.history.plane
    assert plane.shape == (60, 80) and plane.dtype == np.uint8
    assert np.array_equal(plane, np.kron(src > 0, np.ones((2, 2))).astype(np.uint8))
    assert not doc.is_dirty() and doc.status() == _prediction_status()
    assert doc.source == "prediction" and doc.has_saved_file and not doc.unloaded_file()
    assert np.array_equal(doc.prediction(), plane) and doc.prediction() is not plane
    assert metrics.root_area_mm2(plane * 255) == pytest.approx(metrics.root_area_mm2(src))


@pytest.mark.parametrize("size", [(2048, 1536), (1920, 1080), (800, 600)])
def test_a_prediction_opens_stretched_at_any_ratio(tmp_path, size):
    src = np.zeros((480, 640), np.uint8)
    cv2.circle(src, (320, 240), 90, 255, -1)
    cv2.line(src, (0, 0), (639, 479), 255, 9)
    _write(_mask_path(tmp_path), src)
    width, height = size

    read = MaskDocument.read(_image(tmp_path), size)
    doc = MaskDocument()
    notices = doc.open(_image(tmp_path), size)

    assert read.resized_from == (640, 480) and read.not_loaded == ""
    assert [n.kind for n in notices] == ["info"]
    plane = doc.history.plane
    assert plane.shape == (height, width) and set(np.unique(plane)) <= {0, 1}
    assert not doc.is_dirty() and np.array_equal(doc.prediction(), plane)
    ratio = metrics.root_area_mm2(plane * 255) / metrics.root_area_mm2(src)
    assert ratio == pytest.approx(1.0, abs=0.01)


def test_an_edited_save_of_a_stretched_prediction_writes_the_photo_size(tmp_path):
    _write(_mask_path(tmp_path), np.full((30, 40), 255, np.uint8))
    before = _bytes(_mask_path(tmp_path))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (80, 60))
    _paint(doc, (10, 10, 5, 5), 0)

    result = doc.save()

    assert result.ok and result.path == _gt_path(tmp_path)
    assert read_rgba_shape(result.path) == (60, 80)
    assert _bytes(_mask_path(tmp_path)) == before  # the prediction is never written


def test_an_untouched_save_of_a_stretched_prediction_writes_nothing(tmp_path):
    _write(_mask_path(tmp_path), np.full((30, 40), 255, np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (80, 60))

    assert doc.save() == MaskSaveResult(ok=True)

    assert not os.path.exists(_gt_path(tmp_path))


def test_a_gt_shown_over_a_prediction_of_another_size_keeps_the_stretched_prediction(tmp_path):
    src = np.zeros((30, 40), np.uint8)
    src[5:10, 8:20] = 255
    _write(_mask_path(tmp_path), src)
    _write(_gt_path(tmp_path), np.zeros((60, 80), np.uint8))
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), (80, 60)) == []  # GT opens: no stretch notice

    assert doc.source == "gt" and not doc.history.plane.any()
    assert np.array_equal(doc.prediction(), np.kron(src > 0, np.ones((2, 2))).astype(np.uint8))
    assert MaskDocument.read(_image(tmp_path), (80, 60)).resized_from is None


def test_a_hand_back_keeps_the_size_a_prediction_was_stretched_from(tmp_path):
    _write(_mask_path(tmp_path), np.full((30, 40), 255, np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (80, 60))

    handed = mask_read_back(doc, (80, 60), doc.file_stamps())

    assert handed.resized_from == (40, 30) and handed.source == "prediction"
    again = MaskDocument()
    assert again.open(_image(tmp_path), (80, 60), handed) == doc.open(_image(tmp_path), (80, 60))


def test_an_editor_saved_mask_of_another_size_is_still_not_shown(tmp_path):
    write_mask_file(_gt_path(tmp_path), np.ones((60, 80), bool))

    read = MaskDocument.read(_image(tmp_path), (100, 50))
    notices = MaskDocument().open(_image(tmp_path), (100, 50))

    assert notices == [Notice(NOT_SHOWN_SIZE, "warn")]
    assert read.not_loaded == "size" and read.resized_from is None


def test_painting_over_an_unshown_mask_then_saving_replaces_it(tmp_path):
    _write(_gt_path(tmp_path), np.full((60, 80), 255, np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (100, 50))
    _paint(doc, (10, 10, 5, 5))
    assert doc.status() == LayerStatus("dirty")  # unsaved edits win over "not shown"

    result = doc.save()

    assert result.ok and not result.kept and result.path == _gt_path(tmp_path)
    assert read_rgba_shape(result.path) == (50, 100)
    assert not doc.unloaded_file() and doc.has_saved_file
    assert doc.status() == LayerStatus("clean", "GT", GT_LABEL)


def test_a_failed_save_over_an_unshown_mask_says_so_and_keeps_it_flagged(tmp_path, monkeypatch):
    _write(_gt_path(tmp_path), np.full((60, 80), 255, np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (100, 50))
    _paint(doc, (10, 10, 5, 5))
    monkeypatch.setattr(os, "replace", _boom("disk full"))

    assert not doc.save().ok

    assert doc.unloaded_file() and doc.status() == LayerStatus("error", "Not saved", "disk full")


def test_closing_forgets_the_unshown_file(tmp_path):
    _write(_gt_path(tmp_path), np.full((60, 80), 255, np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), (100, 50))

    doc.close()

    assert not doc.unloaded_file()
    assert doc.open(_image(tmp_path / "other"), SIZE) == [] and not doc.unloaded_file()


@pytest.mark.parametrize("which", ["prediction", "gt"])
def test_a_read_says_why_a_file_was_not_loaded(tmp_path, which):
    path = _mask_path(tmp_path) if which == "prediction" else _gt_path(tmp_path)
    assert MaskDocument.read(_image(tmp_path), SIZE).not_loaded == ""
    _write(path, np.full((60, 80), 255, np.uint8))
    assert MaskDocument.read(_image(tmp_path), SIZE).not_loaded == ""
    other = MaskDocument.read(_image(tmp_path), (100, 50))
    # a GT mask of another size is not shown; a prediction is stretched (O8)
    assert (other.not_loaded, other.resized_from) == (
        ("size", None) if which == "gt" else ("", (80, 60))
    )
    with open(path, "wb") as fh:
        fh.write(b"not a png")
    read = MaskDocument.read(_image(tmp_path), SIZE)
    assert (read.not_loaded, read.source) == ("unreadable", which)


def read_rgba_shape(path: str) -> tuple[int, int]:
    return imread_unicode(path, cv2.IMREAD_UNCHANGED).shape[:2]


def test_opening_another_image_starts_fresh(tmp_path, monkeypatch):
    first = tmp_path / "first"
    _model_mask(first)
    doc = MaskDocument()
    doc.open(_image(first), SIZE)
    _paint(doc, (0, 0, 5, 5))
    monkeypatch.setattr(os, "replace", _boom("disk full"))
    assert not doc.save().ok
    monkeypatch.undo()

    assert doc.open(_image(tmp_path / "second"), (30, 20)) == []

    assert doc.history.plane.shape == (20, 30) and not doc.history.plane.any()
    assert not doc.history.can_undo and not doc.history.can_redo
    assert not doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("none", "No mask")


# ------------------------------------------------------------------ save


def test_save_writes_white_on_transparent_rgba(tmp_path):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 10, 20, 4))
    _paint(doc, (40, 30, 3, 25))

    result = doc.save()

    path = _gt_path(tmp_path)
    assert result == MaskSaveResult(ok=True, path=path)
    assert png_colour_type(path) == 6
    rgba = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    on = np.zeros((60, 80), bool)
    on[10:14, 5:25] = True
    on[30:55, 40:43] = True
    assert rgba.shape == (60, 80, 4)
    assert (rgba[on] == 255).all() and not rgba[~on].any()
    assert doc.has_saved_file and not doc.is_dirty() and doc.source == "gt"
    assert doc.status() == LayerStatus("clean", "GT", GT_LABEL)
    assert not (tmp_path / "mask").exists()  # no prediction is ever written


def test_saving_an_emptied_prediction_writes_an_empty_gt_mask(tmp_path):  # O9
    _model_mask(tmp_path)
    _write(_mask_path(tmp_path, ".jpg"), np.zeros((60, 80), np.uint8))  # a legacy JPEG mask
    predictions = sorted(os.listdir(tmp_path / "mask"))
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 80, 60), value=0)  # Clear
    assert doc.is_dirty()

    result = doc.save()

    message = f"Empty mask saved as GT for {STEM}.jpg: root area 0"
    assert result == MaskSaveResult(ok=True, path=_gt_path(tmp_path), message=message)
    assert describe_mask_file(_gt_path(tmp_path))["on_count"] == 0
    assert sorted(os.listdir(tmp_path / "mask")) == predictions  # the predictions stay
    assert doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("clean", "GT", GT_LABEL)


def test_saving_without_edits_writes_nothing(tmp_path):  # O2
    _model_mask(tmp_path)
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)

    assert doc.save() == MaskSaveResult(ok=True)

    assert not (tmp_path / "gt").exists()


def test_a_plane_equal_to_the_prediction_removes_the_gt_file(tmp_path):
    prediction = _model_mask(tmp_path)
    _write(_gt_path(tmp_path), np.zeros((60, 80), np.uint8))  # an empty GT mask
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    assert doc.source == "gt" and not doc.history.plane.any()
    doc.history.commit((0, 0, 80, 60), doc.prediction())  # Load prediction
    doc.touch()

    result = doc.save()

    message = f"Mask equals the prediction: removed gt/mask/{STEM}.png"
    assert result == MaskSaveResult(ok=True, removed=True, message=message)
    assert not os.path.exists(_gt_path(tmp_path)) and os.path.isfile(_mask_path(tmp_path))
    assert np.array_equal(doc.history.plane, prediction) and not doc.is_dirty()
    assert doc.source == "prediction" and doc.status() == _prediction_status()


@pytest.mark.parametrize("case", SYNTH_CASES)
def test_clear_then_save_matches_the_mask_clear_golden(tmp_path, case):
    """``ops.mask_clear`` at the document level: Clear commits zeros, Save writes empty GT."""
    golden = load_goldens()["cases"][case]["mask_clear"]
    folder, image = make_synth_project(str(tmp_path), case)
    doc = MaskDocument()
    doc.open(image, (640, 480))

    _paint(doc, (0, 0, 640, 480), value=0)
    shas = [sha(doc.history.plane)]
    result = doc.save()
    shas.append(sha(doc.history.plane))

    assert shas == golden["per_op_plane_sha256"]
    model = case == "synth_model_mask_and_skeleton"
    assert result.ok and (result.path is not None, result.removed) == (model, False)
    saved = describe_mask_file(os.path.join(folder, "gt", "mask", f"{SYNTH_STEM}.png"))
    assert saved == golden["saved"]
    assert saved.get("on_count") == (0 if model else None)  # O9: an empty GT file


def test_failed_write_keeps_the_old_file_and_stays_dirty(tmp_path, monkeypatch):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    assert doc.save().ok
    path = _gt_path(tmp_path)
    with open(path, "rb") as fh:
        before = fh.read()
    _paint(doc, (30, 30, 10, 10))
    monkeypatch.setattr(os, "replace", _boom("disk full"))

    result = doc.save()

    assert result == MaskSaveResult(ok=False, path=path, error="disk full")
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert os.listdir(tmp_path / "gt" / "mask") == [os.path.basename(path)]  # no temp file
    assert doc.has_saved_file and doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Not saved", "disk full")
    doc.touch()  # the next edit
    assert doc.status() == LayerStatus("dirty", None)


def test_a_gt_file_already_gone_is_not_said_to_be_removed(tmp_path):
    _model_mask(tmp_path)
    _write(_gt_path(tmp_path), np.zeros((60, 80), np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    doc.history.commit((0, 0, 80, 60), doc.prediction())  # back to the prediction
    doc.touch()
    os.remove(_gt_path(tmp_path))  # gone meanwhile

    result = doc.save()

    message = f"Mask equals the prediction: gt/mask/{STEM}.png was already gone"
    assert result == MaskSaveResult(ok=True, removed=True, message=message)
    assert doc.source == "prediction" and not doc.is_dirty()


def test_failed_removal_is_a_result_and_stays_dirty(tmp_path, monkeypatch):
    _model_mask(tmp_path)
    _write(_gt_path(tmp_path), np.zeros((60, 80), np.uint8))
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    doc.history.commit((0, 0, 80, 60), doc.prediction())  # back to the prediction
    doc.touch()
    monkeypatch.setattr(os, "remove", _boom("in use"))

    result = doc.save()

    assert result == MaskSaveResult(ok=False, error="in use")
    assert os.path.isfile(_gt_path(tmp_path)) and os.path.isfile(_mask_path(tmp_path))
    assert doc.has_saved_file and doc.is_dirty() and doc.source == "gt"
    assert doc.status() == LayerStatus("error", "Not saved", "in use")


def test_unwritable_folder_returns_a_result_instead_of_raising(tmp_path):
    with open(tmp_path / "gt", "wb") as fh:  # a file where the GT folder belongs
        fh.write(b"")
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))

    result = doc.save()

    assert not result.ok and result.error
    assert result.path == _gt_path(tmp_path) and not result.removed
    assert doc.is_dirty() and doc.status().state == "error"


def test_a_successful_save_clears_the_error(tmp_path, monkeypatch):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    monkeypatch.setattr(os, "replace", _boom("disk full"))
    assert not doc.save().ok
    monkeypatch.undo()

    assert doc.save().ok

    assert doc.status() == LayerStatus("clean", "GT", GT_LABEL)


# ------------------------------------------------------------------ dirty and revert


def test_dirty_is_a_pixel_comparison_with_the_saved_state(tmp_path):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)

    _paint(doc, (5, 5, 10, 10))
    assert doc.is_dirty() and doc.status() == LayerStatus("dirty", None)
    doc.history.undo()
    doc.touch()
    assert not doc.is_dirty()  # undone back to the saved pixels
    doc.history.redo()
    doc.touch()
    assert doc.is_dirty()
    assert doc.save().ok and not doc.is_dirty()

    _paint(doc, (20, 20, 4, 4))
    _paint(doc, (20, 20, 4, 4), value=0)
    assert not doc.is_dirty() and doc.history.can_undo  # two actions, the saved pixels
    doc.history.undo()
    doc.touch()
    assert doc.is_dirty()


def test_dirty_sees_every_pixel_of_an_odd_sized_plane(tmp_path):
    """7x5 = 35 px: the compare must cover the bytes past the last 8-byte word too."""
    doc = MaskDocument()
    doc.open(_image(tmp_path), (7, 5))
    for y in range(5):
        for x in range(7):
            _paint(doc, (x, y, 1, 1))
            assert doc.is_dirty(), (x, y)
            doc.history.undo()
            doc.touch()
            assert not doc.is_dirty(), (x, y)


def test_dirty_compare_runs_once_per_revision(tmp_path, monkeypatch):
    calls = []
    compare = mask_document._planes_equal

    def counting(a, b):
        calls.append(1)
        return compare(a, b)

    monkeypatch.setattr(mask_document, "_planes_equal", counting)
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    revision = doc.revision

    assert all(doc.is_dirty() for _ in range(3))
    assert len(calls) == 1 and doc.revision == revision
    doc.touch()
    assert doc.revision == revision + 1
    assert doc.is_dirty() and len(calls) == 2
    assert doc.save().ok
    assert not doc.is_dirty() and len(calls) == 2  # a save knows it is clean


def test_revert_restores_the_saved_plane_and_clears_the_history(tmp_path):
    opened = _model_mask(tmp_path)
    changes = []
    doc = MaskDocument(on_history_change=lambda: changes.append(1))
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 10, 10))
    _paint(doc, (30, 10, 10, 10), value=0)
    doc.history.undo()  # a redo branch too
    doc.touch()
    assert changes
    changes.clear()
    revision = doc.revision

    doc.revert()

    assert np.array_equal(doc.history.plane, opened)
    assert not doc.history.can_undo and not doc.history.can_redo
    assert changes  # the owner hears that the history emptied
    assert doc.revision > revision and not doc.is_dirty()
    assert doc.status() == _prediction_status()


def test_revert_goes_back_to_the_last_save(tmp_path):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    assert doc.save().ok
    saved = doc.history.plane.copy()
    _paint(doc, (30, 30, 10, 10))

    doc.revert()

    assert np.array_equal(doc.history.plane, saved)
    assert doc.has_saved_file and not doc.is_dirty()


# ------------------------------------------------------------------ stamps and lifecycle


def test_file_stamps_pin_the_gt_mask_then_every_mask_candidate_in_lookup_order(tmp_path):
    doc = MaskDocument()
    assert doc.file_stamps() == () and doc.write_stamps() == ()
    doc.open(_image(tmp_path), SIZE)
    paths = [_gt_path(tmp_path)] + [_mask_path(tmp_path, ext) for ext in MASK_EXTENSIONS]
    assert doc.file_stamps() == tuple((path, None) for path in paths)
    assert doc.write_stamps() == ((paths[0], None),)

    _paint(doc, (5, 5, 10, 10))
    assert doc.save().ok
    written = doc.file_stamps()
    stat = os.stat(paths[0])
    assert [entry[0] for entry in written] == paths
    assert written[0] == (paths[0], stat.st_mtime_ns, stat.st_size)
    assert doc.file_stamps() == written  # read afresh: equal while nothing changed
    assert doc.write_stamps() == (written[0],)

    _paint(doc, (30, 30, 20, 20))
    assert doc.save().ok
    rewritten = doc.file_stamps()
    assert rewritten != written

    os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    touched = doc.file_stamps()  # same bytes, a new time: another program rewrote it
    assert touched != rewritten

    _write(paths[3], np.zeros((60, 80), np.uint8))  # a legacy .jpg prediction appears
    with_jpg = doc.file_stamps()
    assert with_jpg != touched and doc.write_stamps() == (touched[0],)
    stat = os.stat(paths[3])
    assert with_jpg[3] == (paths[3], stat.st_mtime_ns, stat.st_size)

    doc.close()
    assert doc.file_stamps() == ()


def test_matches_disk_needs_the_same_source(tmp_path):
    _model_mask(tmp_path)
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 80, 60), value=0)
    assert doc.save().path == _gt_path(tmp_path)  # an empty GT mask
    assert doc.matches_disk()

    os.remove(_gt_path(tmp_path))  # gone: the prediction would open now
    assert not doc.matches_disk()


def test_close_forgets_the_document(tmp_path):
    _model_mask(tmp_path)
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 10, 10))

    doc.close()

    assert not doc.is_open() and doc.image_path == "" and doc.history.plane is None
    assert not doc.has_saved_file and not doc.is_dirty() and not doc.history.can_undo
    assert doc.status() == LayerStatus("none")
    result = doc.save()
    assert not result.ok and result.path is None and not result.removed
    doc.revert()  # nothing to revert: no error
    assert not doc.is_open()


def test_small_strokes_on_a_large_canvas_keep_the_history_under_a_mebibyte(tmp_path):
    doc = MaskDocument()
    doc.open(_image(tmp_path), (2048, 1536))

    for i in range(25):
        _paint(doc, (100, 44 + i * 50, 300, 4))

    assert len(doc.history.undo_stack) == 25
    assert doc.history.nbytes < 1024 * 1024

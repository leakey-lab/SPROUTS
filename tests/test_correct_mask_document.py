"""MaskDocument: the Qt-free mask of one image in the Correct workspace (C13).

Today's load and save (``MaskTracingInterface._open_document`` / ``save_mask``) moved
verbatim, plus what the workspace adds: dirty as a pixel comparison with the saved
state, an in-memory revert, a failed-save status and the pinned file stamps. Plane
edits are driven through ``document.history`` followed by ``touch()``, as the mask
layer does after every commit, undo and redo.
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
from app.io_utils import imread_unicode, imwrite_unicode
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
WRONG_SIZE = Notice("Saved mask size does not match the image; starting empty", "warn")
MASK_EXTENSIONS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")  # lookup order
SYNTH_CASES = ("synth_model_mask_and_skeleton", "synth_blank_png")


def _image(tmp_path) -> str:
    """The image path (the document never decodes the image: the editor passes its size)."""
    return os.path.join(str(tmp_path), f"{STEM}.jpg")


def _mask_path(tmp_path, ext: str = ".png") -> str:
    return os.path.normpath(os.path.join(str(tmp_path), "mask", f"{STEM}{ext}"))


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


def test_opens_a_model_mask_thresholded_above_200(tmp_path):
    expected = _model_mask(tmp_path)
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == []

    assert np.array_equal(doc.history.plane, expected)
    assert doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("clean", None)


@pytest.mark.parametrize("case", SYNTH_CASES)
def test_opens_the_synthetic_mask_as_the_goldens_saw_it(tmp_path, case):
    _, image = make_synth_project(str(tmp_path), case)
    doc = MaskDocument()

    assert doc.open(image, (640, 480)) == []

    golden = load_goldens()["cases"][case]["mask"]["per_op_plane_sha256"]
    assert sha(doc.history.plane) == golden[0]  # op 0 only picks the brush
    assert doc.has_saved_file == (case == "synth_model_mask_and_skeleton")


def test_wrong_size_mask_opens_empty_with_a_warning(tmp_path):
    _write(_mask_path(tmp_path), np.full((60, 80), 255, np.uint8))
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), (100, 50)) == [WRONG_SIZE]

    assert doc.history.plane.shape == (50, 100) and not doc.history.plane.any()
    assert not doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("none", "No mask")


def test_undecodable_mask_opens_empty(tmp_path):
    os.makedirs(tmp_path / "mask")
    with open(_mask_path(tmp_path), "wb") as fh:
        fh.write(b"not a png")
    doc = MaskDocument()

    assert doc.open(_image(tmp_path), SIZE) == []

    assert doc.history.plane.shape == (60, 80) and not doc.history.plane.any()
    assert not doc.has_saved_file and not doc.is_dirty()


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

    path = _mask_path(tmp_path)
    assert result == MaskSaveResult(ok=True, path=path)
    assert png_colour_type(path) == 6
    rgba = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    on = np.zeros((60, 80), bool)
    on[10:14, 5:25] = True
    on[30:55, 40:43] = True
    assert rgba.shape == (60, 80, 4)
    assert (rgba[on] == 255).all() and not rgba[~on].any()
    assert doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("clean", None)


def test_saving_an_empty_plane_removes_every_saved_variant(tmp_path):
    _model_mask(tmp_path)
    _write(_mask_path(tmp_path, ".jpg"), np.zeros((60, 80), np.uint8))  # a legacy JPEG mask
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 80, 60), value=0)  # Clear
    assert doc.is_dirty()

    result = doc.save()

    assert result == MaskSaveResult(ok=True, path=None, removed=True)
    assert os.listdir(tmp_path / "mask") == []
    assert not doc.has_saved_file and not doc.is_dirty()
    assert doc.status() == LayerStatus("none", "No mask")


def test_saving_an_empty_plane_without_a_file_removes_nothing(tmp_path):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)

    assert doc.save() == MaskSaveResult(ok=True, path=None, removed=False)

    assert not (tmp_path / "mask").exists()


@pytest.mark.parametrize("case", SYNTH_CASES)
def test_clear_then_save_matches_the_mask_clear_golden(tmp_path, case):
    """``ops.mask_clear`` at the document level: Clear commits zeros, Save removes the file."""
    golden = load_goldens()["cases"][case]["mask_clear"]
    folder, image = make_synth_project(str(tmp_path), case)
    doc = MaskDocument()
    doc.open(image, (640, 480))

    _paint(doc, (0, 0, 640, 480), value=0)
    shas = [sha(doc.history.plane)]
    result = doc.save()
    shas.append(sha(doc.history.plane))

    assert shas == golden["per_op_plane_sha256"]
    assert result.ok and result.path is None
    assert result.removed == (case == "synth_model_mask_and_skeleton")
    assert describe_mask_file(os.path.join(folder, "mask", f"{SYNTH_STEM}.png")) == golden["saved"]


def test_failed_write_keeps_the_old_file_and_stays_dirty(tmp_path, monkeypatch):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    assert doc.save().ok
    path = _mask_path(tmp_path)
    with open(path, "rb") as fh:
        before = fh.read()
    _paint(doc, (30, 30, 10, 10))
    monkeypatch.setattr(os, "replace", _boom("disk full"))

    result = doc.save()

    assert result == MaskSaveResult(ok=False, path=path, error="disk full")
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert os.listdir(tmp_path / "mask") == [os.path.basename(path)]  # no temp file left
    assert doc.has_saved_file and doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Not saved", "disk full")
    doc.touch()  # the next edit
    assert doc.status() == LayerStatus("dirty", None)


def test_failed_removal_is_a_result_and_stays_dirty(tmp_path, monkeypatch):
    _model_mask(tmp_path)
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (0, 0, 80, 60), value=0)
    monkeypatch.setattr(os, "remove", _boom("in use"))

    result = doc.save()

    assert result == MaskSaveResult(ok=False, error="in use")
    assert os.path.isfile(_mask_path(tmp_path))
    assert doc.has_saved_file and doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Not saved", "in use")


def test_unwritable_folder_returns_a_result_instead_of_raising(tmp_path):
    with open(tmp_path / "mask", "wb") as fh:  # a file where the mask folder belongs
        fh.write(b"")
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))

    result = doc.save()

    assert not result.ok and result.error
    assert result.path == _mask_path(tmp_path) and not result.removed
    assert doc.is_dirty() and doc.status().state == "error"


def test_a_successful_save_clears_the_error(tmp_path, monkeypatch):
    doc = MaskDocument()
    doc.open(_image(tmp_path), SIZE)
    _paint(doc, (5, 5, 10, 10))
    monkeypatch.setattr(os, "replace", _boom("disk full"))
    assert not doc.save().ok
    monkeypatch.undo()

    assert doc.save().ok

    assert doc.status() == LayerStatus("clean", None)


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
    assert doc.status() == LayerStatus("clean", None)


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


def test_file_stamps_pin_every_mask_candidate_in_lookup_order(tmp_path):
    doc = MaskDocument()
    assert doc.file_stamps() == ()
    doc.open(_image(tmp_path), SIZE)
    paths = [_mask_path(tmp_path, ext) for ext in MASK_EXTENSIONS]
    assert doc.file_stamps() == tuple((path, None) for path in paths)

    _paint(doc, (5, 5, 10, 10))
    assert doc.save().ok
    written = doc.file_stamps()
    stat = os.stat(paths[0])
    assert [entry[0] for entry in written] == paths
    assert written[0] == (paths[0], stat.st_mtime_ns, stat.st_size)
    assert doc.file_stamps() == written  # read afresh: equal while nothing changed

    _paint(doc, (30, 30, 20, 20))
    assert doc.save().ok
    rewritten = doc.file_stamps()
    assert rewritten != written

    os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    touched = doc.file_stamps()  # same bytes, a new time: another program rewrote it
    assert touched != rewritten

    _write(paths[2], np.zeros((60, 80), np.uint8))  # a legacy .jpg appears
    with_jpg = doc.file_stamps()
    assert with_jpg != touched
    stat = os.stat(paths[2])
    assert with_jpg[2] == (paths[2], stat.st_mtime_ns, stat.st_size)

    doc.close()
    assert doc.file_stamps() == ()


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

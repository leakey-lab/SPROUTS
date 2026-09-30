"""SkeletonDocument: the skeleton of one image for the Correct workspace (Qt-free).

Which file opens (the saved correction, else the generated skeleton, injected resolver
first), the save (at image resolution, so a save never fragments the skeleton; atomic; a failure
is reported, never raised), Discard (re-read the start file), Load file, the status
labels of today's skeleton editor and the pinned file stamps (plan contract C14).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import cv2
import numpy as np
import pytest
from skimage.morphology import skeletonize

from app.config import SKELETON_OUTPUT_SIZE
from app.gui.correct.layer import LayerStatus, Notice
from app.gui.correct.skeleton_document import (
    CORRECTED,
    GENERATED,
    LOADED,
    SkeletonDocument,
    SkeletonSaveResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
W, H = 200, 150


def _write_skeleton(path, row: int = 75, size: tuple[int, int] = (W, H), value: int = 255):
    """A grey file with one horizontal line (what Generate or a correction looks like)."""
    w, h = size
    raster = np.zeros((h, w), np.uint8)
    raster[row, 20 : w - 20] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(str(path), raster)
    return str(path)


@pytest.fixture
def project(tmp_path):
    image = tmp_path / "a.png"
    assert cv2.imwrite(str(image), np.full((H, W, 3), 90, np.uint8))
    return tmp_path, str(image)


def _paths(root):
    return (
        str(root / "skeleton_corrections" / "a_skeleton.png"),
        str(root / "skeletons" / "a_fake.png"),
    )


def _opened(root, image, resolver=None) -> SkeletonDocument:
    doc = SkeletonDocument(resolver)
    doc.open(image, (W, H), str(root))
    return doc


def _draw(doc: SkeletonDocument) -> None:
    """One user action, as the Polyline tool commits it."""
    doc.model.push_undo()
    doc.model.draw_polyline([(30, 30), (150, 120)], thickness=3)
    doc.model.skeletonize()


# ------------------------------------------------------------------ opening
def test_the_correction_wins_over_the_generated_skeleton(project):
    root, image = project
    correction, generated = _paths(root)
    _write_skeleton(generated, row=30)
    _write_skeleton(correction, row=100)
    doc = _opened(root, image)
    assert doc.source == CORRECTED
    assert doc.model.mask[100].any() and not doc.model.mask[30].any()
    assert not doc.is_dirty() and not doc.model.can_undo


def test_the_generated_skeleton_opens_when_there_is_no_correction(project):
    root, image = project
    _write_skeleton(_paths(root)[1], row=40)
    doc = _opened(root, image)
    assert doc.source == GENERATED
    assert doc.model.mask[40].any()
    assert doc.is_open() and doc.image_path == image


def test_the_injected_resolver_is_asked_first(project):
    root, image = project
    _write_skeleton(_paths(root)[1], row=30)
    elsewhere = _write_skeleton(root / "out" / "a_fake.png", row=110)
    asked = []

    def resolver(image_path):
        asked.append(image_path)
        return elsewhere

    doc = _opened(root, image, resolver)
    assert asked and asked[0] == image
    assert doc.source == GENERATED
    assert doc.model.mask[110].any() and not doc.model.mask[30].any()


def test_nothing_on_disk_opens_an_empty_skeleton_at_image_size(project):
    root, image = project
    doc = SkeletonDocument()
    assert doc.open(image, (W, H), None) == []
    assert doc.source is None and doc.save_size is None
    assert doc.model.mask.shape == (H, W) and not doc.model.mask.any()
    assert doc.images_base_folder == str(root)  # the image's folder by default


def test_a_generated_raster_is_upscaled_bilinearly_and_thinned_to_image_size(project):
    root, image = project
    raster = np.zeros((256, 341), np.uint8)
    cv2.line(raster, (20, 30), (320, 200), 191, 2)
    os.makedirs(root / "skeletons")
    cv2.imwrite(_paths(root)[1], raster)
    doc = SkeletonDocument()
    doc.open(image, (640, 480), str(root))
    upscaled = cv2.resize(raster, (640, 480), interpolation=cv2.INTER_LINEAR)
    _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    expected = skeletonize(binary > 0).astype(np.uint8) * 255
    assert doc.save_size == (341, 256)
    assert np.array_equal(doc.model.mask, expected)


def test_an_unreadable_start_file_is_reported_and_opens_empty(project):
    root, image = project
    correction = _paths(root)[0]
    os.makedirs(os.path.dirname(correction))
    Path(correction).write_bytes(b"not a png")
    doc = SkeletonDocument()
    notices = doc.open(image, (W, H), str(root))
    assert notices == [Notice("Could not read skeleton a_skeleton.png", "danger")]
    assert doc.source is None and not doc.model.mask.any()


def test_the_model_is_one_object_for_the_document_lifetime(project):
    root, image = project
    doc = SkeletonDocument()
    model = doc.model
    hook = []
    model.on_action = lambda: hook.append(True)
    doc.open(image, (W, H), str(root))
    doc.close()
    doc.open(image, (W, H), str(root))
    doc.reopen()
    _draw(doc)
    assert doc.model is model and hook == [True]


def test_close_forgets_the_image(project):
    root, image = project
    doc = _opened(root, image)
    _draw(doc)
    doc.close()
    assert not doc.is_open() and not doc.is_dirty()
    assert doc.image_path == "" and doc.source is None and doc.model.mask is None
    assert doc.file_stamps() == ()
    assert doc.status() == LayerStatus("none")
    assert doc.save().ok is False


# ------------------------------------------------------------------ dirty and Discard
def test_dirty_is_a_pixel_comparison_with_the_opened_file(project):
    root, image = project
    _write_skeleton(_paths(root)[1])
    doc = _opened(root, image)
    assert not doc.is_dirty()
    _draw(doc)
    assert doc.is_dirty()
    doc.model.undo()
    assert not doc.is_dirty()


def test_reopen_rereads_the_start_file_and_clears_the_history(project):
    root, image = project
    _write_skeleton(_paths(root)[1], row=60)
    doc = _opened(root, image)
    opened = doc.model.mask.copy()
    _draw(doc)
    assert doc.reopen() == []
    assert np.array_equal(doc.model.mask, opened)
    assert not doc.is_dirty() and not doc.model.can_undo and not doc.model.can_redo
    assert doc.source == GENERATED


def test_reopen_after_a_save_rereads_the_saved_correction(project):
    root, image = project
    _write_skeleton(_paths(root)[1], row=60)
    doc = _opened(root, image)
    _draw(doc)
    assert doc.save().ok
    doc.model.push_undo()
    doc.model.clear()
    doc.reopen()
    reference = SkeletonDocument()
    reference.open(image, (W, H), str(root))
    assert doc.source == CORRECTED
    assert np.array_equal(doc.model.mask, reference.model.mask)


# ------------------------------------------------------------------ Load file
def test_load_file_sets_the_source_by_path_identity(project):
    root, image = project
    correction, generated = _paths(root)
    _write_skeleton(correction, row=100)
    _write_skeleton(generated, row=30)
    other = _write_skeleton(root / "elsewhere" / "b.png", row=50, size=(100, 75))
    doc = _opened(root, image)
    same_generated = os.path.join(str(root), ".", "skeletons", "a_fake.png")
    assert doc.load_file(same_generated) == []
    assert doc.source == GENERATED and doc.model.mask[30].any()
    assert doc.load_file(other) == []
    assert doc.source == LOADED and doc.save_size == (100, 75)
    assert doc.model.mask.shape == (H, W) and not doc.model.can_undo
    doc.load_file(correction.upper() if os.name == "nt" else correction)
    assert doc.source == CORRECTED


def test_load_file_of_an_unreadable_file_changes_nothing(project):
    root, image = project
    _write_skeleton(_paths(root)[1], row=30)
    doc = _opened(root, image)
    before = doc.model.mask.copy()
    bad = root / "bad.png"
    bad.write_bytes(b"not an image")
    assert doc.load_file(str(bad)) == [Notice("Could not read skeleton bad.png", "danger")]
    assert doc.source == GENERATED and np.array_equal(doc.model.mask, before)


# ------------------------------------------------------------------ saving
def test_save_writes_the_correction_at_image_resolution_not_the_loaded_size(project):
    root, image = project
    correction, generated = _paths(root)
    _write_skeleton(generated, row=30, size=(341, 256))
    doc = _opened(root, image)
    _draw(doc)
    result = doc.save()
    assert result == SkeletonSaveResult(True, correction)
    written = cv2.imread(correction, cv2.IMREAD_GRAYSCALE)
    assert written.shape == (H, W)  # the generated file was 341x256
    assert set(np.unique(written).tolist()) == {0, 255}
    assert doc.source == CORRECTED and not doc.is_dirty() and doc.save_error == ""


def test_save_without_a_loaded_raster_writes_at_image_resolution(project):
    root, image = project
    doc = _opened(root, image)
    _draw(doc)
    assert doc.save().ok
    written = cv2.imread(_paths(root)[0], cv2.IMREAD_GRAYSCALE)
    assert (written.shape[1], written.shape[0]) == (W, H)


def _diagonal_generated(path, size):
    """A generated-looking skeleton: 1 px diagonals and a curve at model resolution."""
    w, h = size
    raster = np.zeros((h, w), np.uint8)
    cv2.line(raster, (5, 5), (w - 6, h - 6), 255, 1)
    cv2.line(raster, (5, h - 6), (w // 2, 8), 255, 1)
    cv2.ellipse(raster, (w // 2, h // 2), (w // 3, h // 4), 20, 0, 300, 255, 1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(str(path), raster)


def _components(mask) -> int:
    return cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)[0] - 1


def test_save_then_reopen_gives_back_the_edited_skeleton_exactly(tmp_path):
    """Q1: a correction is saved at image resolution, so saving never fragments it.

    The generated file is small (SKELETON_OUTPUT_SIZE); saving used to shrink the edited
    image-size raster back to it with nearest neighbour, which broke 1 px lines apart.
    """
    w, h = 640, 480
    image = tmp_path / "a.png"
    assert cv2.imwrite(str(image), np.full((h, w, 3), 90, np.uint8))
    _diagonal_generated(tmp_path / "skeletons" / "a_fake.png", SKELETON_OUTPUT_SIZE)
    doc = SkeletonDocument()
    doc.open(str(image), (w, h), str(tmp_path))
    doc.model.push_undo()
    doc.model.erase_segment((320, 240), (320, 240), 2)  # a small eraser dab
    doc.model.skeletonize()
    edited = doc.model.mask.copy()
    assert doc.save().ok

    written = cv2.imread(str(tmp_path / "skeleton_corrections" / "a_skeleton.png"), 0)
    assert (written.shape[1], written.shape[0]) == (w, h)
    reopened = SkeletonDocument()
    reopened.open(str(image), (w, h), str(tmp_path))
    assert reopened.source == CORRECTED
    assert np.array_equal(reopened.model.mask, edited)
    assert _components(reopened.model.mask) == _components(edited)
    assert not reopened.is_dirty()


def test_an_old_small_correction_still_opens_at_image_size(tmp_path):
    """Corrections saved before the fix are 341x256; they open upscaled, as before."""
    w, h = 640, 480
    image = tmp_path / "a.png"
    assert cv2.imwrite(str(image), np.full((h, w, 3), 90, np.uint8))
    _write_skeleton(
        tmp_path / "skeleton_corrections" / "a_skeleton.png", row=100, size=SKELETON_OUTPUT_SIZE
    )
    doc = SkeletonDocument()
    doc.open(str(image), (w, h), str(tmp_path))
    assert doc.source == CORRECTED and doc.model.mask.shape == (h, w)
    assert doc.model.mask.any()


def test_a_failed_save_keeps_the_old_file_and_reports_until_the_next_edit(project, monkeypatch):
    root, image = project
    correction = _write_skeleton(_paths(root)[0], row=100)
    old = Path(correction).read_bytes()
    doc = _opened(root, image)
    _draw(doc)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    result = doc.save()
    monkeypatch.undo()
    assert result.ok is False and result.path is None and "disk full" in result.error
    assert Path(correction).read_bytes() == old
    assert not [name for name in os.listdir(root / "skeleton_corrections") if ".tmp" in name]
    assert doc.save_error == result.error and doc.error_revision == doc.model.revision
    assert doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Not saved", result.error)
    _draw(doc)
    assert doc.status() == LayerStatus("dirty")


def test_an_unwritable_corrections_folder_does_not_raise(project, monkeypatch):
    root, image = project
    doc = _opened(root, image)
    _draw(doc)

    def deny(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", deny)
    result = doc.save()
    assert result.ok is False and "read-only share" in result.error
    assert doc.is_dirty()


# ------------------------------------------------------------------ status
def test_status_labels_follow_the_source(project):
    root, image = project
    correction, generated = _paths(root)
    doc = _opened(root, image)
    assert doc.status() == LayerStatus("none", "No skeleton")
    _write_skeleton(generated, value=0)  # an empty generated skeleton (all black)
    doc.reopen()
    assert doc.status() == LayerStatus("none", "Generated · empty", "Not corrected yet")
    _write_skeleton(generated, row=40)
    doc.reopen()
    assert doc.status() == LayerStatus("none", "Generated", "Not corrected yet")
    _draw(doc)
    assert doc.status() == LayerStatus("dirty")
    doc.save()
    assert doc.status() == LayerStatus("clean", "Saved", correction)
    doc.load_file(_write_skeleton(root / "other.png", row=20))
    assert doc.status() == LayerStatus("none", "Loaded file")


# ------------------------------------------------------------------ file stamps
def _stamp(path):
    st = os.stat(path)
    return (path, st.st_mtime_ns, st.st_size)


def test_file_stamps_name_the_correction_and_the_default_generated_path(project):
    root, image = project
    correction, generated = _paths(root)
    doc = SkeletonDocument()
    assert doc.file_stamps() == ()
    doc.open(image, (W, H), str(root))
    assert doc.file_stamps() == ((correction, None), (generated, None))
    _write_skeleton(generated)
    assert doc.file_stamps() == ((correction, None), _stamp(generated))


def test_our_first_save_changes_the_stamps(project):
    root, image = project
    doc = _opened(root, image)
    before = doc.file_stamps()
    _draw(doc)
    assert doc.save().ok
    after = doc.file_stamps()
    assert after != before and after[0] == _stamp(_paths(root)[0])


def test_rewriting_the_generated_file_changes_the_stamps(project):
    root, image = project
    generated = _write_skeleton(_paths(root)[1], row=30)
    doc = _opened(root, image)
    before = doc.file_stamps()
    _write_skeleton(generated, row=31)
    later = os.stat(generated).st_mtime_ns + 10_000_000
    os.utime(generated, ns=(later, later))
    assert doc.file_stamps() != before and doc.file_stamps()[1] == _stamp(generated)


def test_a_resolver_that_starts_finding_a_file_changes_the_stamps(project):
    root, image = project
    produced = str(root / "processed" / "a_fake.png")
    asked = []

    def resolver(image_path):
        asked.append(image_path)
        return produced

    doc = _opened(root, image, resolver)
    asked.clear()
    before = doc.file_stamps()
    assert before[1] == (_paths(root)[1], None)  # not found: the default path, absent
    _write_skeleton(produced)
    after = doc.file_stamps()
    assert asked  # asked afresh by the call
    assert after != before and after[1] == _stamp(produced)


# ------------------------------------------------------------------ Qt-free
def test_the_module_loads_no_qt():
    code = textwrap.dedent(
        """
        import sys
        import app.gui.correct.skeleton_document
        print(sorted(name for name in sys.modules if name.split(".")[0] == "PyQt6"))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"

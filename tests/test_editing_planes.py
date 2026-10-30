"""app.editing.planes: the plane compare, the saved plane and the raster-document base.

The Correct mask and class-label documents decide "dirty", "equal to the prediction" (a
save then removes the GT mask) and "the file on disk is the saved state" with
:func:`planes_equal`; :class:`SavedPlane` keeps the plane as last opened or saved and
compares at most once per revision; :class:`PlaneDocument` is their shared state machine.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.editing import planes
from app.editing.planes import PlaneDocument, SavedPlane, planes_equal
from app.gui.patch_history import PatchHistory

ROOT = Path(__file__).resolve().parents[1]


def _plane(h=37, w=53, seed=0) -> np.ndarray:
    return (np.random.default_rng(seed).random((h, w)) > 0.6).astype(np.uint8)


# ------------------------------------------------------------------------- planes_equal
def test_equal_planes_and_one_changed_pixel_anywhere():
    a = _plane()
    assert planes_equal(a, a.copy())
    for y, x in ((0, 0), (36, 52), (36, 0), (0, 52), (18, 26)):  # corners, last row, middle
        b = a.copy()
        b[y, x] ^= 1
        assert not planes_equal(a, b), (y, x)
        assert not planes_equal(b, a), (y, x)


def test_none_shape_and_dtype_mismatches_are_not_equal():
    a = _plane()
    assert not planes_equal(a, None) and not planes_equal(None, a)
    assert not planes_equal(None, None)
    assert not planes_equal(a, a[:-1])
    assert not planes_equal(a, a.T.copy())
    assert not planes_equal(a, a.astype(np.int32))  # same values, another dtype


@pytest.mark.parametrize("shape", [(0, 0), (0, 5), (5, 0)])
def test_empty_planes_of_one_shape_are_equal(shape):
    assert planes_equal(np.zeros(shape, np.uint8), np.zeros(shape, np.uint8))


def test_views_fortran_order_read_only_and_3d_arrays_compare_by_value():
    a = _plane(40, 60)
    b = a.copy()
    assert planes_equal(a[:, ::2], b[:, ::2])  # column-strided views
    assert planes_equal(np.asfortranarray(a), b)
    frozen = a.copy()
    frozen.flags.writeable = False
    assert planes_equal(frozen, b)
    cube, other = np.stack([a, a]), np.stack([a, a])
    other[1, 3, 4] ^= 1
    assert planes_equal(cube, cube.copy()) and not planes_equal(cube, other)
    b[2, 2] ^= 1  # column 2 is in the strided view
    assert not planes_equal(a[:, ::2], b[:, ::2])


def test_agrees_with_array_equal_on_random_planes():
    rng = np.random.default_rng(7)
    for _ in range(400):
        h, w = rng.integers(1, 70, size=2)
        a = rng.integers(0, 4, size=(h, w), dtype=np.uint8)
        b = a.copy()
        if rng.random() < 0.5:
            b[rng.integers(h), rng.integers(w)] += 1
        assert planes_equal(a, b) == bool(np.array_equal(a, b))


# --------------------------------------------------------------------------- SavedPlane
def test_take_copies_and_reuses_the_held_buffer():
    saved, plane = SavedPlane(), _plane()
    saved.take(plane, 1)
    held = saved.plane
    assert held is not plane and np.array_equal(held, plane)

    plane[0, 0] ^= 1
    saved.take(plane, 2)  # same shape: written into the same buffer
    assert saved.plane is held and np.array_equal(held, plane)

    bigger = _plane(40, 60)
    saved.take(bigger, 3)  # another shape: a new copy
    assert saved.plane is not held and np.array_equal(saved.plane, bigger)

    saved.take(None, 4)
    assert saved.plane is None


def test_differs_compares_at_most_once_per_revision(monkeypatch):
    calls = []
    real = planes.planes_equal
    monkeypatch.setattr(planes, "planes_equal", lambda a, b: calls.append(1) or real(a, b))
    saved, plane = SavedPlane(), _plane()
    saved.take(plane, 5)

    assert not saved.differs(plane, 5) and calls == []  # taken at this revision: clean
    plane[3, 3] ^= 1
    assert saved.differs(plane, 6) and saved.differs(plane, 6) and len(calls) == 1
    plane[3, 3] ^= 1
    assert not saved.differs(plane, 7) and len(calls) == 2  # back to the saved pixels


def test_differs_without_a_saved_plane_reads_unsaved():
    assert SavedPlane().differs(_plane(), 1)


def test_restore_writes_in_place_or_returns_a_copy():
    saved, plane = SavedPlane(), _plane()
    saved.take(plane, 1)
    edited = plane.copy()
    edited[:5] = 1

    kept = saved.restore(edited)
    assert kept is edited and np.array_equal(edited, plane)  # holders of it stay valid

    other = np.ones((4, 4), np.uint8)
    kept = saved.restore(other)
    assert kept is not saved.plane and np.array_equal(kept, plane)


# ------------------------------------------------------------------------ PlaneDocument
class _Doc(PlaneDocument):
    def __init__(self):
        super().__init__(PatchHistory())
        self.events: list[str] = []

    def adopt(self, plane):
        self.image_path, self.gt_path = "a.png", "gt/mask/a.png"
        self.history.reset(plane)
        self._restart()

    def is_dirty(self) -> bool:
        return self.is_open() and not self._plane_saved()

    def _reverted(self) -> None:
        self.events.append("reverted")


def test_a_document_is_clean_after_open_and_dirty_after_an_edit():
    doc = _Doc()
    assert not doc.is_open() and doc.write_stamps() == () and doc.revision == 0
    doc.adopt(_plane())
    revision = doc.revision
    assert doc.is_open() and not doc.is_dirty() and revision == 1

    doc.history.commit((0, 0, 2, 2), np.full((2, 2), 1, np.uint8))
    doc.touch()
    assert doc.revision == revision + 1 and doc.is_dirty()
    assert doc.write_stamps() == (("gt/mask/a.png", None),)


def test_revert_puts_the_saved_plane_back_in_place_and_clears_the_history():
    doc = _Doc()
    opened = _plane()
    doc.adopt(opened.copy())
    plane = doc.history.plane
    doc.history.commit((0, 0, 3, 3), np.full((3, 3), 1, np.uint8))
    doc.touch()

    doc.revert()

    assert doc.history.plane is plane and np.array_equal(plane, opened)
    assert not doc.history.can_undo and not doc.is_dirty()
    assert doc.events == ["reverted"]


def test_revert_while_closed_does_nothing():
    doc = _Doc()
    doc.revert()
    assert doc.events == [] and doc.revision == 0


def test_a_failed_save_is_reported_until_the_next_edit():
    doc = _Doc()
    doc.adopt(_plane())
    assert doc._save_failed(OSError("disk full")) == "disk full"
    assert (doc._save_error, doc._error_revision) == ("disk full", doc.revision)
    doc.touch()
    assert doc._error_revision != doc.revision  # the status no longer shows it
    assert not doc.unloaded_file()


def test_the_module_loads_neither_qt_nor_torch():
    probe = (
        "import sys, app.editing.planes; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6', 'torch')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"

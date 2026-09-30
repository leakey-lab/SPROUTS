"""Patch-based undo of the skeleton model (KNOWN_ISSUES #6, S1).

``SkeletonCorrectionModel`` records each action as the box of pixels it changed, before and
after, instead of a copy of the whole mask. These tests replay seeded random action
sequences on the model and on a whole-snapshot history (the previous implementation, kept
here as the reference) and require the same mask, the same Undo / Redo availability and the
same unsaved state after every step, including edits made without ``push_undo()``, writes in
place, a replaced mask and the depth limit. They also pin the depth (100 actions), the
history's memory on a 2048 px skeleton and the box an undo or redo reports to the display.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.editing.history import SnapshotHistory
from app.gui.skeleton_graph_model import SkeletonCorrectionModel


class SnapshotReference:
    """The skeleton model's history before S1: a whole copy of the mask per action."""

    def __init__(self, depth: int) -> None:
        self.history: SnapshotHistory[np.ndarray] = SnapshotHistory(maxlen=depth)
        self.mask: np.ndarray | None = None

    def push(self) -> None:
        if self.mask is not None:
            self.history.push(self.mask.copy())

    def undo(self) -> bool:
        if self.mask is None or not self.history.can_undo:
            return False
        self.mask = self.history.undo(self.mask.copy())
        return True

    def redo(self) -> bool:
        if self.mask is None or not self.history.can_redo:
            return False
        self.mask = self.history.redo(self.mask.copy())
        return True


def _walk(rng, w: int, h: int, n: int, step: int) -> list[tuple[int, int]]:
    x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
    points = [(x, y)]
    for _ in range(n - 1):
        x = int(np.clip(x + rng.integers(-step, step + 1), 0, w - 1))
        y = int(np.clip(y + rng.integers(-step, step + 1), 0, h - 1))
        points.append((x, y))
    return points


def _edit(model: SkeletonCorrectionModel, rng) -> None:
    """One edit method call, as the tools make them."""
    h, w = model.mask.shape
    kind = int(rng.integers(0, 6))
    if kind == 0:
        model.draw_polyline(_walk(rng, w, h, int(rng.integers(2, 6)), 30), thickness=3)
    elif kind == 1:
        pts = _walk(rng, w, h, 2, 20)
        model.erase_segment(pts[0], pts[1], int(rng.integers(1, 8)))
    elif kind == 2:
        model.erase_polyline(_walk(rng, w, h, int(rng.integers(2, 5)), 25), 9)
    elif kind == 3:
        model.erase_circle(_walk(rng, w, h, 1, 0)[0], int(rng.integers(1, 10)))
    elif kind == 4:
        model.skeletonize()
    else:
        model.clear() if rng.random() < 0.2 else model.skeletonize()


def _write_in_place(model: SkeletonCorrectionModel, rng) -> None:
    """A raw write into the mask array (the legacy in-place protocol)."""
    h, w = model.mask.shape
    x, y = int(rng.integers(0, w - 4)), int(rng.integers(0, h - 4))
    model.mask[y : y + int(rng.integers(1, 5)), x : x + int(rng.integers(1, 5))] = 255


def _replay(seed: int, size: tuple[int, int], depth: int, steps: int) -> None:
    rng = np.random.default_rng(seed)
    model = SkeletonCorrectionModel(max_stack_size=depth)
    ref = SnapshotReference(depth)
    model.set_empty(size)
    ref.mask = model.mask.copy()
    for step in range(steps):
        roll = rng.random()
        stepped = False  # an undo or redo: the reference computes the state itself
        if roll < 0.40:  # an action: push, then 1-3 edits (sometimes a write in place)
            model.push_undo()
            ref.push()
            if rng.random() < 0.2:
                _write_in_place(model, rng)
            for _ in range(int(rng.integers(1, 4))):
                _edit(model, rng)
        elif roll < 0.62:
            assert model.undo() is ref.undo(), step
            stepped = True
        elif roll < 0.80:
            assert model.redo() is ref.redo(), step
            stepped = True
        elif roll < 0.86:  # an edit without push_undo(): not undoable by itself
            _edit(model, rng)
        elif roll < 0.90:
            _write_in_place(model, rng)
        elif roll < 0.93:
            model.drop_redo()
            ref.history.drop_redo()
        elif roll < 0.96:
            model.mark_saved()
        elif roll < 0.98:  # the array replaced, same shape
            model.mask = np.where(rng.random(model.mask.shape) < 0.01, 255, 0).astype(np.uint8)
        else:
            model.clear_history()
            ref.history.clear()
        if not stepped:  # every edit is deterministic: the reference takes its result
            ref.mask = model.mask.copy()
        assert np.array_equal(model.mask, ref.mask), (seed, step)
        assert (model.can_undo, model.can_redo) == (
            ref.history.can_undo,
            ref.history.can_redo,
        ), (seed, step)


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_undo_and_redo_match_a_whole_snapshot_history(seed):
    _replay(seed, (120, 90), depth=6, steps=400)


def test_undo_and_redo_match_a_whole_snapshot_history_at_full_depth():
    _replay(99, (160, 120), depth=100, steps=600)


def test_the_history_keeps_100_actions_by_default():
    model = SkeletonCorrectionModel()
    model.set_empty((200, 150))
    states = [model.mask.copy()]
    for k in range(110):
        model.push_undo()
        model.draw_polyline([(k, 10), (k, 140)], thickness=1)
        states.append(model.mask.copy())

    assert model.max_stack_size == 100 and len(model._undo_stack) == 100
    for k in range(100):
        assert model.undo() is True
        assert np.array_equal(model.mask, states[109 - k]), k
    assert model.undo() is False
    assert np.array_equal(model.mask, states[10])  # the oldest kept state
    for _ in range(100):
        assert model.redo() is True
    assert np.array_equal(model.mask, states[-1])


def test_the_default_depth_follows_the_module_constant(monkeypatch):
    monkeypatch.setattr(sgm, "UNDO_DEPTH", 7)
    assert SkeletonCorrectionModel().max_stack_size == 7
    assert SkeletonCorrectionModel(max_stack_size=3).max_stack_size == 3


def _dense(size: tuple[int, int], seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, h = size
    canvas = np.zeros((h, w), np.uint8)
    for _ in range(400):
        pts = np.array(_walk(rng, w, h, 8, 40), np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [pts], False, 255, 3)
    return canvas


def test_a_hundred_small_edits_at_2048_px_keep_the_history_small():
    model = SkeletonCorrectionModel()
    model.load_from_raster(_dense((2048, 1536)), target_size=(2048, 1536))
    frame = model.mask.nbytes
    for k in range(100):
        model.push_undo()
        x, y = 100 + (k * 173) % 1800, 100 + (k * 97) % 1300
        model.draw_polyline([(x, y), (x + 80, y + 30), (x + 120, y - 20)], thickness=3)
        model.skeletonize()
    model.push_undo()  # records the last action

    patches = model.history_nbytes - frame  # the base copy is one frame
    assert model.history_nbytes >= frame
    assert patches < 1024 * 1024, patches  # 25 whole snapshots were 75 MiB
    assert len(model._undo_stack) == 100


def test_the_byte_budget_drops_the_oldest_actions_first(monkeypatch):
    monkeypatch.setattr(sgm, "UNDO_BYTE_BUDGET", 64 * 1024)
    model = SkeletonCorrectionModel()
    model.set_empty((400, 300))
    states = [model.mask.copy()]
    rng = np.random.default_rng(8)
    for _ in range(30):  # noisy full-frame actions: patches that do not compress
        model.push_undo()
        model.mask[:] = np.where(rng.random((300, 400)) < 0.3, 255, 0).astype(np.uint8)
        states.append(model.mask.copy())
    model.push_undo()
    states.append(model.mask.copy())

    kept = len(model._undo_stack)
    assert 1 <= kept < 30
    for k in range(kept):
        assert model.undo() is True
        assert np.array_equal(model.mask, states[-2 - k]), k
    assert model.undo() is False


def test_undo_and_redo_report_the_box_they_changed():
    model = SkeletonCorrectionModel()
    model.set_empty((300, 200))
    model.push_undo()
    model.draw_polyline([(40, 50), (120, 90)], thickness=3)
    model.skeletonize()
    after = model.mask.copy()
    model.take_changed_box()

    assert model.undo() is True
    x, y, w, h = model.take_changed_box()
    ys, xs = np.nonzero(after)
    assert x <= xs.min() and y <= ys.min() and x + w > xs.max() and y + h > ys.max()
    assert w <= 120 - 40 + 8 and h <= 90 - 50 + 8  # the action's box, not the frame
    assert model.redo() is True
    assert model.take_changed_box() == (x, y, w, h)
    assert np.array_equal(model.mask, after)


def test_undo_and_redo_change_the_mask_in_place():
    model = SkeletonCorrectionModel()
    model.set_empty((80, 60))
    live = model.mask
    model.push_undo()
    model.draw_polyline([(5, 5), (70, 50)], thickness=1)
    assert model.undo() and model.mask is live
    assert model.redo() and model.mask is live


def test_a_mask_of_another_shape_undoes_to_the_old_array():
    model = SkeletonCorrectionModel()
    model.set_empty((60, 40))
    model.draw_polyline([(5, 20), (50, 20)], thickness=1)
    first = model.mask.copy()
    model.push_undo()
    model.mask = np.full((20, 30), 255, np.uint8)
    model.push_undo()
    model.erase_circle((10, 10), 3)
    second = model.mask.copy()

    assert model.undo() is True and model.mask.shape == (20, 30)
    assert model.undo() is True and np.array_equal(model.mask, first)
    assert model.take_changed_box() is None  # the whole array changed
    assert model.redo() is True and model.mask.shape == (20, 30)
    assert model.redo() is True and np.array_equal(model.mask, second)


def test_undo_to_the_saved_state_reads_clean_and_redo_after_a_new_edit_is_gone():
    model = SkeletonCorrectionModel()
    model.set_empty((100, 80))
    model.push_undo()
    model.draw_polyline([(10, 10), (90, 70)], thickness=1)
    model.mark_saved()
    saved = model.mask.copy()
    model.push_undo()
    model.erase_circle((50, 40), 6)
    assert model.has_unsaved_changes()

    assert model.undo() is True and not model.has_unsaved_changes()
    assert np.array_equal(model.mask, saved)
    model.push_undo()  # a new action ends the redo branch
    model.draw_polyline([(10, 70), (90, 10)], thickness=1)
    assert model.redo() is False and model.can_undo


def test_clear_history_and_a_new_document_free_the_base_copy():
    model = SkeletonCorrectionModel()
    model.set_empty((64, 48))
    model.push_undo()
    model.draw_polyline([(1, 1), (60, 40)], thickness=1)
    assert model.history_nbytes > 0

    model.clear_history()
    assert model.history_nbytes == 0 and not model.can_undo
    model.push_undo()
    model.set_empty((64, 48))
    assert model.history_nbytes == 0 and not model.can_undo


@pytest.mark.parametrize("seed", [7, 8])
def test_masks_of_other_shapes_and_dtypes_undo_like_whole_snapshots(seed):
    rng = np.random.default_rng(seed)
    model = SkeletonCorrectionModel(max_stack_size=5)
    ref = SnapshotReference(5)
    model.set_empty((40, 30))
    ref.mask = model.mask.copy()
    for step in range(300):
        roll = rng.random()
        stepped = False
        if roll < 0.35:
            model.push_undo()
            ref.push()
            if model.mask.dtype == np.uint8 and min(model.mask.shape) >= 8:
                _edit(model, rng)
        elif roll < 0.55:
            assert model.undo() is ref.undo(), step
            stepped = True
        elif roll < 0.72:
            assert model.redo() is ref.redo(), step
            stepped = True
        elif roll < 0.85:  # another array: another shape, sometimes another dtype
            if rng.random() < 0.5:
                model.push_undo()
                ref.push()
            h, w = int(rng.integers(8, 40)), int(rng.integers(8, 40))
            dtype = np.uint8 if rng.random() < 0.8 else np.int32
            model.mask = np.where(rng.random((h, w)) < 0.05, 255, 0).astype(dtype)
        elif model.mask.dtype == np.uint8 and min(model.mask.shape) >= 8:
            _write_in_place(model, rng)
        if not stepped:
            ref.mask = model.mask.copy()
        assert model.mask.dtype == ref.mask.dtype, (seed, step)
        assert np.array_equal(model.mask, ref.mask), (seed, step)
        assert (model.can_undo, model.can_redo) == (
            ref.history.can_undo,
            ref.history.can_redo,
        ), (seed, step)

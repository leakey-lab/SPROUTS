"""Patch-based undo of the skeleton model (KNOWN_ISSUES #6, S1).

``SkeletonCorrectionModel`` records each action as the box of pixels it changed, before and
after, instead of a copy of the whole mask. These tests replay seeded random action
sequences that follow the edit protocol (every edit inside an action opened by
``push_undo()``: tool edits, writes in place, a replaced mask) on the model and on a
whole-snapshot history (the previous implementation, kept here as the reference) and require
the same mask, the same Undo / Redo availability and the same unsaved state after every
step, up to the depth limit. They also pin the depth (100 actions), the history's memory on
a 2048 px skeleton, the box an undo or redo reports to the display, and what happens outside
the protocol: an array of another shape starts a new history, any array is kept as 2D
C-contiguous uint8, and an edit outside an action is logged, is in no step and never makes
a later thinning trust a state it did not thin.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.editing.history import SnapshotHistory
from app.editing.thinning import _skeletonize_uint8
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
    """A raw write into the mask array (the legacy in-place protocol: right after a push)."""
    h, w = model.mask.shape
    x, y = int(rng.integers(0, w - 4)), int(rng.integers(0, h - 4))
    model.mask[y : y + int(rng.integers(1, 5)), x : x + int(rng.integers(1, 5))] = 255


def _replay(seed: int, size: tuple[int, int], depth: int, steps: int) -> None:
    rng = np.random.default_rng(seed)
    model = SkeletonCorrectionModel(max_stack_size=depth)
    ref = SnapshotReference(depth)
    model.set_empty(size)
    ref.mask = model.mask.copy()
    saved = model.mask.copy()
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
        elif roll < 0.86:  # an action of one edit (the release of an eraser drag thins)
            model.push_undo()
            ref.push()
            _edit(model, rng)
        elif roll < 0.90:  # an action that only writes in place
            model.push_undo()
            ref.push()
            _write_in_place(model, rng)
        elif roll < 0.93:
            model.drop_redo()
            ref.history.drop_redo()
        elif roll < 0.96:
            model.mark_saved()
            saved = model.mask.copy()
        elif roll < 0.98:  # the array replaced, same shape, as one action (Load prediction)
            model.push_undo()
            ref.push()
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
        assert model.has_unsaved_changes() == (not np.array_equal(saved, model.mask)), step


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
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


def test_a_mask_of_another_shape_starts_a_new_history():
    model = SkeletonCorrectionModel()
    model.set_empty((60, 40))
    model.push_undo()
    model.draw_polyline([(5, 20), (50, 20)], thickness=1)
    model.push_undo()
    full = np.full((20, 30), 255, np.uint8)
    model.mask = full.copy()

    assert not model.can_undo and not model.can_redo and model.history_nbytes == 0
    assert model.take_changed_box() is None  # the whole array changed
    model.push_undo()
    model.erase_circle((10, 10), 3)
    assert model.undo() is True and np.array_equal(model.mask, full)
    assert model.undo() is False


def test_any_mask_is_kept_as_a_2d_contiguous_uint8_array():
    model = SkeletonCorrectionModel()
    model.set_empty((30, 20))
    plain = np.zeros((20, 30), np.uint8)
    model.mask = plain
    assert model.mask is plain  # what the app hands over is kept, not copied
    strided = np.zeros((20, 60), np.uint8)[:, ::2]
    for other in (plain.astype(np.int32) + 255, plain.astype(bool), strided, plain.T.copy().T):
        model.mask = other
        assert model.mask.dtype == np.uint8 and model.mask.flags.c_contiguous
        assert np.array_equal(model.mask, other.astype(np.uint8))
    with pytest.raises(ValueError):
        model.mask = np.zeros((20, 30, 3), np.uint8)
    with pytest.raises(ValueError):
        model.load_thinned(np.zeros((2, 20, 30), np.uint8))


def test_an_edit_outside_an_action_is_logged_and_in_no_step(caplog):
    model = SkeletonCorrectionModel()
    model.set_empty((80, 60))
    model.push_undo()
    model.draw_polyline([(5, 5), (70, 5)], thickness=1)
    drawn = model.mask.copy()
    assert model.undo() and model.redo()  # no action is open now
    model.mask[40:44, 30:34] = 255  # in place, no push_undo(): outside the protocol

    with caplog.at_level(logging.WARNING, logger=sgm.__name__):
        assert model.undo() is True
    assert "outside an undo action" in caplog.text
    expected = np.zeros_like(drawn)
    expected[40:44, 30:34] = 255  # the line is undone, the block stays: it is in no step
    assert np.array_equal(model.mask, expected)
    assert model.redo() is True
    assert np.array_equal(model.mask, np.maximum(drawn, expected))


def test_after_an_edit_outside_an_action_no_state_is_trusted_as_thin(monkeypatch):
    # A thin skeleton, an action, then a thick block written outside any action: undoing the
    # action gives a state no thinning made (the block is in no step). The next action's
    # thinning must take the whole frame, not a window around its own edit.
    model = SkeletonCorrectionModel()
    thin = np.zeros((120, 160), np.uint8)
    thin[20, 10:150] = 255
    model.load_from_raster(thin, target_size=(160, 120))
    model.push_undo()
    model.draw_polyline([(10, 100), (60, 100)], thickness=3)
    model.skeletonize()
    assert model.undo() and model.redo()  # no action is open now
    model.mask[60:80, 100:140] = 255  # outside any action
    model.undo()  # the line goes, the block stays
    model.push_undo()
    model.draw_polyline([(10, 40), (30, 40)], thickness=3)
    edited = model.mask.copy()
    shapes = []
    real = sgm._skeletonize_uint8
    monkeypatch.setattr(sgm, "_skeletonize_uint8", lambda m: shapes.append(m.shape) or real(m))

    model.skeletonize()

    assert shapes == [(120, 160)]  # the whole frame
    assert np.array_equal(model.mask, _skeletonize_uint8(edited))


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


def test_the_saved_skeleton_is_one_buffer_a_save_copies_into():
    model = SkeletonCorrectionModel()
    model.set_empty((64, 48))
    held = model.saved_mask
    assert held is not None and held is not model.mask
    model.push_undo()
    model.draw_polyline([(1, 1), (60, 40)], thickness=1)
    assert model.has_unsaved_changes() and not model.saved_mask.any()

    model.mark_saved()

    assert model.saved_mask is held and np.array_equal(held, model.mask)  # no new frame
    assert held is not model.mask and not model.has_unsaved_changes()
    model.set_empty((32, 24))  # another size: a new buffer
    assert model.saved_mask.shape == (24, 32) and not model.saved_mask.any()

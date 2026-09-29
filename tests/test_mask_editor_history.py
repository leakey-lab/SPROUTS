"""PE-06: patch-based undo/redo for the raster editors (Qt-free core)."""

import numpy as np
import pytest

from app.gui.patch_history import COMPRESS_MIN_BYTES, Patch, PatchHistory


def test_patch_round_trips_small_and_compressed_regions():
    rng = np.random.default_rng(0)
    small = rng.integers(0, 3, (8, 5), dtype=np.uint8)
    p = Patch.capture(3, 4, small, small + 1)
    assert p.rect == (3, 4, 5, 8)
    assert np.array_equal(p.before(), small) and np.array_equal(p.after(), small + 1)

    big = np.zeros((400, 400), np.uint8)
    big[100:120, 50:300] = 1
    q = Patch.capture(0, 0, big, np.zeros_like(big))
    assert big.nbytes >= COMPRESS_MIN_BYTES
    assert q.nbytes < big.nbytes // 20  # stored zlib-compressed
    assert np.array_equal(q.before(), big) and not q.after().any()


def test_commit_records_only_the_changed_box():
    h = PatchHistory()
    h.reset(np.zeros((100, 200), np.uint8))
    after = np.zeros((50, 50), np.uint8)
    after[10:12, 20:25] = 1
    patch = h.commit((30, 40, 50, 50), after)
    assert patch.rect == (50, 50, 5, 2)
    assert h.plane[50:52, 50:55].all() and h.plane.sum() == 10
    assert len(h.undo_stack) == 1 and len(h.redo_stack) == 0


def test_noop_commit_is_still_one_undo_entry():
    h = PatchHistory()
    h.reset(np.zeros((10, 10), np.uint8))
    patch = h.commit((0, 0, 10, 10), np.zeros((10, 10), np.uint8))
    assert patch.is_empty and len(h.undo_stack) == 1
    assert h.undo() is patch and len(h.redo_stack) == 1


def test_commit_clips_to_the_plane():
    h = PatchHistory()
    h.reset(np.zeros((10, 10), np.uint8))
    with pytest.raises(ValueError):
        h.commit((5, 5, 10, 10), np.ones((10, 10), np.uint8))
    assert h.clip((-3, 8, 10, 10)) == (0, 8, 7, 2)


def test_undo_redo_matches_full_snapshot_oracle():
    """Random paint / erase / clear edits; every undo and redo equals the recorded state."""
    rng = np.random.default_rng(42)
    shape = (200, 300)
    h = PatchHistory(maxlen=25)
    h.reset(np.zeros(shape, np.uint8))
    oracle = [h.plane.copy()]
    for _ in range(60):
        kind = rng.integers(0, 3)
        if kind == 2:
            rect = (0, 0, shape[1], shape[0])
            after = np.zeros(shape, np.uint8)
        else:
            x, y = int(rng.integers(0, 250)), int(rng.integers(0, 150))
            w, hh = int(rng.integers(1, 50)), int(rng.integers(1, 50))
            rect = h.clip((x, y, w, hh))
            x, y, w, hh = rect
            after = h.plane[y : y + hh, x : x + w].copy()
            after[rng.random((hh, w)) < 0.5] = 0 if kind == 1 else int(rng.integers(1, 4))
        h.commit(rect, after)
        oracle.append(h.plane.copy())
    oracle = oracle[-26:]  # the history keeps the last 25 edits
    assert len(h.undo_stack) == 25
    for step in range(25):
        assert h.undo() is not None
        assert np.array_equal(h.plane, oracle[-2 - step])
    assert h.undo() is None
    for step in range(25):
        assert h.redo() is not None
        assert np.array_equal(h.plane, oracle[step + 1])
    assert h.redo() is None


def test_small_strokes_on_a_large_canvas_cost_kilobytes_not_megabytes():
    h = PatchHistory()
    h.reset(np.zeros((1536, 2048), np.uint8))
    for i in range(25):
        y = 40 + i * 50
        after = h.plane[y : y + 12, 100:400].copy()
        after[4:8, :] = 1
        h.commit((100, y, 300, 12), after)
    assert h.nbytes < 1024 * 1024
    assert len(h.undo_stack) == 25


def test_byte_budget_evicts_oldest_entries():
    h = PatchHistory(maxlen=100, byte_budget=64 * 1024)
    rng = np.random.default_rng(1)
    h.reset(np.zeros((300, 300), np.uint8))
    for _ in range(10):
        noisy = rng.integers(0, 255, (100, 100), dtype=np.uint8)  # incompressible
        h.commit((0, 0, 100, 100), noisy)
    assert h.nbytes <= 64 * 1024 or len(h.undo_stack) == 1
    assert 1 <= len(h.undo_stack) < 10


def test_on_change_fires_for_history_changes():
    calls = []
    h = PatchHistory(on_change=lambda: calls.append(1))
    h.reset(np.zeros((4, 4), np.uint8))
    h.commit((0, 0, 2, 2), np.ones((2, 2), np.uint8))
    h.undo()
    h.redo()
    assert len(calls) == 3
    assert h.can_undo and not h.can_redo

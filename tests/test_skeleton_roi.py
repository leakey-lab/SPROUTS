"""ROI skeletonize: thinning only around an edit gives exactly the full-frame result.

``SkeletonCorrectionModel.skeletonize()`` thins a window around the pixels that changed
since a known skeletonize fixed point (the snapshot of the current action) instead of
the whole frame (plan step P5a-3, spec D24). These tests replay seeded random edits the
way the skeleton tools make them (Polyline commit, eraser drag, Connect, branch delete,
undo / redo) on three skeletons and compare every result with scikit-image run on the
whole edited frame: there must be 0 mismatches, and the window path must be the rule.

Always on: about 50 edits per case. With ``SPROUTS_PERF_TESTS=1``: 300 edits per case
and a 2,800-edit run over the three cases.
"""

from __future__ import annotations

import os
from itertools import pairwise

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.gui.skeleton_graph_model import SkeletonCorrectionModel

PERF = os.environ.get("SPROUTS_PERF_TESTS") == "1"
_full_frame = sgm._skeletonize_uint8  # the reference: scikit-image on the whole frame


# --------------------------------------------------------------------------------------
# Cases (deterministic, built in memory)
# --------------------------------------------------------------------------------------


def _synthetic_640() -> np.ndarray:
    """The committed golden case's generated skeleton (341x256, grey 1-3 px lines) opened
    at 640x480, as the editor opens it."""
    raster = np.zeros((256, 341), np.uint8)
    pts = np.array([[20, 30], [120, 90], [200, 80], [320, 200]], np.int32)
    cv2.polylines(raster, [pts.reshape(-1, 1, 2)], False, 191, 2)
    cv2.line(raster, (60, 240), (300, 20), 191, 1)
    cv2.line(raster, (150, 150), (150, 250), 191, 3)
    model = SkeletonCorrectionModel()
    model.load_from_raster(raster, target_size=(640, 480))
    return model.mask.copy()


def _dense_640() -> np.ndarray:
    """A dense 640x480 skeleton: 260 seeded random walks, 3 px wide, thinned."""
    rng = np.random.default_rng(20260929)
    canvas = np.zeros((480, 640), np.uint8)
    for _ in range(260):
        x, y = rng.uniform(0, 640), rng.uniform(0, 480)
        angle = rng.uniform(0, 2 * np.pi)
        pts = []
        for _ in range(int(rng.integers(4, 16))):
            angle += rng.normal(0, 0.6)
            x = float(np.clip(x + 14 * np.cos(angle), 0, 639))
            y = float(np.clip(y + 14 * np.sin(angle), 0, 479))
            pts.append((int(x), int(y)))
        cv2.polylines(canvas, [np.array(pts, np.int32).reshape(-1, 1, 2)], False, 255, 3)
    return _full_frame(canvas)


def _upscale_2048() -> np.ndarray:
    """The dense skeleton opened on a 2048x1536 image (nearest upscale, then thinned)."""
    model = SkeletonCorrectionModel()
    model.load_from_raster(_dense_640(), target_size=(2048, 1536))
    return model.mask.copy()


CASES = {"synthetic 640": _synthetic_640, "dense 640": _dense_640, "upscale 2048": _upscale_2048}
SEEDS = {"synthetic 640": 11, "dense 640": 12, "upscale 2048": 13}


@pytest.fixture(scope="module")
def bases() -> dict[str, np.ndarray]:
    return {name: build() for name, build in CASES.items()}


def _opened(base: np.ndarray) -> SkeletonCorrectionModel:
    """A model that opened ``base`` like a skeleton file of the image's size."""
    model = SkeletonCorrectionModel()
    model.load_from_raster(base, target_size=(base.shape[1], base.shape[0]))
    assert np.array_equal(model.mask, base)
    return model


@pytest.fixture
def thinned(monkeypatch) -> list[tuple[int, int]]:
    """Shapes the model hands to scikit-image (a window, or the whole frame)."""
    shapes: list[tuple[int, int]] = []

    def spy(mask: np.ndarray) -> np.ndarray:
        shapes.append(mask.shape)
        return _full_frame(mask)

    monkeypatch.setattr(sgm, "_skeletonize_uint8", spy)
    return shapes


def test_the_cases_are_real_skeletons(bases):
    assert int((bases["synthetic 640"] > 0).sum()) > 1000
    assert int((bases["dense 640"] > 0).sum()) >= 15_000
    assert bases["upscale 2048"].shape == (1536, 2048)
    for base in bases.values():
        assert np.array_equal(_full_frame(base), base)  # thinning a skeleton changes nothing


# --------------------------------------------------------------------------------------
# Editor-like random edits (the skeleton tools' model calls)
# --------------------------------------------------------------------------------------


def _walk(rng, w: int, h: int, n: int, step: int) -> list[tuple[int, int]]:
    x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
    pts = [(x, y)]
    for _ in range(n - 1):
        x = int(np.clip(x + rng.integers(-step, step + 1), 0, w - 1))
        y = int(np.clip(y + rng.integers(-step, step + 1), 0, h - 1))
        pts.append((x, y))
    return pts


def _polyline(model: SkeletonCorrectionModel, rng) -> None:
    """PolylineTool.commit: a new line, or a picked branch redrawn."""
    h, w = model.mask.shape
    model.push_undo()
    if rng.random() < 0.3:
        model.erase_polyline(_walk(rng, w, h, int(rng.integers(2, 6)), 40), 9)
    model.draw_polyline(_walk(rng, w, h, int(rng.integers(2, 9)), 60), thickness=3)


def _eraser(model: SkeletonCorrectionModel, rng) -> None:
    """EraserTool: press, then a segment per mouse move (the release thins)."""
    h, w = model.mask.shape
    radius = int(rng.integers(1, 21))
    pts = _walk(rng, w, h, int(rng.integers(1, 13)), 25)
    model.push_undo()
    model.erase_segment(pts[0], pts[0], radius)
    for p0, p1 in pairwise(pts):
        model.erase_segment(p0, p1, radius)


def _connect(model: SkeletonCorrectionModel, rng) -> None:
    """ConnectTool.release: a 3 px line from an endpoint to a nearby point."""
    h, w = model.mask.shape
    ends = model.endpoints()
    if ends:
        x, y = ends[int(rng.integers(0, len(ends)))]
    else:
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
    to = (
        int(np.clip(x + rng.integers(-40, 41), 0, w - 1)),
        int(np.clip(y + rng.integers(-40, 41), 0, h - 1)),
    )
    model.push_undo()
    model.draw_polyline([(x, y), to], thickness=3)


def _delete_branch(model: SkeletonCorrectionModel, rng) -> None:
    """PolylineTool.delete_branch: erase a 9 px band along a branch."""
    h, w = model.mask.shape
    model.push_undo()
    model.erase_polyline(_walk(rng, w, h, int(rng.integers(2, 6)), 30), 9)


EDITS = (_polyline, _eraser, _connect, _delete_branch)


def _replay(model: SkeletonCorrectionModel, seed: int, edits: int, thinned: list) -> dict:
    """Run ``edits`` random actions; compare each thinning with the full-frame one."""
    rng = np.random.default_rng(seed)
    frame = model.mask.shape
    stats = {"edits": 0, "mismatches": [], "window": 0, "nothing": 0, "frame": 0}
    step = 0
    while stats["edits"] < edits:
        step += 1
        roll = rng.random()
        if roll < 0.06 and model.can_undo:
            model.undo()
            continue
        if roll < 0.09 and model.can_redo:
            model.redo()
            continue
        EDITS[step % len(EDITS)](model, rng)
        edited = model.mask.copy()
        thinned.clear()
        model.skeletonize()
        if not np.array_equal(model.mask, _full_frame(edited)):
            stats["mismatches"].append(step)
        if not thinned:
            stats["nothing"] += 1  # the edit changed no pixel: nothing to thin
        elif frame in thinned:
            stats["frame"] += 1
        else:
            stats["window"] += 1
        stats["edits"] += 1
    return stats


def _assert_exact_and_local(stats: dict) -> None:
    assert stats["mismatches"] == [], stats
    # The window is the rule: the whole frame is thinned for at most 1 edit in 10.
    assert stats["frame"] <= stats["edits"] // 10, stats


@pytest.mark.parametrize("case", sorted(CASES))
def test_window_skeletonize_equals_the_full_frame_after_random_edits(case, bases, thinned):
    stats = _replay(_opened(bases[case]), SEEDS[case], 50, thinned)
    _assert_exact_and_local(stats)


@pytest.mark.skipif(not PERF, reason="long run: set SPROUTS_PERF_TESTS=1")
@pytest.mark.parametrize("case", sorted(CASES))
def test_window_skeletonize_300_random_edits_per_case(case, bases, thinned):
    stats = _replay(_opened(bases[case]), 100 + SEEDS[case], 300, thinned)
    _assert_exact_and_local(stats)


@pytest.mark.skipif(not PERF, reason="long run: set SPROUTS_PERF_TESTS=1")
def test_window_skeletonize_2800_random_edits_over_the_three_cases(bases, thinned):
    runs = {"synthetic 640": 1100, "dense 640": 1100, "upscale 2048": 600}
    for case, edits in runs.items():
        stats = _replay(_opened(bases[case]), 1000 + SEEDS[case], edits, thinned)
        _assert_exact_and_local(stats)


# --------------------------------------------------------------------------------------
# When the window is (not) used
# --------------------------------------------------------------------------------------


def test_a_base_that_is_not_a_fixed_point_takes_the_full_frame_path(thinned):
    model = SkeletonCorrectionModel()
    model.set_empty((120, 90))
    block = np.zeros((90, 120), np.uint8)
    block[20:60, 20:100] = 255  # a solid block: thinning changes it everywhere
    model.mask = block
    model.push_undo()
    model.draw_polyline([(5, 80), (110, 85)], thickness=3)
    edited = model.mask.copy()
    thinned.clear()
    model.skeletonize()
    assert thinned == [(90, 120)]
    assert np.array_equal(model.mask, _full_frame(edited))


def test_without_a_snapshot_the_whole_frame_is_thinned(bases, thinned):
    model = _opened(bases["synthetic 640"])
    model.draw_polyline([(100, 400), (300, 380)], thickness=3)  # no push_undo()
    edited = model.mask.copy()
    thinned.clear()
    model.skeletonize()
    assert thinned == [(480, 640)]
    assert np.array_equal(model.mask, _full_frame(edited))


def test_a_small_edit_is_thinned_in_a_small_window(bases, thinned):
    model = _opened(bases["upscale 2048"])
    model.push_undo()
    model.draw_polyline([(1000, 700), (1040, 720)], thickness=3)
    edited = model.mask.copy()
    thinned.clear()
    model.skeletonize()
    assert len(thinned) == 1
    h, w = thinned[0]
    assert h <= 21 + 5 + 32 and w <= 41 + 5 + 32  # the edit box + 16 px on each side
    assert np.array_equal(model.mask, _full_frame(edited))


def test_an_edit_that_changes_no_pixel_thins_nothing(bases, thinned):
    model = _opened(bases["synthetic 640"])
    before = model.mask.copy()
    model.push_undo()
    model.erase_circle((630, 10), 4)  # empty background
    thinned.clear()
    model.skeletonize()
    assert thinned == []
    assert np.array_equal(model.mask, before)


def test_an_in_place_write_right_after_push_undo_stays_exact(bases, thinned):
    # The legacy protocol: push_undo(), then write model.mask in place.
    model = _opened(bases["dense 640"])
    model.push_undo()
    model.mask[200:230, 300:360] = 255
    model.mask[0:12, 0:40] = 1  # any non-zero value is foreground
    edited = model.mask.copy()
    model.skeletonize()
    assert np.array_equal(model.mask, _full_frame(edited))
    assert model.mask.dtype == np.uint8 and set(np.unique(model.mask).tolist()) <= {0, 255}


def test_an_in_place_write_pushed_again_before_thinning_stays_exact(bases, thinned):
    model = _opened(bases["dense 640"])
    model.push_undo()
    model.mask[100:160, 100:220] = 255  # in place, right after push_undo(): a thick block
    model.push_undo()  # the next action starts before anything thinned the block
    model.draw_polyline([(300, 300), (340, 320)], thickness=3)
    edited = model.mask.copy()
    model.skeletonize()
    assert np.array_equal(model.mask, _full_frame(edited))


def test_edits_at_the_image_border_stay_exact(bases, thinned):
    model = _opened(bases["dense 640"])
    edits = [
        lambda m: m.erase_circle((0, 0), 15),
        lambda m: m.draw_polyline([(0, 479), (639, 470)], thickness=3),
        lambda m: m.erase_segment((639, 0), (639, 479), 6),
        lambda m: m.draw_polyline([(630, 5), (639, 0), (600, 0)], thickness=9),
    ]
    for edit in edits:
        model.push_undo()
        edit(model)
        edited = model.mask.copy()
        model.skeletonize()
        assert np.array_equal(model.mask, _full_frame(edited))


def test_undo_redo_then_edit_stays_exact_on_the_window_path(bases, thinned):
    model = _opened(bases["dense 640"])
    for x in (100, 300, 500):
        model.push_undo()
        model.draw_polyline([(x, 100), (x + 40, 300)], thickness=3)
        model.skeletonize()
    model.undo()
    model.undo()
    model.redo()
    model.push_undo()
    model.draw_polyline([(50, 400), (400, 420)], thickness=3)
    edited = model.mask.copy()
    thinned.clear()
    model.skeletonize()
    assert thinned and (480, 640) not in thinned
    assert np.array_equal(model.mask, _full_frame(edited))


def test_the_changed_box_covers_every_pixel_the_window_thinning_changed(bases):
    model = _opened(bases["synthetic 640"])
    model.push_undo()
    model.draw_polyline([(100, 300), (180, 330), (260, 310)], thickness=9)
    model.take_changed_box()  # the drawn stroke, reported to the display already
    before = model.mask.copy()
    model.skeletonize()
    x, y, w, h = model.take_changed_box()
    ys, xs = np.nonzero(before != model.mask)
    assert xs.size > 0
    assert x <= xs.min() and y <= ys.min() and x + w > xs.max() and y + h > ys.max()
    assert w <= 260 - 100 + 2 * 12 and h <= 330 - 300 + 2 * 12  # local to the stroke

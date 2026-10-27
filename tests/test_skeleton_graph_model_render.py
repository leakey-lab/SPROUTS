"""``SkeletonCorrectionModel.render_to_size``: the raster a skeleton Save writes (S03-05).

At the mask's own size the nearest resize is a copy, so a mask the model knows to be a fixed
point of the thinning (``_fixed_point``, which every tool action ends in) is returned as a
copy instead of being thinned again. These tests replay the skeleton tools' model calls
(``tests/test_skeleton_roi.py``) with eraser drags before their release, undo, redo, Clear,
Load prediction, Load file, saves and the read-back hand-over, and after every step compare
the render with the full path (nearest resize, then full-frame thinning): the bytes must be
identical, and the flag must only ever be set on a 0/255 fixed point.
"""

from __future__ import annotations

from itertools import pairwise

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.editing.thinning import thin_raster
from app.gui.skeleton_graph_model import SkeletonCorrectionModel
from tests.test_skeleton_roi import CASES, EDITS, SEEDS, _opened, _walk

_full_frame = sgm._skeletonize_uint8  # scikit-image on the whole frame


def _full_render(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """``render_to_size`` as it was before the fixed-point copy: resize, then thin."""
    return _full_frame(cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST))


def _check(model: SkeletonCorrectionModel, where) -> np.ndarray:
    """The Save render equals the full path; a set flag means a 0/255 fixed point."""
    mask = model.mask
    size = (mask.shape[1], mask.shape[0])
    got = model.render_to_size(size)
    assert got.dtype == np.uint8 and np.array_equal(got, _full_render(mask, size)), where
    assert not np.shares_memory(got, mask), where  # a new array, as before
    if model._fixed_point:
        assert not np.count_nonzero((mask != 0) & (mask != 255)), where
        assert np.array_equal(_full_frame(mask), mask), where
    return got


def _prediction_raster(base: np.ndarray, rng) -> np.ndarray:
    """A grey generated file of the case at the generated size (341 x 256)."""
    raster = cv2.resize(base, (341, 256), interpolation=cv2.INTER_AREA)
    for _ in range(3):
        a, b = _walk(rng, 341, 256, 2, 120)
        cv2.line(raster, a, b, 191, 2)
    return raster


def _replay(model: SkeletonCorrectionModel, seed: int, steps: int) -> dict[str, int]:
    rng = np.random.default_rng(seed)
    h, w = model.mask.shape
    raster = _prediction_raster(model.mask, rng)
    done: dict[str, int] = {}
    for step in range(steps):
        roll = rng.random()
        if roll < 0.08 and model.can_undo:
            name = "undo"
            model.undo()
        elif roll < 0.14 and model.can_redo:
            name = "redo"
            model.redo()
        elif roll < 0.18:  # the layer's Clear: one undoable action
            name = "clear"
            model.push_undo()
            model.clear()
        elif roll < 0.22:  # Load prediction: the plane through the setter, undoable
            name = "load prediction"
            model.push_undo()
            model.mask = thin_raster(raster, (w, h))
        elif roll < 0.25:  # Load file: an unsaved document from another raster
            name = "load file"
            model.load_from_raster(raster, target_size=(w, h), saved=False)
        elif roll < 0.33:  # a Save: the render is written, the model marked saved
            name = "save"
            _check(model, (seed, step, "before save"))
            model.mark_saved()
        elif roll < 0.38 and not model.has_unsaved_changes():  # the read-back hand-over
            name = "hand-over"
            tips = model.endpoint_array()
            model.load_thinned(model.mask, tips[:, 1] * w + tips[:, 0])
        elif roll < 0.50:  # an eraser drag, rendered before its release thins it
            name = "eraser drag"
            radius = int(rng.integers(1, 15))
            points = _walk(rng, w, h, int(rng.integers(2, 8)), 25)
            model.push_undo()
            model.erase_segment(points[0], points[0], radius)
            for p0, p1 in pairwise(points):
                model.erase_segment(p0, p1, radius)
            _check(model, (seed, step, "mid-drag"))
            model.skeletonize()
        else:  # Polyline, Eraser, Connect, branch delete: each ends thinned
            name = "tool"
            EDITS[step % len(EDITS)](model, rng)
            model.skeletonize()
        _check(model, (seed, step, name))
        done[name] = done.get(name, 0) + 1
    return done


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_save_render_is_the_full_path_after_every_editor_step(case):
    steps = 40 if case == "upscale 2048" else 120
    done = _replay(_opened(CASES[case]()), seed=500 + SEEDS[case], steps=steps)
    assert done["tool"] >= steps // 4 and done.get("save", 0) >= 1, done


def test_a_known_fixed_point_is_copied_and_anything_else_is_thinned(monkeypatch):
    model = _opened(CASES["synthetic 640"]())
    calls = []

    def spy(mask: np.ndarray) -> np.ndarray:
        calls.append(mask.shape)
        return _full_frame(mask)

    monkeypatch.setattr(sgm, "_skeletonize_uint8", spy)
    _check(model, "opened")
    assert calls == []  # thinned when opened: the Save copies it
    model.push_undo()
    model.draw_polyline([(10, 10), (200, 120)], thickness=3)  # not thinned yet
    calls.clear()
    rendered = _check(model, "drawn")
    assert calls == [(480, 640)]
    assert not np.array_equal(rendered, model.mask)  # the 3 px line is saved thinned
    model.skeletonize()
    calls.clear()
    model.render_to_size((640, 480))
    assert calls == []


def test_another_size_is_resized_then_thinned():
    model = SkeletonCorrectionModel()
    model.set_empty((60, 40))
    model.push_undo()
    model.draw_polyline([(5, 20), (50, 22), (55, 35)], thickness=3)
    model.skeletonize()
    for size in ((30, 20), (120, 80), (61, 40)):
        assert np.array_equal(model.render_to_size(size), _full_render(model.mask, size))
    empty = SkeletonCorrectionModel().render_to_size((30, 20))
    assert empty.shape == (20, 30) and empty.dtype == np.uint8 and not empty.any()

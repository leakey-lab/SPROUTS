"""SkeletonCorrectionModel editing contract used by the skeleton editors.

Covers dirty tracking (PE-01 / AR-02 / RB-05), the revision counter and cached
topology/endpoints (PE-03), the eraser drag (PE-02), clear/erase_polyline (AR-12),
the undo flags backed by SnapshotHistory (AR-11) and the hooks the Correct workspace
uses (plan contract C4): ``on_action``, ``drop_redo``, ``take_changed_box`` and the
lazy scikit-image import with its ``prewarm_skeletonize`` hook.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import textwrap
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.gui.skeleton_graph_model import (
    SkeletonCorrectionModel,
    _skeletonize_uint8,
    vectorize_skeleton,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _model_with_line(size=(60, 40)) -> SkeletonCorrectionModel:
    model = SkeletonCorrectionModel()
    model.set_empty(size)
    model.draw_polyline([(5, 20), (50, 20)], thickness=1)
    model.mark_saved()
    return model


def _cross() -> np.ndarray:
    cr = np.zeros((11, 11), np.uint8)
    cr[5, 1:10] = 255
    cr[1:10, 5] = 255
    return cr


# --------------------------------------------------------------------------------------
# Unsaved changes
# --------------------------------------------------------------------------------------


def test_a_model_without_a_mask_has_nothing_to_save():
    assert SkeletonCorrectionModel().has_unsaved_changes() is False


def test_freshly_loaded_documents_are_clean():
    model = SkeletonCorrectionModel()
    model.set_empty((30, 20))
    assert model.has_unsaved_changes() is False

    raster = np.zeros((20, 30), np.uint8)
    raster[10, 3:27] = 255
    model.load_from_raster(raster, target_size=(30, 20))
    assert model.has_unsaved_changes() is False


def test_an_edit_that_changes_pixels_is_unsaved_until_marked_saved():
    model = _model_with_line()
    model.push_undo()
    model.erase_circle((20, 20), 3)
    assert model.has_unsaved_changes() is True

    model.mark_saved()
    assert model.has_unsaved_changes() is False

    model.push_undo()
    model.draw_polyline([(5, 5), (5, 30)], thickness=1)
    assert model.has_unsaved_changes() is True


def test_undoing_back_to_the_saved_state_reads_clean():
    model = _model_with_line()
    model.push_undo()
    model.erase_circle((20, 20), 3)
    assert model.undo() is True
    assert model.has_unsaved_changes() is False
    assert model.redo() is True
    assert model.has_unsaved_changes() is True


def test_an_edit_that_changes_nothing_stays_clean():
    model = _model_with_line()
    model.push_undo()
    model.erase_circle((10, 5), 2)  # empty background above the line
    assert model.has_unsaved_changes() is False


def test_loading_a_new_document_discards_the_unsaved_state():
    model = _model_with_line()
    model.push_undo()
    model.clear()
    assert model.has_unsaved_changes() is True
    model.set_empty((60, 40))
    assert model.has_unsaved_changes() is False
    assert model.can_undo is False


# --------------------------------------------------------------------------------------
# Revision
# --------------------------------------------------------------------------------------


MUTATIONS = {
    "set_empty": lambda m: m.set_empty((60, 40)),
    "load_from_raster": lambda m: m.load_from_raster(
        np.full((40, 60), 255, np.uint8) * np.eye(40, 60, dtype=np.uint8), target_size=(60, 40)
    ),
    "push_undo": lambda m: m.push_undo(),
    "draw_polyline": lambda m: m.draw_polyline([(1, 1), (30, 30)], thickness=1),
    "erase_circle": lambda m: m.erase_circle((10, 20), 2),
    "erase_segment": lambda m: m.erase_segment((5, 20), (25, 20), 2),
    "erase_polyline": lambda m: m.erase_polyline([(5, 20), (25, 20)], thickness=3),
    "skeletonize": lambda m: m.skeletonize(),
    "clear": lambda m: m.clear(),
    "assign_mask": lambda m: setattr(m, "mask", np.zeros((40, 60), np.uint8)),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_mutation_moves_the_revision(name):
    model = _model_with_line()
    before = model.revision
    MUTATIONS[name](model)
    assert model.revision != before


def test_undo_and_redo_move_the_revision_only_when_they_act():
    model = _model_with_line()
    rev = model.revision
    assert model.undo() is False and model.redo() is False
    assert model.revision == rev

    model.push_undo()
    model.clear()
    rev = model.revision
    assert model.undo() is True
    assert model.revision != rev
    rev = model.revision
    assert model.redo() is True
    assert model.revision != rev


def test_queries_leave_the_revision_alone():
    model = _model_with_line()
    rev = model.revision
    model.topology()
    model.endpoints()
    model.has_unsaved_changes()
    model.render_to_size((30, 20))
    assert (model.can_undo, model.can_redo) == (False, False)
    assert model.revision == rev


# --------------------------------------------------------------------------------------
# Undo flags and clear
# --------------------------------------------------------------------------------------


def test_can_undo_and_can_redo_follow_the_history():
    model = SkeletonCorrectionModel()
    assert (model.can_undo, model.can_redo) == (False, False)
    model.set_empty((10, 10))
    model.push_undo()
    assert (model.can_undo, model.can_redo) == (True, False)
    model.undo()
    assert (model.can_undo, model.can_redo) == (False, True)
    model.redo()
    assert (model.can_undo, model.can_redo) == (True, False)


def test_clear_erases_everything_and_undoes_in_one_step():
    model = _model_with_line()
    before = model.mask.copy()
    model.push_undo()
    model.clear()
    assert int(model.mask.max()) == 0
    assert model.undo() is True
    assert np.array_equal(model.mask, before)


def test_clear_without_a_mask_is_a_no_op():
    model = SkeletonCorrectionModel()
    model.clear()
    assert model.mask is None


# --------------------------------------------------------------------------------------
# Eraser drag (PE-02)
# --------------------------------------------------------------------------------------


def test_a_fast_eraser_drag_leaves_no_fragments_between_mouse_events():
    model = SkeletonCorrectionModel()
    model.set_empty((640, 480))
    model.mask[240, 100:540] = 255  # a 1px horizontal root
    radius = 3  # the default 6 px eraser diameter
    xs = list(range(120, 521, 25))  # one mouse event every 25 px

    model.erase_segment((xs[0], 240), (xs[0], 240), radius)  # press
    for x0, x1 in pairwise(xs):
        model.erase_segment((x0, 240), (x1, 240), radius)  # moves

    assert int((model.mask[240, 120:521] > 0).sum()) == 0
    assert int((model.mask[240, 100:117] > 0).sum()) == 17  # untouched outside the sweep


def test_a_zero_length_segment_erases_exactly_the_eraser_disc():
    a = _model_with_line()
    b = _model_with_line()
    a.mask[:] = 255
    b.mask[:] = 255
    a.erase_segment((30, 20), (30, 20), 6)
    b.erase_circle((30, 20), 6)
    assert np.array_equal(a.mask, b.mask)


@pytest.mark.parametrize(
    "p0, p1, radius",
    [
        ((10, 10), (50, 30), 4),
        ((40, 5), (12, 33), 1),
        ((5, 30), (55, 30), 9),
        ((30, 5), (31, 35), 2),
    ],
)
def test_the_eraser_footprint_is_the_disc_swept_along_the_segment(p0, p1, radius):
    model = _model_with_line()
    model.mask[:] = 255
    model.erase_segment(p0, p1, radius)
    erased = model.mask == 0

    # Both end discs are fully erased, exactly as erase_circle would.
    for end in (p0, p1):
        disc = np.full_like(model.mask, 255)
        cv2.circle(disc, end, radius, 0, -1)
        assert not np.any((disc == 0) & ~erased)

    # Everything well inside the swept capsule is gone, nothing beyond disc reach is.
    ys, xs = np.nonzero(np.ones_like(model.mask))
    (ax, ay), (bx, by) = p0, p1
    abx, aby = bx - ax, by - ay
    t = np.clip(((xs - ax) * abx + (ys - ay) * aby) / float(abx * abx + aby * aby), 0, 1)
    dist = np.hypot(xs - (ax + t * abx), ys - (ay + t * aby)).reshape(model.mask.shape)
    assert erased[dist <= radius - 0.5].all()
    assert not erased[dist > radius + 1].any()


# --------------------------------------------------------------------------------------
# erase_polyline (the Select -> edit commit and branch delete)
# --------------------------------------------------------------------------------------


def test_erase_polyline_removes_a_band_along_the_path():
    model = SkeletonCorrectionModel()
    model.set_empty((40, 30))
    model.mask[:] = 255
    model.erase_polyline([(5, 15), (20, 15), (20, 25)], thickness=5)
    assert int(model.mask[13:18, 5:21].max()) == 0  # rows 13..17 along the first leg
    assert int(model.mask[15:26, 18:23].max()) == 0  # columns 18..22 along the second leg
    assert int(model.mask[15, 30]) == 255 and int(model.mask[5, 5]) == 255


def test_erase_polyline_needs_two_points():
    model = _model_with_line()
    before = model.mask.copy()
    rev = model.revision
    model.erase_polyline([(20, 20)], thickness=9)
    assert np.array_equal(model.mask, before) and model.revision == rev


# --------------------------------------------------------------------------------------
# Cached topology and endpoints (PE-03)
# --------------------------------------------------------------------------------------


@pytest.fixture
def vectorize_calls(monkeypatch):
    calls = []

    def counting(mask, *, simplify_epsilon=1.5):
        calls.append(simplify_epsilon)
        return vectorize_skeleton(mask, simplify_epsilon=simplify_epsilon)

    monkeypatch.setattr(sgm, "vectorize_skeleton", counting)
    return calls


def test_topology_is_computed_once_per_revision_and_epsilon(vectorize_calls):
    model = _model_with_line()
    first = model.topology()
    assert model.topology() is first
    assert vectorize_calls == [1.5]

    model.topology(simplify_epsilon=0.0)
    assert vectorize_calls == [1.5, 0.0]

    model.push_undo()
    model.erase_circle((20, 20), 2)
    second = model.topology()
    assert second is not first
    assert vectorize_calls == [1.5, 0.0, 1.5]


def test_cached_topology_matches_a_fresh_vectorization_after_every_edit():
    model = SkeletonCorrectionModel()
    model.set_empty((80, 60))
    steps = [
        lambda: model.draw_polyline([(10, 10), (70, 50)], thickness=3),
        model.skeletonize,
        lambda: model.draw_polyline([(10, 50), (70, 10)], thickness=3),
        model.skeletonize,
        lambda: model.erase_segment((35, 25), (45, 35), 3),
        lambda: model.erase_polyline([(10, 10), (30, 23)], thickness=5),
        model.clear,
    ]
    for step in steps:
        model.topology()  # warm the cache before the edit
        model.endpoints()
        model.push_undo()
        step()
        fresh = vectorize_skeleton(model.mask, simplify_epsilon=1.5)
        assert model.topology() == fresh
        assert set(model.endpoints()) == set(fresh.endpoints)
    while model.undo():
        assert model.topology() == vectorize_skeleton(model.mask, simplify_epsilon=1.5)


def test_writing_the_mask_in_place_right_after_push_undo_is_seen(vectorize_calls):
    # The legacy interface pattern: push_undo() then model.mask.fill(0).
    model = _model_with_line()
    assert model.endpoints() and model.topology().endpoints
    model.push_undo()
    model.mask.fill(0)
    assert model.endpoints() == []
    assert model.topology().endpoints == []
    assert model.has_unsaved_changes() is True


def test_endpoints_of_the_cross_are_its_four_arm_tips():
    model = SkeletonCorrectionModel()
    model.mask = _cross()
    assert sorted(model.endpoints()) == [(1, 5), (5, 1), (5, 9), (9, 5)]


def test_endpoints_equal_the_vectorized_endpoints_on_random_skeletons():
    rng = np.random.default_rng(3)
    for _ in range(25):
        model = SkeletonCorrectionModel()
        model.set_empty((160, 120))
        for _ in range(int(rng.integers(1, 8))):
            x, y, ang = rng.uniform(0, 160), rng.uniform(0, 120), rng.uniform(0, 2 * math.pi)
            pts = []
            for _ in range(int(rng.integers(3, 20))):
                ang += rng.normal(0, 0.5)
                x, y = x + 6 * math.cos(ang), y + 6 * math.sin(ang)
                pts.append((int(x), int(y)))
            model.draw_polyline(pts, thickness=3)
        model.skeletonize()
        assert set(model.endpoints()) == set(vectorize_skeleton(model.mask).endpoints)


def test_endpoints_never_run_the_full_vectorization(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("endpoints() must not vectorize the skeleton")

    monkeypatch.setattr(sgm, "vectorize_skeleton", forbidden)
    model = _model_with_line()
    assert sorted(model.endpoints()) == [(5, 20), (50, 20)]


def test_endpoints_are_cached_until_the_next_edit():
    model = _model_with_line()
    first = model.endpoints()
    assert model.endpoints() is first
    model.push_undo()
    model.erase_circle((50, 20), 2)
    assert model.endpoints() is not first


def test_any_nonzero_pixel_counts_as_skeleton():
    model = SkeletonCorrectionModel()
    model.mask = (_cross() > 0).astype(np.uint8)  # 0/1 instead of 0/255
    assert sorted(model.endpoints()) == [(1, 5), (5, 1), (5, 9), (9, 5)]
    model.skeletonize()
    assert model.mask.dtype == np.uint8
    assert set(np.unique(model.mask).tolist()) == {0, 255}


def test_queries_on_an_empty_model_return_empty_results():
    model = SkeletonCorrectionModel()
    assert model.endpoints() == []
    assert model.topology().polylines == []


@pytest.mark.parametrize("dtype", [np.uint8, np.int32, np.float32, bool])
def test_is_empty_is_mask_any_negated_for_every_mask(dtype):
    model = SkeletonCorrectionModel()
    assert model.is_empty()  # nothing loaded
    model.set_empty((30, 20))
    assert model.is_empty()
    for value in (255, 1):
        mask = np.zeros((20, 30), dtype)
        mask[19, 29] = value  # the last pixel only
        model.mask = mask
        assert model.is_empty() is (not mask.any()) is False
    model.mask = np.zeros((20, 30), dtype)[:, ::2]  # a strided view
    assert model.is_empty()
    model.set_empty((30, 20))
    model.push_undo()
    model.draw_polyline([(1, 1), (8, 8)], thickness=1)
    assert not model.is_empty()
    model.clear()
    assert model.is_empty()


# --------------------------------------------------------------------------------------
# Hooks for the Correct workspace (C4): on_action, drop_redo, take_changed_box
# --------------------------------------------------------------------------------------


def test_on_action_fires_once_per_pushed_snapshot_after_the_push():
    model = _model_with_line()
    seen = []
    model.on_action = lambda: seen.append(len(model._undo_stack))
    model.push_undo()
    model.erase_circle((20, 20), 3)
    model.skeletonize()
    model.undo()
    model.redo()
    model.push_undo()
    assert seen == [1, 2]  # once per push, called after the snapshot is on the stack


def test_on_action_is_not_called_without_a_mask():
    model = SkeletonCorrectionModel()
    seen = []
    model.on_action = lambda: seen.append(True)
    model.push_undo()
    assert seen == []


def test_drop_redo_forgets_the_redo_branch_and_keeps_undo_and_the_mask():
    model = _model_with_line()
    model.push_undo()
    model.erase_circle((20, 20), 3)
    model.push_undo()
    model.erase_circle((40, 20), 3)
    assert model.undo() is True
    shown = model.mask.copy()
    rev = model.revision
    model.drop_redo()
    assert (model.can_undo, model.can_redo) == (True, False)
    assert model.redo() is False
    assert np.array_equal(model.mask, shown) and model.revision == rev
    assert model.undo() is True  # the undo stack is intact
    assert np.array_equal(model.mask, _model_with_line().mask)


def _drained(model: SkeletonCorrectionModel) -> SkeletonCorrectionModel:
    model.take_changed_box()
    return model


def _filled_model(size=(60, 40)) -> SkeletonCorrectionModel:
    """A model whose every pixel is set, so each eraser pixel is a real change."""
    model = SkeletonCorrectionModel()
    model.set_empty(size)
    model.mask = np.full((size[1], size[0]), 255, np.uint8)
    return model


def _changed_bounds(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(before != after)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


EDITS = {
    "erase_circle": (True, lambda m: m.erase_circle((30, 20), 4)),
    "erase_segment": (True, lambda m: m.erase_segment((10, 12), (45, 30), 3)),
    "erase_polyline": (True, lambda m: m.erase_polyline([(8, 8), (30, 25), (50, 12)], 9)),
    "draw_polyline_1px": (False, lambda m: m.draw_polyline([(4, 30), (55, 6)], thickness=1)),
    "draw_polyline_3px": (False, lambda m: m.draw_polyline([(8, 8), (30, 25)], thickness=3)),
    "draw_polyline_9px": (False, lambda m: m.draw_polyline([(20, 5), (21, 33)], thickness=9)),
    "clear": (False, lambda m: m.clear()),
}


@pytest.mark.parametrize("name", sorted(EDITS))
def test_take_changed_box_covers_each_changed_pixel_and_stays_local(name):
    erases, edit = EDITS[name]
    if erases:
        model = _drained(_filled_model())
    else:
        model = _drained(_model_with_line())
        model.push_undo()  # a snapshot changes no pixel
    before = model.mask.copy()
    edit(model)
    x, y, w, h = model.take_changed_box()
    x0, y0, x1, y1 = _changed_bounds(before, model.mask)
    assert x <= x0 and y <= y0 and x + w >= x1 and y + h >= y1  # nothing changed outside
    assert x >= x0 - 3 and y >= y0 - 3 and x + w <= x1 + 3 and y + h <= y1 + 3  # local
    assert model.take_changed_box() == (0, 0, 0, 0)  # reset by the call


def test_take_changed_box_is_the_union_of_the_edits_since_the_last_call():
    model = _drained(_filled_model((200, 100)))
    model.erase_circle((20, 20), 2)
    model.erase_circle((150, 70), 2)
    x, y, w, h = model.take_changed_box()
    assert x <= 18 and y <= 18 and x + w >= 153 and y + h >= 73
    assert x >= 15 and y >= 15 and x + w <= 156 and y + h <= 76
    model.erase_circle((100, 50), 1)
    x, y, w, h = model.take_changed_box()
    assert x <= 99 and y <= 49 and x + w >= 102 and y + h >= 52
    assert x >= 96 and y >= 46 and x + w <= 105 and y + h <= 55


def test_take_changed_box_is_clipped_to_the_image():
    model = _drained(_filled_model((60, 40)))
    model.erase_circle((0, 0), 6)
    model.erase_segment((58, 38), (59, 39), 6)
    x, y, w, h = model.take_changed_box()
    assert (x, y) == (0, 0) and (x + w, y + h) == (60, 40)


def test_take_changed_box_is_empty_when_no_pixel_changed():
    model = _drained(_model_with_line())
    model.push_undo()
    model.topology()
    model.endpoints()
    model.has_unsaved_changes()
    model.render_to_size((30, 20))
    assert model.take_changed_box() == (0, 0, 0, 0)


UNKNOWN_AFTER = {
    "set_empty": lambda m: m.set_empty((60, 40)),
    "load_from_raster": lambda m: m.load_from_raster(
        np.eye(40, 60, dtype=np.uint8) * 255, target_size=(60, 40)
    ),
    "mask_setter": lambda m: setattr(m, "mask", np.zeros((40, 60), np.uint8)),
    "full_frame_skeletonize": lambda m: m.skeletonize(),  # no snapshot to start from
}
# Undo and redo report the box they wrote back (patch history, S1):
# tests/test_skeleton_patch_history.py::test_undo_and_redo_report_the_box_they_changed.


@pytest.mark.parametrize("name", sorted(UNKNOWN_AFTER))
def test_take_changed_box_is_unknown_after_whole_mask_changes(name):
    model = SkeletonCorrectionModel()
    model.set_empty((60, 40))
    model.draw_polyline([(5, 20), (50, 20)], thickness=3)  # not thin yet
    _drained(model)
    UNKNOWN_AFTER[name](model)
    assert model.take_changed_box() is None
    assert model.take_changed_box() == (0, 0, 0, 0)  # known again after the call


def test_a_new_model_reports_an_unknown_box():
    assert SkeletonCorrectionModel().take_changed_box() is None


# --------------------------------------------------------------------------------------
# Lazy scikit-image (C4): the import waits for the first skeletonize or the prewarm
# --------------------------------------------------------------------------------------


def _run_python(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_scikit_image_loads_on_the_first_skeletonize_not_at_import():
    result = _run_python(
        """
        import sys
        import app.gui.skeleton_graph_model as sgm
        print("import", "skimage" in sys.modules)
        model = sgm.SkeletonCorrectionModel()
        model.set_empty((40, 30))
        model.draw_polyline([(5, 5), (30, 20)], thickness=3)
        print("edit", "skimage" in sys.modules)
        model.skeletonize()
        print("skeletonize", "skimage.morphology" in sys.modules)
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["import", "False", "edit", "False", "skeletonize", "True"]


def test_prewarm_loads_scikit_image_once_from_a_worker_thread():
    result = _run_python(
        """
        import builtins
        import sys
        import threading
        import app.gui.skeleton_graph_model as sgm
        worker = threading.Thread(target=sgm.prewarm_skeletonize)
        worker.start()
        worker.join(120)
        print("prewarmed", "skimage.morphology" in sys.modules)
        imported = []
        real_import = builtins.__import__
        def spy(name, *args, **kwargs):
            imported.append(name)
            return real_import(name, *args, **kwargs)
        builtins.__import__ = spy
        sgm.prewarm_skeletonize()
        builtins.__import__ = real_import
        print("again", sum(name.startswith("skimage") for name in imported))
        model = sgm.SkeletonCorrectionModel()
        model.set_empty((40, 30))
        model.draw_polyline([(5, 5), (30, 20)], thickness=3)
        model.skeletonize()
        print("thin", int(model.mask.max()), int((model.mask > 0).sum()) < 40)
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["prewarmed", "True", "again", "0", "thin", "255", "True"]


# ----------------------------------------------- opening a small generated file
def test_a_small_file_opens_upscaled_smoothly_as_one_line():
    """The generated skeleton is small (SKELETON_OUTPUT_SIZE, 341 x 256) with ~2 px lines.
    Opening it at image size (640 x 480) upscales the grey file bilinearly before
    thresholding, so the line keeps its connectivity and its measured length: within 1 %
    (bilinear +0.23 %; a nearest-neighbour staircase measures +2.19 %). The lengths are
    ``root_length_mm`` values, each file with its own per-axis pixel pitch (W3-T8 review I1).
    """
    from app.config import SKELETON_OUTPUT_SIZE
    from app.inference.metrics import root_length_mm

    w, h = SKELETON_OUTPUT_SIZE
    small = np.zeros((h, w), np.uint8)
    cv2.line(small, (20, 20), (320, 236), 255, 2)
    cv2.ellipse(small, (170, 128), (100, 70), 15, 0, 270, 255, 2)
    model = SkeletonCorrectionModel()
    model.load_from_raster(small, target_size=(640, 480))
    mask = model.mask
    assert mask.shape == (480, 640)
    source = _skeletonize_uint8(small)
    n_src, _ = cv2.connectedComponents(source, connectivity=8)
    n_new, _ = cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)
    assert n_new == n_src
    assert abs(root_length_mm(mask) / root_length_mm(source) - 1.0) < 0.01


def test_a_file_at_image_size_opens_unchanged():
    """A correction saved at image resolution reopens pixel for pixel (no resize)."""
    model = SkeletonCorrectionModel()
    raster = np.zeros((60, 80), np.uint8)
    cv2.line(raster, (5, 5), (70, 50), 255, 1)
    cv2.line(raster, (5, 50), (40, 8), 255, 1)
    model.load_from_raster(raster, target_size=(80, 60))
    first = model.mask.copy()
    model.load_from_raster(first, target_size=(80, 60))
    assert np.array_equal(model.mask, first)

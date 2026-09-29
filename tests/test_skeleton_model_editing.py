"""SkeletonCorrectionModel editing contract used by the Correct Skeleton editor.

Covers dirty tracking (PE-01 / AR-02 / RB-05), the revision counter and cached
topology/endpoints (PE-03), the eraser drag (PE-02), clear/erase_polyline (AR-12)
and the undo flags backed by SnapshotHistory (AR-11).
"""

from __future__ import annotations

import math
from itertools import pairwise

import cv2
import numpy as np
import pytest

import app.gui.skeleton_graph_model as sgm
from app.gui.skeleton_graph_model import SkeletonCorrectionModel, vectorize_skeleton


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

"""Pure placement rules for the floating canvas overlays (UXE-09, UI-15, UI-07, UXE-08).

``plan_overlays`` is Qt-free: it takes measured overlay sizes and returns rectangles. The
sizes below are the real ones measured in the editors (audit overlay_thresholds.py):
Correct Mask rail 44x217, dock 623x46 (427 px compact), zoom cluster 165x40, status chip
425x28; Trace GT dock 607x46 and class panel 153x172; Correct Skeleton rail 85x286, dock
585x56 (782x56 with the eraser slider), hint chip 213x30; Enhance popover 264x257.
"""

from __future__ import annotations

import pytest

from app.gui.widgets.overlay_geometry import (
    Insets,
    LayoutMetrics,
    OverlaySpec,
    Rect,
    plan_overlays,
)


def _chip(natural=(425, 28), min_width=182, extra_line=18):
    """A chip that elides/wraps down to ``min_width``; wrapping adds one text line."""

    def measure(max_width):
        width, height = natural
        if max_width is None or max_width >= width:
            return natural
        return max(min_width, max_width), height + extra_line

    return measure


MASK = dict(rail=(44, 217), dock=(623, 46), dock_compact_width=427, zoom=(165, 40))
GT = dict(rail=(44, 217), dock=(607, 46), dock_compact_width=411, zoom=(165, 40), side=(153, 172))
SKELETON = dict(rail=(85, 286), dock=(585, 56), dock_compact_width=487)
SKELETON_ERASER = dict(rail=(85, 286), dock=(782, 56), dock_compact_width=586)


def _collisions(plan):
    items = list(plan.rects.items())
    return [
        (a, b) for i, (a, ra) in enumerate(items) for b, rb in items[i + 1 :] if ra.intersects(rb)
    ]


def _outside(plan, host):
    width, height = host
    return [
        role
        for role, r in plan.rects.items()
        if r.x < 0 or r.y < 0 or r.right > width or r.bottom > height
    ]


# --------------------------------------------------------------------------- #
#  Rect
# --------------------------------------------------------------------------- #
def test_rect_edges_are_exclusive_so_touching_rects_do_not_intersect():
    a = Rect(0, 0, 10, 10)
    assert (a.right, a.bottom) == (10, 10)
    assert not a.intersects(Rect(10, 0, 5, 5))
    assert not a.intersects(Rect(0, 10, 5, 5))
    assert a.intersects(Rect(9, 9, 5, 5))


# --------------------------------------------------------------------------- #
#  Bottom row: dock, zoom cluster (UXE-09 / UI-15)
# --------------------------------------------------------------------------- #
def test_roomy_canvas_keeps_the_classic_positions():
    plan = plan_overlays(OverlaySpec(host=(1400, 900), chip=_chip(), **MASK))
    assert plan.rects["dock"] == Rect(388, 836, 623, 46)  # bottom-centre, 18 px margin
    assert plan.rects["zoom_cluster"] == Rect(1221, 842, 165, 40)  # bottom-right
    assert plan.rects["rail"] == Rect(14, 341, 44, 217)  # left, vertically centred
    assert plan.rects["top_chip"] == Rect(14, 14, 425, 28)  # top-left
    assert not plan.dock_compact and not plan.zoom_lifted


def test_dock_slides_left_of_the_zoom_cluster_at_the_minimum_window():
    # 907x562 is the Correct Mask canvas at the 1169x700 minimum window, where the
    # zoom cluster used to cover 'Save mask' (UI-15).
    plan = plan_overlays(OverlaySpec(host=(907, 562), chip=_chip(), **MASK))
    dock, zoom = plan.rects["dock"], plan.rects["zoom_cluster"]
    assert dock == Rect(95, 498, 623, 46)
    assert dock.right + 10 == zoom.x
    assert not plan.dock_compact and not plan.zoom_lifted


def test_zoom_cluster_lifts_above_the_dock_when_the_span_is_too_narrow():
    plan = plan_overlays(OverlaySpec(host=(780, 562), chip=_chip(), **MASK))
    dock, zoom = plan.rects["dock"], plan.rects["zoom_cluster"]
    assert plan.zoom_lifted and not plan.dock_compact
    assert dock == Rect(78, 498, 623, 46)
    assert zoom == Rect(601, 448, 165, 40)  # right-aligned, 10 px above the dock


def test_dock_turns_compact_only_when_it_cannot_fit_at_full_width():
    plan = plan_overlays(OverlaySpec(host=(620, 562), chip=_chip(), **MASK))
    assert plan.dock_compact
    assert plan.rects["dock"].w == 427
    assert plan.zoom_lifted  # 427 px still does not fit beside the zoom cluster


def test_compact_dock_stays_beside_the_zoom_cluster_when_it_fits_there():
    plan = plan_overlays(OverlaySpec(host=(640, 562), chip=_chip(), **MASK))
    dock, zoom = plan.rects["dock"], plan.rects["zoom_cluster"]
    assert plan.dock_compact and not plan.zoom_lifted
    assert dock.right + 10 <= zoom.x and dock.x >= 14


def test_a_tall_rail_that_reaches_the_bottom_row_narrows_the_dock_span():
    plan = plan_overlays(OverlaySpec(host=(900, 360), **SKELETON))
    rail, dock = plan.rects["rail"], plan.rects["dock"]
    assert rail.bottom > dock.y  # the rail really reaches into the dock's band
    assert dock.x >= rail.right + 10


# --------------------------------------------------------------------------- #
#  Right column: side panel yields to the popover (UI-07 / UXE-09)
# --------------------------------------------------------------------------- #
def test_open_popover_pushes_the_class_panel_below_it():
    # The Trace GT canvas at a 1280x800 window: they used to overlap by 153x26.
    plan = plan_overlays(OverlaySpec(host=(1018, 662), chip=_chip(), popover=(264, 257), **GT))
    pop, side = plan.rects["popover"], plan.rects["side_panel"]
    assert pop == Rect(740, 14, 264, 257)
    assert side == Rect(851, 281, 153, 172)


def test_class_panel_is_centred_when_the_popover_is_closed():
    plan = plan_overlays(OverlaySpec(host=(1018, 662), chip=_chip(), **GT))
    assert plan.rects["side_panel"] == Rect(851, 245, 153, 172)


def test_class_panel_shrinks_between_the_popover_and_the_bottom_row():
    plan = plan_overlays(OverlaySpec(host=(907, 480), chip=_chip(), popover=(264, 257), **GT))
    side, zoom = plan.rects["side_panel"], plan.rects["zoom_cluster"]
    assert side.y == 281
    assert side.bottom + 10 <= zoom.y
    assert side.h < 172


# --------------------------------------------------------------------------- #
#  Top row: chip beside / below the prompt; prompt clear of the popover
# --------------------------------------------------------------------------- #
def test_prompt_moves_left_of_an_open_popover_and_the_chip_wraps_beside_it():
    spec = OverlaySpec(
        host=(907, 562), chip=_chip((213, 30)), prompt=(340, 44), popover=(264, 257), **SKELETON
    )
    plan = plan_overlays(spec)
    pop, prompt, chip = plan.rects["popover"], plan.rects["prompt"], plan.rects["top_chip"]
    assert prompt == Rect(279, 14, 340, 44)
    assert prompt.right + 10 == pop.x
    assert chip == Rect(14, 14, 213, 30)  # 255 px free beside the prompt: no wrap needed


def test_chip_drops_below_the_prompt_when_there_is_no_room_beside_it():
    spec = OverlaySpec(
        host=(907, 562), chip=_chip((213, 30)), prompt=(340, 44), popover=(464, 257), **SKELETON
    )
    plan = plan_overlays(spec)
    prompt, chip = plan.rects["prompt"], plan.rects["top_chip"]
    assert prompt.x == 79
    assert chip == Rect(14, 68, 213, 30)  # 10 px below the prompt


def test_chip_is_fitted_to_the_width_left_of_the_popover():
    plan = plan_overlays(OverlaySpec(host=(700, 562), chip=_chip(), popover=(264, 257), **MASK))
    chip, pop = plan.rects["top_chip"], plan.rects["popover"]
    assert chip.right + 10 <= pop.x
    assert plan.chip_width == pop.x - 10 - 14


def test_rail_is_pushed_below_a_chip_it_would_hit_on_a_short_canvas():
    plan = plan_overlays(OverlaySpec(host=(900, 300), chip=_chip((213, 60)), **SKELETON))
    assert plan.rects["rail"].y >= plan.rects["top_chip"].bottom + 10


# --------------------------------------------------------------------------- #
#  Sweep: no collisions, nothing leaves the host (the UXE-09 acceptance test)
# --------------------------------------------------------------------------- #
PROFILES = {
    "correct-mask": (dict(chip=_chip(), **MASK), 600),
    "correct-mask+popover": (dict(chip=_chip(), popover=(264, 257), **MASK), 600),
    "trace-gt": (dict(chip=_chip(), **GT), 600),
    "trace-gt+popover": (dict(chip=_chip(), popover=(264, 257), **GT), 600),
    "skeleton": (dict(chip=_chip((213, 30)), zoom=(165, 40), **SKELETON), 600),
    "skeleton-eraser+prompt": (
        dict(chip=_chip((213, 30)), zoom=(165, 40), prompt=(340, 44), **SKELETON_ERASER),
        640,  # the 586 px compact eraser dock needs 614 px
    ),
    "skeleton+prompt+popover": (
        dict(
            chip=_chip((213, 30)), zoom=(165, 40), prompt=(340, 44), popover=(264, 257), **SKELETON
        ),
        640,
    ),
}


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_overlays_never_collide_or_leave_the_host(profile):
    kwargs, min_width = PROFILES[profile]
    failures = []
    for width in range(min_width, 1401, 40):
        for height in range(520, 901, 40):
            plan = plan_overlays(OverlaySpec(host=(width, height), **kwargs))
            hits, out = _collisions(plan), _outside(plan, (width, height))
            if hits or out:
                failures.append(((width, height), hits, out))
    assert not failures, failures[:5]


# --------------------------------------------------------------------------- #
#  Insets for fit-to-view (UXE-08)
# --------------------------------------------------------------------------- #
def test_insets_describe_the_canvas_left_clear_by_the_overlays():
    plan = plan_overlays(OverlaySpec(host=(1018, 662), chip=_chip(), **GT))
    # left: rail right 58 + 8; top: chip bottom 42 + 8; right: 1018 - class panel x 851 + 8;
    # bottom: 662 - dock top 598 + 8.
    assert plan.insets == Insets(left=66, top=50, right=175, bottom=72)


def test_insets_fall_back_to_the_default_padding_without_overlays():
    plan = plan_overlays(OverlaySpec(host=(800, 600)), LayoutMetrics(default_inset=24))
    assert plan.insets == Insets(24, 24, 24, 24)
    assert plan.rects == {}

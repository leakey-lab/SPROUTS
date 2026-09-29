"""
Picking and smoothing geometry for the editors, on plain ``(x, y)`` tuples.

Qt-free ports of the math that used to live inside
``SkeletonCorrectionInterface`` (AR-12): the interface converts ``QPoint``
to tuples at its edge and calls these. The arithmetic is kept operation for
operation, so results (ties, thresholds, rounding) are identical to the
originals.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]  # (x, y); ints are accepted everywhere
BezierSegment = tuple[Point, Point, Point]  # (control 1, control 2, end point)


def dist2_point_to_segment(p: Point, a: Point, b: Point) -> float:
    """Squared distance from ``p`` to the segment ``a``-``b`` (to ``a`` if it is degenerate)."""
    px, py = float(p[0]), float(p[1])
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-6:
        return apx * apx + apy * apy
    t = (apx * abx + apy * aby) / denom
    t = max(0.0, min(1.0, t))
    cx = ax + t * abx
    cy = ay + t * aby
    dx = px - cx
    dy = py - cy
    return dx * dx + dy * dy


def nearest_vertex_index(points: Sequence[Point], p: Point, max_dist: float) -> int | None:
    """Index of the point closest to ``p`` if within ``max_dist``; the first one wins ties."""
    px, py = p
    best_i: int | None = None
    best_d2 = 0.0
    for i, (x, y) in enumerate(points):
        dx = x - px
        dy = y - py
        d2 = dx * dx + dy * dy
        if best_i is None or d2 < best_d2:
            best_d2 = d2
            best_i = i
    if best_i is None or best_d2 > max_dist * max_dist:
        return None
    return best_i


def nearest_segment_index(points: Sequence[Point], p: Point, max_dist: float) -> int | None:
    """Index ``i`` of the segment ``points[i]``-``points[i + 1]`` closest to ``p``, if in reach."""
    best_i: int | None = None
    best_d2 = 0.0
    for i in range(len(points) - 1):
        d2 = dist2_point_to_segment(p, points[i], points[i + 1])
        if best_i is None or d2 < best_d2:
            best_d2 = d2
            best_i = i
    if best_i is None or best_d2 > float(max_dist * max_dist):
        return None
    return best_i


def nearest_polyline_index(
    polylines: Sequence[Sequence[Point]], p: Point, max_dist: float
) -> int | None:
    """Index of the polyline passing closest to ``p``, if in reach.

    Polylines with fewer than two points have no segment and are skipped.
    """
    best_i: int | None = None
    best_d2 = 0.0
    for i, poly in enumerate(polylines):
        if len(poly) < 2:
            continue
        d2 = min(dist2_point_to_segment(p, poly[j], poly[j + 1]) for j in range(len(poly) - 1))
        if best_i is None or d2 < best_d2:
            best_d2 = d2
            best_i = i
    if best_i is None or best_d2 > float(max_dist * max_dist):
        return None
    return best_i


def _span(points: Sequence[Point], i: int) -> tuple[float, ...]:
    """Coordinates of the four Catmull-Rom points around span ``i`` -> ``i + 1``.

    The first and last spans reuse their end point as the missing neighbour.
    Returned flat as ``(x0, y0, x1, y1, x2, y2, x3, y3)``.
    """
    p0 = points[i - 1] if i > 0 else points[i]
    p3 = points[i + 2] if (i + 2) < len(points) else points[i + 1]
    return tuple(float(c) for q in (p0, points[i], points[i + 1], p3) for c in q)


def catmull_rom_bezier(points: Sequence[Point]) -> list[BezierSegment]:
    """Cubic Bezier segments of the uniform Catmull-Rom spline through ``points``.

    Draw with ``moveTo(points[0])`` then ``cubicTo(c1, c2, end)`` per segment.
    """
    segments: list[BezierSegment] = []
    for i in range(len(points) - 1):
        x0, y0, x1, y1, x2, y2, x3, y3 = _span(points, i)
        c1 = (x1 + (x2 - x0) / 6.0, y1 + (y2 - y0) / 6.0)
        c2 = (x2 - (x3 - x1) / 6.0, y2 - (y3 - y1) / 6.0)
        segments.append((c1, c2, (x2, y2)))
    return segments


def sample_catmull_rom(
    points: Sequence[Point],
    *,
    step_px: float = 0.75,
    bounds: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    """Integer pixels along the Catmull-Rom spline through ``points``, for raster drawing.

    Each span is sampled about every ``step_px`` pixels of its chord (at least
    twice), rounded, clamped into ``bounds`` = ``(width, height)`` when given,
    and consecutive duplicates are dropped. Fewer than three points have no
    curve: two points come back unchanged (not clamped), one or none gives ``[]``.
    """
    if len(points) < 2:
        return []
    if len(points) < 3:
        return [(int(round(x)), int(round(y))) for x, y in points]

    out: list[tuple[int, int]] = []

    def add(ix: int, iy: int) -> None:
        if bounds is not None:
            w, h = bounds
            ix = max(0, min(ix, w - 1))
            iy = max(0, min(iy, h - 1))
        if not out or out[-1] != (ix, iy):
            out.append((ix, iy))

    for i in range(len(points) - 1):
        x0, y0, x1, y1, x2, y2, x3, y3 = _span(points, i)
        seg_len = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, int(seg_len / max(step_px, 0.25)))
        for s in range(steps + 1):
            t = float(s) / float(steps)
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2.0 * x1)
                + (-x0 + x2) * t
                + (2.0 * x0 - 5.0 * x1 + 4.0 * x2 - x3) * t2
                + (-x0 + 3.0 * x1 - 3.0 * x2 + x3) * t3
            )
            y = 0.5 * (
                (2.0 * y1)
                + (-y0 + y2) * t
                + (2.0 * y0 - 5.0 * y1 + 4.0 * y2 - y3) * t2
                + (-y0 + 3.0 * y1 - 3.0 * y2 + y3) * t3
            )
            add(int(round(x)), int(round(y)))
    return out

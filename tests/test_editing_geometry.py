"""app.editing.geometry: Qt-free picking and Catmull-Rom smoothing (AR-12 step 1).

Two kinds of tests:
- hand-derived values for each function;
- golden equivalence with verbatim copies of the SkeletonCorrectionInterface
  methods they replace (as of a1599e6), on random inputs.
"""

from __future__ import annotations

import math
import random
from itertools import pairwise

import pytest

from app.editing.geometry import (
    catmull_rom_bezier,
    dist2_point_to_segment,
    nearest_polyline_index,
    nearest_segment_index,
    nearest_vertex_index,
    sample_catmull_rom,
)

# --------------------------------------------------------------------------------------
# Hand-derived values
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p, a, b, expected",
    [
        ((2, 2), (0, 0), (4, 0), 4.0),  # above the middle: perpendicular foot
        ((0, 0), (1, 0), (3, 0), 1.0),  # before a: distance to a
        ((6, 0), (0, 0), (4, 0), 4.0),  # past b: distance to b
        ((3, 4), (0, 0), (0, 0), 25.0),  # degenerate segment: distance to the point
        ((1, 1), (0, 0), (2, 2), 0.0),  # on the segment
    ],
)
def test_dist2_point_to_segment(p, a, b, expected):
    assert dist2_point_to_segment(p, a, b) == pytest.approx(expected)


def test_nearest_vertex_index_picks_the_closest_vertex_within_reach():
    pts = [(0, 0), (10, 0), (10, 10)]
    assert nearest_vertex_index(pts, (9, 1), max_dist=3) == 1  # d2 = 2 <= 9
    assert nearest_vertex_index(pts, (9, 1), max_dist=1) is None  # d2 = 2 > 1
    assert nearest_vertex_index([(0, 0), (2, 0)], (1, 0), max_dist=5) == 0  # tie: first wins
    assert nearest_vertex_index([], (0, 0), max_dist=5) is None


def test_nearest_segment_index_picks_the_closest_segment_within_reach():
    pts = [(0, 0), (10, 0), (10, 10)]
    assert nearest_segment_index(pts, (5, 1), max_dist=2) == 0
    assert nearest_segment_index(pts, (11, 5), max_dist=2) == 1
    assert nearest_segment_index(pts, (20, 20), max_dist=2) is None
    assert nearest_segment_index([(0, 0)], (0, 0), max_dist=2) is None


def test_nearest_polyline_index_ignores_single_point_polylines():
    lines = [[(0, 0), (10, 0)], [(0, 5), (10, 5)]]
    assert nearest_polyline_index(lines, (3, 4), max_dist=2) == 1  # 1 px vs 4 px away
    assert nearest_polyline_index(lines, (3, 40), max_dist=2) is None
    assert nearest_polyline_index([[(3, 4)], [(0, 0), (10, 0)]], (3, 4), max_dist=5) == 1
    assert nearest_polyline_index([], (0, 0), max_dist=5) is None


def test_catmull_rom_bezier_control_points_on_a_straight_line():
    # Equally spaced collinear points: the controls sit at 1/6 and 5/6 of each span.
    segs = catmull_rom_bezier([(0, 0), (3, 0), (6, 0)])
    assert segs == [
        ((0.5, 0.0), (2.0, 0.0), (3.0, 0.0)),
        ((4.0, 0.0), (5.5, 0.0), (6.0, 0.0)),
    ]


def test_sample_catmull_rom_passes_through_every_control_point():
    pts = [(10, 10), (40, 25), (70, 10), (100, 40)]
    out = sample_catmull_rom(pts, step_px=0.75)
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert all(p in out for p in pts)
    assert all(a != b for a, b in pairwise(out))  # consecutive duplicates removed
    assert all(max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 2 for a, b in pairwise(out))


def test_sample_catmull_rom_clamps_to_bounds():
    out = sample_catmull_rom([(-5, 3), (20, -8), (40, 50)], bounds=(30, 20))
    assert out
    assert all(0 <= x <= 29 and 0 <= y <= 19 for x, y in out)


def test_sample_catmull_rom_short_inputs():
    assert sample_catmull_rom([]) == []
    assert sample_catmull_rom([(1, 2)]) == []
    # Two points stay a straight line, returned unchanged (and unclamped, as before).
    assert sample_catmull_rom([(1, 2), (-4, 50)], bounds=(10, 10)) == [(1, 2), (-4, 50)]


# --------------------------------------------------------------------------------------
# Golden equivalence with the interface methods being replaced
# --------------------------------------------------------------------------------------


class _Original:
    """SkeletonCorrectionInterface picking/smoothing methods, copied verbatim (a1599e6).

    Only the data sources are injected: ``_polyline_points`` and ``_polyline_smooth``
    are plain attributes, ``clamp_to_image`` clamps to ``image_size`` like the
    pixmap-backed original, and the endpoint/topology pickers take their lists as
    arguments instead of calling ``self.model.topology()``.
    """

    def __init__(self, points, *, smooth=True, image_size=None):
        self._polyline_points = points
        self._polyline_smooth = smooth
        self._image_size = image_size

    def clamp_to_image(self, pt):
        from PyQt6.QtCore import QPoint

        if self._image_size is None:
            return QPoint(0, 0)
        w, h = self._image_size
        return QPoint(max(0, min(pt.x(), w - 1)), max(0, min(pt.y(), h - 1)))

    def _nearest_endpoint(self, endpoints, pos, max_dist=10):
        from PyQt6.QtCore import QPoint

        if not endpoints:
            return None
        best = None
        best_d2 = None
        for x, y in endpoints:
            dx = x - pos.x()
            dy = y - pos.y()
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = QPoint(x, y)
        if best is None or best_d2 is None:
            return None
        if best_d2 <= (max_dist * max_dist):
            return best
        return None

    def _nearest_polyline_handle_index(self, pos, max_dist=8):
        if not self._polyline_points:
            return None
        best_i = None
        best_d2 = None
        for i, p in enumerate(self._polyline_points):
            dx = p.x() - pos.x()
            dy = p.y() - pos.y()
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_i = i
        if best_d2 is None or best_i is None:
            return None
        if best_d2 <= max_dist * max_dist:
            return best_i
        return None

    def _nearest_polyline_segment_index(self, pos, max_dist=8):
        if len(self._polyline_points) < 2:
            return None

        def dist2_point_to_segment(px, py, ax, ay, bx, by):
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

        px, py = float(pos.x()), float(pos.y())
        best_i = None
        best_d2 = None
        for i in range(len(self._polyline_points) - 1):
            a = self._polyline_points[i]
            b = self._polyline_points[i + 1]
            d2 = dist2_point_to_segment(
                px, py, float(a.x()), float(a.y()), float(b.x()), float(b.y())
            )
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_i = i
        if best_d2 is None or best_i is None:
            return None
        if best_d2 <= float(max_dist * max_dist):
            return best_i
        return None

    def _build_polyline_path(self, points, cursor_pos):
        from PyQt6.QtGui import QPainterPath

        pts = list(points)
        if cursor_pos is not None and (not pts or cursor_pos != pts[-1]):
            pts.append(cursor_pos)
        path = QPainterPath()
        if not pts:
            return path

        p0 = pts[0]
        path.moveTo(float(p0.x()), float(p0.y()))

        if (not self._polyline_smooth) or len(pts) < 3:
            for p in pts[1:]:
                path.lineTo(float(p.x()), float(p.y()))
            return path

        for i in range(len(pts) - 1):
            P0 = pts[i - 1] if i > 0 else pts[i]
            P1 = pts[i]
            P2 = pts[i + 1]
            P3 = pts[i + 2] if (i + 2) < len(pts) else pts[i + 1]

            c1x = float(P1.x()) + (float(P2.x()) - float(P0.x())) / 6.0
            c1y = float(P1.y()) + (float(P2.y()) - float(P0.y())) / 6.0
            c2x = float(P2.x()) - (float(P3.x()) - float(P1.x())) / 6.0
            c2y = float(P2.y()) - (float(P3.y()) - float(P1.y())) / 6.0
            path.cubicTo(c1x, c1y, c2x, c2y, float(P2.x()), float(P2.y()))

        return path

    def _nearest_topology_polyline(self, polylines, pos, max_dist=12):
        if not polylines:
            return None

        px, py = float(pos.x()), float(pos.y())
        best_poly = None
        best_d2 = None

        def dist2_point_to_segment(px, py, ax, ay, bx, by):
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

        for poly in polylines:
            if len(poly) < 2:
                continue
            local_best = None
            for i in range(len(poly) - 1):
                ax, ay = float(poly[i][0]), float(poly[i][1])
                bx, by = float(poly[i + 1][0]), float(poly[i + 1][1])
                d2 = dist2_point_to_segment(px, py, ax, ay, bx, by)
                if local_best is None or d2 < local_best:
                    local_best = d2
            if local_best is None:
                continue
            if best_d2 is None or local_best < best_d2:
                best_d2 = local_best
                best_poly = [(int(x), int(y)) for (x, y) in poly]

        if best_d2 is None or best_poly is None:
            return None
        if best_d2 <= float(max_dist * max_dist):
            return best_poly
        return None

    def _sample_smooth_polyline_points(self, points, *, step_px=0.75):
        from PyQt6.QtCore import QPoint

        if len(points) < 2:
            return []

        if len(points) < 3:
            return [(p.x(), p.y()) for p in points]

        out = []

        def add_pt(ix, iy):
            pt = self.clamp_to_image(QPoint(ix, iy))
            tup = (pt.x(), pt.y())
            if not out or out[-1] != tup:
                out.append(tup)

        for i in range(len(points) - 1):
            P0 = points[i - 1] if i > 0 else points[i]
            P1 = points[i]
            P2 = points[i + 1]
            P3 = points[i + 2] if (i + 2) < len(points) else points[i + 1]

            dx = float(P2.x() - P1.x())
            dy = float(P2.y() - P1.y())
            seg_len = math.hypot(dx, dy)
            steps = max(2, int(seg_len / max(step_px, 0.25)))

            for s in range(steps + 1):
                t = float(s) / float(steps)
                t2 = t * t
                t3 = t2 * t

                x = 0.5 * (
                    (2.0 * float(P1.x()))
                    + (-float(P0.x()) + float(P2.x())) * t
                    + (
                        2.0 * float(P0.x())
                        - 5.0 * float(P1.x())
                        + 4.0 * float(P2.x())
                        - float(P3.x())
                    )
                    * t2
                    + (-float(P0.x()) + 3.0 * float(P1.x()) - 3.0 * float(P2.x()) + float(P3.x()))
                    * t3
                )
                y = 0.5 * (
                    (2.0 * float(P1.y()))
                    + (-float(P0.y()) + float(P2.y())) * t
                    + (
                        2.0 * float(P0.y())
                        - 5.0 * float(P1.y())
                        + 4.0 * float(P2.y())
                        - float(P3.y())
                    )
                    * t2
                    + (-float(P0.y()) + 3.0 * float(P1.y()) - 3.0 * float(P2.y()) + float(P3.y()))
                    * t3
                )
                add_pt(int(round(x)), int(round(y)))

        return out


def _random_cases(n: int, seed: int):
    rng = random.Random(seed)
    for _ in range(n):
        k = rng.randint(1, 12)
        pts = [(rng.randint(-10, 210), rng.randint(-10, 160)) for _ in range(k)]
        if rng.random() < 0.2 and k > 1:  # repeated points: degenerate segments
            pts[rng.randrange(k)] = pts[0]
        query = (rng.randint(-20, 220), rng.randint(-20, 170))
        yield pts, query, rng.choice([3, 8, 12, 30, 400])


def test_vertex_and_endpoint_picking_match_the_originals():
    from PyQt6.QtCore import QPoint

    for pts, (qx, qy), reach in _random_cases(400, seed=1):
        orig = _Original([QPoint(x, y) for x, y in pts])
        want = orig._nearest_polyline_handle_index(QPoint(qx, qy), max_dist=reach)
        assert nearest_vertex_index(pts, (qx, qy), max_dist=reach) == want

        endpoint = orig._nearest_endpoint(pts, QPoint(qx, qy), max_dist=reach)
        got = nearest_vertex_index(pts, (qx, qy), max_dist=reach)
        assert (None if got is None else pts[got]) == (
            None if endpoint is None else (endpoint.x(), endpoint.y())
        )


def test_segment_picking_matches_the_original():
    from PyQt6.QtCore import QPoint

    for pts, (qx, qy), reach in _random_cases(400, seed=2):
        orig = _Original([QPoint(x, y) for x, y in pts])
        want = orig._nearest_polyline_segment_index(QPoint(qx, qy), max_dist=reach)
        assert nearest_segment_index(pts, (qx, qy), max_dist=reach) == want


def test_polyline_picking_matches_the_original():
    from PyQt6.QtCore import QPoint

    rng = random.Random(3)
    for _ in range(200):
        polylines = [pts for pts, _q, _r in _random_cases(rng.randint(0, 8), rng.random())]
        q = (rng.randint(-20, 220), rng.randint(-20, 170))
        reach = rng.choice([3, 12, 40])
        want = _Original([])._nearest_topology_polyline(polylines, QPoint(*q), max_dist=reach)
        got = nearest_polyline_index(polylines, q, max_dist=reach)
        assert (None if got is None else polylines[got]) == want


def test_bezier_path_matches_the_original():
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QPainterPath

    for pts, _q, _r in _random_cases(300, seed=4):
        if len(pts) < 3:
            continue
        qpts = [QPoint(x, y) for x, y in pts]
        want = _Original(qpts, smooth=True)._build_polyline_path(qpts, None)

        got = QPainterPath()
        got.moveTo(float(pts[0][0]), float(pts[0][1]))
        for (c1x, c1y), (c2x, c2y), (ex, ey) in catmull_rom_bezier(pts):
            got.cubicTo(c1x, c1y, c2x, c2y, ex, ey)

        assert got.elementCount() == want.elementCount()
        for i in range(want.elementCount()):
            a, b = got.elementAt(i), want.elementAt(i)
            assert (a.type, a.x, a.y) == (b.type, b.x, b.y)


@pytest.mark.parametrize("step_px", [0.75, 0.3, 2.0])
def test_spline_sampling_matches_the_original(step_px):
    from PyQt6.QtCore import QPoint

    for pts, _q, _r in _random_cases(120, seed=5):
        qpts = [QPoint(x, y) for x, y in pts]
        orig = _Original(qpts, image_size=(120, 90))
        want = orig._sample_smooth_polyline_points(qpts, step_px=step_px)
        assert sample_catmull_rom(pts, step_px=step_px, bounds=(120, 90)) == want

"""Skeleton vectorization: termination on loops (UXE-01) and unchanged output elsewhere.

``vectorize_skeleton`` walks the 1px skeleton from every node (endpoint or junction)
to the next node. A closed loop that hangs off a single junction pixel used to send
that walk back into its own start node, where it kept going round the loop forever
and froze the Correct Skeleton editor.

The termination tests run the walk in a child process with a timeout, so a
regression fails the test instead of hanging the suite. The equivalence tests pin
the output for skeletons whose walks never return to their start node: there the
fix must not change a single point.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest
from skimage.morphology import skeletonize

from app.gui.skeleton_graph_model import vectorize_skeleton

REPO_ROOT = Path(__file__).resolve().parents[1]
CHILD_TIMEOUT_S = 60

# Runs vectorize_skeleton on every array of an .npz file and dumps the topologies.
_CHILD_CODE = """
import json, sys
sys.path.insert(0, sys.argv[4])
import numpy as np
from app.gui.skeleton_graph_model import vectorize_skeleton
data = np.load(sys.argv[1])
eps = float(sys.argv[3])
out = {}
for key in data.files:
    t = vectorize_skeleton(data[key], simplify_epsilon=eps)
    out[key] = {"endpoints": t.endpoints, "junctions": t.junctions, "polylines": t.polylines}
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(out, f)
"""


def _vectorize_isolated(tmp_path: Path, masks: dict[str, np.ndarray], eps: float) -> dict:
    """Vectorize ``masks`` in a child process; fail (not hang) if it never returns."""
    npz = tmp_path / "masks.npz"
    out = tmp_path / "topologies.json"
    np.savez(npz, **masks)
    cmd = [sys.executable, "-c", _CHILD_CODE, str(npz), str(out), str(eps), str(REPO_ROOT)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f"vectorize_skeleton did not return within {CHILD_TIMEOUT_S}s (loop trap)")
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(out.read_text(encoding="utf-8"))
    return {
        key: {
            "endpoints": [tuple(p) for p in topo["endpoints"]],
            "junctions": [tuple(p) for p in topo["junctions"]],
            "polylines": [[tuple(p) for p in poly] for poly in topo["polylines"]],
        }
        for key, topo in raw.items()
    }


def _skeleton(mask: np.ndarray) -> np.ndarray:
    """1px uint8 0/255 skeleton, computed independently of the code under test."""
    return skeletonize(mask > 0).astype(np.uint8) * 255


def _degree(skel: np.ndarray) -> np.ndarray:
    """8-neighbour count of every pixel (hand-rolled, not the module's helper)."""
    fg = np.pad((skel > 0).astype(np.int32), 1)
    h, w = skel.shape
    deg = np.zeros((h, w), dtype=np.int32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                deg += fg[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
    return deg


def _pixels(where: np.ndarray) -> set[tuple[int, int]]:
    ys, xs = np.nonzero(where)
    return {(int(x), int(y)) for x, y in zip(xs, ys, strict=True)}


def _lollipop() -> np.ndarray:
    """A 1px loop hanging off one junction pixel, plus its stem (the UXE-01 repro)."""
    m = np.zeros((200, 200), np.uint8)
    cv2.circle(m, (100, 80), 30, 255, 1)
    cv2.line(m, (100, 110), (100, 180), 255, 1)
    return _skeleton(m)


def _loop_drawn_onto_its_branch(rng: np.random.Generator) -> np.ndarray:
    """A stem that curls back onto itself, drawn the way the editor draws (3px, skeletonized)."""
    m = np.zeros((480, 640), np.uint8)
    cx, cy = rng.uniform(150, 490), rng.uniform(150, 330)
    r = rng.uniform(12, 60)
    a0 = rng.uniform(0, 2 * np.pi)
    stem_end = (cx + r * np.cos(a0), cy + r * np.sin(a0))
    stem_start = (stem_end[0] + 90 * np.cos(a0), stem_end[1] + 90 * np.sin(a0))
    ring = [
        (cx + r * np.cos(a0 + t), cy + r * np.sin(a0 + t)) for t in np.linspace(0, 2 * np.pi, 24)
    ]
    pts = np.array([stem_start] + ring, np.int32).reshape(-1, 1, 2)
    cv2.polylines(m, [pts], False, 255, 3)
    return _skeleton(m)


def _assert_topology_invariants(skel: np.ndarray, topo: dict) -> None:
    """Properties every exact (epsilon 0) vectorization of a connected skeleton must have."""
    deg = _degree(skel)
    fg = skel > 0
    nodes = _pixels(fg & (deg != 2))
    assert set(topo["endpoints"]) == _pixels(fg & (deg == 1))
    assert set(topo["junctions"]) == _pixels(fg & (deg >= 3))

    covered: set[tuple[int, int]] = set()
    for poly in topo["polylines"]:
        assert poly[0] in nodes and poly[-1] in nodes, "a polyline must run node to node"
        for (x0, y0), (x1, y1) in pairwise(poly):
            assert max(abs(x1 - x0), abs(y1 - y0)) == 1, "consecutive points are 8-neighbours"
        closed = len(poly) > 2 and poly[0] == poly[-1]
        body = poly[:-1] if closed else poly
        assert len(set(body)) == len(body), "a polyline never revisits a pixel"
        covered.update(poly)
    assert covered == _pixels(fg & (deg > 0)), "every connected skeleton pixel is traced"


# --------------------------------------------------------------------------------------
# Termination (UXE-01)
# --------------------------------------------------------------------------------------


def test_loop_hanging_off_a_junction_terminates_as_closed_loop_plus_stem(tmp_path):
    skel = _lollipop()
    topo = _vectorize_isolated(tmp_path, {"lollipop": skel}, eps=0.0)["lollipop"]

    assert topo["endpoints"] == [(100, 180)]
    assert topo["junctions"] == [(100, 110)]
    closed = [p for p in topo["polylines"] if p[0] == p[-1]]
    stems = [p for p in topo["polylines"] if p[0] != p[-1]]
    assert len(closed) == 1 and closed[0][0] == (100, 110)
    assert len(stems) == 1 and {stems[0][0], stems[0][-1]} == {(100, 110), (100, 180)}
    _assert_topology_invariants(skel, topo)


def test_loops_drawn_back_onto_their_branch_all_terminate(tmp_path):
    rng = np.random.default_rng(1)
    shapes = {f"shape{i}": _loop_drawn_onto_its_branch(rng) for i in range(40)}
    # Guard the fixture: with seed 1 at least one of these trapped the old walk.
    assert any(_reference_walk_returns_to_start(s) for s in shapes.values())

    exact = _vectorize_isolated(tmp_path, shapes, eps=0.0)
    for key, skel in shapes.items():
        _assert_topology_invariants(skel, exact[key])

    simplified = _vectorize_isolated(tmp_path, shapes, eps=1.5)
    for key in shapes:
        assert sorted(simplified[key]["endpoints"]) == sorted(exact[key]["endpoints"])
        assert len(simplified[key]["polylines"]) == len(exact[key]["polylines"])


# --------------------------------------------------------------------------------------
# Equivalence: output unchanged wherever no walk returns to its start node
# --------------------------------------------------------------------------------------


class _WalkReturnedToStart(Exception):
    """The reference walk came back into its own start node (the UXE-01 trap)."""


_REF_NEIGHBORS8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _reference_rdp(points, epsilon):
    """Verbatim copy of ``_rdp_simplify`` as of a1599e6."""
    if len(points) < 3:
        return list(points)
    pts = np.array(points, dtype=np.float32)
    start = pts[0]
    end = pts[-1]
    seg = end - start
    seg_len_sq = float(seg[0] ** 2 + seg[1] ** 2)
    if seg_len_sq == 0:
        dists = np.linalg.norm(pts - start, axis=1)
    else:
        t = np.clip(((pts - start) @ seg) / seg_len_sq, 0.0, 1.0)
        proj = start + np.outer(t, seg)
        dists = np.linalg.norm(pts - proj, axis=1)
    idx = int(np.argmax(dists))
    max_dist = float(dists[idx])
    if max_dist <= epsilon:
        return [points[0], points[-1]]
    left = _reference_rdp(points[: idx + 1], epsilon)
    right = _reference_rdp(points[idx:], epsilon)
    return left[:-1] + right


def _reference_vectorize(mask_1px: np.ndarray, simplify_epsilon: float) -> tuple:
    """Verbatim copy of vectorize_skeleton as of a1599e6, raising instead of looping.

    Self-contained (its helpers are copied too, the degree count is the hand-rolled
    ``_degree``), so its output is the golden value for every skeleton that never
    triggers the one new check, ``_WalkReturnedToStart``.
    """
    mask_1px = np.where(mask_1px.astype(np.uint8) > 0, 255, 0).astype(np.uint8)
    skel_bool = mask_1px > 0
    h, w = skel_bool.shape
    deg = _degree(skel_bool)
    node_mask = skel_bool & (deg != 2)
    ys, xs = np.where(node_mask)
    nodes = {(int(x), int(y)) for x, y in zip(xs, ys, strict=True)}
    endpoints = [(x, y) for (x, y) in nodes if int(deg[y, x]) == 1]
    junctions = [(x, y) for (x, y) in nodes if int(deg[y, x]) >= 3]
    visited_edges = set()

    def iter_neighbors(p):
        x, y = p
        for dy, dx in _REF_NEIGHBORS8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and skel_bool[ny, nx]:
                yield (nx, ny)

    def mark_edge(a, b):
        visited_edges.add((a, b))
        visited_edges.add((b, a))

    polylines = []
    for n in list(nodes):
        for nb in iter_neighbors(n):
            if (n, nb) in visited_edges:
                continue
            path = [n, nb]
            mark_edge(n, nb)
            prev = n
            cur = nb
            while True:
                if cur == n:
                    raise _WalkReturnedToStart(n)
                if cur in nodes and cur != n:
                    break
                nbs = [p for p in iter_neighbors(cur) if p != prev]
                if not nbs:
                    break
                nxt = nbs[0]
                path.append(nxt)
                mark_edge(cur, nxt)
                prev, cur = cur, nxt
            if simplify_epsilon > 0:
                path = _reference_rdp(path, simplify_epsilon)
            polylines.append(path)

    if not nodes:
        ys2, xs2 = np.where(skel_bool)
        remaining = {(int(x), int(y)) for x, y in zip(xs2, ys2, strict=True)}
        while remaining:
            start = next(iter(remaining))
            nbs = list(iter_neighbors(start))
            if not nbs:
                remaining.remove(start)
                continue
            prev = start
            cur = nbs[0]
            path = [start, cur]
            remaining.discard(start)
            remaining.discard(cur)
            while True:
                nbs2 = [p for p in iter_neighbors(cur) if p != prev]
                if not nbs2:
                    break
                nxt = nbs2[0]
                if nxt == start:
                    path.append(nxt)
                    break
                path.append(nxt)
                remaining.discard(nxt)
                prev, cur = cur, nxt
            if simplify_epsilon > 0:
                path = _reference_rdp(path, simplify_epsilon)
            polylines.append(path)

    return endpoints, junctions, polylines


def _reference_walk_returns_to_start(skel: np.ndarray) -> bool:
    try:
        _reference_vectorize(skel, 0.0)
    except _WalkReturnedToStart:
        return True
    return False


def _random_root_system(rng: np.random.Generator, h: int, w: int, n_roots: int, thick: int):
    """Wandering, branching, crossing polylines, skeletonized like the editor does."""
    m = np.zeros((h, w), np.uint8)
    for _ in range(n_roots):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        ang = rng.uniform(0, 2 * math.pi)
        pts = []
        for _ in range(int(rng.integers(5, 30))):
            ang += rng.normal(0, 0.4)
            x += 7 * math.cos(ang)
            y += 7 * math.sin(ang)
            pts.append((int(x), int(y)))
        cv2.polylines(m, [np.array(pts, np.int32).reshape(-1, 1, 2)], False, 255, thick)
    return _skeleton(m)


def _loop_free_cases() -> dict[str, np.ndarray]:
    cross = np.zeros((11, 11), np.uint8)
    cross[5, 1:10] = 255
    cross[1:10, 5] = 255
    ring = np.zeros((60, 60), np.uint8)
    cv2.circle(ring, (30, 30), 20, 255, 1)  # a node-less loop takes the other branch
    cases = {"cross": cross, "ring": _skeleton(ring), "empty": np.zeros((9, 9), np.uint8)}
    rng = np.random.default_rng(7)
    for i in range(60):
        thick = (1, 2, 3)[i % 3]
        cases[f"roots{i}"] = _random_root_system(rng, 160, 200, int(rng.integers(1, 12)), thick)
    return {k: v for k, v in cases.items() if not _reference_walk_returns_to_start(v)}


def test_degree_map_counts_every_8_neighbour_including_at_the_borders():
    from app.gui.skeleton_graph_model import _compute_degree_map

    field = np.random.default_rng(0).random((37, 53)) < 0.5  # every degree 0..8 occurs
    field[0, :] = field[:, 0] = field[-1, :] = field[:, -1] = True  # busy borders
    deg = _compute_degree_map(field)
    assert deg.dtype == np.uint8
    assert np.array_equal(deg, _degree(field))


@pytest.mark.parametrize("eps", [0.0, 1.0, 1.5])
def test_output_identical_to_reference_when_no_walk_returns_to_its_start(eps):
    cases = _loop_free_cases()
    assert len(cases) >= 40, "fixture should keep most random root systems"
    for key, skel in cases.items():
        endpoints, junctions, polylines = _reference_vectorize(skel, eps)
        topo = vectorize_skeleton(skel, simplify_epsilon=eps)
        assert topo.endpoints == endpoints, key
        assert topo.junctions == junctions, key
        assert topo.polylines == polylines, key


REAL_SKELETONS = Path("D:/2024 EF image data/skeletons")
REAL_SAMPLE = (
    "24EFCAM1_T001_L011_2024.07.18_111618_001_SMM_fake.png",
    "24EFCAM1_T001_L012_2024.07.18_111616_001_SMM_fake.png",
    "24EFCAM1_T001_L013_2024.07.18_111615_001_SMM_fake.png",
)


@pytest.mark.skipif(not REAL_SKELETONS.is_dir(), reason="real 2024 EF dataset not present")
def test_real_generated_skeletons_unchanged():
    from app.gui.skeleton_graph_model import SkeletonCorrectionModel

    checked = 0
    for name in REAL_SAMPLE:
        path = REAL_SKELETONS / name
        if not path.exists():
            continue
        gray = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
        model = SkeletonCorrectionModel()
        model.load_from_raster(gray, target_size=(640, 480))
        if _reference_walk_returns_to_start(model.mask):
            continue
        endpoints, junctions, polylines = _reference_vectorize(model.mask, 1.5)
        topo = vectorize_skeleton(model.mask, simplify_epsilon=1.5)
        assert (topo.endpoints, topo.junctions, topo.polylines) == (
            endpoints,
            junctions,
            polylines,
        ), name
        checked += 1
    if not checked:
        pytest.skip("no sample skeleton found")

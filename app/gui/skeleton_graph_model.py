"""
Skeleton correction model utilities.

This module intentionally lives in the *correction stream* and does NOT affect the
existing skeleton generation / overlay / measurement logic.

The editor keeps an internal binary skeleton mask (uint8 0/255) and provides:
- Raster import (threshold + optional inversion + resize) and skeletonization
- Polyline drawing + erasing on the raster mask
- Vectorization helpers (endpoints/junctions + polyline tracing) to support
  connect/snap UX
- Undo/redo, unsaved-change tracking and cached topology for the editor
- Saving corrected skeletons as `<base>_skeleton.png`
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from skimage.morphology import skeletonize

from app.editing.history import SnapshotHistory

logger = logging.getLogger(__name__)

Point = tuple[int, int]  # (x, y)


@dataclass(frozen=True)
class SkeletonTopology:
    """Derived topology information from a 1px skeleton mask."""

    endpoints: list[Point]
    junctions: list[Point]
    polylines: list[list[Point]]


def _neighbors8() -> list[tuple[int, int]]:
    return [
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    ]


def _ensure_uint8_binary(mask: np.ndarray) -> np.ndarray:
    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    if mask.ndim != 2:
        raise ValueError("Expected a 2D binary mask.")
    # Normalize to 0/255. uint8 scalars keep np.where from building an int64 temporary.
    return np.where(mask > 0, np.uint8(255), np.uint8(0))


def _skeletonize_uint8(mask: np.ndarray) -> np.ndarray:
    """Skeletonize a uint8 0/255 foreground mask to 1px (uint8 0/255)."""
    mask = _ensure_uint8_binary(mask)
    skel_bool = skeletonize(mask > 0)
    return skel_bool.astype(np.uint8) * 255


# 3x3 kernel that sums all 8 neighbors (center weight = 0) -- used by
# _compute_degree_map to count the 8-connected neighbor degree in one
# filter pass instead of eight array-slicing iterations (F-096 / Perf).
_NEIGHBOR_KERNEL = np.array(
    [[1, 1, 1], [1, 0, 1], [1, 1, 1]],
    dtype=np.uint8,
)


def _compute_degree_map(skel_bool: np.ndarray) -> np.ndarray:
    """Degree (# of 8-neighbors) for each skeleton pixel.

    One ``cv2.filter2D`` pass with a 3x3 all-ones kernel (center = 0) and a
    zero border. The kernel is symmetric, so this correlation equals the
    convolution used before (scipy.ndimage, 6-8x slower); sums are at most 8,
    so the uint8 output is exact.
    """
    return cv2.filter2D(
        skel_bool.astype(np.uint8), -1, _NEIGHBOR_KERNEL, borderType=cv2.BORDER_CONSTANT
    )


def _rdp_simplify(points: Sequence[Point], epsilon: float) -> list[Point]:
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(points) < 3:
        return list(points)

    pts = np.array(points, dtype=np.float32)
    start = pts[0]
    end = pts[-1]

    # Distance from point to line segment (start-end)
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

    left = _rdp_simplify(points[: idx + 1], epsilon)
    right = _rdp_simplify(points[idx:], epsilon)
    return left[:-1] + right


def vectorize_skeleton(mask_1px: np.ndarray, *, simplify_epsilon: float = 1.5) -> SkeletonTopology:
    """
    Vectorize a *1px* skeleton mask into polylines and derive endpoints/junctions.

    This is a best-effort topology extraction suitable for UI affordances (endpoint
    picking + connect). It is not intended to be a perfect graph extractor.

    Every polyline runs from one node (endpoint or junction) to the next. A loop
    attached to a single junction comes back as a closed polyline that starts and
    ends at that junction.
    """
    mask_1px = _ensure_uint8_binary(mask_1px)
    skel_bool = mask_1px > 0
    h, w = skel_bool.shape

    deg = _compute_degree_map(skel_bool)
    node_mask = skel_bool & (deg != 2)

    ys, xs = np.where(node_mask)
    nodes = {(int(x), int(y)) for x, y in zip(xs, ys, strict=True)}

    endpoints = [(x, y) for (x, y) in nodes if int(deg[y, x]) == 1]
    junctions = [(x, y) for (x, y) in nodes if int(deg[y, x]) >= 3]

    # Edge visitation (undirected) on pixel graph
    visited_edges = set()

    def iter_neighbors(p: Point) -> Iterable[Point]:
        x, y = p
        for dy, dx in _neighbors8():
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and skel_bool[ny, nx]:
                yield (nx, ny)

    def mark_edge(a: Point, b: Point) -> None:
        visited_edges.add((a, b))
        visited_edges.add((b, a))

    polylines: list[list[Point]] = []
    # No node-to-node chain can hold more points than the skeleton has pixels.
    max_path_len = int(skel_bool.sum()) + 1

    # Trace from each node along each unvisited neighbor direction
    for n in list(nodes):
        for nb in iter_neighbors(n):
            if (n, nb) in visited_edges:
                continue
            path = [n, nb]
            mark_edge(n, nb)
            prev = n
            cur = nb
            while True:
                # Stop at ANY node, the start node included: a loop hanging off a
                # single junction comes back to where it started and must close as
                # [n, ..., n] instead of going round forever (UXE-01).
                if cur in nodes:
                    break
                if len(path) > max_path_len:
                    logger.warning("Skeleton walk from %s exceeded %d points", n, max_path_len)
                    break
                nbs = [p for p in iter_neighbors(cur) if p != prev]
                if not nbs:
                    break
                nxt = nbs[0]
                path.append(nxt)
                mark_edge(cur, nxt)
                prev, cur = cur, nxt

            if simplify_epsilon > 0:
                path = _rdp_simplify(path, simplify_epsilon)
            polylines.append(path)

    # Handle loops with no nodes (all degree==2)
    if not nodes:
        ys2, xs2 = np.where(skel_bool)
        remaining = {(int(x), int(y)) for x, y in zip(xs2, ys2, strict=True)}
        while remaining:
            start = next(iter(remaining))
            # pick an arbitrary neighbor to start walking
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
                path = _rdp_simplify(path, simplify_epsilon)
            polylines.append(path)

    return SkeletonTopology(endpoints=endpoints, junctions=junctions, polylines=polylines)


class SkeletonCorrectionModel:
    """
    Holds the editable skeleton mask with undo/redo, unsaved-change tracking and
    cached topology.

    Representation:
    - `mask` is uint8 0/255 foreground mask at *editor resolution* (typically image size).

    Edit protocol: call `push_undo()` once at the start of a user action, then any
    edit methods (`erase_*`, `draw_polyline`, `clear`, `skeletonize`); the action
    undoes as one step. Every mutation moves `revision`, which keys the cached
    `topology()` / `endpoints()` and the unsaved-change check, so change the mask
    through these methods. Code that still writes `mask` in place must do it right
    after `push_undo()` (which moves the revision too) and before any query.
    """

    def __init__(self, max_stack_size: int = 25) -> None:
        self._mask: np.ndarray | None = None
        # F-019 / AR-11: bounded undo/redo of whole-mask snapshots.
        self._history: SnapshotHistory[np.ndarray] = SnapshotHistory(maxlen=max_stack_size)
        self._revision = 0
        # "Unsaved" means: differs from this baseline (the last load or save).
        self._saved_mask: np.ndarray | None = None
        self._saved_revision = 0
        # Derived data, dropped by every mutation (see _touch).
        self._topologies: dict[float, SkeletonTopology] = {}
        self._endpoints: list[Point] | None = None
        self._unsaved: bool | None = None

    # -------------------- state --------------------
    @property
    def mask(self) -> np.ndarray | None:
        """The editable uint8 0/255 skeleton, or None before an image is loaded."""
        return self._mask

    @mask.setter
    def mask(self, value: np.ndarray | None) -> None:
        # Replacing the array is an edit (push_undo() first to make it undoable).
        self._mask = value
        self._touch()

    @property
    def revision(self) -> int:
        """Changes whenever the mask may have changed; use it as a cache key."""
        return self._revision

    @property
    def max_stack_size(self) -> int:
        """Undo depth (number of user actions kept)."""
        return self._history.maxlen

    @property
    def can_undo(self) -> bool:
        """Whether undo() would restore an earlier state (drives the Undo button)."""
        return self._mask is not None and self._history.can_undo

    @property
    def can_redo(self) -> bool:
        """Whether redo() would restore an undone state (drives the Redo button)."""
        return self._mask is not None and self._history.can_redo

    @property
    def _undo_stack(self) -> deque[np.ndarray]:
        # Read-only legacy view (tests/test_regression.py checks its bound).
        return self._history.undo_stack

    @property
    def _redo_stack(self) -> deque[np.ndarray]:
        return self._history.redo_stack

    def has_unsaved_changes(self) -> bool:
        """True when the mask differs from the last loaded or saved one.

        An exact pixel comparison, memoized per revision: undoing back to the
        saved state, or erasing empty background, reads as clean.
        """
        if self._mask is None or self._revision == self._saved_revision:
            return False
        if self._unsaved is None:
            saved = self._saved_mask
            self._unsaved = not (
                saved is not None
                and saved.shape == self._mask.shape
                and np.array_equal(saved, self._mask)
            )
        return self._unsaved

    def mark_saved(self) -> None:
        """Take the current mask as the saved state (call after a successful save)."""
        self._saved_mask = None if self._mask is None else self._mask.copy()
        self._saved_revision = self._revision
        self._unsaved = False

    def _touch(self) -> None:
        """Record a (possible) mask change: new revision, derived data dropped."""
        self._revision += 1
        self._topologies.clear()
        self._endpoints = None
        self._unsaved = None

    # -------------------- undo/redo --------------------
    def push_undo(self) -> None:
        """Snapshot the mask before a user action (one undo step per action).

        It also moves the revision, because the caller is about to edit.
        """
        if self._mask is None:
            return
        self._history.push(self._mask.copy())
        self._touch()

    def undo(self) -> bool:
        """Restore the state before the last action; False when there is none."""
        if self._mask is None or not self._history.can_undo:
            return False
        # Park a copy, so no outside reference to the live mask can alter the redo state.
        self._mask = self._history.undo(self._mask.copy())
        self._touch()
        return True

    def redo(self) -> bool:
        """Re-apply the last undone action; False when there is none."""
        if self._mask is None or not self._history.can_redo:
            return False
        self._mask = self._history.redo(self._mask.copy())
        self._touch()
        return True

    def clear_history(self) -> None:
        self._history.clear()

    # -------------------- load / transform --------------------
    def set_empty(self, size: tuple[int, int]) -> None:
        w, h = size
        self._start_document(np.zeros((h, w), dtype=np.uint8))

    def load_from_raster(
        self,
        raster_gray: np.ndarray,
        *,
        target_size: tuple[int, int],
        threshold: str = "otsu",
    ) -> None:
        """Load skeleton from a grayscale raster image into `mask` at target_size."""
        if raster_gray.ndim != 2:
            raise ValueError("Expected a grayscale image.")

        img = raster_gray
        if threshold == "otsu":
            _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            _, binary = cv2.threshold(img, 50, 255, cv2.THRESH_BINARY)

        # Ensure skeleton is foreground (white) by inverting if background dominates.
        if float(np.mean(binary)) > 127.0:
            binary = 255 - binary

        w_t, h_t = target_size
        if (binary.shape[1], binary.shape[0]) != (w_t, h_t):
            binary = cv2.resize(binary, (w_t, h_t), interpolation=cv2.INTER_NEAREST)

        self._start_document(_skeletonize_uint8(binary))

    def _start_document(self, mask: np.ndarray) -> None:
        """Adopt a freshly loaded mask: empty history, nothing unsaved."""
        self._mask = mask
        self._history.clear()
        self._touch()
        self.mark_saved()

    # -------------------- edits --------------------
    def erase_circle(self, center: Point, radius: int) -> None:
        if self._mask is None:
            return
        x, y = center
        cv2.circle(self._mask, (int(x), int(y)), int(radius), 0, thickness=-1)
        self._touch()

    def erase_segment(self, p0: Point, p1: Point, radius: int) -> None:
        """Erase along one eraser drag step, from ``p0`` to ``p1`` (PE-02).

        Stamps the ``erase_circle`` disc on every pixel of the segment, so a fast
        drag erases exactly what a slow drag through the same pixels would: no
        fragments survive between far-apart mouse events, and nothing outside the
        eraser ring is touched. ``p0 == p1`` stamps a single disc.
        """
        if self._mask is None:
            return
        x0, y0 = int(p0[0]), int(p0[1])
        x1, y1 = int(p1[0]), int(p1[1])
        r = int(radius)
        n = max(abs(x1 - x0), abs(y1 - y0))
        for i in range(n + 1):
            t = i / n if n else 0.0
            center = (x0 + round((x1 - x0) * t), y0 + round((y1 - y0) * t))
            cv2.circle(self._mask, center, r, 0, thickness=-1)
        self._touch()

    def draw_polyline(self, points: Sequence[Point], thickness: int = 3) -> None:
        self._stroke_polyline(points, thickness, 255)

    def erase_polyline(self, points: Sequence[Point], thickness: int) -> None:
        """Erase a ``thickness``-px band along an open polyline (e.g. a branch being redrawn)."""
        self._stroke_polyline(points, thickness, 0)

    def _stroke_polyline(self, points: Sequence[Point], thickness: int, value: int) -> None:
        if self._mask is None or len(points) < 2:
            return
        pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(self._mask, [pts], isClosed=False, color=value, thickness=int(thickness))
        self._touch()

    def clear(self) -> None:
        """Erase every skeleton pixel (call push_undo() first to make it undoable)."""
        if self._mask is None:
            return
        self._mask.fill(0)
        self._touch()

    def skeletonize(self) -> None:
        if self._mask is None:
            return
        self._mask = _skeletonize_uint8(self._mask)
        self._touch()

    # -------------------- derived data --------------------
    def topology(self, *, simplify_epsilon: float = 1.5) -> SkeletonTopology:
        """Vectorized topology of the mask, cached until the next mutation.

        Repeated calls return the same object: treat it as read-only.
        """
        if self._mask is None:
            return SkeletonTopology(endpoints=[], junctions=[], polylines=[])
        key = float(simplify_epsilon)
        topo = self._topologies.get(key)
        if topo is None:
            topo = vectorize_skeleton(self._mask, simplify_epsilon=simplify_epsilon)
            self._topologies[key] = topo
        return topo

    def endpoints(self) -> list[Point]:
        """Skeleton tips (pixels with exactly one 8-neighbour) as (x, y), row-major.

        The same set as ``topology().endpoints``, from one filter pass instead of
        the full vectorization; cached until the next mutation (read-only list).
        """
        if self._mask is None:
            return []
        if self._endpoints is None:
            skel = _ensure_uint8_binary(self._mask) > 0
            ys, xs = np.nonzero(skel & (_compute_degree_map(skel) == 1))
            self._endpoints = list(zip(xs.tolist(), ys.tolist(), strict=True))
        return self._endpoints

    # -------------------- save --------------------
    def render_to_size(self, size: tuple[int, int]) -> np.ndarray:
        """
        Render current mask to a different size (uint8 0/255) using nearest neighbor.
        """
        if self._mask is None:
            w, h = size
            return np.zeros((h, w), dtype=np.uint8)
        w, h = size
        out = cv2.resize(self._mask, (w, h), interpolation=cv2.INTER_NEAREST)
        out = _skeletonize_uint8(out)
        return out

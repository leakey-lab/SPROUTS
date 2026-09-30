"""
Skeleton correction model utilities (Qt-free: numpy and cv2).

This module intentionally lives in the *correction stream* and does NOT affect the
existing skeleton generation / overlay / measurement logic.

The editor keeps an internal binary skeleton mask (uint8 0/255) and provides:
- Raster import (threshold + optional inversion + resize) and skeletonization
- Polyline drawing + erasing on the raster mask
- Vectorization helpers (endpoints/junctions + polyline tracing) to support
  connect/snap UX
- Undo/redo as changed-box patches, unsaved-change tracking and cached topology for
  the editor; the endpoints are recomputed only around the pixels that changed
- Saving corrected skeletons as `<base>_skeleton.png`
- Hooks for the Correct workspace: ``on_action`` (a new undoable action),
  ``drop_redo`` (one undo timeline across layers) and ``take_changed_box`` (the
  region a display must refresh)

scikit-image (about 0.2 s to import, most of it scipy) is imported on the first
skeletonize, or ahead of time by :func:`prewarm_skeletonize`, which ``MainWindow``
runs in the thread pool once the window shows; importing this module stays cheap.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from app.editing.history import SnapshotHistory
from app.gui.patch_history import Patch

logger = logging.getLogger(__name__)

Point = tuple[int, int]  # (x, y)
Box = tuple[int, int, int, int]  # (x, y, width, height) in mask pixels

_NO_CHANGE = (0, 0, 0, 0)  # an empty (x0, y0, x1, y1) box

UNDO_DEPTH = 100  # user actions kept (the default of SkeletonCorrectionModel)
UNDO_BYTE_BUDGET = 64 * 1024 * 1024  # stored patches; the oldest actions go first past it
_STEP_OVERHEAD = 64  # rough Python object cost of one history step, counted in nbytes
_COMPRESS_MIN = 1024  # patch sides from this size on are stored zlib-compressed

# Window skeletonize (SkeletonCorrectionModel._thin_window): thin the changed box +
# _ROI_PAD px, write back the changed box + _ROI_CORE px, provided the _ROI_BAND px band
# just outside that core lost no pixel.
_ROI_PAD = 16
_ROI_CORE = 8
_ROI_BAND = 2

# scikit-image's skeletonize, imported on first use (see prewarm_skeletonize).
_skimage_skeletonize: Callable[[np.ndarray], np.ndarray] | None = None


def _skeletonize_fn() -> Callable[[np.ndarray], np.ndarray]:
    """scikit-image's ``skeletonize``, imported (once) on first use."""
    global _skimage_skeletonize
    if _skimage_skeletonize is None:
        from skimage.morphology import skeletonize

        _skimage_skeletonize = skeletonize
    return _skimage_skeletonize


def prewarm_skeletonize() -> None:
    """Import scikit-image's skeletonize now, so the first skeleton edit does not wait.

    Idempotent: later calls return at once. Safe from a worker thread (Python's import
    lock makes a skeletonize on another thread wait for the import to finish).
    """
    _skeletonize_fn()


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
    skel_bool = _skeletonize_fn()(mask > 0)
    return skel_bool.astype(np.uint8) * 255


def _union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, ...]:
    """Union of two ``(x0, y0, x1, y1)`` boxes; an empty box adds nothing."""
    if a[2] <= a[0] or a[3] <= a[1]:
        return b
    if b[2] <= b[0] or b[3] <= b[1]:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _plain_uint8(a: np.ndarray) -> bool:
    """A 2D, C-contiguous uint8 array (what cv2 reads without a copy)."""
    return a.dtype == np.uint8 and a.ndim == 2 and a.flags.c_contiguous


def _equal_uint8(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two plain uint8 arrays of one shape are equal (one cv2 pass, no temporary:
    0.05 ms at 2048 x 1536 against 0.5 ms for ``np.array_equal``)."""
    return cv2.norm(a, b, cv2.NORM_INF) == 0


def _same_layout(a: np.ndarray | None, b: np.ndarray) -> bool:
    """``a`` is a 2D array of ``b``'s shape and dtype (a patch of one fits the other)."""
    return a is not None and a.ndim == 2 and a.shape == b.shape and a.dtype == b.dtype


def _patch_edges(patch: Patch) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` of a patch (an empty box for an empty patch)."""
    return (patch.x, patch.y, patch.x + patch.width, patch.y + patch.height)


def _capture(x: int, y: int, before: np.ndarray, after: np.ndarray) -> Patch:
    """A patch of the skeleton history: from 1 KiB on, zlib-compressed (thin lines on a
    blank frame compress about 20x)."""
    return Patch.capture(x, y, before, after, compress_min=_COMPRESS_MIN)


def _widened(
    patch: Patch, base: np.ndarray, mask: np.ndarray, box: tuple[int, ...], *, keep_before: bool
) -> Patch:
    """``patch`` grown to cover ``box`` too, where ``mask`` left ``base`` without an action.

    The side next to "now" (after for the newest undo step, ``keep_before``; before for
    the next redo step) becomes the mask; the other side keeps the patch's values, and
    is the base elsewhere (the base is the step's side next to now, see ``_record``).
    """
    x0, y0, x1, y1 = _union(box, _patch_edges(patch))
    region = (slice(y0, y1), slice(x0, x1))
    kept = base[region].copy()
    if not patch.is_empty:
        rows = slice(patch.y - y0, patch.y - y0 + patch.height)
        cols = slice(patch.x - x0, patch.x - x0 + patch.width)
        kept[rows, cols] = patch.before() if keep_before else patch.after()
    now = mask[region]
    return _capture(x0, y0, kept, now) if keep_before else _capture(x0, y0, now, kept)


def _state(step: _Step, base: np.ndarray, *, back: bool) -> np.ndarray:
    """The whole state before (``back``) or after ``step``, whose other state is ``base``."""
    if step.whole is not None:
        return step.whole[0 if back else 1]
    state = base.copy()
    patch = step.patch
    if not patch.is_empty:
        state[patch.slices] = patch.before() if back else patch.after()
    return state


class _Step:
    """One undoable action of the skeleton model: the box it changed, before and after.

    A step is *open* from ``push_undo()`` until the next history call records it by
    comparing the mask with the state the action started from (the model's base), so
    edits of any kind, writes in place included, are recorded. ``patch`` is the changed
    box (empty when nothing changed); ``whole`` holds the two whole arrays instead when
    the mask was replaced by one of another shape or dtype. ``fixed`` says whether the
    states before and after the action are known skeletonize fixed points.
    """

    __slots__ = ("fixed", "opened", "patch", "whole")

    def __init__(self, fixed_before: bool, opened: int) -> None:
        self.patch: Patch | None = None
        self.whole: tuple[np.ndarray, np.ndarray] | None = None
        self.fixed = (fixed_before, False)
        self.opened = opened  # the model's revision when the action started

    @property
    def nbytes(self) -> int:
        """Stored size: the patch (compressed where it was) or the two whole arrays."""
        if self.whole is not None:
            return self.whole[0].nbytes + self.whole[1].nbytes + _STEP_OVERHEAD
        return _STEP_OVERHEAD + (0 if self.patch is None else self.patch.nbytes)


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


def _tip_indices(mask: np.ndarray, box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Flat indices ``y * w + x`` of the tips (set pixels with exactly one set 8-neighbour)
    inside ``box`` ``(x0, y0, x1, y1)`` (None: the whole mask), ascending.

    Reads the box plus a one-pixel ring (outside the mask counts as background), so the
    degrees inside the box equal those of a whole-frame :func:`_compute_degree_map`.
    Foreground is what ``_ensure_uint8_binary`` keeps: non-zero after a uint8 cast.
    """
    h, w = mask.shape
    x0, y0, x1, y1 = (0, 0, w, h) if box is None else box
    wx0, wy0, wx1, wy1 = max(0, x0 - 1), max(0, y0 - 1), min(w, x1 + 1), min(h, y1 + 1)
    window = mask[wy0:wy1, wx0:wx1]
    on = np.greater(window if window.dtype == np.uint8 else window.astype(np.uint8), 0)
    degree = cv2.filter2D(on.view(np.uint8), -1, _NEIGHBOR_KERNEL, borderType=cv2.BORDER_CONSTANT)
    tips = np.logical_and(on, degree == 1)
    if box is None:
        return np.flatnonzero(tips)
    core = tips[y0 - wy0 : y1 - wy0, x0 - wx0 : x1 - wx0]
    rows, cols = np.divmod(np.flatnonzero(core), x1 - x0)
    return (rows + y0) * w + (cols + x0)


def skeleton_tips(mask: np.ndarray) -> np.ndarray:
    """Flat indices ``y * w + x`` of the tips of a 2D skeleton ``mask``, ascending, as
    :meth:`SkeletonCorrectionModel.endpoint_array` finds them; any thread."""
    if mask.ndim != 2:
        raise ValueError("Expected a 2D binary mask.")
    return _tip_indices(mask)


def thin_raster(
    raster_gray: np.ndarray, target_size: tuple[int, int], threshold: str = "otsu"
) -> np.ndarray:
    """A grayscale skeleton raster at ``target_size``, 1 px thin (uint8 0/255); any thread.

    A raster of another size (the generated skeleton is ``SKELETON_OUTPUT_SIZE``) is
    upscaled bilinearly *before* thresholding, so thin lines come out as smooth,
    connected 1 px lines of the right length instead of a nearest-neighbour
    staircase. A raster already at ``target_size`` (a correction saved at image
    resolution) is only thresholded and thinned, so it reopens unchanged.
    """
    if raster_gray.ndim != 2:
        raise ValueError("Expected a grayscale image.")

    img = raster_gray
    w_t, h_t = target_size
    if (img.shape[1], img.shape[0]) != (w_t, h_t):
        img = cv2.resize(img, (w_t, h_t), interpolation=cv2.INTER_LINEAR)
    if threshold == "otsu":
        _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(img, 50, 255, cv2.THRESH_BINARY)

    # Ensure skeleton is foreground (white) by inverting if background dominates.
    if float(np.mean(binary)) > 127.0:
        binary = 255 - binary
    return _skeletonize_uint8(binary)


def _patch_tips(tips: np.ndarray, mask: np.ndarray, dirty: tuple[int, ...]) -> np.ndarray:
    """``tips`` (ascending flat indices) with those within one pixel of ``dirty``
    ``(x0, y0, x1, y1)`` recomputed from ``mask``: a pixel's degree reads its 3x3
    neighbourhood, so nothing farther can have changed."""
    h, w = mask.shape
    x0, y0 = max(0, dirty[0] - 1), max(0, dirty[1] - 1)
    x1, y1 = min(w, dirty[2] + 1), min(h, dirty[3] + 1)
    rows, cols = np.divmod(tips, w)
    outside = (cols < x0) | (cols >= x1) | (rows < y0) | (rows >= y1)
    merged = np.concatenate((tips[outside], _tip_indices(mask, (x0, y0, x1, y1))))
    merged.sort(kind="stable")
    return merged


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
    after `push_undo()` (which moves the revision too) and before any query, or assign
    the array back through the `mask` setter; `take_changed_box()` sees only the edit
    methods, undo / redo and the setter.

    History (S1): an action is stored as the box of pixels it changed, before and after
    (:class:`_Step`), found by comparing the mask with a copy of the state the action
    started from (the *base*, one frame). Undo and redo write that box back in place, so
    they cost the box, not the frame, and a depth of 100 actions costs about the base plus
    the boxes (25 whole snapshots were 75 MiB at 2048 x 1536). The results are the states
    a whole-snapshot history restores, bit for bit, edits without ``push_undo()``
    included (tests/test_skeleton_patch_history.py).

    `on_action` (default None) is called at the end of every `push_undo()` that pushed
    a step: the Correct workspace records the action in its undo journal there.
    """

    def __init__(self, max_stack_size: int | None = None) -> None:
        self._mask: np.ndarray | None = None
        # F-019 / AR-11 / S1: bounded undo/redo of changed-box patches (see _Step).
        self._history: SnapshotHistory[_Step] = SnapshotHistory(
            maxlen=UNDO_DEPTH if max_stack_size is None else max_stack_size,
            byte_budget=UNDO_BYTE_BUDGET,
            sizeof=lambda step: step.nbytes,
        )
        self._base: np.ndarray | None = None  # the mask at the last history call
        self._open: _Step | None = None  # the action since push_undo(), not yet recorded
        self._revision = 0
        # "Unsaved" means: differs from this baseline (the last load or save).
        self._saved_mask: np.ndarray | None = None
        self._saved_revision = 0
        # Derived data, dropped by every mutation (see _touch).
        self._topologies: dict[float, SkeletonTopology] = {}
        self._endpoints: list[Point] | None = None
        self._unsaved: bool | None = None
        # Pixels that may have changed since take_changed_box() (x0, y0, x1, y1);
        # None: unknown (the whole mask may have changed).
        self._changed: tuple[int, ...] | None = None
        # Endpoints (S2): flat indices y * w + x of the tips, ascending, as of revision
        # _tips_revision, and the box that may have changed since (None: unknown).
        self._tips: np.ndarray | None = None
        self._tips_revision = -1
        self._tips_shape: tuple[int, ...] = ()
        self._tips_dirty: tuple[int, ...] | None = None
        self.on_action: Callable[[], None] | None = None
        # Window skeletonize (_thin_window) needs a known fixed point of skeletonize:
        # whether the mask is one (the open step says whether its base is one).
        self._fixed_point = False
        self._diff: np.ndarray | None = None  # scratch: pixels changed since the base

    # -------------------- state --------------------
    @property
    def mask(self) -> np.ndarray | None:
        """The editable uint8 0/255 skeleton, or None before an image is loaded."""
        return self._mask

    @mask.setter
    def mask(self, value: np.ndarray | None) -> None:
        # Replacing the array is an edit (push_undo() first to make it undoable).
        self._mask = value
        self._changed = self._tips_dirty = None
        self._fixed_point = False
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
    def _undo_stack(self) -> deque[_Step]:
        # Read-only legacy view (tests/test_regression.py checks its bound).
        return self._history.undo_stack

    @property
    def _redo_stack(self) -> deque[_Step]:
        return self._history.redo_stack

    @property
    def history_nbytes(self) -> int:
        """Memory the undo history holds: its patches plus the base copy (one frame)."""
        base = 0 if self._base is None else self._base.nbytes
        return self._history.nbytes + base

    def has_unsaved_changes(self) -> bool:
        """True when the mask differs from the last loaded or saved one.

        An exact pixel comparison, memoized per revision: undoing back to the
        saved state, or erasing empty background, reads as clean.
        """
        if self._mask is None or self._revision == self._saved_revision:
            return False
        if self._unsaved is None:
            saved, mask = self._saved_mask, self._mask
            if saved is None or saved.shape != mask.shape:
                self._unsaved = True
            elif _plain_uint8(saved) and _plain_uint8(mask):
                self._unsaved = not _equal_uint8(saved, mask)
            else:
                self._unsaved = not np.array_equal(saved, mask)
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

    def _note_change(self, x0: int, y0: int, x1: int, y1: int, *, display: bool = True) -> None:
        """Pixels in ``[x0, x1) x [y0, y1)`` may have changed (clipped to the mask).

        The box goes to the endpoint cache and, with ``display``, to take_changed_box().
        """
        if self._mask is None:
            return
        h, w = self._mask.shape[:2]
        box = (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))
        if self._tips_dirty is not None:
            self._tips_dirty = _union(self._tips_dirty, box)
        if display and self._changed is not None:
            self._changed = _union(self._changed, box)

    def take_changed_box(self) -> Box | None:
        """The ``(x, y, w, h)`` union of the pixels that may have changed since the last call.

        ``(0, 0, 0, 0)`` when nothing changed; ``None`` when unknown: after a load,
        ``set_empty``, the ``mask`` setter, a full-frame skeletonize, or an undo / redo
        that replaced the array (a mask of another shape). Undo and redo otherwise report
        the box they wrote back. Each call starts a new interval. Its only caller is the
        Correct skeleton layer's item update: a second caller would take boxes the item
        then never sees.
        """
        box, self._changed = self._changed, _NO_CHANGE
        if box is None:
            return None
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            return (0, 0, 0, 0)
        return (x0, y0, x1 - x0, y1 - y0)

    # -------------------- undo/redo --------------------
    def push_undo(self) -> None:
        """Start a user action (one undo step per action): the next history call records it.

        The previous action is recorded now. It also moves the revision, because the caller
        is about to edit, and then calls ``on_action`` (nothing happens without a mask).
        """
        if self._mask is None:
            return
        self._record()
        # The caller may now write the mask in place: it is thin again only once thinned.
        fixed, self._fixed_point = self._fixed_point, False
        self._touch()
        self._open = _Step(fixed, self._revision)
        self._history.push(self._open)
        if self.on_action is not None:
            self.on_action()

    def undo(self) -> bool:
        """Restore the state before the last action; False when there is none."""
        if self._mask is None or not self._history.can_undo:
            return False
        self._record()
        step = self._history.undo_stack[-1]
        self._swap(step, back=True)
        self._history.undo(step)  # the same step moves onto the redo stack
        self._touch()
        return True

    def redo(self) -> bool:
        """Re-apply the last undone action; False when there is none."""
        if self._mask is None or not self._history.can_redo:
            return False
        self._record()
        step = self._history.redo_stack[-1]  # never open: push_undo() ends the redo branch
        self._swap(step, back=False)
        self._history.redo(step)
        self._touch()
        return True

    def drop_redo(self) -> None:
        """Forget the redo branch (a newer action elsewhere ended it); the mask stays."""
        self._history.drop_redo()

    def clear_history(self) -> None:
        """Forget every undo and redo step (the mask stays as it is) and the base copy."""
        self._history.clear()
        self._open = self._base = None

    def _record(self) -> None:
        """Bring the base up to the mask before a history call.

        With an open action, the box where the mask differs from the base is its patch
        (before = base, after = mask). Otherwise that box holds edits made without
        ``push_undo()`` since the last history call: the states next to "now" take them
        (the newest undo step's after, the next redo step's before), as the whole
        snapshots a snapshot history parks at each call hold them. Invariant between
        calls: the base is the newest undo step's after and the next redo step's before.
        """
        mask, base, step = self._mask, self._base, self._open
        self._open = None
        if step is None:
            self._take_gap()
            return
        step.fixed = (step.fixed[0], self._fixed_point)
        if not _same_layout(base, mask):  # the array was replaced by another shape
            step.whole = (base, mask.copy())
            self._base = mask.copy()
            self._tips_dirty = None
            return
        box = self._box_of_changes(base)
        if box is None:
            step.patch = Patch.capture(0, 0, mask[:0, :0], mask[:0, :0])
            return
        region = (slice(box[1], box[3]), slice(box[0], box[2]))
        step.patch = _capture(box[0], box[1], base[region], mask[region])
        base[region] = mask[region]
        if self._tips_revision < step.opened:  # endpoints older than the action may have
            self._note_change(*box, display=False)  # missed its writes in place

    def _take_gap(self) -> None:
        """Edits since the last history call, no action open: see :meth:`_record`."""
        mask, base = self._mask, self._base
        undo, redo = self._history.undo_stack, self._history.redo_stack
        below, above = (undo[-1] if undo else None), (redo[-1] if redo else None)
        steps = [s for s in (below, above) if s is not None]
        if not steps:  # nothing to undo past: the base alone catches up
            if not _same_layout(base, mask):
                self._base = mask.copy()
            elif (box := self._box_of_changes(base)) is not None:
                region = (slice(box[1], box[3]), slice(box[0], box[2]))
                base[region] = mask[region]
            return
        fixed = self._fixed_point
        if _same_layout(base, mask) and all(s.whole is None for s in steps):
            box = self._box_of_changes(base)
            if box is None:
                return
            if below is not None:
                below.patch = _widened(below.patch, base, mask, box, keep_before=True)
                below.fixed = (below.fixed[0], fixed)
            if above is not None:
                above.patch = _widened(above.patch, base, mask, box, keep_before=False)
                above.fixed = (fixed, above.fixed[1])
            region = (slice(box[1], box[3]), slice(box[0], box[2]))
            base[region] = mask[region]
            return
        # The array was replaced by another shape: the neighbours keep whole arrays.
        if below is not None:
            below.whole, below.patch = (_state(below, base, back=True), mask.copy()), None
            below.fixed = (below.fixed[0], fixed)
        if above is not None:
            above.whole, above.patch = (mask.copy(), _state(above, base, back=False)), None
            above.fixed = (fixed, above.fixed[1])
        self._base = mask.copy()
        self._tips_dirty = None

    def _swap(self, step: _Step, *, back: bool) -> None:
        """Make the mask the state before (``back``) or after ``step``, in place when the
        step is a patch (the base follows); its box goes to the display and the endpoints.
        Called right after :meth:`_record`, so the mask is the step's other state."""
        mask, base = self._mask, self._base
        now_fixed, to_fixed = self._fixed_point, step.fixed[0 if back else 1]
        step.fixed = (to_fixed, now_fixed) if back else (now_fixed, to_fixed)
        self._fixed_point = to_fixed
        patch = step.patch
        if patch is not None and _same_layout(base, mask):
            if not patch.is_empty:
                values = patch.before() if back else patch.after()
                mask[patch.slices] = values
                base[patch.slices] = values
                self._note_change(*_patch_edges(patch))
            return
        # The array itself changes (a mask of another shape or dtype): whole arrays.
        target = _state(step, base, back=back)
        self._mask, self._base = target.copy(), target.copy()
        self._changed = self._tips_dirty = None

    def _box_of_changes(self, base: np.ndarray) -> tuple[int, int, int, int] | None:
        """``(x0, y0, x1, y1)`` around the pixels where the mask differs from ``base``
        (same shape and dtype); None when there are none."""
        mask = self._mask
        if _plain_uint8(mask) and _plain_uint8(base):
            if _equal_uint8(base, mask):  # the usual case at a history call: 5x cheaper
                return None
            x, y, w, h = cv2.boundingRect(cv2.compare(base, mask, cv2.CMP_NE, dst=self._scratch()))
            return None if w == 0 or h == 0 else (x, y, x + w, y + h)
        changed = base != mask
        rows, cols = np.flatnonzero(changed.any(axis=1)), np.flatnonzero(changed.any(axis=0))
        if not rows.size:
            return None
        return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1

    def _scratch(self) -> np.ndarray:
        """A uint8 array of the mask's shape for comparisons (reused)."""
        diff = self._diff
        if diff is None or diff.shape != self._mask.shape:
            diff = self._diff = np.empty(self._mask.shape, np.uint8)
        return diff

    # -------------------- load / transform --------------------
    def set_empty(self, size: tuple[int, int]) -> None:
        """Start a new, empty document of ``size`` ``(w, h)``: no skeleton, no history."""
        w, h = size
        self._start_document(np.zeros((h, w), dtype=np.uint8))

    def load_from_raster(
        self,
        raster_gray: np.ndarray,
        *,
        target_size: tuple[int, int],
        threshold: str = "otsu",
        saved: bool = True,
    ) -> None:
        """Load a skeleton from a grayscale raster into ``mask`` at ``target_size``, 1 px thin
        (:func:`thin_raster`); the history is emptied.

        ``saved=False`` loads it as an unsaved edit (the Load button): the saved baseline
        stays the last opened or saved skeleton, so the loaded one is unsaved while it
        differs from it.
        """
        self._start_document(thin_raster(raster_gray, target_size, threshold), saved=saved)

    def load_thinned(self, mask: np.ndarray, tips: np.ndarray | None = None) -> None:
        """Adopt ``mask``, a :func:`thin_raster` result made ahead (on a worker thread), as
        :meth:`load_from_raster` would; ``tips`` (:func:`skeleton_tips` of it) seed the
        endpoint cache."""
        self._start_document(mask)
        if tips is not None:
            self._tips, self._tips_shape = tips, mask.shape
            self._tips_revision, self._tips_dirty = self._revision, _NO_CHANGE

    def _start_document(self, mask: np.ndarray, *, saved: bool = True) -> None:
        """Adopt a freshly loaded, thinned (or empty) mask: empty history; nothing unsaved,
        unless ``saved`` is False (the saved baseline is kept, see load_from_raster)."""
        self._mask = mask
        self.clear_history()
        self._fixed_point = True
        self._changed = self._tips_dirty = None
        self._touch()
        if saved:
            self.mark_saved()

    # -------------------- edits --------------------
    def erase_circle(self, center: Point, radius: int) -> None:
        """Erase the filled disc of ``radius`` px around ``center`` ``(x, y)``."""
        if self._mask is None:
            return
        x, y, r = int(center[0]), int(center[1]), int(radius)
        cv2.circle(self._mask, (x, y), r, 0, thickness=-1)
        self._note_change(x - r - 1, y - r - 1, x + r + 2, y + r + 2)
        self._fixed_point = False
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
        self._note_change(
            min(x0, x1) - r - 1, min(y0, y1) - r - 1, max(x0, x1) + r + 2, max(y0, y1) + r + 2
        )
        self._fixed_point = False
        self._touch()

    def draw_polyline(self, points: Sequence[Point], thickness: int = 3) -> None:
        """Draw an open polyline through ``points`` ``(x, y)``, ``thickness`` px wide."""
        self._stroke_polyline(points, thickness, 255)

    def erase_polyline(self, points: Sequence[Point], thickness: int) -> None:
        """Erase a ``thickness``-px band along an open polyline (e.g. a branch being redrawn)."""
        self._stroke_polyline(points, thickness, 0)

    def _stroke_polyline(self, points: Sequence[Point], thickness: int, value: int) -> None:
        if self._mask is None or len(points) < 2:
            return
        pts = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(self._mask, [pts], isClosed=False, color=value, thickness=int(thickness))
        # A thick cv2 line reaches thickness / 2 (+1 for its round caps) past its points.
        pad = int(thickness) // 2 + 2
        lo, hi = pts.min(axis=(0, 1)), pts.max(axis=(0, 1))
        self._note_change(lo[0] - pad, lo[1] - pad, hi[0] + pad + 1, hi[1] + pad + 1)
        self._fixed_point = False
        self._touch()

    def clear(self) -> None:
        """Erase every skeleton pixel (call push_undo() first to make it undoable)."""
        if self._mask is None:
            return
        self._note_change(*self._foreground_bounds())
        self._mask.fill(0)
        self._fixed_point = True  # nothing left to thin
        self._touch()

    def _foreground_bounds(self) -> tuple[int, int, int, int]:
        """``(x0, y0, x1, y1)`` around every set pixel (the whole mask if it cannot tell)."""
        mask = self._mask
        h, w = mask.shape[:2]
        if not _plain_uint8(mask):
            return (0, 0, w, h)
        x, y, bw, bh = cv2.boundingRect(mask)
        return (x, y, x + bw, y + bh)

    def skeletonize(self) -> None:
        """Thin the mask to a 1 px skeleton (scikit-image's Zhang-Suen thinning).

        The result is always the whole frame's thinning, bit for bit. When the mask was
        edited from a state known to be thin already (every tool action starts with
        ``push_undo()``, whose base is that state), only a window around the changed
        pixels is thinned, in place (:meth:`_thin_window`): about 0.1 ms instead of
        1-2 ms at 640x480, and under 1 ms instead of 20-30 ms on a dense 2048x1536
        skeleton.
        """
        if self._mask is None:
            return
        box = self._thin_window()
        if box is None:
            self._mask = _skeletonize_uint8(self._mask)
            self._changed = self._tips_dirty = None
        else:
            self._note_change(*box)
        self._fixed_point = True
        self._touch()

    def _fixed_snapshot(self) -> np.ndarray | None:
        """The base of the open action when it is a known skeletonize fixed point."""
        step, base, mask = self._open, self._base, self._mask
        if step is None or not step.fixed[0] or not _plain_uint8(mask):
            return None
        return base if _same_layout(base, mask) and _plain_uint8(base) else None

    def _thin_window(self) -> tuple[int, ...] | None:
        """Thin only around the pixels changed since a known fixed point, in place.

        Returns the ``(x0, y0, x1, y1)`` box of the pixels it changed (empty when none),
        or None when the whole frame must be thinned instead: no action is open from a
        state known to be thin, or the window run deleted a pixel of the quiet band.

        Why this equals thinning the whole frame. The base S is a fixed point
        (thinning it deletes nothing) and the mask differs from S only inside the box B
        (found by comparing the two, so writes made in place are seen too).
        scikit-image's Zhang-Suen thinning only deletes pixels, decides each one from
        its 3x3 neighbourhood as the sub-iteration began, and treats pixels outside the
        array as background. The window W = B + 16 px is thinned on its own; the core
        C = B + 8 px is written back only if the 2 px band Q just outside C lost no
        pixel. By induction over sub-iterations: pixels of C see only C and the inner
        ring of Q, the same in both runs, so they get the same decisions; the inner
        ring of Q gets the same decisions (none); the outer ring and everything beyond
        see only pixels of S, a fixed-point neighbourhood, so nothing outside C changes
        in the full run either; and the window's zero border can reach C only through
        Q, which the check rules out.
        """
        base = self._fixed_snapshot()
        if base is None:
            return None
        mask = self._mask
        changed = self._box_of_changes(base)
        if changed is None:
            return _NO_CHANGE  # nothing changed since the fixed point: already thin
        x, y, w, h = changed[0], changed[1], changed[2] - changed[0], changed[3] - changed[1]
        rows, cols = mask.shape
        # Window W, core C and the outer edge of the band Q, in image pixels.
        wx0, wy0 = max(0, x - _ROI_PAD), max(0, y - _ROI_PAD)
        wx1, wy1 = min(cols, x + w + _ROI_PAD), min(rows, y + h + _ROI_PAD)
        cx0, cy0 = max(0, x - _ROI_CORE), max(0, y - _ROI_CORE)
        cx1, cy1 = min(cols, x + w + _ROI_CORE), min(rows, y + h + _ROI_CORE)
        qx0, qy0 = max(0, cx0 - _ROI_BAND), max(0, cy0 - _ROI_BAND)
        qx1, qy1 = min(cols, cx1 + _ROI_BAND), min(rows, cy1 + _ROI_BAND)
        before = mask[wy0:wy1, wx0:wx1]
        after = _skeletonize_uint8(before)
        # Q as four strips around C: top, bottom, left, right (rows, then columns).
        for r0, r1, c0, c1 in (
            (qy0, cy0, qx0, qx1),
            (cy1, qy1, qx0, qx1),
            (cy0, cy1, qx0, cx0),
            (cy0, cy1, cx1, qx1),
        ):
            if r1 > r0 and c1 > c0:
                rows_w, cols_w = slice(r0 - wy0, r1 - wy0), slice(c0 - wx0, c1 - wx0)
                if np.any((before[rows_w, cols_w] != 0) & (after[rows_w, cols_w] == 0)):
                    return None
        core = (slice(cy0 - wy0, cy1 - wy0), slice(cx0 - wx0, cx1 - wx0))
        dx, dy, dw, dh = cv2.boundingRect(cv2.compare(before[core], after[core], cv2.CMP_NE))
        before[core] = after[core]  # `before` is a view: this writes the mask
        if dw == 0 or dh == 0:
            return _NO_CHANGE
        return (cx0 + dx, cy0 + dy, cx0 + dx + dw, cy0 + dy + dh)

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
            xy = self.endpoint_array()
            self._endpoints = list(zip(xy[:, 0].tolist(), xy[:, 1].tolist(), strict=True))
        return self._endpoints

    def endpoint_array(self) -> np.ndarray:
        """The tips of :meth:`endpoints` as an ``(n, 2)`` int64 array of ``(x, y)``, row-major.

        Windowed (S2): after an edit, only the tips within one pixel of the pixels that
        changed are recomputed (a tip depends on its 3x3 neighbourhood), from the boxes
        the edits, undo and redo report plus, while an action is open, its difference to
        the base (writes in place); anything unknown recomputes the whole frame. The set
        and its order always equal a whole-frame computation.
        """
        mask = self._mask
        if mask is None:
            return np.zeros((0, 2), np.int64)
        if mask.ndim != 2:
            raise ValueError("Expected a 2D binary mask.")
        if self._tips is None or self._tips_revision != self._revision:
            self._tips = self._fresh_tips()
            self._tips_revision = self._revision
            self._tips_dirty = _NO_CHANGE
        ys, xs = np.divmod(self._tips, mask.shape[1])
        return np.stack((xs, ys), axis=1)

    def _fresh_tips(self) -> np.ndarray:
        """The tip indices of the mask, patched from the cached ones where it can."""
        mask, tips, dirty = self._mask, self._tips, self._tips_dirty
        step, base = self._open, self._base
        if dirty is not None and step is not None and _same_layout(base, mask):
            dirty = _union(dirty, self._box_of_changes(base) or _NO_CHANGE)  # in place
        if tips is None or dirty is None or self._tips_shape != mask.shape:
            self._tips_shape = mask.shape
            return _tip_indices(mask)
        if dirty[2] <= dirty[0] or dirty[3] <= dirty[1]:
            return tips
        return _patch_tips(tips, mask, dirty)

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

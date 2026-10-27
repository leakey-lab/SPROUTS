"""Vector links of a 1 px skeleton raster, for drawing it magnified (Qt-free: numpy only).

:func:`skeleton_links` gives the unit m-adjacency links between set pixels;
:func:`tile_segments` merges the links of one box into collinear runs, the segments the
skeleton layer item (:mod:`app.gui.canvas_layers.skeleton`) draws per 256 px tile. Both only
read the mask, the box plus a one-pixel margin.
"""

from __future__ import annotations

import numpy as np

Box = tuple[int, int, int, int]  # (x, y, width, height) in skeleton pixels

# Forward link directions (dx, dy): each undirected 8-neighbour pair once, from the pixel
# met first in row-major order (E, S, SE, SW).
_FORWARD = ((1, 0), (0, 1), (1, 1), (-1, 1))
_NO_LINKS = np.zeros((0, 4), np.int32)


def skeleton_links(mask: np.ndarray, box: Box | None = None) -> np.ndarray:
    """Unit m-adjacency links of the 1 px skeleton ``mask`` whose first pixel is in ``box``.

    Rows are ``(x0, y0, x1, y1)`` int32 pixel coordinates. A link joins two 8-neighbours
    that are set; a diagonal one only when neither pixel of its 2x2 corner is set
    (m-adjacency: no corner triangles). Its first pixel is the one met first in row-major
    order, so a mask split into boxes yields every link exactly once. An isolated pixel
    is a dot ``(x, y, x, y)``. ``box`` is ``(x, y, width, height)``; None = the whole
    mask. ``mask`` is only read.
    """
    x0, y0, x1, y1 = clip_box(box, mask.shape)
    if x1 <= x0 or y1 <= y0 or not mask[y0:y1, x0:x1].any():
        return _NO_LINKS.copy()
    *links, lone = _link_planes(mask, x0, y0, x1, y1)
    parts = []
    for (dx, dy), plane in zip(_FORWARD, links, strict=True):
        xs, ys = _coords(plane)
        parts.append(np.stack([xs, ys, xs + dx, ys + dy], axis=1))
    xs, ys = _coords(lone)
    parts.append(np.stack([xs, ys, xs, ys], axis=1))
    return _offset(np.concatenate(parts), x0, y0)


def tile_segments(mask: np.ndarray, box: Box) -> np.ndarray:
    """The links of ``box`` (see :func:`skeleton_links`) with collinear runs merged.

    Chained links of one direction become one ``(x0, y0, x1, y1)`` int32 run from the
    first pixel of its first link to the last pixel of its last link; runs never leave
    the box (a line crossing its edge is split there, the two runs sharing the pixel).
    Dots stay dots. Vectorised: no Python loop over pixels.
    """
    x0, y0, x1, y1 = clip_box(box, mask.shape)
    if x1 <= x0 or y1 <= y0 or not mask[y0:y1, x0:x1].any():
        return _NO_LINKS.copy()
    east, south, south_east, south_west, lone = _link_planes(mask, x0, y0, x1, y1)
    xs, ys = _coords(lone)
    parts = [
        _east_runs(east),
        _south_runs(south),
        _diagonal_runs(south_east, 1),
        _diagonal_runs(south_west, -1),
        np.stack([xs, ys, xs, ys], axis=1),
    ]
    return _offset(np.concatenate(parts), x0, y0)


def clip_box(box: Box | None, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` of ``box`` clipped to a mask of ``shape`` (end exclusive)."""
    h, w = shape[0], shape[1]
    if box is None:
        return 0, 0, w, h
    x, y, bw, bh = (int(v) for v in box)
    return max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)


def _offset(links: np.ndarray, x0: int, y0: int) -> np.ndarray:
    out = links.astype(np.int32, copy=False)
    out[:, 0::2] += x0
    out[:, 1::2] += y0
    return out


def _link_planes(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> tuple[np.ndarray, ...]:
    """Boolean planes over the box: the E, S, SE and SW links starting at each pixel, and
    the isolated pixels. Reads the box plus a one-pixel margin (outside the mask is 0)."""
    h, w = y1 - y0, x1 - x0
    mh, mw = mask.shape[0], mask.shape[1]
    win = np.zeros((h + 2, w + 2), bool)
    sx0, sy0, sx1, sy1 = max(x0 - 1, 0), max(y0 - 1, 0), min(x1 + 1, mw), min(y1 + 1, mh)
    np.greater(
        mask[sy0:sy1, sx0:sx1],
        0,
        out=win[sy0 - y0 + 1 : sy1 - y0 + 1, sx0 - x0 + 1 : sx1 - x0 + 1],
    )

    def near(dx: int, dy: int) -> np.ndarray:
        return win[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]

    own, east, west, south, north = near(0, 0), near(1, 0), near(-1, 0), near(0, 1), near(0, -1)
    south_east, south_west = near(1, 1), near(-1, 1)
    four = east | west | south | north
    return (
        own & east,
        own & south,
        own & south_east & ~(east | south),
        own & south_west & ~(west | south),
        own & ~(four | south_east | south_west | near(1, -1) | near(-1, -1)),
    )


def _coords(plane: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(xs, ys)`` of the set pixels of ``plane``, row-major (flat indices: ~10x faster
    than a 2-D ``np.nonzero``)."""
    ys, xs = np.divmod(np.flatnonzero(plane), plane.shape[1])
    return xs, ys


def _east_runs(links: np.ndarray) -> np.ndarray:
    """Merged runs of E links: pair the k-th run start with the k-th run end, row-major."""
    starts = links.copy()
    starts[:, 1:] &= ~links[:, :-1]
    ends = links.copy()
    ends[:, :-1] &= ~links[:, 1:]
    xs, ys = _coords(starts)
    xe, _ = _coords(ends)
    return np.stack([xs, ys, xe + 1, ys], axis=1)


def _south_runs(links: np.ndarray) -> np.ndarray:
    """Merged runs of S links, paired column by column (the transposed planes)."""
    starts = links.copy()
    starts[1:, :] &= ~links[:-1, :]
    ends = links.copy()
    ends[:-1, :] &= ~links[1:, :]
    ys, xs = _coords(starts.T)  # column-major: (row of the transpose = x, column = y)
    ye, _ = _coords(ends.T)
    return np.stack([xs, ys, xs, ye + 1], axis=1)


def _diagonal_runs(links: np.ndarray, dx: int) -> np.ndarray:
    """Merged runs of SE (``dx`` 1) or SW (``dx`` -1) links, paired along each diagonal."""
    starts = links.copy()
    ends = links.copy()
    if dx > 0:  # the previous link of a run starts at (x - 1, y - 1), the next at (x + 1, y + 1)
        starts[1:, 1:] &= ~links[:-1, :-1]
        ends[:-1, :-1] &= ~links[1:, 1:]
    else:  # previous at (x + 1, y - 1), next at (x - 1, y + 1)
        starts[1:, :-1] &= ~links[:-1, 1:]
        ends[:-1, 1:] &= ~links[1:, :-1]
    xs, ys = _coords(starts)
    xe, ye = _coords(ends)
    first = np.lexsort((ys, xs - dx * ys))  # by diagonal, then down it
    last = np.lexsort((ye, xe - dx * ye))
    xs, ys, xe, ye = xs[first], ys[first], xe[last], ye[last]
    return np.stack([xs, ys, xe + dx, ye + 1], axis=1)

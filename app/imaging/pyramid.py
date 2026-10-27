"""Half-scale image pyramids for level-of-detail rendering (Qt-free: numpy + cv2 only).

Level 0 is the image itself; level k is a reducer applied to level k-1, with ceil sizes.
Level-k pixel (x, y) covers the level-0 pixels ``[x * 2**k, (x + 1) * 2**k)`` by
``[y * 2**k, (y + 1) * 2**k)``: a level with an odd side replicates its last row or column
before it is halved, so every output pixel depends only on its own 2x2 block.

Two reducers:

* :func:`area_half` - the area average (``cv2.INTER_AREA`` over 2x2 blocks) for photos and
  premultiplied edit-layer canvases: alias-free when minified.
* :func:`max_half` - a 2x2 max-pool for line overlays: a 1 px line never breaks into dashes.

:class:`ImagePyramid` builds its levels lazily and caches them. After
:meth:`ImagePyramid.invalidate` with a rect, the next read of a built level recomputes only
the blocks under that rect (patched level by level), which always equals a full rebuild.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import cv2
import numpy as np

Rect = tuple[int, int, int, int]  # (x, y, width, height) in level-0 pixels

_LEVEL_EPS = 1e-6  # absorbs float noise at exact powers of two (0.5, 0.25, ...)
_MAX_PENDING = 8  # dirty rects kept apart per level before they are merged into one


def _pad_even(img: np.ndarray) -> np.ndarray:
    """``img`` with its last row / column replicated so both sides are even."""
    h, w = img.shape[:2]
    if h % 2 == 0 and w % 2 == 0:
        return img
    pad = ((0, h % 2), (0, w % 2)) + ((0, 0),) * (img.ndim - 2)
    return np.pad(img, pad, mode="edge")


def _halve(img: np.ndarray, even: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Apply the even-size block reducer ``even``; an odd last row / column is replicated.

    The even part is reduced in place (no copy of a large odd image); only the odd edge
    is padded.
    """
    h, w = img.shape[:2]
    if h % 2 == 0 and w % 2 == 0:
        return even(img)
    out = np.empty(((h + 1) // 2, (w + 1) // 2) + img.shape[2:], img.dtype)
    eh, ew = h - h % 2, w - w % 2
    if eh and ew:
        out[: eh // 2, : ew // 2] = even(img[:eh, :ew])
    if w % 2:
        out[:, -1] = even(_pad_even(img[:, -1:]))[:, 0]
    if h % 2:
        out[-1, :] = even(_pad_even(img[-1:, :]))[0, :]
    return out


def _area_even(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    out = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    return out.reshape(out.shape[:2] + img.shape[2:])


_MAX_KERNEL = np.ones((2, 2), np.uint8)
_DILATE_DTYPES = (np.uint8, np.uint16, np.int16, np.float32, np.float64)


def _max_even(img: np.ndarray) -> np.ndarray:
    if img.dtype in _DILATE_DTYPES and (img.ndim == 2 or img.shape[2] <= 4):
        # A 2x2 dilation anchored at the top-left holds each block's maximum at its
        # top-left pixel; about 2x faster than four strided numpy maxima.
        dilated = cv2.dilate(img, _MAX_KERNEL, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
        out = np.ascontiguousarray(dilated[0::2, 0::2])
        return out.reshape(out.shape[:2] + img.shape[2:])
    top = np.maximum(img[0::2, 0::2], img[0::2, 1::2])
    return np.maximum(top, np.maximum(img[1::2, 0::2], img[1::2, 1::2]))


def area_half(img: np.ndarray) -> np.ndarray:
    """Area-average ``img`` to ``(ceil(w / 2), ceil(h / 2))`` (``cv2.INTER_AREA``).

    Each output pixel is the rounded mean of its 2x2 block (an odd last row or column is
    replicated first). Works for gray ``(h, w)`` and any channel count ``(h, w, c)``;
    premultiplied colour stays premultiplied.
    """
    return _halve(img, _area_even)


def max_half(img: np.ndarray) -> np.ndarray:
    """2x2 max-pool of ``img`` to ``(ceil(w / 2), ceil(h / 2))``.

    A line overlay stays connected at every level (a nearest decimation drops pixels).
    Works for gray ``(h, w)`` and any channel count ``(h, w, c)``.
    """
    return _halve(img, _max_even)


def level_sizes(width: int, height: int, min_side: int) -> tuple[tuple[int, int], ...]:
    """``(width, height)`` of each level, level 0 first: halved (ceil) until the longer side
    is at most ``min_side`` (at least 1). What :class:`ImagePyramid` builds, without an image."""
    sizes = [(width, height)]
    limit = max(1, int(min_side))
    while max(width, height) > limit:
        width, height = (width + 1) // 2, (height + 1) // 2
        sizes.append((width, height))
    return tuple(sizes)


def _union(a: Rect, b: Rect) -> Rect:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return x0, y0, x1 - x0, y1 - y0


def _touch(a: Rect, b: Rect) -> bool:
    """Whether ``a`` and ``b`` overlap or share an edge."""
    return (
        a[0] <= b[0] + b[2] and b[0] <= a[0] + a[2] and a[1] <= b[1] + b[3] and b[1] <= a[1] + a[3]
    )


def add_rect(pending: list[Rect], rect: Rect, limit: int = _MAX_PENDING) -> None:
    """Add the dirty ``rect`` to ``pending``, merging it with the rects it touches; past
    ``limit`` rects they are all merged into their union."""
    for i, other in enumerate(pending):
        if _touch(other, rect):
            rect = _union(pending.pop(i), rect)
            add_rect(pending, rect, limit)  # the grown rect may touch others now
            return
    pending.append(rect)
    if len(pending) > limit:
        merged = pending[0]
        for other in pending[1:]:
            merged = _union(merged, other)
        pending[:] = [merged]


class ImagePyramid:
    """Level 0 = the image; level k = reducer(level k-1), built lazily and cached."""

    def __init__(
        self,
        image: np.ndarray,
        *,
        reducer: Callable[[np.ndarray], np.ndarray] = area_half,
        min_side: int = 32,
    ) -> None:
        """Wrap ``image`` (kept by reference as level 0, never modified).

        Levels go down to the first one whose longer side is at most ``min_side``.
        ``reducer`` halves an image with ceil sizes, each output pixel depending only on
        its own 2x2 block (as :func:`area_half` and :func:`max_half` do), so that a
        dirty rect can be patched locally.
        """
        if image.ndim < 2 or image.shape[0] < 1 or image.shape[1] < 1:
            raise ValueError(f"ImagePyramid needs a non-empty image, got shape {image.shape}")
        self._reducer = reducer
        self._sizes = level_sizes(image.shape[1], image.shape[0], min_side)
        self._levels: list[np.ndarray | None] = [image] + [None] * (len(self._sizes) - 1)
        # Per built level: level-0 rects changed since that level was last brought up to date.
        self._pending: list[list[Rect]] = [[] for _ in self._sizes]

    @property
    def count(self) -> int:
        """Number of levels (at least 1)."""
        return len(self._sizes)

    def level(self, k: int) -> np.ndarray:
        """Level ``k`` (0 = the image), built or patched from the levels above it when needed."""
        if not 0 <= k < self.count:
            raise IndexError(f"pyramid level {k} out of range 0..{self.count - 1}")
        for j in range(1, k + 1):
            if self._levels[j] is None:
                self._levels[j] = self._reducer(self._levels[j - 1])
                self._pending[j].clear()
            elif self._pending[j]:
                for rect in self._pending[j]:
                    self._patch(j, rect)
                self._pending[j].clear()
        return self._levels[k]

    def level_size(self, k: int) -> tuple[int, int]:
        """``(width, height)`` of level ``k``, without building it."""
        return self._sizes[k]

    def invalidate(self, rect: Rect | None = None) -> None:
        """Level-0 pixels inside ``rect`` (``None``: anywhere) changed; built levels follow.

        Nothing is recomputed here: the next :meth:`level` read patches the affected blocks
        of each built level (a ``None`` rect drops them, to be rebuilt on the next read).
        """
        if rect is None:
            for k in range(1, self.count):
                self._levels[k] = None
                self._pending[k].clear()
            return
        w0, h0 = self._sizes[0]
        x0, y0 = max(0, int(rect[0])), max(0, int(rect[1]))
        x1, y1 = min(w0, int(rect[0]) + int(rect[2])), min(h0, int(rect[1]) + int(rect[3]))
        if x1 <= x0 or y1 <= y0:
            return
        clipped = (x0, y0, x1 - x0, y1 - y0)
        for k in range(1, self.count):
            if self._levels[k] is not None:
                add_rect(self._pending[k], clipped)

    def _patch(self, k: int, rect: Rect) -> None:
        """Recompute the blocks of level ``k`` under the level-0 ``rect`` from level ``k - 1``."""
        shift = k - 1
        prev = self._levels[k - 1]
        pw, ph = self._sizes[k - 1]
        # rect in level k-1 pixels (covering), widened to whole 2x2 blocks
        x0 = (rect[0] >> shift) & ~1
        y0 = (rect[1] >> shift) & ~1
        x1 = min(pw, (((rect[0] + rect[2] + (1 << shift) - 1) >> shift) + 1) & ~1)
        y1 = min(ph, (((rect[1] + rect[3] + (1 << shift) - 1) >> shift) + 1) & ~1)
        out = self._reducer(prev[y0:y1, x0:x1])
        self._levels[k][y0 // 2 : y0 // 2 + out.shape[0], x0 // 2 : x0 // 2 + out.shape[1]] = out

    @staticmethod
    def level_for_scale(device_scale: float, count: int, *, nearest: bool = False) -> int:
        """The level to draw at ``device_scale`` device pixels per level-0 pixel.

        ``floor(-log2 s)`` for ``s < 1`` (the level is then minified by less than 2x),
        else 0. ``nearest=True`` gives ``ceil(-log2 s)`` instead: the finest level with at
        least one device pixel per level pixel (a nearest draw that minifies drops rows).
        Clamped to ``0 .. count - 1``.
        """
        if count <= 1 or not device_scale < 1.0:
            return 0
        if device_scale <= 0.0:
            return count - 1
        x = -math.log2(device_scale)
        k = math.ceil(x - _LEVEL_EPS) if nearest else math.floor(x + _LEVEL_EPS)
        return max(0, min(k, count - 1))

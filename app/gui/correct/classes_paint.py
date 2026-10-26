"""The class labels' paint rules (Qt-free at import), as ``mask_tools`` holds the mask's.

A class-id plane is shown on the page's :class:`~app.gui.paint_canvas.PaintCanvas` in the
registry's colours: :func:`class_lut` gives the canvas colour (B, G, R, A) of each id and
:func:`canvas_ids` reads a painted canvas box back to ids by exact colour match, the
committed plane's id kept under any colour no class owns (registry colours are unique,
retired classes included). :func:`histogram` counts ids exactly at any size. The former
Trace GT editor's code, moved verbatim without Qt: a colour is parsed from ``#rrggbb``
instead of through ``QColor``.
"""

from __future__ import annotations

import functools

import cv2
import numpy as np

from app.config import GT_BACKGROUND_ID, GT_UNKNOWN_COLOR
from app.gt import Registry

__all__ = ["N_IDS", "canvas_ids", "class_lut", "histogram"]

N_IDS = 256  # class ids are uint8
# cv2.calcHist counts in float32, exact up to 2**24 per value: arrays up to this many
# pixels are counted by it (about 10x faster than np.bincount), larger ones by np.bincount.
_EXACT_HIST_MAX = 1 << 24
# A canvas word that never occurs (alpha 0 but a colour: a transparent pixel reads back as
# 0): the expected word of committed ids no class owns, so they are always colour-matched.
_NEVER_ON_CANVAS = 1


def _bgra(color: str) -> tuple[int, int, int, int]:
    """``#rrggbb`` -> opaque (B, G, R, A), the byte order of the canvas arrays."""
    value = int(color[1:7], 16)
    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, 255


def _word(color: str) -> int:
    """``color`` as one canvas pixel word ``B | G << 8 | R << 16 | A << 24``."""
    b, g, r, a = _bgra(color)
    return b | (g << 8) | (r << 16) | (a << 24)


def histogram(ids: np.ndarray) -> np.ndarray:
    """Pixels per value (256 int64 counts) of a uint8 array of any shape; exact."""
    if ids.size == 0:
        return np.zeros(N_IDS, np.int64)
    if ids.size > _EXACT_HIST_MAX:
        return np.bincount(ids.ravel(), minlength=N_IDS).astype(np.int64)
    if ids.ndim != 2:
        ids = np.ascontiguousarray(ids).reshape(1, -1)
    return cv2.calcHist([ids], [0], None, [N_IDS], [0, N_IDS]).ravel().astype(np.int64)


def class_lut(registry: Registry) -> np.ndarray:
    """Class id -> B, G, R, A ``(256, 4)``: background transparent, ids no class owns grey,
    every class (retired included) in its own colour."""
    lut = np.zeros((N_IDS, 4), np.uint8)
    lut[:] = _bgra(GT_UNKNOWN_COLOR)
    for c in registry.classes:
        lut[c.id] = _bgra(c.color)
    lut[GT_BACKGROUND_ID] = 0
    return lut


@functools.lru_cache(maxsize=8)
def _committed_words(registry: Registry) -> np.ndarray:
    """The canvas word (as int32, for ``cv2.LUT``) that shows each committed class id.

    0 (transparent) for background, the class colour for every class (retired included),
    ``_NEVER_ON_CANVAS`` for ids no class owns. Read-only: shared by every call.
    """
    words = np.full(N_IDS, _NEVER_ON_CANVAS, np.uint32)
    for c in registry.classes:
        words[c.id] = _word(c.color)
    words[GT_BACKGROUND_ID] = 0
    words = words.view(np.int32)
    words.flags.writeable = False
    return words


def _match_colours(words: np.ndarray, ids: np.ndarray, registry: Registry) -> np.ndarray:
    """Class ids of canvas ``words`` (1-D): transparent is background, a class colour is
    its class, any other colour keeps its id in ``ids`` (the committed plane). A new array."""
    ids = ids.copy()
    ids[(words >> 24) == 0] = GT_BACKGROUND_ID
    for c in registry.classes:  # colours are unique, retired included
        ids[words == _word(c.color)] = c.id
    return ids


def canvas_ids(
    argb: np.ndarray, registry: Registry, fallback: np.ndarray | None = None
) -> np.ndarray:
    """Canvas pixels -> ``(h, w)`` uint8 class ids by exact colour match (a new array).

    ``argb`` is ``(h, w, 4)`` B, G, R, A. Transparent is background; a colour no class owns
    keeps its ``fallback`` id (the committed plane), else 0. With a ``fallback``, a pixel
    that still shows its committed id's colour keeps that id and only the others (the
    pixels a stroke changed) are colour-matched: the same ids as matching every pixel, at a
    fraction of the cost on a long stroke's box.
    """
    argb = np.ascontiguousarray(argb)
    # Zero-copy view: one little-endian word per pixel, B | G<<8 | R<<16 | A<<24.
    words = argb.view("<u4")[:, :, 0].reshape(-1)
    if fallback is None:
        ids = np.zeros(words.shape, np.uint8)
        return _match_colours(words, ids, registry).reshape(argb.shape[:2])
    ids = np.array(fallback, np.uint8, order="C", copy=True)
    flat = ids.reshape(-1)
    shown = cv2.LUT(ids, _committed_words(registry)).reshape(-1).view(np.uint32)
    changed = np.flatnonzero(words != shown)
    if changed.size:
        flat[changed] = _match_colours(words[changed], flat[changed], registry)
    return ids

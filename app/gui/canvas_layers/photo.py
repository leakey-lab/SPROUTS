"""The photo layer of every image canvas (Correct, Trace GT, Library).

:class:`PhotoLayerItem` draws a decoded image (BGR or gray numpy array), smooth at every
zoom: the level of an area-averaged pyramid (:mod:`app.imaging.pyramid`) chosen from the
painter's device scale, drawn with bilinear filtering, exposed rect only.

:class:`TiledLevels` draws pyramid levels without seams and makes only the tiles on
screen, so the first frame of a new level costs the visible part, not a whole 6000 px
image (on GL: only those textures are uploaded). The raster layer's minified coverage
uses it too.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import cv2
import numpy as np
from PyQt6.QtCore import QPointF, QRectF, QSizeF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from app.imaging.pyramid import ImagePyramid

from . import device_scale

_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_ANTIALIAS = QPainter.RenderHint.Antialiasing

Rect = tuple[int, int, int, int]  # (x, y, width, height)
MakeTile = Callable[[int, int, int, int, int], QPixmap]  # (level, x0, y0, x1, y1) -> pixmap


def array_to_pixmap(arr: np.ndarray) -> QPixmap:
    """A ``QPixmap`` owning a copy of ``arr``: uint8 gray ``(h, w)`` or BGR ``(h, w, 3)``.

    One ``cv2.cvtColor`` pass writes straight into a Qt-owned ``Format_RGB32`` image
    (bytes B, G, R, 255), the raster pixmap's native format, so no further copy is made.
    ``arr`` may be a strided view (a tile of a larger image).
    """
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    if arr.dtype != np.uint8 or not (arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] == 3)):
        raise ValueError(
            f"expected uint8 gray (h, w) or BGR (h, w, 3), got {arr.shape} {arr.dtype}"
        )
    h, w = arr.shape[:2]
    image = QImage(w, h, QImage.Format.Format_RGB32)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(h, image.bytesPerLine())
    dst = rows[:, : w * 4].reshape(h, w, 4)
    code = cv2.COLOR_GRAY2BGRA if arr.ndim == 2 else cv2.COLOR_BGR2BGRA
    cv2.cvtColor(arr, code, dst=dst)
    return QPixmap.fromImage(image)


class TiledLevels:
    """Pixmaps of image-pyramid levels, drawn with bilinear filtering and no seams.

    Level pixel x covers image pixels ``[x * 2**level, (x + 1) * 2**level)``, so every
    draw maps level pixels to image pixels by exactly ``2**level``, whatever it draws (no
    phase shift between partial and full repaints). A level of at most ``single_pixels``
    pixels is one pixmap, drawn ``margin`` px around the exposed rect. A larger level is
    split into ``tile`` px tiles made by ``make`` when first drawn: each carries ``margin``
    px of its neighbours and is drawn whole but clipped to its own cell, so bilinear
    filtering reads true neighbours at every tile edge. The caller sets the painter's
    hints (smoothing, no antialiasing) before :meth:`draw`.
    """

    def __init__(self, make: MakeTile, *, tile: int, margin: int, single_pixels: int) -> None:
        """Tiles of ``tile`` px (plus ``margin``) made by ``make(level, x0, y0, x1, y1)``."""
        self._make = make
        self._tile = tile
        self._margin = margin
        self._single = single_pixels
        # (level, tx, ty) -> (pixmap, level x, level y of its top-left pixel)
        self._tiles: dict[tuple[int, int, int], tuple[QPixmap, int, int]] = {}

    def clear(self) -> None:
        """Forget every tile."""
        self._tiles.clear()

    def built(self) -> dict[tuple[int, int, int], int]:
        """``(level, tx, ty) -> cacheKey`` of every made tile."""
        return {key: tile[0].cacheKey() for key, tile in self._tiles.items()}

    def drop(self, level: int, rect: Rect, level_size: tuple[int, int]) -> None:
        """Forget the tiles of ``level`` whose pixels (margin included) touch the level-0
        ``rect``; they are made again when next drawn."""
        lw, lh = level_size
        if lw * lh <= self._single:
            self._tiles.pop((level, 0, 0), None)
            return
        x, y, w, h = rect
        step = (1 << level) - 1
        t, m = self._tile, self._margin
        x0, y0 = (x >> level) - m, (y >> level) - m
        x1, y1 = ((x + w + step) >> level) + m, ((y + h + step) >> level) + m
        for ty in range(max(0, y0) // t, max(0, y1 - 1) // t + 1):
            for tx in range(max(0, x0) // t, max(0, x1 - 1) // t + 1):
                self._tiles.pop((level, tx, ty), None)

    def draw(
        self,
        painter: QPainter,
        level: int,
        level_size: tuple[int, int],
        image_size: tuple[int, int],
        exposed: QRectF,
    ) -> None:
        """Draw ``level`` (``level_size`` px) over the image-pixel rect ``exposed``."""
        f = 1 << level
        m = self._margin
        lw, lh = level_size
        x0 = max(0, math.floor(exposed.left() / f) - m)
        y0 = max(0, math.floor(exposed.top() / f) - m)
        x1 = min(lw, math.ceil(exposed.right() / f) + m)
        y1 = min(lh, math.ceil(exposed.bottom() / f) + m)
        if x1 <= x0 or y1 <= y0:
            return
        if lw * lh <= self._single:
            pixmap, ox, oy = self._get(level, 0, 0, level_size)
            _draw_area(painter, pixmap, ox, oy, (x0, y0, x1, y1), f, image_size)
            return
        t = self._tile
        width, height = image_size
        for ty in range(y0 // t, (y1 - 1) // t + 1):
            for tx in range(x0 // t, (x1 - 1) // t + 1):
                left, top = tx * t * f, ty * t * f
                right = min(min((tx + 1) * t, lw) * f, width)
                bottom = min(min((ty + 1) * t, lh) * f, height)
                cell = QRectF(left, top, right - left, bottom - top).intersected(exposed)
                if cell.isEmpty():
                    continue
                pixmap, ox, oy = self._get(level, tx, ty, level_size)
                painter.save()
                painter.setClipRect(cell, Qt.ClipOperation.IntersectClip)
                area = (ox, oy, ox + pixmap.width(), oy + pixmap.height())
                _draw_area(painter, pixmap, ox, oy, area, f, image_size)
                painter.restore()

    def _get(
        self, level: int, tx: int, ty: int, level_size: tuple[int, int]
    ) -> tuple[QPixmap, int, int]:
        tile = self._tiles.get((level, tx, ty))
        if tile is None:
            lw, lh = level_size
            if lw * lh <= self._single:
                box = (0, 0, lw, lh)
            else:
                t, m = self._tile, self._margin
                box = (
                    max(0, tx * t - m),
                    max(0, ty * t - m),
                    min(lw, (tx + 1) * t + m),
                    min(lh, (ty + 1) * t + m),
                )
            tile = (self._make(level, *box), box[0], box[1])
            self._tiles[(level, tx, ty)] = tile
        return tile


def _draw_area(
    painter: QPainter,
    pixmap: QPixmap,
    ox: int,
    oy: int,
    area: tuple[int, int, int, int],
    f: int,
    image_size: tuple[int, int],
) -> None:
    """Draw level pixels ``area`` (x0, y0, x1, y1) of a pixmap whose top-left is level
    pixel ``(ox, oy)``, at ``f`` image pixels per level pixel, cut at the image edge."""
    x0, y0, x1, y1 = area
    tx1, ty1 = min(x1 * f, image_size[0]), min(y1 * f, image_size[1])
    target = QRectF(x0 * f, y0 * f, tx1 - x0 * f, ty1 - y0 * f)
    source = QRectF(x0 - ox, y0 - oy, tx1 / f - x0, ty1 / f - y0)
    painter.drawPixmap(target, pixmap, source)


class PhotoLayerItem(QGraphicsItem):
    """The photo, smooth at every zoom: an area-averaged pyramid level chosen from the
    device scale, drawn with bilinear filtering (exposed rect only)."""

    # Level pixels of bilinear context drawn beyond the exposed rect, and carried by each
    # tile beyond its cell: a partial repaint equals a full one (no seams).
    MARGIN = 2
    TILE = 512  # level pixels per tile side; a level up to (4 * TILE) ** 2 px is one pixmap

    def __init__(self, parent: QGraphicsItem | None = None, *, smooth: bool = True) -> None:
        """An empty photo item; ``smooth=False`` always draws level 0 with exact pixels."""
        super().__init__(parent)
        self._smooth = bool(smooth)
        self._pyramid: ImagePyramid | None = None
        self._levels: TiledLevels | None = None
        self._full: QPixmap | None = None
        self._width = 0
        self._height = 0
        self._level_painted = -1
        # Makes option.exposedRect the dirty part rather than the whole item.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    # ------------------------------------------------------------------ API
    def set_image(self, image: np.ndarray | None) -> None:
        """Show ``image`` (uint8 BGR ``(h, w, 3)`` or gray ``(h, w)``); ``None`` clears.

        The array is kept by reference and never modified; nothing is converted here.
        """
        self._pyramid = None if image is None else ImagePyramid(image)
        self._levels = None
        if image is not None:
            single = (4 * self.TILE) ** 2
            self._levels = TiledLevels(
                self._make_tile, tile=self.TILE, margin=self.MARGIN, single_pixels=single
            )
        self._full = None
        width, height = (0, 0) if image is None else (int(image.shape[1]), int(image.shape[0]))
        if (width, height) != (self._width, self._height):
            self.prepareGeometryChange()
            self._width, self._height = width, height
        self.update()

    def image_size(self) -> tuple[int, int]:
        """``(width, height)`` of the image; ``(0, 0)`` when empty."""
        return self._width, self._height

    def pixmap(self) -> QPixmap:
        """The full-resolution image (level 0) as one pixmap; null when empty."""
        if self._pyramid is None:
            return QPixmap()
        if self._full is None:
            self._full = array_to_pixmap(self._pyramid.level(0))
        return self._full

    def level_painted(self) -> int:
        """Pyramid level used by the last paint; -1 before any paint."""
        return self._level_painted

    def built_tiles(self) -> dict[tuple[int, int, int], int]:
        """``(level, tx, ty) -> cacheKey`` of each converted tile pixmap (a test hook)."""
        return {} if self._levels is None else self._levels.built()

    # ------------------------------------------------------------ QGraphicsItem
    def boundingRect(self) -> QRectF:
        """``(0, 0, w, h)`` in image pixels."""
        return QRectF(QPointF(0, 0), QSizeF(self._width, self._height))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Draw the exposed part from the pyramid level that suits the device scale."""
        pyramid, levels = self._pyramid, self._levels
        if pyramid is None or levels is None:
            return
        exposed = option.exposedRect.intersected(self.boundingRect())
        if exposed.isEmpty():
            return
        level = 0
        if self._smooth:
            level = ImagePyramid.level_for_scale(device_scale(painter), pyramid.count)
        old_smooth, old_aa = painter.testRenderHint(_SMOOTH), painter.testRenderHint(_ANTIALIAS)
        if old_smooth != self._smooth:
            painter.setRenderHint(_SMOOTH, self._smooth)
        if old_aa:
            painter.setRenderHint(_ANTIALIAS, False)  # plain rect fills, hard rect clips
        size = (self._width, self._height)
        levels.draw(painter, level, pyramid.level_size(level), size, exposed)
        if old_smooth != self._smooth:
            painter.setRenderHint(_SMOOTH, old_smooth)
        if old_aa:
            painter.setRenderHint(_ANTIALIAS, True)
        self._level_painted = level

    # ------------------------------------------------------------- internals
    def _make_tile(self, level: int, x0: int, y0: int, x1: int, y1: int) -> QPixmap:
        return array_to_pixmap(self._pyramid.level(level)[y0:y1, x0:x1])

"""An editable ARGB canvas layer (the Correct mask, the Trace GT classes).

:class:`RasterLayerItem` reads its ``QImage`` through a source callable at paint time, so
no second reference to the canvas exists (painting a stroke never detaches a copy), and
it draws the exposed rect only:

* **Device scale >= 1: exact pixels.** The canvas is shown in 256 px tile pixmaps drawn
  nearest; a stroke drops only the tiles it touched (on GL only those textures are
  uploaded again). Tile edges are snapped to the device pixels where a global nearest
  mapping switches image pixels, so tiles meet without gaps or blended seams, and every
  image pixel shows as a block of ``floor(s)`` or ``ceil(s)`` device pixels.
* **Device scale < 1: area coverage,** drawn bilinear: above 0.5 the canvas itself,
  below a premultiplied area-averaged pyramid of it (:mod:`app.imaging.pyramid`, patched
  lazily by dirty rect), so a 1 px stroke fades instead of vanishing. The coverage of
  the level in use is kept in seam-free tiles (:class:`~.photo.TiledLevels`): a stroke
  remakes (and on GL uploads) only the tiles it touched.

The painter's smoothing and antialiasing are set for the layer while it draws and
restored afterwards, and drawing is clipped to the exposed rect.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from app.imaging.pyramid import ImagePyramid, area_half

from . import device_scale
from .photo import TiledLevels

ImageSource = Callable[[], "QImage | None"]
Rect = tuple[int, int, int, int]

_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_ANTIALIAS = QPainter.RenderHint.Antialiasing
_PREMULTIPLIED = QImage.Format.Format_ARGB32_Premultiplied
_MAX_PENDING = 32  # coverage rects kept apart before they are merged into their union


def _bgra_view(image: QImage, rect: QRect) -> np.ndarray:
    """Read-only ``(h, w, 4)`` premultiplied B, G, R, A of ``rect`` of ``image``.

    A view of the canvas memory when it is already premultiplied ARGB32 (valid only until
    the canvas is painted again), else a converted numpy copy of that rect.
    """
    converted = image.format() != _PREMULTIPLIED
    if converted:
        image = image.copy(rect).convertToFormat(_PREMULTIPLIED)
        rect = QRect(0, 0, rect.width(), rect.height())
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())
    x0, y0 = rect.x(), rect.y()
    block = rows[y0 : y0 + rect.height(), x0 * 4 : (x0 + rect.width()) * 4]
    block = block.reshape(rect.height(), rect.width(), 4)
    return block.copy() if converted else block  # a view must not outlive its local image


def _array_pixmap(arr: np.ndarray) -> QPixmap:
    """A pixmap of a premultiplied ``(h, w, 4)`` B, G, R, A array (may be a strided view)."""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QPixmap.fromImage(QImage(arr.data, w, h, w * 4, _PREMULTIPLIED).copy())


def _union(rects: list[Rect]) -> Rect:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0


def _snapped_rect(x: int, y: int, w: int, h: int, dev) -> QRectF:
    """The item rect whose device edges are where a global nearest mapping of ``dev``
    (scale + translate) enters image pixels ``x``, ``x + w`` (and ``y``, ``y + h``)."""
    sx, sy, dx, dy = dev.m11(), dev.m22(), dev.dx(), dev.dy()
    left, right = math.ceil(x * sx + dx - 0.5), math.ceil((x + w) * sx + dx - 0.5)
    top, bottom = math.ceil(y * sy + dy - 0.5), math.ceil((y + h) * sy + dy - 0.5)
    return QRectF((left - dx) / sx, (top - dy) / sy, (right - left) / sx, (bottom - top) / sy)


class _Coverage:
    """Area-averaged levels (1, 2, ...) of the canvas, for device scales up to 0.5.

    Level 1 is ``area_half`` of the canvas, deeper levels come from an ``ImagePyramid``
    over it. Dirty canvas rects are folded in lazily, when a level is next read.
    """

    MIN_SIDE = 32

    def __init__(self) -> None:
        """Nothing built yet: the levels are made at the first minified paint."""
        self.reset()

    def reset(self) -> None:
        """Forget everything (the canvas was replaced)."""
        self._levels: ImagePyramid | None = None  # its level j = coverage level j + 1
        self._pending: list[Rect] = []

    def changed(self, rect: Rect) -> None:
        """Canvas pixels in ``rect`` changed (folded in when a level is next read)."""
        if self._levels is None:
            return
        self._pending.append(rect)
        if len(self._pending) > _MAX_PENDING:
            self._pending[:] = [_union(self._pending)]

    def count(self, canvas: QImage) -> int:
        """Coverage levels of ``canvas``, level 0 (the canvas itself) included."""
        self._sync(canvas)
        return 1 + self._levels.count

    def level(self, canvas: QImage, level: int) -> np.ndarray:
        """Coverage ``level`` (>= 1) of ``canvas``, up to date."""
        self._sync(canvas)
        return self._levels.level(level - 1)

    def level_size(self, level: int) -> tuple[int, int]:
        """``(width, height)`` of coverage ``level`` (>= 1)."""
        return self._levels.level_size(level - 1)

    def _sync(self, canvas: QImage) -> None:
        w, h = canvas.width(), canvas.height()
        if self._levels is None:
            half = area_half(_bgra_view(canvas, QRect(0, 0, w, h)))
            self._levels = ImagePyramid(half, min_side=self.MIN_SIDE)
            self._pending.clear()
            return
        if not self._pending:
            return
        half = self._levels.level(0)
        for x, y, rw, rh in self._pending:
            # whole 2x2 blocks of the canvas around the rect
            x0, y0 = x & ~1, y & ~1
            x1, y1 = min(w, (x + rw + 1) & ~1), min(h, (y + rh + 1) & ~1)
            block = area_half(_bgra_view(canvas, QRect(x0, y0, x1 - x0, y1 - y0)))
            hy, hx = y0 // 2, x0 // 2
            half[hy : hy + block.shape[0], hx : hx + block.shape[1]] = block
            self._levels.invalidate((hx, hy, block.shape[1], block.shape[0]))
        self._pending.clear()


class RasterLayerItem(QGraphicsItem):
    """An editable ARGB canvas (a QImage read at paint time): exact pixels at device
    scale >= 1 from 256 px tile pixmaps, area coverage below 1."""

    TILE = 256
    # Coverage (device scale < 1): level pixels of bilinear context around the exposed
    # rect and around each tile, and the tile size of levels over (2 * COVER_TILE) ** 2 px.
    MARGIN = 2
    COVER_TILE = 512

    def __init__(self, source: ImageSource, parent: QGraphicsItem | None = None) -> None:
        """Draw ``source()``: the canvas ``QImage`` (ideally ``Format_ARGB32_Premultiplied``),
        or ``None`` while nothing is open. Never keeps a reference to the image."""
        super().__init__(parent)
        self._source = source
        self._tiles: dict[tuple[int, int], QPixmap] = {}
        self._coverage = _Coverage()
        self._cover: TiledLevels | None = None  # tiles of the coverage level in use
        self._cover_level = -1
        self._drawing: QImage | None = None  # the canvas, during a paint only
        self._size = self._source_size()
        # Makes option.exposedRect the dirty part rather than the whole item.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    # ------------------------------------------------------------------ API
    def canvas_changed(self, rect: QRect) -> None:
        """Pixels inside ``rect`` (image pixels) were painted: drop the tiles it touches,
        mark the coverage stale there and repaint that part."""
        rect = rect.intersected(QRect(0, 0, int(self._size.width()), int(self._size.height())))
        if rect.isEmpty():
            return
        t = self.TILE
        for ty in range(rect.top() // t, rect.bottom() // t + 1):
            for tx in range(rect.left() // t, rect.right() // t + 1):
                self._tiles.pop((tx, ty), None)
        box = (rect.x(), rect.y(), rect.width(), rect.height())
        self._coverage.changed(box)
        if self._cover is not None:
            self._cover.drop(self._cover_level, box, self._level_size(self._cover_level))
        self.update(QRectF(rect))

    def canvas_replaced(self) -> None:
        """The canvas was replaced, resized or changed wholesale: rebuild and repaint all."""
        self._tiles.clear()
        self._coverage.reset()
        self._cover, self._cover_level = None, -1
        size = self._source_size()
        if size != self._size:
            self.prepareGeometryChange()
            self._size = size
        self.update()

    def built_tiles(self) -> dict[tuple[int, int], int]:
        """``(tx, ty) -> cacheKey`` of each built exact-pixel tile pixmap (a test hook)."""
        return {key: pixmap.cacheKey() for key, pixmap in self._tiles.items()}

    def coverage_tiles(self) -> dict[tuple[int, int, int], int]:
        """``(level, tx, ty) -> cacheKey`` of each built coverage tile (a test hook)."""
        return {} if self._cover is None else self._cover.built()

    # ------------------------------------------------------------ QGraphicsItem
    def boundingRect(self) -> QRectF:
        """``(0, 0, w, h)`` in canvas pixels."""
        return QRectF(QPointF(0, 0), self._size)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Draw the exposed part: exact tiles when magnified, area coverage when minified."""
        image = self._source()
        if image is None or image.isNull():
            return
        exposed = option.exposedRect.intersected(QRectF(image.rect()))
        if exposed.isEmpty():
            return
        painter.save()
        painter.setRenderHint(_SMOOTH, False)
        painter.setRenderHint(_ANTIALIAS, False)  # before the clip: a hard rect clip
        painter.setClipRect(exposed, Qt.ClipOperation.IntersectClip)
        scale = device_scale(painter)
        if scale >= 1.0:
            self._paint_tiles(painter, image, exposed)
        else:
            self._drawing = image
            try:
                self._paint_coverage(painter, image, exposed, scale)
            finally:
                self._drawing = None  # never keep the canvas: it would detach on paint
        painter.restore()

    # ------------------------------------------------------------- internals
    def _source_size(self) -> QSizeF:
        image = self._source()
        if image is None or image.isNull():
            return QSizeF(0, 0)
        return QSizeF(image.size())

    def _level_size(self, level: int) -> tuple[int, int]:
        if level == 0:
            return int(self._size.width()), int(self._size.height())
        return self._coverage.level_size(level)

    def _tile(self, image: QImage, tx: int, ty: int) -> QPixmap:
        pixmap = self._tiles.get((tx, ty))
        if pixmap is None:
            t = self.TILE
            pixmap = QPixmap.fromImage(image.copy(QRect(tx * t, ty * t, t, t)))
            self._tiles[(tx, ty)] = pixmap
        return pixmap

    def _paint_tiles(self, painter: QPainter, image: QImage, exposed: QRectF) -> None:
        """Whole tiles (the clip keeps them to the exposed rect), nearest."""
        t = self.TILE
        area = exposed.toAlignedRect()
        dev = painter.deviceTransform()
        # Scale + translate (every canvas view): snap tile edges to device pixels.
        snap = dev.m11() > 0 and dev.m22() > 0 and dev.m12() == 0 and dev.m21() == 0
        for ty in range(area.top() // t, area.bottom() // t + 1):
            for tx in range(area.left() // t, area.right() // t + 1):
                pixmap = self._tile(image, tx, ty)
                x, y, w, h = tx * t, ty * t, pixmap.width(), pixmap.height()
                target = _snapped_rect(x, y, w, h, dev) if snap else QRectF(x, y, w, h)
                painter.drawPixmap(target, pixmap, QRectF(0, 0, w, h))

    def _paint_coverage(
        self, painter: QPainter, image: QImage, exposed: QRectF, scale: float
    ) -> None:
        """The coverage level for ``scale``, bilinear, from its tiles."""
        level = 0
        if scale <= 0.5:
            level = ImagePyramid.level_for_scale(scale, self._coverage.count(image))
        if self._cover is None or level != self._cover_level:
            t = self.COVER_TILE
            self._cover = TiledLevels(
                self._make_cover_tile, tile=t, margin=self.MARGIN, single_pixels=(2 * t) ** 2
            )
            self._cover_level = level
        painter.setRenderHint(_SMOOTH, True)
        size = (image.width(), image.height())
        self._cover.draw(painter, level, self._level_size(level), size, exposed)

    def _make_cover_tile(self, level: int, x0: int, y0: int, x1: int, y1: int) -> QPixmap:
        canvas = self._drawing
        if level == 0:
            image = canvas.copy(QRect(x0, y0, x1 - x0, y1 - y0))
            if image.format() != _PREMULTIPLIED:
                image = image.convertToFormat(_PREMULTIPLIED)
            return QPixmap.fromImage(image)
        return _array_pixmap(self._coverage.level(canvas, level)[y0:y1, x0:x1])

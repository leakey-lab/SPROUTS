"""The photo layer of every image canvas (Correct, Trace GT, Library).

:class:`PhotoLayerItem` draws a decoded image (BGR or gray numpy array), smooth at every
zoom: the level of an area-averaged pyramid (:mod:`app.imaging.pyramid`) chosen from the
painter's device scale, drawn with bilinear filtering, exposed rect only. Between about
76 % and 50 % the half-size level is blended over the full-size one (see
:attr:`PhotoLayerItem.BLEND_FROM`), so fine texture does not shimmer there.

:class:`TiledLevels` draws pyramid levels without seams and makes only the tiles on
screen, so the first frame of a new level costs the visible part, not a whole 6000 px
image (on GL: only those textures are uploaded). The raster layer's minified coverage
uses it too.

Once the view has been still for a moment, the photo warms what one zoom step in or out
would draw (see :attr:`PhotoLayerItem.WARM_STEP`): the tiles are made in short idle
slices and, on OpenGL, drawn once invisibly so their textures are uploaded before the
first frame that shows them.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from functools import partial

import cv2
import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QPointF, QRectF, QSizeF, Qt, QTimer
from PyQt6.QtGui import QImage, QPaintEngine, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from app.imaging.pyramid import ImagePyramid

from . import device_scale

_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_ANTIALIAS = QPainter.RenderHint.Antialiasing

_GL_ENGINES = (QPaintEngine.Type.OpenGL, QPaintEngine.Type.OpenGL2)

Rect = tuple[int, int, int, int]  # (x, y, width, height)
MakeTile = Callable[[int, int, int, int, int], QPixmap]  # (level, x0, y0, x1, y1) -> pixmap
TileKey = tuple[int, int, int]  # (level, tx, ty)


def _is_gl(painter: QPainter) -> bool:
    engine = painter.paintEngine()
    return engine is not None and engine.type() in _GL_ENGINES


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

    ``make`` must not hold the item that keeps these levels (a bound method of it would):
    that reference cycle keeps a removed item, its pyramid and its tile pixmaps allocated
    until a cyclic-GC pass frees them, in a pause of whichever thread runs it.
    """

    def __init__(self, make: MakeTile, *, tile: int, margin: int, single_pixels: int) -> None:
        """Tiles of ``tile`` px (plus ``margin``) made by ``make(level, x0, y0, x1, y1)``."""
        self._make = make
        self._tile = tile
        self._margin = margin
        self._single = single_pixels
        # (level, tx, ty) -> (pixmap, level x, level y of its top-left pixel)
        self._tiles: dict[TileKey, tuple[QPixmap, int, int]] = {}
        self._undrawn: set[TileKey] = set()  # made ahead by warm(), not drawn yet

    def clear(self) -> None:
        """Forget every tile."""
        self._tiles.clear()
        self._undrawn.clear()

    def built(self) -> dict[TileKey, int]:
        """``(level, tx, ty) -> cacheKey`` of every made tile."""
        return {key: tile[0].cacheKey() for key, tile in self._tiles.items()}

    def levels(self) -> set[int]:
        """The levels that have tiles."""
        return {key[0] for key in self._tiles}

    def retain(self, levels: set[int] | frozenset[int]) -> None:
        """Forget the tiles of every level not in ``levels``."""
        for key in [key for key in self._tiles if key[0] not in levels]:
            self._forget(key)

    def drop(self, level: int, rect: Rect, level_size: tuple[int, int]) -> None:
        """Forget the tiles of ``level`` whose pixels (margin included) touch the level-0
        ``rect``; they are made again when next drawn."""
        lw, lh = level_size
        if lw * lh <= self._single:
            self._forget((level, 0, 0))
            return
        x, y, w, h = rect
        step = (1 << level) - 1
        t, m = self._tile, self._margin
        x0, y0 = (x >> level) - m, (y >> level) - m
        x1, y1 = ((x + w + step) >> level) + m, ((y + h + step) >> level) + m
        for ty in range(max(0, y0) // t, max(0, y1 - 1) // t + 1):
            for tx in range(max(0, x0) // t, max(0, x1 - 1) // t + 1):
                self._forget((level, tx, ty))

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
        for key, where in self._shown(level, level_size, image_size, exposed):
            pixmap, ox, oy = self._get(*key, level_size)
            self._undrawn.discard(key)
            if isinstance(where, tuple):  # a one-pixmap level: its exposed box
                _draw_area(painter, pixmap, ox, oy, where, f, image_size)
                continue
            painter.save()
            painter.setClipRect(where, Qt.ClipOperation.IntersectClip)
            area = (ox, oy, ox + pixmap.width(), oy + pixmap.height())
            _draw_area(painter, pixmap, ox, oy, area, f, image_size)
            painter.restore()

    def warm(
        self,
        level: int,
        level_size: tuple[int, int],
        image_size: tuple[int, int],
        exposed: QRectF,
        deadline: float,
    ) -> bool:
        """Make the tiles :meth:`draw` would show for ``exposed``, at least one and then
        until ``deadline`` (a ``time.perf_counter()`` value); True while some are missing.

        The tiles made here wait for :meth:`upload`.
        """
        made = 0
        for key, _where in self._shown(level, level_size, image_size, exposed):
            if key in self._tiles:
                continue
            if made and time.perf_counter() >= deadline:
                return True
            self._get(*key, level_size)
            self._undrawn.add(key)
            made += 1
        return False

    def pending_uploads(self) -> int:
        """How many tiles :meth:`warm` made that nothing has drawn yet."""
        return len(self._undrawn)

    def upload(self, painter: QPainter, limit: int) -> bool:
        """On an OpenGL engine, draw up to ``limit`` tiles made ahead once, invisibly
        (opacity 0, one pixel): their textures are uploaded now instead of in the first
        frame that shows them. Nothing on other engines. True while some still wait."""
        if not self._undrawn or not _is_gl(painter):
            return False
        painter.save()
        painter.setOpacity(0.0)
        for _ in range(min(limit, len(self._undrawn))):
            tile = self._tiles.get(self._undrawn.pop())
            if tile is not None:
                pixmap = tile[0]
                painter.drawPixmap(QRectF(0, 0, 1, 1), pixmap, QRectF(pixmap.rect()))
        painter.restore()
        return bool(self._undrawn)

    def _shown(
        self,
        level: int,
        level_size: tuple[int, int],
        image_size: tuple[int, int],
        exposed: QRectF,
    ) -> Iterator[tuple[TileKey, tuple[int, int, int, int] | QRectF]]:
        """The tiles :meth:`draw` shows for ``exposed``: ``(key, box)`` of a one-pixmap level
        (its level-pixel box, margin included), else ``(key, cell)`` for each tile whose cell
        (image px) meets ``exposed``."""
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
            yield (level, 0, 0), (x0, y0, x1, y1)
            return
        t = self._tile
        width, height = image_size
        for ty in range(y0 // t, (y1 - 1) // t + 1):
            for tx in range(x0 // t, (x1 - 1) // t + 1):
                left, top = tx * t * f, ty * t * f
                right = min(min((tx + 1) * t, lw) * f, width)
                bottom = min(min((ty + 1) * t, lh) * f, height)
                cell = QRectF(left, top, right - left, bottom - top).intersected(exposed)
                if not cell.isEmpty():
                    yield (level, tx, ty), cell

    def _forget(self, key: TileKey) -> None:
        self._tiles.pop(key, None)
        self._undrawn.discard(key)

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


def _pyramid_tile(pyramid: ImagePyramid, level: int, x0: int, y0: int, x1: int, y1: int) -> QPixmap:
    """Level pixels ``[x0, x1) x [y0, y1)`` of ``pyramid`` level ``level`` as a pixmap."""
    return array_to_pixmap(pyramid.level(level)[y0:y1, x0:x1])


class PhotoLayerItem(QGraphicsItem):
    """The photo, smooth at every zoom: an area-averaged pyramid level chosen from the
    device scale, drawn with bilinear filtering (exposed rect only)."""

    # Level pixels of bilinear context drawn beyond the exposed rect, and carried by each
    # tile beyond its cell: a partial repaint equals a full one (no seams).
    MARGIN = 2
    TILE = 512  # level pixels per tile side; a level up to (4 * TILE) ** 2 px is one pixmap
    # Below 100 % the full-size level is minified by up to 2x, which aliases fine texture.
    # From this fraction of that octave down (device scale 2 ** -0.4, about 0.76) the
    # half-size level is blended over it, its weight rising linearly in log2 scale to 1 at
    # 0.5, where it takes over: continuous there, and as sharp as before from 0.76 up. On
    # the real 640 x 480 JPEGs this is at least as close to the ideal area resample as the
    # plain draw at every zoom (closer from 0.55 to 0.7); a plain trilinear blend (from 1.0)
    # and blending the lower octaves were softer than the ideal, so neither is done.
    BLEND_FROM = 0.4
    _MIN_BLEND = 1 / 64  # a weight too small to see: the second draw is skipped
    # Idle warm-up: what one zoom step in or out by WARM_STEP (the ladder's largest ratio,
    # 0.333 -> 0.5) would draw, WARM_DELAY_MS after the last paint (a glide or a burst of
    # wheel notches paints faster), in slices of WARM_BUDGET_MS; on OpenGL then
    # UPLOADS_PER_FRAME made-ahead tiles are drawn invisibly per frame (their upload).
    WARM_STEP = 1.5
    WARM_DELAY_MS = 120
    WARM_BUDGET_MS = 4.0
    UPLOADS_PER_FRAME = 6

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
        self._blend_painted = 0.0
        self._warm_plan: list[tuple[int, QRectF]] = []  # (level, image-px area) to warm
        self._warm_gl = False  # the last paint was on OpenGL: uploads follow the warm-up
        # A pixel of the photo in view when the plan was made: an update there brings a
        # frame (a view repaints only for updates that meet its viewport).
        self._warm_spot = QRectF(0, 0, 1, 1)
        self._warm_timer: QTimer | None = None  # made at the first paint that plans
        # Makes option.exposedRect the dirty part rather than the whole item.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    # ------------------------------------------------------------------ API
    def set_image(self, image: np.ndarray | None) -> None:
        """Show ``image`` (uint8 BGR ``(h, w, 3)`` or gray ``(h, w)``); ``None`` clears.

        The array is kept by reference and never modified; nothing is converted here.
        """
        self._pyramid = None if image is None else ImagePyramid(image)
        self._levels = None
        self._warm_plan = []  # the old image's warm-up is void
        if self._warm_timer is not None:
            self._warm_timer.stop()
        if image is not None:
            single = (4 * self.TILE) ** 2
            # Tiles made from the pyramid, not through this item: no reference cycle, so
            # the item and its pixmaps go as soon as its scene lets it go.
            self._levels = TiledLevels(
                partial(_pyramid_tile, self._pyramid),
                tile=self.TILE,
                margin=self.MARGIN,
                single_pixels=single,
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

    def blend_painted(self) -> float:
        """Weight of the half-size level blended over level 0 in the last paint (0: none)."""
        return self._blend_painted

    def warm_levels(self, budget_ms: float = WARM_BUDGET_MS) -> bool:
        """Make tiles of the warm-up plan (what the last paint's next zoom step would draw)
        for about ``budget_ms`` (at least one tile); True while some remain.

        Runs by itself when the view is idle; tests and benches may call it directly.
        """
        levels, pyramid = self._levels, self._pyramid
        if levels is None or pyramid is None:
            self._warm_plan = []
            return False
        deadline = time.perf_counter() + budget_ms / 1000.0
        size = (self._width, self._height)
        while self._warm_plan:
            level, area = self._warm_plan[0]
            if levels.warm(level, pyramid.level_size(level), size, area, deadline):
                return True
            self._warm_plan.pop(0)
        return False

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
        level, blend = 0, 0.0
        if self._smooth:
            scale = device_scale(painter)
            level = ImagePyramid.level_for_scale(scale, pyramid.count)
            if level == 0 and pyramid.count > 1:
                blend = self._blend_weight(scale)
        old_smooth, old_aa = painter.testRenderHint(_SMOOTH), painter.testRenderHint(_ANTIALIAS)
        if old_smooth != self._smooth:
            painter.setRenderHint(_SMOOTH, self._smooth)
        if old_aa:
            painter.setRenderHint(_ANTIALIAS, False)  # plain rect fills, hard rect clips
        size = (self._width, self._height)
        levels.draw(painter, level, pyramid.level_size(level), size, exposed)
        if blend:
            opacity = painter.opacity()
            painter.setOpacity(opacity * blend)  # over the opaque level 0: a weighted mean
            levels.draw(painter, 1, pyramid.level_size(1), size, exposed)
            painter.setOpacity(opacity)
        if old_smooth != self._smooth:
            painter.setRenderHint(_SMOOTH, old_smooth)
        if old_aa:
            painter.setRenderHint(_ANTIALIAS, True)
        self._level_painted = level
        self._blend_painted = blend
        if self._smooth:
            self._plan_warm(painter, widget, exposed)
            more = levels.upload(painter, self.UPLOADS_PER_FRAME)
            if more and self._warm_timer is not None:
                self._warm_timer.start(0)  # another frame for the rest of the uploads

    @classmethod
    def _blend_weight(cls, scale: float) -> float:
        """Weight of level 1 over level 0 at device ``scale`` (0 when too small to show)."""
        if not 0.0 < scale < 1.0:
            return 0.0
        start = cls.BLEND_FROM
        weight = 1.0 if start >= 1.0 else (-math.log2(scale) - start) / (1.0 - start)
        if start >= 1.0 or weight < cls._MIN_BLEND:
            return 0.0
        return min(1.0, weight)

    # ------------------------------------------------------------- internals
    def _levels_at(self, scale: float) -> tuple[int, ...]:
        """The pyramid levels a paint at device ``scale`` draws."""
        count = self._pyramid.count
        level = ImagePyramid.level_for_scale(scale, count)
        if level == 0 and count > 1 and self._blend_weight(scale):
            return 0, 1
        return (level,)

    def _plan_warm(self, painter: QPainter, widget: QWidget | None, exposed: QRectF) -> None:
        """Plan what a zoom step in or out from this paint would draw; warm it when idle.

        The area is the part of the image in view (the viewport mapped back; the exposed
        rect without a widget): a step in shows part of it, a step out by ``WARM_STEP``
        about any anchor in the view at most the view grown by half its size on every side.
        """
        if QCoreApplication.instance() is None:
            return
        view = exposed
        if widget is not None:
            inverse, ok = painter.worldTransform().inverted()
            if ok:
                view = inverse.mapRect(QRectF(widget.rect())).intersected(self.boundingRect())
        if view.isEmpty():
            return
        grow_x, grow_y = view.width() / 2, view.height() / 2
        grown = view.adjusted(-grow_x, -grow_y, grow_x, grow_y).intersected(self.boundingRect())
        scale = device_scale(painter)
        plan = [(level, view) for level in self._levels_at(scale * self.WARM_STEP)]
        plan += [(level, grown) for level in self._levels_at(scale / self.WARM_STEP)]
        self._warm_plan = plan
        self._warm_gl = _is_gl(painter)
        centre = view.center()
        self._warm_spot = QRectF(math.floor(centre.x()), math.floor(centre.y()), 1, 1)
        if self._warm_timer is None:
            self._warm_timer = QTimer()
            self._warm_timer.setSingleShot(True)
            self._warm_timer.timeout.connect(self._warm_tick)
        self._warm_timer.start(self.WARM_DELAY_MS)  # restarted by every paint: idle only

    def _warm_tick(self) -> None:
        """One idle slice: make tiles; then, on OpenGL, a frame to upload them."""
        if sip.isdeleted(self) or self._levels is None:
            return  # the item is gone, or it shows nothing any more
        if self.warm_levels(self.WARM_BUDGET_MS):
            self._warm_timer.start(0)  # the next slice, after the events waiting
        elif self._warm_gl and self._levels.pending_uploads():
            # A frame; its paint draws them invisibly. Asked for over a pixel in view: an
            # update off screen (the top-left pixel once zoomed in) brings no frame.
            self.update(self._warm_spot)

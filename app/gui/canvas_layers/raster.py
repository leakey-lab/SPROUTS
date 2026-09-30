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

Once the view has been still for a moment, and only while a view shows the layer, the
layer makes ahead what the next zoom would draw (see :attr:`RasterLayerItem.WARM_STEP`):
a step in over the view, a step out over the view grown by half its size, and from a
minified zoom the exact tiles of actual pixels about the view's centre (Ctrl+1, the zoom
readout, the zoom presets). The tiles are made in short idle slices and, on OpenGL, drawn
once invisibly so their textures are uploaded before the first frame that shows them. An
edit drops the tiles it touches at every level; coverage tiles are kept only for the level
last drawn and the levels planned, and let go when idle.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QPointF, QRect, QRectF, QSizeF, Qt, QTimer
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from app.imaging.pyramid import ImagePyramid, area_half

from . import device_scale
from .photo import TiledLevels, _is_gl

ImageSource = Callable[[], "QImage | None"]
Rect = tuple[int, int, int, int]

_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_ANTIALIAS = QPainter.RenderHint.Antialiasing
_PREMULTIPLIED = QImage.Format.Format_ARGB32_Premultiplied
_MAX_PENDING = 32  # coverage rects kept apart before they are merged into their union
_EXACT = -1  # a warm-up entry of exact-pixel tiles (coverage entries are levels 0, 1, ...)
_ACTUAL_MARGIN = 2  # canvas px around the actual-pixels view planned (its anchor rounds)


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


def _coverage_count(width: int, height: int, min_side: int) -> int:
    """Coverage levels of a ``width`` x ``height`` canvas, level 0 included, without building
    them: level 1 is the half-size canvas, then halving (ceil) until the longer side is at
    most ``min_side`` (what ``1 + ImagePyramid(level 1, min_side).count`` gives)."""
    w, h = (width + 1) // 2, (height + 1) // 2
    count, limit = 2, max(1, int(min_side))
    while max(w, h) > limit:
        w, h = (w + 1) // 2, (h + 1) // 2
        count += 1
    return count


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

    def level(self, canvas: QImage, level: int) -> np.ndarray:
        """Coverage ``level`` (>= 1) of ``canvas``, up to date (its size: the canvas halved,
        ceil, ``level`` times; the count: :func:`_coverage_count`)."""
        self._sync(canvas)
        return self._levels.level(level - 1)

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
    # Idle warm-up (as the photo's): what a zoom step in or out by WARM_STEP would draw,
    # WARM_DELAY_MS after the last paint, in slices of WARM_BUDGET_MS, only while a view
    # shows the layer; on OpenGL then made-ahead tiles are drawn invisibly, per frame
    # UPLOADS_PER_FRAME exact tiles (256 KiB each) or COVER_UPLOADS_PER_FRAME coverage
    # tiles (up to 1 MiB each), the step in's kind first: short idle frames.
    WARM_STEP = 1.5
    WARM_DELAY_MS = 120
    WARM_BUDGET_MS = 4.0
    UPLOADS_PER_FRAME = 6
    COVER_UPLOADS_PER_FRAME = 2

    def __init__(self, source: ImageSource, parent: QGraphicsItem | None = None) -> None:
        """Draw ``source()``: the canvas ``QImage`` (ideally ``Format_ARGB32_Premultiplied``),
        or ``None`` while nothing is open. Never keeps a reference to the image."""
        super().__init__(parent)
        self._source = source
        self._tiles: dict[tuple[int, int], QPixmap] = {}
        self._undrawn: set[tuple[int, int]] = set()  # exact tiles made ahead, not drawn yet
        self._coverage = _Coverage()
        self._cover: TiledLevels | None = None  # coverage tiles: level drawn + levels planned
        # The coverage level last drawn: its tiles stay while exact pixels are shown (freeing
        # GL textures costs the next frame; zooming back out needs them again).
        self._cover_drawn: int | None = None
        self._cover_keep: frozenset[int] = frozenset()  # levels to keep (drawn + planned)
        self._cover_kept: frozenset[int] = frozenset()  # what the tiles were last cut to
        self._drawing: QImage | None = None  # the canvas, during a paint or warm-up only
        self._warm_plan: list[tuple[int, QRectF]] = []  # (_EXACT or coverage level, area)
        self._warm_gl = False  # the last paint was on OpenGL: uploads follow the warm-up
        self._warm_exact_first = True  # a step in shows exact tiles: upload them first
        self._warm_spot = QRectF(0, 0, 1, 1)  # a pixel in view: an update there brings a frame
        self._warm_timer: QTimer | None = None  # made at the first paint that plans
        self._size = self._source_size()
        # Makes option.exposedRect the dirty part rather than the whole item.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    # ------------------------------------------------------------------ API
    def canvas_changed(self, rect: QRect) -> None:
        """Pixels inside ``rect`` (image pixels) were painted: drop the tiles it touches
        (made-ahead ones and every coverage level's too), mark the coverage stale there
        and repaint that part."""
        rect = rect.intersected(QRect(0, 0, int(self._size.width()), int(self._size.height())))
        if rect.isEmpty():
            return
        t = self.TILE
        for ty in range(rect.top() // t, rect.bottom() // t + 1):
            for tx in range(rect.left() // t, rect.right() // t + 1):
                self._tiles.pop((tx, ty), None)
                self._undrawn.discard((tx, ty))
        box = (rect.x(), rect.y(), rect.width(), rect.height())
        self._coverage.changed(box)
        if self._cover is not None:
            for level in self._cover.levels():
                self._cover.drop(level, box, self._level_size(level))
        self.update(QRectF(rect))

    def canvas_replaced(self) -> None:
        """The canvas was replaced, resized or changed wholesale: rebuild and repaint all."""
        self._tiles.clear()
        self._undrawn.clear()
        self._coverage.reset()
        self._cover, self._cover_drawn = None, None
        self._cover_keep = self._cover_kept = frozenset()
        self._warm_plan = []  # the old canvas's warm-up is void
        if self._warm_timer is not None:
            self._warm_timer.stop()
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

    def warm_tiles(self, budget_ms: float = WARM_BUDGET_MS) -> bool:
        """Make tiles of the warm-up plan (what the last paint's next zoom would draw) for
        about ``budget_ms`` (at least one tile); True while some remain.

        Runs by itself while a view shows the layer and is idle; tests and benches may call
        it directly.
        """
        image = self._source()
        if image is None or image.isNull():
            self._warm_plan = []
            return False
        deadline = time.perf_counter() + budget_ms / 1000.0
        self._keep_covers()
        size = (image.width(), image.height())
        self._drawing = image
        try:
            while self._warm_plan:
                kind, area = self._warm_plan[0]
                if kind == _EXACT:
                    more = self._warm_exact(image, area, deadline)
                else:
                    level_size = self._level_size(kind)
                    more = self._covers().warm(kind, level_size, size, area, deadline)
                if more:
                    return True
                self._warm_plan.pop(0)
        finally:
            self._drawing = None  # never keep the canvas: it would detach on paint
        return False

    def pending_uploads(self) -> int:
        """How many tiles the warm-up made that nothing has drawn yet."""
        cover = 0 if self._cover is None else self._cover.pending_uploads()
        return len(self._undrawn) + cover

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
        """Draw the exposed part: exact tiles when magnified, area coverage when minified;
        then plan the idle warm-up (on OpenGL, upload a few tiles made ahead)."""
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
        self._plan_warm(painter, widget, exposed, scale)
        if self._upload(painter) and self._warm_timer is not None:
            self._warm_timer.start(0)  # another frame for the rest of the uploads

    # ------------------------------------------------------------- internals
    def _source_size(self) -> QSizeF:
        image = self._source()
        if image is None or image.isNull():
            return QSizeF(0, 0)
        return QSizeF(image.size())

    def _level_size(self, level: int) -> tuple[int, int]:
        """``(w, h)`` of coverage ``level``: the canvas halved (ceil) ``level`` times."""
        f = (1 << level) - 1
        return (int(self._size.width()) + f) >> level, (int(self._size.height()) + f) >> level

    def _coverage_level(self, scale: float) -> int:
        """The coverage level drawn at device ``scale`` (< 1): 0 above 0.5 (the canvas
        itself), else the pyramid level for it."""
        if scale > 0.5:
            return 0
        width, height = int(self._size.width()), int(self._size.height())
        count = _coverage_count(width, height, _Coverage.MIN_SIDE)
        return ImagePyramid.level_for_scale(scale, count)

    def _covers(self) -> TiledLevels:
        """The coverage tiles (made at their first use)."""
        if self._cover is None:
            t = self.COVER_TILE
            self._cover = TiledLevels(
                self._make_cover_tile, tile=t, margin=self.MARGIN, single_pixels=(2 * t) ** 2
            )
        return self._cover

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
                self._undrawn.discard((tx, ty))
                x, y, w, h = tx * t, ty * t, pixmap.width(), pixmap.height()
                target = _snapped_rect(x, y, w, h, dev) if snap else QRectF(x, y, w, h)
                painter.drawPixmap(target, pixmap, QRectF(0, 0, w, h))

    def _paint_coverage(
        self, painter: QPainter, image: QImage, exposed: QRectF, scale: float
    ) -> None:
        """The coverage level for ``scale``, bilinear, from its tiles."""
        level = self._coverage_level(scale)
        painter.setRenderHint(_SMOOTH, True)
        size = (image.width(), image.height())
        self._covers().draw(painter, level, self._level_size(level), size, exposed)
        self._cover_drawn = level

    def _make_cover_tile(self, level: int, x0: int, y0: int, x1: int, y1: int) -> QPixmap:
        canvas = self._drawing
        if level == 0:
            image = canvas.copy(QRect(x0, y0, x1 - x0, y1 - y0))
            if image.format() != _PREMULTIPLIED:
                image = image.convertToFormat(_PREMULTIPLIED)
            return QPixmap.fromImage(image)
        return _array_pixmap(self._coverage.level(canvas, level)[y0:y1, x0:x1])

    # ------------------------------------------------------------- idle warm-up
    def _warm_kind(self, scale: float) -> int:
        """What a paint at device ``scale`` draws: ``_EXACT`` tiles or its coverage level."""
        return _EXACT if scale >= 1.0 else self._coverage_level(scale)

    def _plan_warm(
        self, painter: QPainter, widget: QWidget | None, exposed: QRectF, scale: float
    ) -> None:
        """Plan what the next zoom from this paint would draw; warm it once idle.

        The area is the part of the canvas in view (the viewport mapped back; the exposed
        rect without a widget): a step in by ``WARM_STEP`` shows part of it, a step out at
        most the view grown by half its size on every side, whatever the anchor. From a
        zoom whose step in stays minified, actual pixels about the view's centre is planned
        too (Ctrl+1, the zoom readout; the presets above 100 % show part of it).
        """
        if QCoreApplication.instance() is None:
            return
        bounds = self.boundingRect()
        view, actual = exposed, QRectF()
        if widget is not None:
            inverse, ok = painter.worldTransform().inverted()
            if ok:
                frame = QRectF(widget.rect())
                view = inverse.mapRect(frame).intersected(bounds)
                centre = inverse.map(frame.center())
                item = self.sceneTransform()  # canvas px per scene px (1 in the editors)
                w = frame.width() / max(1e-9, item.m11()) + 2 * _ACTUAL_MARGIN
                h = frame.height() / max(1e-9, item.m22()) + 2 * _ACTUAL_MARGIN
                actual = QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h).intersected(bounds)
        if view.isEmpty():
            return
        grow_x, grow_y = view.width() / 2, view.height() / 2
        grown = view.adjusted(-grow_x, -grow_y, grow_x, grow_y).intersected(bounds)
        step_in = self._warm_kind(scale * self.WARM_STEP)
        plan = [(step_in, view), (self._warm_kind(scale / self.WARM_STEP), grown)]
        if step_in != _EXACT and not actual.isEmpty():
            plan.append((_EXACT, actual))
        self._warm_plan = plan
        self._warm_gl = _is_gl(painter)
        self._warm_exact_first = step_in == _EXACT
        centre = view.center()
        self._warm_spot = QRectF(math.floor(centre.x()), math.floor(centre.y()), 1, 1)
        keep = {kind for kind, _area in plan if kind != _EXACT}
        if self._cover_drawn is not None:
            keep.add(self._cover_drawn)
        self._cover_keep = frozenset(keep)  # applied when idle: freeing costs frame time
        if self._warm_timer is None:
            self._warm_timer = QTimer()
            self._warm_timer.setSingleShot(True)
            self._warm_timer.timeout.connect(self._warm_tick)
        self._warm_timer.start(self.WARM_DELAY_MS)  # restarted by every paint: idle only

    def _keep_covers(self) -> None:
        """Keep the coverage tiles of the level last drawn and of the levels planned only
        (by the last paint): the other levels' tiles, and on GL their textures, are freed."""
        keep = self._cover_keep
        if keep != self._cover_kept:
            self._cover_kept = keep
            if self._cover is not None:
                self._cover.retain(keep)

    def _warm_exact(self, image: QImage, area: QRectF, deadline: float) -> bool:
        """Make the exact tiles over ``area``, at least one and then until ``deadline``;
        True while some are missing. They wait for :meth:`_upload`."""
        box = area.toAlignedRect().intersected(image.rect())
        if box.isEmpty():
            return False
        t, made = self.TILE, 0
        for ty in range(box.top() // t, box.bottom() // t + 1):
            for tx in range(box.left() // t, box.right() // t + 1):
                if (tx, ty) in self._tiles:
                    continue
                if made and time.perf_counter() >= deadline:
                    return True
                self._tile(image, tx, ty)
                self._undrawn.add((tx, ty))
                made += 1
        return False

    def _upload(self, painter: QPainter) -> bool:
        """On an OpenGL engine, draw a few tiles made ahead once, invisibly (opacity 0,
        one pixel): their textures are uploaded now instead of in the first frame that shows
        them. One kind per frame (see ``UPLOADS_PER_FRAME``). Nothing on other engines.
        True while some still wait."""
        if not self.pending_uploads() or not _is_gl(painter):
            return False
        cover = self._cover is not None and self._cover.pending_uploads() > 0
        if cover and not (self._undrawn and self._warm_exact_first):
            self._cover.upload(painter, self.COVER_UPLOADS_PER_FRAME)
        else:
            painter.save()
            painter.setOpacity(0.0)
            limit = self.UPLOADS_PER_FRAME
            while self._undrawn and limit:
                pixmap = self._tiles.get(self._undrawn.pop())
                if pixmap is not None:
                    painter.drawPixmap(QRectF(0, 0, 1, 1), pixmap, QRectF(pixmap.rect()))
                    limit -= 1
            painter.restore()
        return self.pending_uploads() > 0

    def _on_screen(self) -> bool:
        """True while the layer is shown and a view of its scene is visible."""
        scene = self.scene()
        if not self.isVisible() or scene is None:
            return False
        return any(view.isVisible() for view in scene.views())

    def _warm_tick(self) -> None:
        """One idle slice while the layer is on screen; then, on OpenGL, a frame to upload.

        Off screen (another page, the layer hidden) the plan is dropped: the next paint
        plans again.
        """
        if sip.isdeleted(self):
            return  # the item is gone
        if not self._on_screen():
            self._warm_plan = []
            return
        if self.warm_tiles(self.WARM_BUDGET_MS):
            self._warm_timer.start(0)  # the next slice, after the events waiting
        elif self._warm_gl and self.pending_uploads():
            # A frame; its paint draws them invisibly. Asked for over a pixel in view: a
            # view repaints only for updates that meet its viewport.
            self.update(self._warm_spot)

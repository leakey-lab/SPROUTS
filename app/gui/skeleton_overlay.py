"""Scene helpers of the skeleton layer: its overlay palette and its decorations.

- :func:`overlay_indices` (Qt-free) turns the 1-px skeleton mask into palette indices:
  the line, plus a one-pixel dark halo so the skeleton-coloured line stays readable on
  bright roots (UXE-13). Display only: the model's mask is never modified. The
  skeleton layer item (:mod:`app.gui.canvas_layers.skeleton`) draws them with
  :data:`LINE_INDEX`, :data:`HALO_INDEX` and :data:`HALO_ALPHA`.
- :class:`SkeletonDecorations` owns the transient vector items drawn over the canvas:
  endpoint markers (one dots item), the highlighted endpoint, the polyline preview and
  its handles, and the Connect preview line. The dots (markers, highlight, handles) grow
  with the image up to 100 % and keep that screen size above it (:class:`DotsItem`), so a
  marker never hides the line it marks at 1600 %. Decorations sit on pixel centres, like
  the skeleton line at high zoom: a pixel ``(x, y)`` is drawn at ``(x + 0.5, y + 0.5)``.

Colours come from :mod:`app.gui.widgets.tokens`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPixmapCache,
    QTransform,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
    QWidget,
)

from app.editing.geometry import catmull_rom_bezier
from app.gui.widgets import tokens

LINE_INDEX = 255
HALO_INDEX = 128
HALO_ALPHA = 170  # BG_0 at ~67 %: the orange line reads at 9:1 against it
_KERNEL = np.ones((3, 3), np.uint8)

ENDPOINT_RADIUS = 4
HANDLE_RADIUS = 4
CENTRE = 0.5  # a pixel (x, y) is drawn around (x + 0.5, y + 0.5), where the line is
_REACH_PX = 2.5  # device px a dot's outline, antialiasing and rounding reach past its radius
_PARTIAL_MAX = 256  # more changed dots than this repaint the whole item
_SPRITES_MAX = 8  # dot sprites kept per item (one per zoom band / pixel ratio)


def overlay_indices(mask: np.ndarray) -> np.ndarray:
    """Palette indices for ``mask``: LINE_INDEX on the skeleton, HALO_INDEX on its
    8-neighbours, 0 elsewhere. ``mask`` is not modified."""
    line = mask > 0
    halo = cv2.dilate(line.view(np.uint8), _KERNEL)
    out = np.where(halo > 0, np.uint8(HALO_INDEX), np.uint8(0))
    out[line] = LINE_INDEX
    return out


def _cosmetic_pen(color: str | QColor, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCosmetic(True)
    return pen


def polyline_path(points: Sequence[tuple[float, float]], smooth: bool) -> QPainterPath:
    """Straight segments, or a Catmull-Rom curve through ``points`` when ``smooth``."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(float(points[0][0]), float(points[0][1]))
    if not smooth or len(points) < 3:
        for x, y in points[1:]:
            path.lineTo(float(x), float(y))
        return path
    for c1, c2, end in catmull_rom_bezier(points):
        path.cubicTo(*c1, *c2, *end)
    return path


def _dots(points: Iterable[tuple[float, float]], r: float) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)  # touching dots merge instead of cancelling
    for x, y in points:
        path.addEllipse(QPointF(float(x), float(y)), r, r)
    return path


def _centred(points: Iterable[tuple[float, float]] | np.ndarray) -> np.ndarray:
    """Pixels ``(x, y)`` as an ``(n, 2)`` float array of their centres."""
    if not isinstance(points, np.ndarray):
        points = list(points)
    return np.asarray(points, dtype=np.float64).reshape(-1, 2) + CENTRE


def _changed_rows(old: np.ndarray, new: np.ndarray) -> np.ndarray:
    """The rows of ``old`` missing from ``new`` and of ``new`` missing from ``old``."""
    if old.shape == new.shape and np.array_equal(old, new):
        return new[:0]
    a, b = old[:, 0] + 1j * old[:, 1], new[:, 0] + 1j * new[:, 1]
    return np.concatenate((old[~np.isin(a, b)], new[~np.isin(b, a)]))


class DotsItem(QGraphicsPathItem):
    """Dots of ``radius`` image px up to 100 % zoom, of ``radius`` screen px above it.

    Built for hundreds of dots that change a few at a time (the endpoint markers, S2):

    - :meth:`set_points` repaints only the dots that appeared or went, within bounds that
      only grow (a large change resets them), so a device cache redraws only those parts
      of its pixmap instead of every dot;
    - :meth:`paint` draws only the dots that meet the exposed rect, each a blit of one
      antialiased dot sprite (13x faster than filling a path of ellipses: 0.9 against
      11.7 ms for 1,629 dots), and makes QPixmapCache big enough for a device cache;
    - :meth:`path` builds the dots in image px on demand (hit tests, bounds checks).

    Above 100 % a dot is ``radius`` screen px wide, so it never grows past the line it
    marks.
    """

    def __init__(self, radius: float) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
        self._radius = float(radius)
        self._xy = np.zeros((0, 2), np.float64)  # dot centres, image px
        self._bounds = QRectF()
        self._full: QPainterPath | None = None  # path(), built on demand
        self._sprites: dict[tuple, tuple[QPixmap, float]] = {}

    # ------------------------------------------------------------------ data
    def set_points(self, points: Iterable[tuple[float, float]] | np.ndarray) -> None:
        """Dots at ``points`` (image px); only the dots that changed are repainted."""
        if not isinstance(points, np.ndarray):
            points = list(points)
        xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        old, self._xy, self._full = self._xy, xy, None
        if not len(xy):
            if len(old) or not self._bounds.isNull():
                self.prepareGeometryChange()
                self._bounds = QRectF()
                self.update()
            return
        changed = _changed_rows(old, xy)
        if not len(changed):
            return
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        pad = self._radius + 1.0
        needed = QRectF(lo[0] - pad, lo[1] - pad, hi[0] - lo[0] + 2 * pad, hi[1] - lo[1] + 2 * pad)
        if len(changed) > _PARTIAL_MAX or not self._bounds.contains(needed):
            if needed != self._bounds:
                self.prepareGeometryChange()
                big = len(changed) > _PARTIAL_MAX or self._bounds.isNull()
                self._bounds = needed if big else self._bounds.united(needed)
            self.update()
            return
        margin = self._repaint_margin()
        for x, y in changed.tolist():
            self.update(QRectF(x - margin, y - margin, 2 * margin, 2 * margin))

    def _repaint_margin(self) -> float:
        """Image px a dot reaches from its centre at the smallest zoom of the views."""
        scene = self.scene()
        views = scene.views() if scene is not None else []
        zoom = min((math.hypot(v.transform().m11(), v.transform().m12()) for v in views), default=1)
        zoom = max(zoom, 1e-3)
        return (self._radius * min(zoom, 1.0) + _REACH_PX) / zoom

    # ------------------------------------------------------------------ QGraphicsItem
    def boundingRect(self) -> QRectF:
        """Every dot with its outline, image px (only grows between large changes)."""
        return QRectF(self._bounds)

    def path(self) -> QPainterPath:
        """Every dot at ``radius`` image px."""
        if self._full is None:
            self._full = _dots(self._xy.tolist(), self._radius)
        return self._full

    def shape(self) -> QPainterPath:
        """The dots (hit tests)."""
        return self.path()

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        """The dots that meet ``option.exposedRect`` (every dot when it is empty)."""
        xy, world = self._xy, painter.worldTransform()
        zoom = abs(world.determinant()) ** 0.5
        if not len(xy) or zoom <= 0.0:
            return
        radius = self._radius * min(zoom, 1.0)  # screen px
        rect = option.exposedRect if option is not None else QRectF()
        if not rect.isEmpty():
            grow = (radius + _REACH_PX) / zoom
            keep = (xy[:, 0] >= rect.left() - grow) & (xy[:, 0] <= rect.right() + grow)
            keep &= (xy[:, 1] >= rect.top() - grow) & (xy[:, 1] <= rect.bottom() + grow)
            xy = xy[keep]
            if not len(xy):
                return
        self._make_cache_room(painter)
        if world.type().value > QTransform.TransformationType.TxScale.value:  # rotated
            painter.setPen(self.pen())
            painter.setBrush(self.brush())
            painter.drawPath(_dots(xy.tolist(), radius / zoom))
            return
        sprite, half = self._sprite(painter, radius)
        ratio = sprite.devicePixelRatio()
        # Whole device pixels, rounded the same wherever a device cache's pixmap starts.
        xs = np.floor((xy[:, 0] * world.m11() + (world.dx() - half)) * ratio + 0.5) / ratio
        ys = np.floor((xy[:, 1] * world.m22() + (world.dy() - half)) * ratio + 0.5) / ratio
        # Only the dots on the device (a device cache at 200 %: 108 of 1,738). viewport() is
        # in logical px for a widget and in pixels for a pixmap: at most too wide, never short.
        area, side = painter.viewport(), 2.0 * half
        on = (xs > area.left() - side) & (xs < area.right() + 1.0)
        on &= (ys > area.top() - side) & (ys < area.bottom() + 1.0)
        xs, ys = xs[on].tolist(), ys[on].tolist()
        painter.save()
        painter.resetTransform()  # blit in device-independent pixels, unscaled
        for x, y in zip(xs, ys, strict=True):
            painter.drawPixmap(QPointF(x, y), sprite)
        painter.restore()

    # ------------------------------------------------------------------ helpers
    def _sprite(self, painter: QPainter, radius: float) -> tuple[QPixmap, float]:
        """One dot of ``radius`` screen px as the painter would draw it, and its half size."""
        device = painter.device()
        ratio = device.devicePixelRatioF() if device is not None else 1.0
        smooth = painter.testRenderHint(QPainter.RenderHint.Antialiasing)
        pen, brush = self.pen(), self.brush()
        key = (
            round(radius, 4),
            ratio,
            smooth,
            pen.color().rgba(),
            pen.widthF(),
            brush.color().rgba(),
        )
        hit = self._sprites.get(key)
        if hit is not None:
            return hit
        half = math.ceil(radius + pen.widthF() + 1.0)
        side = max(1, math.ceil(2 * half * ratio))
        sprite = QPixmap(side, side)
        sprite.setDevicePixelRatio(ratio)
        sprite.fill(Qt.GlobalColor.transparent)
        p = QPainter(sprite)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, smooth)
        p.setPen(pen)
        p.setBrush(brush)
        p.drawEllipse(QPointF(half, half), radius, radius)
        p.end()
        if len(self._sprites) >= _SPRITES_MAX:
            self._sprites.clear()
        self._sprites[key] = (sprite, float(half))
        return sprite, float(half)

    def _make_cache_room(self, painter: QPainter) -> None:
        """Let QPixmapCache keep this item's device cache pixmap (the painter's device then).

        Its default 10 MiB holds less than one viewport pixmap on a large or HiDPI screen,
        and a pixmap that does not fit is never kept: every frame would redraw every dot
        (25 ms at 2600 x 1500 px with 1,738 dots, against 0.6 ms from the cache).
        """
        device = painter.device()
        if self.cacheMode() == QGraphicsItem.CacheMode.NoCache or device is None:
            return
        need_kb = 2 * device.width() * device.height() * max(device.depth(), 32) // 8192 + 4096
        if QPixmapCache.cacheLimit() < need_kb:
            QPixmapCache.setCacheLimit(need_kb)


class SkeletonDecorations:
    """Transient vector items over the skeleton canvas (never part of the saved data)."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self.endpoints_item = self._path_item(
            _cosmetic_pen(tokens.BG_0, 1), QBrush(QColor(tokens.SKEL)), z=3, dots=ENDPOINT_RADIUS
        )
        # Hundreds of markers: drawn once per zoom, then each frame is a blit.
        self.endpoints_item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.highlight_item = self._path_item(
            _cosmetic_pen(tokens.BG_0, 1),
            QBrush(QColor(tokens.SEL)),
            z=3.5,
            dots=ENDPOINT_RADIUS + 1,
        )
        self.polyline_item = self._path_item(_cosmetic_pen(tokens.ACCENT, 2), None, z=2)
        self.handles_item = self._path_item(
            _cosmetic_pen(tokens.ACCENT, 2), None, z=3, dots=HANDLE_RADIUS
        )
        self.connect_item = QGraphicsLineItem()
        self.connect_item.setPen(_cosmetic_pen(tokens.ACCENT, 2))
        self.connect_item.setZValue(2)
        self.connect_item.setVisible(False)
        scene.addItem(self.connect_item)

    def _path_item(
        self, pen: QPen, brush: QBrush | None, z: float, dots: float | None = None
    ) -> QGraphicsPathItem:
        item = QGraphicsPathItem() if dots is None else DotsItem(dots)
        item.setPen(pen)
        if brush is not None:
            item.setBrush(brush)
        item.setZValue(z)
        self._scene.addItem(item)
        return item

    def view_scale(self) -> float:
        """Screen px per image px of the first view showing the canvas (1 without one)."""
        views = self._scene.views()
        if not views:
            return 1.0
        transform = views[0].transform()
        return math.hypot(transform.m11(), transform.m12()) or 1.0

    # ---------------------------------------------------------------- endpoints
    def set_endpoints(self, points: Iterable[tuple[int, int]] | np.ndarray) -> None:
        """Markers on the pixels ``points`` (an ``(n, 2)`` array or ``(x, y)`` pairs); only
        the markers that changed are repainted."""
        self.endpoints_item.set_points(_centred(points))

    def set_endpoints_visible(self, visible: bool) -> None:
        self.endpoints_item.setVisible(visible)

    def highlight_endpoint(self, point: QPoint | None) -> None:
        pts = [] if point is None else [(point.x(), point.y())]
        self.highlight_item.set_points(_centred(pts))

    # ---------------------------------------------------------------- polyline
    def show_polyline(
        self, points: Sequence[QPoint], cursor: QPoint | None, *, smooth: bool
    ) -> None:
        pts = [(p.x(), p.y()) for p in points]
        handles = _centred(pts)
        if cursor is not None and (not pts or (cursor.x(), cursor.y()) != pts[-1]):
            pts.append((cursor.x(), cursor.y()))
        self.polyline_item.setPath(polyline_path(_centred(pts).tolist(), smooth))
        self.handles_item.set_points(handles)

    def clear_polyline(self) -> None:
        self.polyline_item.setPath(QPainterPath())
        self.handles_item.set_points(())

    # ---------------------------------------------------------------- connect
    def show_connect(self, start: QPoint, end: QPoint) -> None:
        self.connect_item.setLine(
            start.x() + CENTRE, start.y() + CENTRE, end.x() + CENTRE, end.y() + CENTRE
        )
        self.connect_item.setVisible(True)

    def clear_connect(self) -> None:
        self.connect_item.setVisible(False)

    def clear_transient(self) -> None:
        """Drop every in-progress preview (not the endpoint markers)."""
        self.clear_polyline()
        self.clear_connect()
        self.highlight_endpoint(None)

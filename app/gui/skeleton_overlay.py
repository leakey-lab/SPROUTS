"""Scene helpers of the skeleton layer: its overlay palette and its decorations.

- :func:`overlay_indices` (Qt-free) turns the 1-px skeleton mask into palette indices:
  the line, plus a one-pixel dark halo so the skeleton-coloured line stays readable on
  bright roots (UXE-13). Display only: the model's mask is never modified. The
  skeleton layer item (:mod:`app.gui.canvas_layers.skeleton`) draws them with
  :data:`LINE_INDEX`, :data:`HALO_INDEX` and :data:`HALO_ALPHA`.
- :class:`SkeletonDecorations` owns the transient vector items drawn over the canvas:
  endpoint markers (one path item), the highlighted endpoint, the polyline preview and
  its handles, and the Connect preview line. The dots (markers, highlight, handles) grow
  with the image up to 100 % and keep that screen size above it (:class:`DotsItem`), so a
  marker never hides the line it marks at 1600 %.

Colours come from :mod:`app.gui.widgets.tokens`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
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


class DotsItem(QGraphicsPathItem):
    """Dots of ``radius`` image px up to 100 % zoom, of ``radius`` screen px above it.

    ``path()`` holds the dots in image px (hit tests, bounds, and the drawing up to 100 %).
    Above 100 % the dots are drawn at ``radius / zoom`` image px, a path kept per zoom, so
    they never leave those bounds and the line they mark stays visible.
    """

    def __init__(self, radius: float) -> None:
        super().__init__()
        self._radius = float(radius)
        self._points: tuple[tuple[float, float], ...] = ()
        self._zoomed: tuple[float, QPainterPath] | None = None  # (radius, path) above 100 %

    def set_points(self, points: Iterable[tuple[float, float]]) -> None:
        """Dots at ``points`` (image px)."""
        self._points = tuple((float(x), float(y)) for x, y in points)
        self._zoomed = None
        self.setPath(_dots(self._points, self._radius))

    def paint(self, painter: QPainter, option, widget: QWidget | None = None) -> None:
        zoom = abs(painter.worldTransform().determinant()) ** 0.5
        if zoom <= 1.0 or not self._points:
            super().paint(painter, option, widget)
            return
        radius = self._radius / zoom
        if self._zoomed is None or self._zoomed[0] != radius:
            self._zoomed = (radius, _dots(self._points, radius))
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(self._zoomed[1])


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

    # ---------------------------------------------------------------- endpoints
    def set_endpoints(self, points: Iterable[tuple[int, int]]) -> None:
        self.endpoints_item.set_points(points)

    def set_endpoints_visible(self, visible: bool) -> None:
        self.endpoints_item.setVisible(visible)

    def highlight_endpoint(self, point: QPoint | None) -> None:
        pts = [] if point is None else [(point.x(), point.y())]
        self.highlight_item.set_points(pts)

    # ---------------------------------------------------------------- polyline
    def show_polyline(
        self, points: Sequence[QPoint], cursor: QPoint | None, *, smooth: bool
    ) -> None:
        pts = [(p.x(), p.y()) for p in points]
        if cursor is not None and (not pts or (cursor.x(), cursor.y()) != pts[-1]):
            pts.append((cursor.x(), cursor.y()))
        self.polyline_item.setPath(polyline_path(pts, smooth))
        self.handles_item.set_points((p.x(), p.y()) for p in points)

    def clear_polyline(self) -> None:
        self.polyline_item.setPath(QPainterPath())
        self.handles_item.set_points(())

    # ---------------------------------------------------------------- connect
    def show_connect(self, start: QPoint, end: QPoint) -> None:
        self.connect_item.setLine(
            float(start.x()), float(start.y()), float(end.x()), float(end.y())
        )
        self.connect_item.setVisible(True)

    def clear_connect(self) -> None:
        self.connect_item.setVisible(False)

    def clear_transient(self) -> None:
        """Drop every in-progress preview (not the endpoint markers)."""
        self.clear_polyline()
        self.clear_connect()
        self.highlight_endpoint(None)

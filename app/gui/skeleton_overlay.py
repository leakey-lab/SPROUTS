"""Scene helpers of the skeleton layer: its overlay palette and its decorations.

- :func:`overlay_indices` (Qt-free) turns the 1-px skeleton mask into palette indices:
  the line, plus a one-pixel dark halo so the skeleton-coloured line stays readable on
  bright roots (UXE-13). Display only: the model's mask is never modified. The
  skeleton layer item (:mod:`app.gui.canvas_layers.skeleton`) draws them with
  :data:`LINE_INDEX`, :data:`HALO_INDEX` and :data:`HALO_ALPHA`.
- :class:`SkeletonDecorations` owns the transient vector items drawn over the canvas:
  endpoint markers (one path item), the highlighted endpoint, the polyline preview and
  its handles, and the Connect preview line.

Colours come from :mod:`app.gui.widgets.tokens`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsPathItem, QGraphicsScene

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


class SkeletonDecorations:
    """Transient vector items over the skeleton canvas (never part of the saved data)."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self.endpoints_item = self._path_item(
            _cosmetic_pen(tokens.BG_0, 1), QBrush(QColor(tokens.SKEL)), z=3
        )
        self.highlight_item = self._path_item(
            _cosmetic_pen(tokens.BG_0, 1), QBrush(QColor(tokens.SEL)), z=3.5
        )
        self.polyline_item = self._path_item(_cosmetic_pen(tokens.ACCENT, 2), None, z=2)
        self.handles_item = self._path_item(_cosmetic_pen(tokens.ACCENT, 2), None, z=3)
        self.connect_item = QGraphicsLineItem()
        self.connect_item.setPen(_cosmetic_pen(tokens.ACCENT, 2))
        self.connect_item.setZValue(2)
        self.connect_item.setVisible(False)
        scene.addItem(self.connect_item)

    def _path_item(self, pen: QPen, brush: QBrush | None, z: float) -> QGraphicsPathItem:
        item = QGraphicsPathItem()
        item.setPen(pen)
        if brush is not None:
            item.setBrush(brush)
        item.setZValue(z)
        self._scene.addItem(item)
        return item

    @staticmethod
    def _dots(points: Iterable[tuple[float, float]], r: float) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)  # touching dots merge instead of cancelling
        for x, y in points:
            path.addEllipse(QPointF(float(x), float(y)), r, r)
        return path

    # ---------------------------------------------------------------- endpoints
    def set_endpoints(self, points: Iterable[tuple[int, int]]) -> None:
        self.endpoints_item.setPath(self._dots(points, ENDPOINT_RADIUS))

    def set_endpoints_visible(self, visible: bool) -> None:
        self.endpoints_item.setVisible(visible)

    def highlight_endpoint(self, point: QPoint | None) -> None:
        pts = [] if point is None else [(point.x(), point.y())]
        self.highlight_item.setPath(self._dots(pts, ENDPOINT_RADIUS + 1))

    # ---------------------------------------------------------------- polyline
    def show_polyline(
        self, points: Sequence[QPoint], cursor: QPoint | None, *, smooth: bool
    ) -> None:
        pts = [(p.x(), p.y()) for p in points]
        if cursor is not None and (not pts or (cursor.x(), cursor.y()) != pts[-1]):
            pts.append((cursor.x(), cursor.y()))
        self.polyline_item.setPath(polyline_path(pts, smooth))
        self.handles_item.setPath(self._dots([(p.x(), p.y()) for p in points], HANDLE_RADIUS))

    def clear_polyline(self) -> None:
        self.polyline_item.setPath(QPainterPath())
        self.handles_item.setPath(QPainterPath())

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

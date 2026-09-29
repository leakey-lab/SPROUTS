"""
QGraphicsView for the skeleton correction editor.

Zoom, fit, pan (sticky toggle, Space-hold, middle-drag) and the OpenGL viewport
come from :class:`CanvasViewMixin`, shared with the other canvases. This view
delegates tool input to the parent interface in image pixels:
``on_tool_mouse_press`` / ``on_tool_mouse_move`` (drags and hover) /
``on_tool_mouse_release`` / ``on_tool_mouse_double_click``, keys to
``on_key_press`` first, and every zoom change to ``on_zoom_changed``.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QCursor, QKeyEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import QGraphicsView

from .canvas_background import configure_canvas_background
from .canvas_view import CanvasViewMixin

logger = logging.getLogger(__name__)


class SkeletonCorrectionGraphicsView(CanvasViewMixin, QGraphicsView):
    """Canvas of the skeleton editor; tools live in the parent interface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.skeleton_interface = parent
        self._init_canvas()
        configure_canvas_background(self)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.LosslessImageRendering
            | QPainter.RenderHint.TextAntialiasing
        )
        self.zoom_changed.connect(self._report_zoom)

    def _report_zoom(self, factor: float) -> None:
        """Tell the editor about every zoom change (wheel, keys, fit, buttons)."""
        handler = getattr(self.skeleton_interface, "on_zoom_changed", None)
        if handler is not None:
            handler(factor)

    def _tool_cursor(self) -> QCursor | Qt.CursorShape:
        iface = self.skeleton_interface
        eraser = getattr(iface, "TOOL_ERASER", None)
        if eraser is not None and getattr(iface, "current_tool", None) == eraser:
            # Legacy bitmap ring; the cross is used once the editor drops it.
            cursor = getattr(iface, "brush_cursor", None)
            if cursor is not None:
                return cursor
        return Qt.CursorShape.CrossCursor

    def map_to_image(self, pos: QPointF) -> QPoint:
        return self.skeleton_interface.clamp_to_image(self._scene_pixel(pos))

    # ------------------------------------------------------------------ tools
    def _tool_press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or self.skeleton_interface is None:
            return False
        self.drawing = True
        self.skeleton_interface.on_tool_mouse_press(self.map_to_image(event.position()), event)
        event.accept()
        return True

    def _tool_move(self, event: QMouseEvent) -> bool:
        if self.skeleton_interface is None or self.is_panning_mode():
            return False
        if event.buttons() & Qt.MouseButton.LeftButton and not self.drawing:
            return False  # a left drag that did not start as a tool press
        # Drags of the current tool, and hover (polyline rubber band, previews).
        self.skeleton_interface.on_tool_mouse_move(self.map_to_image(event.position()), event)
        if self.drawing:
            event.accept()
            return True
        return False

    def _tool_release(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self.drawing:
            return False
        self.drawing = False
        self.skeleton_interface.on_tool_mouse_release(self.map_to_image(event.position()), event)
        event.accept()
        return True

    def _tool_double_click(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or self.skeleton_interface is None:
            return False
        self.skeleton_interface.on_tool_mouse_double_click(
            self.map_to_image(event.position()), event
        )
        event.accept()
        return True

    # --------------------------------------------------------------- keyboard
    def keyPressEvent(self, event: QKeyEvent):
        handler = getattr(self.skeleton_interface, "on_key_press", None)
        if handler is not None and handler(event):
            event.accept()
            return
        super().keyPressEvent(event)

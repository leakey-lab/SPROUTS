"""
QGraphicsView for the skeleton correction editor.

This mirrors the interaction patterns used in the existing mask tracing view:
- Ctrl + mouse wheel zoom
- Toggle pan mode (hand drag)
- Delegate tool-specific mouse handling to the parent interface
- GPU-accelerated rendering via QOpenGLWidget viewport
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import QGraphicsView, QWidget

from .canvas_background import configure_canvas_background

logger = logging.getLogger(__name__)


# See notes in other views: enabling QOpenGLWidget can break embedded QtWebEngine.
def _env_bool(name: str, *, default: bool) -> bool:
    v = os.environ.get(name, None)
    if v is None:
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "on")


# Enabled by default; temporarily disabled when showing QtWebEngine visualizations.
DEFAULT_OPENGL_VIEWPORT_ENABLED = _env_bool("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", default=True)

# Try to use OpenGL viewport for GPU-accelerated rendering
try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget

    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False


class SkeletonCorrectionGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.skeleton_interface = parent

        self._opengl_viewport_enabled = False
        self.set_opengl_viewport_enabled(DEFAULT_OPENGL_VIEWPORT_ENABLED)
        configure_canvas_background(self)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.LosslessImageRendering
            | QPainter.RenderHint.TextAntialiasing
        )

        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.pan_mode = False
        self.drawing = False
        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def set_opengl_viewport_enabled(self, enabled: bool) -> None:
        """Enable/disable QOpenGLWidget viewport at runtime (QtWebEngine compatibility)."""
        enabled = bool(enabled)

        if enabled and self._opengl_viewport_enabled:
            return
        if (not enabled) and (not self._opengl_viewport_enabled):
            return

        if enabled and HAS_OPENGL:
            try:
                self.setViewport(QOpenGLWidget())
                self._opengl_viewport_enabled = True
                return
            except (RuntimeError, OSError) as e:
                # OpenGL viewport creation can fail on systems without a usable
                # GL context; fall back to a plain QWidget viewport instead of
                # silently swallowing the error.
                logger.warning("Failed to set OpenGL viewport, falling back to software: %s", e)

        self.setViewport(QWidget())
        self._opengl_viewport_enabled = False

    def set_pan_mode(self, enabled: bool) -> None:
        self.pan_mode = enabled
        self.drawing = False
        self.update_cursor()

    def update_cursor(self) -> None:
        """Update cursor based on current mode and tool."""
        if self.pan_mode:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            # Use brush cursor for Eraser tool
            if (
                self.skeleton_interface
                and hasattr(self.skeleton_interface, "current_tool")
                and self.skeleton_interface.current_tool == self.skeleton_interface.TOOL_ERASER
            ):
                self.viewport().setCursor(self.skeleton_interface.brush_cursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.CrossCursor)
                # Ensure we reset the view cursor too if it was previously set
                self.setCursor(Qt.CursorShape.CrossCursor)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            zoom_in = event.angleDelta().y() > 0
            if zoom_in and self.zoom_factor < self.max_zoom:
                factor = 1.25
                self.zoom_factor *= factor
            elif not zoom_in and self.zoom_factor > self.min_zoom:
                factor = 0.8
                self.zoom_factor *= factor
            else:
                event.ignore()
                return

            self.scale(factor, factor)
            if hasattr(self.skeleton_interface, "on_zoom_changed"):
                self.skeleton_interface.on_zoom_changed(self.zoom_factor)
            event.accept()
            return

        super().wheelEvent(event)

    def map_to_image(self, pos: QPointF) -> QPoint:
        scene_pos = self.mapToScene(pos.toPoint())
        x = int(scene_pos.x())
        y = int(scene_pos.y())
        return self.skeleton_interface.clamp_to_image(QPoint(x, y))

    def mousePressEvent(self, event: QMouseEvent):
        if self.pan_mode:
            if event.button() in [Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton]:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            super().mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.map_to_image(event.position())
            self.drawing = True
            self.skeleton_interface.on_tool_mouse_press(pos, event)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.pan_mode:
            super().mouseMoveEvent(event)
            return

        # Forward mouse moves if drawing (dragging) OR if not panning (for hover effects like polyline preview)
        # Check if we should forward:
        # 1. We are drawing (Left button held from a press)
        # 2. Or we are tracking mouse (hover) and want to update previews (Polyline)

        # Note: self.drawing is set in mousePress.
        # If we just move without press, self.drawing is False.

        should_forward = False
        if self.drawing and (event.buttons() & Qt.MouseButton.LeftButton):
            should_forward = True
        elif not (event.buttons() & Qt.MouseButton.LeftButton):
            # Hover case (no left button)
            should_forward = True

        if should_forward:
            pos = self.map_to_image(event.position())
            self.skeleton_interface.on_tool_mouse_move(pos, event)
            if self.drawing:
                event.accept()
                return
            # If hovering, we usually still want to propagate to super to allow cursor updates etc,
            # but if we handled it meaningfully we might accept.
            # For now, let's just forward and then call super if not drawing.

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.pan_mode:
            if event.button() in [Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton]:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            super().mouseReleaseEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.map_to_image(event.position())
            self.drawing = False
            self.skeleton_interface.on_tool_mouse_release(pos, event)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if self.pan_mode:
            super().mouseDoubleClickEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = self.map_to_image(event.position())
            self.skeleton_interface.on_tool_mouse_double_click(pos, event)
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if hasattr(self.skeleton_interface, "on_key_press"):
            handled = self.skeleton_interface.on_key_press(event)
            if handled:
                event.accept()
                return
        super().keyPressEvent(event)

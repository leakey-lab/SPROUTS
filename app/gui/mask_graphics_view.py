"""
Graphics view for the mask tracing interface (Correct Mask and Trace GT).

Zoom, fit, pan and the OpenGL viewport come from :class:`CanvasViewMixin`
(shared with the other canvases). This view adds the editor-specific routing:
left-button strokes and hover moves go to the owning interface through
``begin_stroke`` / ``continue_stroke`` / ``end_stroke`` and
``update_brush_preview``, and B + wheel resizes the brush.
"""

import logging

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import QFrame, QGraphicsView

from .canvas_background import configure_canvas_background
from .canvas_view import CanvasViewMixin

logger = logging.getLogger(__name__)


class MaskTracingGraphicsView(CanvasViewMixin, QGraphicsView):
    """Canvas of the mask editor: strokes, brush-ring hover and B + wheel brush size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mask_tracing_interface = parent
        self._init_canvas()
        configure_canvas_background(self)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def _mask_pixmap(self):
        return getattr(self.mask_tracing_interface, "mask_pixmap", None)

    # ------------------------------------------------------------------ wheel
    def wheelEvent(self, event: QWheelEvent):
        iface = self.mask_tracing_interface
        # B held: the wheel changes brush size instead of the view.
        if not self.drawing and getattr(iface, "b_key_pressed", False):
            dy = event.angleDelta().y()
            if dy:
                iface.step_brush_size(1 if dy > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    # ----------------------------------------------------------------- cursor
    def refresh_cursor(self):
        super().refresh_cursor()
        iface = self.mask_tracing_interface
        if hasattr(iface, "set_brush_preview_visible"):
            iface.set_brush_preview_visible(not self.is_panning_mode())

    # ------------------------------------------------------------------ tools
    def _tool_press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self._mask_pixmap():
            return False
        self.drawing = True
        self.mask_tracing_interface.begin_stroke(self.map_to_image(event.position()))
        event.accept()
        return True

    def _tool_move(self, event: QMouseEvent) -> bool:
        if not self._mask_pixmap():
            return False
        iface = self.mask_tracing_interface
        pos = self.map_to_image(event.position())
        iface.update_brush_preview(pos)
        if self.drawing and event.buttons() & Qt.MouseButton.LeftButton:
            iface.continue_stroke(pos)
            event.accept()
            return True
        return False

    def _tool_release(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self.drawing:
            return False
        self.drawing = False
        self.mask_tracing_interface.end_stroke()
        event.accept()
        return True

    def leaveEvent(self, event):
        iface = self.mask_tracing_interface
        if hasattr(iface, "set_brush_preview_visible"):
            iface.set_brush_preview_visible(False)
        super().leaveEvent(event)

    def enterEvent(self, event):
        self.refresh_cursor()
        super().enterEvent(event)

    # ---------------------------------------------------------------- helpers
    def map_to_image(self, pos: QPointF) -> QPoint:
        """Map a viewport position to a clamped integer image pixel."""
        pixel = self._scene_pixel(pos)
        pixmap = self._mask_pixmap()
        x = max(0, min(pixel.x(), pixmap.width() - 1))
        y = max(0, min(pixel.y(), pixmap.height() - 1))
        return QPoint(x, y)

    def scrollContentsBy(self, dx: int, dy: int):
        """Prevent scrolling mid-stroke (the stroke would smear)."""
        if not self.drawing:
            super().scrollContentsBy(dx, dy)

    def ensureVisible(self, *args, **kwargs):
        """Prevent automatic scrolling during drawing."""
        if not self.drawing:
            super().ensureVisible(*args, **kwargs)

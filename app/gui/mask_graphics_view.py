"""
Graphics view for the mask tracing interface.
Handles zooming, panning, and drawing event routing.

Zoom model: the view transform is the single source of truth. ``zoom_factor``
is read from it, every zoom goes through :meth:`set_zoom`, and
``zoom_changed`` tells the interface to refresh its readout. Panning works in
three ways without leaving the current tool: the pan toggle, holding Space,
or dragging with the middle mouse button.
"""

import logging
import math
import os

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPainter, QTransform, QWheelEvent
from PyQt6.QtWidgets import QFrame, QGraphicsView, QWidget

from .canvas_background import configure_canvas_background

logger = logging.getLogger(__name__)


def _env_bool(name: str, *, default: bool) -> bool:
    v = os.environ.get(name, None)
    if v is None:
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "on")


# Enabled by default; temporarily disabled when showing QtWebEngine visualizations.
DEFAULT_OPENGL_VIEWPORT_ENABLED = _env_bool("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", default=True)

try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget

    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False


class MaskTracingGraphicsView(QGraphicsView):
    """
    Custom QGraphicsView for mask tracing with zoom, pan, and draw modes.

    Drawing is delegated to the owning interface through ``begin_stroke`` /
    ``continue_stroke`` / ``end_stroke`` and ``update_brush_preview``.
    """

    zoom_changed = pyqtSignal(float)

    MIN_ZOOM = 0.1
    MAX_ZOOM = 20.0
    WHEEL_STEP = 1.2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mask_tracing_interface = parent

        self._opengl_viewport_enabled = False
        self.set_opengl_viewport_enabled(DEFAULT_OPENGL_VIEWPORT_ENABLED)
        configure_canvas_background(self)

        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        self.pan_mode = False  # sticky pan toggle from the tool rail
        self.space_pan = False  # temporary pan while Space is held
        self.drawing = False
        self._pan_origin = None  # QPointF while a pan drag is active

    # ------------------------------------------------------------------ setup
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
                self.viewport().setMouseTracking(True)
                return
            except (RuntimeError, OSError) as e:
                # OpenGL context creation can fail on headless / driverless
                # systems; fall through to a standard software viewport.
                logger.warning("Failed to set OpenGL viewport: %s", e)

        self.setViewport(QWidget())
        self._opengl_viewport_enabled = False
        # The brush preview follows the pointer, so hover moves are needed.
        self.viewport().setMouseTracking(True)

    # ------------------------------------------------------------------- zoom
    @property
    def zoom_factor(self) -> float:
        return self.transform().m11()

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        """Set an absolute zoom, keeping the scene point under ``anchor`` fixed.

        ``anchor`` is a viewport position; ``None`` anchors on the view centre.
        """
        factor = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(factor)))
        if anchor is None:
            anchor = QPointF(self.viewport().rect().center())
        anchor_pt = anchor.toPoint()
        scene_pt = self.mapToScene(anchor_pt)
        self.setTransform(QTransform.fromScale(factor, factor))
        # Scroll so the same scene point sits under the anchor again.
        drift = self.mapFromScene(scene_pt) - anchor_pt
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + drift.x())
        v.setValue(v.value() + drift.y())
        self.zoom_changed.emit(self.zoom_factor)

    def zoom_by(self, step: float, anchor: QPointF | None = None) -> None:
        self.set_zoom(self.zoom_factor * step, anchor)

    def set_content_rect(self, rect: QRectF) -> None:
        """Set the image bounds; the scene rect is padded so edges can be panned to centre."""
        self._content_rect = QRectF(rect)
        pad_x, pad_y = rect.width() * 0.5, rect.height() * 0.5
        self.setSceneRect(rect.adjusted(-pad_x, -pad_y, pad_x, pad_y))

    def fit_to_view(self) -> None:
        """Scale the image into the viewport with a small margin and centre it."""
        rect = getattr(self, "_content_rect", None)
        if rect is None:
            rect = self.sceneRect()
        vp = self.viewport().rect()
        if rect.isEmpty() or vp.width() < 2 or vp.height() < 2:
            return
        margin = 24
        sx = (vp.width() - 2 * margin) / rect.width()
        sy = (vp.height() - 2 * margin) / rect.height()
        factor = max(self.MIN_ZOOM, min(self.MAX_ZOOM, min(sx, sy)))
        self.setTransform(QTransform.fromScale(factor, factor))
        self.centerOn(rect.center())
        self.zoom_changed.emit(self.zoom_factor)

    def wheelEvent(self, event: QWheelEvent):
        if self.drawing:
            event.ignore()
            return

        iface = self.mask_tracing_interface
        # B held: the wheel changes brush size instead of the view.
        if getattr(iface, "b_key_pressed", False):
            iface.step_brush_size(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = self.WHEEL_STEP if event.angleDelta().y() > 0 else 1 / self.WHEEL_STEP
            self.zoom_by(step, event.position())
            event.accept()
            return

        super().wheelEvent(event)

    # -------------------------------------------------------------------- pan
    def is_panning_mode(self) -> bool:
        return self.pan_mode or self.space_pan

    def set_mode(self, pan_mode: bool):
        """Switch the sticky pan toggle on or off."""
        self.pan_mode = bool(pan_mode)
        self.drawing = False
        self.refresh_cursor()

    def set_space_pan(self, active: bool):
        self.space_pan = bool(active)
        if not active:
            self._pan_origin = None
        self.refresh_cursor()

    def refresh_cursor(self):
        if self._pan_origin is not None:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.is_panning_mode():
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        iface = self.mask_tracing_interface
        if hasattr(iface, "set_brush_preview_visible"):
            iface.set_brush_preview_visible(not self.is_panning_mode())

    def _start_pan(self, pos: QPointF):
        self._pan_origin = QPointF(pos)
        self.refresh_cursor()

    def _pan_to(self, pos: QPointF):
        delta = pos - self._pan_origin
        self._pan_origin = QPointF(pos)
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() - round(delta.x()))
        v.setValue(v.value() - round(delta.y()))

    def _end_pan(self):
        self._pan_origin = None
        self.refresh_cursor()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event: QMouseEvent):
        iface = self.mask_tracing_interface
        button = event.button()
        pans = button == Qt.MouseButton.MiddleButton or (
            button == Qt.MouseButton.LeftButton and self.is_panning_mode()
        )
        if pans:
            self._start_pan(event.position())
            event.accept()
            return

        if button == Qt.MouseButton.LeftButton and iface.mask_pixmap:
            self.drawing = True
            iface.begin_stroke(self.map_to_image(event.position()))
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        iface = self.mask_tracing_interface
        if self._pan_origin is not None:
            self._pan_to(event.position())
            event.accept()
            return

        if iface.mask_pixmap:
            pos = self.map_to_image(event.position())
            iface.update_brush_preview(pos)
            if self.drawing and event.buttons() & Qt.MouseButton.LeftButton:
                iface.continue_stroke(pos)
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._pan_origin is not None and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        ):
            self._end_pan()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.drawing = False
            self.mask_tracing_interface.end_stroke()
            event.accept()
            return

        super().mouseReleaseEvent(event)

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
        scene_pos = self.mapToScene(pos.toPoint())
        pixmap = self.mask_tracing_interface.mask_pixmap
        x = math.floor(scene_pos.x())
        y = math.floor(scene_pos.y())
        x = max(0, min(x, pixmap.width() - 1))
        y = max(0, min(y, pixmap.height() - 1))
        return QPoint(x, y)

    def scrollContentsBy(self, dx: int, dy: int):
        """Prevent scrolling mid-stroke (the stroke would smear)."""
        if not self.drawing:
            super().scrollContentsBy(dx, dy)

    def ensureVisible(self, *args, **kwargs):
        """Prevent automatic scrolling during drawing."""
        if not self.drawing:
            super().ensureVisible(*args, **kwargs)

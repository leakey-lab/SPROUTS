"""The canvas of the editor (Correct): :class:`EditorCanvasView` (contract C8).

Zoom, fit, pan and the OpenGL viewport come from :class:`~app.gui.canvas_view.CanvasViewMixin`,
shared with the Library canvas. The view itself holds no tool logic: it hands the input
that is not a pan to an :class:`InputSink` (the Correct ``ToolRouter``), with pointer
positions already floored to an image pixel and clamped to the image:

* a left press (not panning, an image open) starts a drag when the sink accepts it; the
  drag's moves and its release go to the sink, and hover moves go there too
  (``dragging=False``, not while a left drag would pan); a left drag the sink did not
  accept is not forwarded;
* a double-click, the wheel and the keys go to the sink first; what it does not consume
  goes to the mixin (Ctrl+wheel zoom, Ctrl+0 / Ctrl+1 / Ctrl+= / Ctrl+-, Space pan,
  PageUp / PageDown left to the parents);
* leaving the canvas and every pan-state change (sticky pan, Space) are reported, so the
  sink can hide its brush ring;
* a lost keyboard ends the holds whose release the view will not see (``end_holds``, also
  on request): the sink's (B) first, then the mixin's Space;
* while a drag is in progress the wheel is ignored and the view never scrolls (the stroke
  would smear). :meth:`EditorCanvasView.end_gesture` forgets a drag the editor has ended
  itself (open, layer switch, navigation): the rest of it arrives as hover and its release
  is not forwarded.

The view antialiases what the items draw as vectors (brush rings, previews, decorations);
pixmap smoothing is left to each item (the photo is smooth, edit layers stay exact).

The raster viewport is opaque while the view has a scene: the view paints every pixel of
each repaint itself (:meth:`EditorCanvasView.drawBackground`), so Qt no longer paints the
window's background under the canvas first. Never the OpenGL viewport.
"""

from __future__ import annotations

import math
from typing import Protocol

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QTransform, QWheelEvent
from PyQt6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QWidget

from .canvas_background import configure_canvas_background
from .canvas_view import CanvasViewMixin

__all__ = ["EditorCanvasView", "InputSink"]

_LEFT = Qt.MouseButton.LeftButton
_LEFT_BIT = _LEFT.value


class InputSink(Protocol):
    """Where EditorCanvasView sends tool input (positions in image pixels)."""

    def canvas_size(self) -> QSize:
        """The image size; an empty size means there is nothing to edit."""
        ...

    def canvas_press(self, pos: QPoint, event: QMouseEvent) -> bool:
        """A left press at ``pos``; True when a drag starts (its moves and release follow)."""
        ...

    def canvas_move(self, pos: QPoint, event: QMouseEvent, dragging: bool) -> None:
        """The pointer moved to ``pos``: part of an accepted drag, or hover."""
        ...

    def canvas_release(self, pos: QPoint, event: QMouseEvent) -> None:
        """The accepted drag ended at ``pos``."""
        ...

    def canvas_double_click(self, pos: QPoint, event: QMouseEvent) -> None:
        """A left double-click at ``pos`` (it arrives instead of the second press)."""
        ...

    def canvas_wheel(self, event: QWheelEvent) -> bool:
        """A wheel turn; True when consumed (e.g. B + wheel resizes the brush)."""
        ...

    def canvas_key_press(self, event: QKeyEvent) -> bool:
        """A key press on the canvas, before the canvas keys; True when consumed."""
        ...

    def canvas_key_release(self, event: QKeyEvent) -> bool:
        """A key release on the canvas, before the canvas keys; True when consumed."""
        ...

    def canvas_hover_left(self) -> None:
        """The pointer left the canvas."""
        ...

    def canvas_pan_changed(self, panning: bool) -> None:
        """A left drag now pans (sticky pan mode or Space held), or no longer does."""
        ...

    def canvas_focus_lost(self) -> None:
        """The releases of held keys will not reach the canvas: end the holds (B)."""
        ...


class EditorCanvasView(CanvasViewMixin, QGraphicsView):
    """The canvas of the editor (Correct): zoom/pan/fit from the mixin, tool input to an
    InputSink."""

    OPAQUE_RASTER_VIEWPORT = True  # see drawBackground
    _opaque_viewport = False  # the raster viewport is opaque now

    def __init__(self, sink: InputSink | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sink = sink
        # A left press reached the view and its release has not (hover's cheap test; checked
        # against event.buttons() only while set, so a release a modal box took self-heals).
        self._left_down = False
        # True from an accepted left press until that button is released, even after
        # end_gesture(): the rest of such a drag is hover, any other left drag is not ours.
        self._drag_owned = False
        self._init_canvas()
        configure_canvas_background(self)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

    # ------------------------------------------------------------------ API
    def set_sink(self, sink: InputSink | None) -> None:
        """Send tool input to ``sink`` from now on (None: a plain pan/zoom canvas).

        A drag of the previous sink is forgotten; its release goes nowhere.
        """
        self._sink = sink
        self.drawing = False
        self._drag_owned = False

    def map_to_image(self, pos: QPointF) -> QPoint:
        """The image pixel under viewport position ``pos``: floored, clamped to the image.

        ``QPoint(0, 0)`` while the sink has no image (or there is no sink).
        """
        size = self._sink.canvas_size() if self._sink is not None else QSize()
        if size.isEmpty():
            return QPoint(0, 0)
        return self._pixel_in(pos, size)

    def end_gesture(self) -> None:
        """Forget a drag in progress (``drawing = False``).

        The rest of that drag reaches the sink as hover moves and its release is not
        forwarded; the wheel and scrolling work again at once.
        """
        self.drawing = False

    def _pixel_in(self, pos: QPointF, size: QSize) -> QPoint:
        """The scene pixel under ``pos``, clamped to ``size``, inlined: every hover runs it."""
        scene = self.mapToScene(pos.toPoint())
        x = min(max(math.floor(scene.x()), 0), size.width() - 1)
        y = min(max(math.floor(scene.y()), 0), size.height() - 1)
        return QPoint(x, y)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Track the left button (a new press decides who owns its drag), then dispatch."""
        if event.button() == _LEFT:
            self._left_down = True
            self._drag_owned = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """The second press of a double-click: it starts no drag of the sink's."""
        if event.button() == _LEFT:
            self._left_down = True
            self._drag_owned = False
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Dispatch, then forget the left button's drag."""
        super().mouseReleaseEvent(event)
        if event.button() == _LEFT:
            self._left_down = False
            self._drag_owned = False

    def _tool_press(self, event: QMouseEvent) -> bool:
        sink = self._sink
        if sink is None or event.button() != _LEFT:
            return False
        size = sink.canvas_size()
        if size.isEmpty():
            return False
        self.drawing = bool(sink.canvas_press(self._pixel_in(event.position(), size), event))
        self._drag_owned = self.drawing
        event.accept()
        return True

    def _tool_move(self, event: QMouseEvent) -> bool:
        sink = self._sink
        if sink is None:
            return False
        if self.drawing:
            sink.canvas_move(self.map_to_image(event.position()), event, True)
            event.accept()
            return True
        if self.pan_mode or self.space_pan:
            return False
        if self._left_down and not self._drag_owned:
            if event.buttons().value & _LEFT_BIT:
                return False  # a left drag that did not start as an accepted press
            self._left_down = False  # its release went elsewhere (e.g. a modal box)
        size = sink.canvas_size()
        if size.isEmpty():
            return False
        sink.canvas_move(self._pixel_in(event.position(), size), event, False)
        return False  # hover: the scene still gets its hover events

    def _tool_release(self, event: QMouseEvent) -> bool:
        if event.button() != _LEFT or not self.drawing:
            return False
        self.drawing = False
        if self._sink is not None:
            self._sink.canvas_release(self.map_to_image(event.position()), event)
        event.accept()
        return True

    def _tool_double_click(self, event: QMouseEvent) -> bool:
        sink = self._sink
        if sink is None or event.button() != _LEFT:
            return False
        size = sink.canvas_size()
        if size.isEmpty():
            return False
        sink.canvas_double_click(self._pixel_in(event.position(), size), event)
        event.accept()
        return True

    # ------------------------------------------------------------------ wheel / keys
    def wheelEvent(self, event: QWheelEvent) -> None:
        """The sink first (e.g. B + wheel), then the mixin; nothing while drawing."""
        if self.drawing:
            event.ignore()
            return
        if self._sink is not None and self._sink.canvas_wheel(event):
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """The sink first, then the canvas keys of the mixin."""
        if self._sink is not None and self._sink.canvas_key_press(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """The sink first, then the mixin (the end of a Space pan)."""
        if self._sink is not None and self._sink.canvas_key_release(event):
            event.accept()
            return
        super().keyReleaseEvent(event)

    def end_holds(self) -> None:
        """End the holds whose release this view will not see: the sink's (B), then Space.

        Runs when the view loses the keyboard (the mixin's ``focusOutEvent``) and when the
        library tree's key relay asks for it.
        """
        if self._sink is not None:
            self._sink.canvas_focus_lost()
        super().end_holds()

    # ------------------------------------------------------------------ hover / pan state
    def refresh_cursor(self) -> None:
        """Show the cursor for the current state and tell the sink whether a drag pans."""
        super().refresh_cursor()
        if self._sink is not None:
            self._sink.canvas_pan_changed(self.is_panning_mode())

    def leaveEvent(self, event) -> None:
        """The pointer left the canvas (the sink hides its brush ring)."""
        if self._sink is not None:
            self._sink.canvas_hover_left()
        super().leaveEvent(event)

    def enterEvent(self, event) -> None:
        """The pointer is back: refresh the cursor (and the sink's pan state)."""
        self.refresh_cursor()
        super().enterEvent(event)

    # ------------------------------------------------------------------ the opaque viewport
    def setScene(self, scene: QGraphicsScene | None) -> None:
        """Show ``scene``; the raster viewport is opaque only while there is one."""
        super().setScene(scene)
        self._sync_opaque_viewport()

    def _configure_viewport(self) -> None:
        super()._configure_viewport()
        self._sync_opaque_viewport()  # after every viewport swap

    def _sync_opaque_viewport(self) -> None:
        if self._opengl_viewport_enabled:  # a QOpenGLWidget's attributes are never touched
            self._opaque_viewport = False
            return
        # Without a scene the view paints nothing: the window's background must show.
        opaque = self.OPAQUE_RASTER_VIEWPORT and self.scene() is not None
        self._opaque_viewport = opaque
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """The canvas colour under the items; on the opaque viewport, on every pixel painted.

        The theme's transparent widget backgrounds would make Qt paint the window under the
        whole canvas on every frame before this fill (1.7-3.1 ms of a frame at devicePixelRatio
        2), hence the opaque raster viewport, which must cover each device pixel of a repaint
        itself. The fill is made in viewport pixels: QGraphicsView's own, in scene coordinates,
        can lose the device pixel straddling a .5 edge at 125-175 % scaling (it would keep the
        previous frame's pixel).
        """
        if not self._opaque_viewport or painter.device() is not self.viewport():
            super().drawBackground(painter, rect)  # also QGraphicsView.render() onto another device
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setWorldTransform(QTransform())
        painter.fillRect(self.viewport().rect().adjusted(-1, -1, 1, 1), self.backgroundBrush())
        painter.restore()

    # ------------------------------------------------------------------ no scrolling mid-drag
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Scroll, except during a drag (the stroke would smear).

        A scroll repaints the whole canvas, as for a viewport that is not opaque: Qt would
        otherwise move an opaque viewport's pixels when nothing floats over it.
        """
        if not self.drawing:
            super().scrollContentsBy(dx, dy)
            if self._opaque_viewport:
                self.viewport().update()

    def ensureVisible(self, *args, **kwargs) -> None:
        """Scroll to show a region, except during a drag."""
        if not self.drawing:
            super().ensureVisible(*args, **kwargs)

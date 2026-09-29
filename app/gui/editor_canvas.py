"""The canvas of every editor (Correct and Trace GT): :class:`EditorCanvasView` (contract C8).

Zoom, fit, pan and the OpenGL viewport come from :class:`~app.gui.canvas_view.CanvasViewMixin`,
shared with the Library canvas. The view itself holds no tool logic: it hands the input
that is not a pan to an :class:`InputSink` (the Correct ``ToolRouter``, the Trace GT
editor), with pointer positions already floored to an image pixel and clamped to the
image:

* a left press (not panning, an image open) starts a drag when the sink accepts it; the
  drag's moves and its release go to the sink, and hover moves go there too
  (``dragging=False``, not while a left drag would pan); a left drag the sink did not
  accept is not forwarded;
* a double-click, the wheel and the keys go to the sink first; what it does not consume
  goes to the mixin (Ctrl+wheel zoom, Ctrl+0 / Ctrl+1 / Ctrl+= / Ctrl+-, Space pan,
  PageUp / PageDown left to the parents);
* leaving the canvas and every pan-state change (sticky pan, Space) are reported, so the
  sink can hide its brush ring;
* while a drag is in progress the wheel is ignored and the view never scrolls (the stroke
  would smear). :meth:`EditorCanvasView.end_gesture` forgets a drag the editor has ended
  itself (open, layer switch, navigation): the rest of it arrives as hover and its release
  is not forwarded.

The view antialiases what the items draw as vectors (brush rings, previews, decorations);
pixmap smoothing is left to each item (the photo is smooth, edit layers stay exact).
"""

from __future__ import annotations

import math
from typing import Protocol

from PyQt6.QtCore import QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import QFrame, QGraphicsView, QWidget

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


class EditorCanvasView(CanvasViewMixin, QGraphicsView):
    """The canvas of every editor (Correct and Trace GT): zoom/pan/fit from the mixin,
    tool input to an InputSink."""

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
        """``CanvasViewMixin._scene_pixel`` clamped to ``size``, inlined: every hover runs it."""
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

    # ------------------------------------------------------------------ no scrolling mid-drag
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Scroll, except during a drag (the stroke would smear)."""
        if not self.drawing:
            super().scrollContentsBy(dx, dy)

    def ensureVisible(self, *args, **kwargs) -> None:
        """Scroll to show a region, except during a drag."""
        if not self.drawing:
            super().ensureVisible(*args, **kwargs)

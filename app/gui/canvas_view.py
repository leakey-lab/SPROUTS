"""Shared zoom / pan / fit / viewport behaviour of every image canvas.

``CanvasViewMixin`` is mixed into each image ``QGraphicsView`` as
``class X(CanvasViewMixin, QGraphicsView)``: the Library display, the Correct
Mask / Trace GT canvas and the Correct Skeleton canvas. So all three behave the
same way:

Zoom
    The view transform is the single source of truth. ``zoom_factor`` is read
    from it, every zoom goes through :meth:`set_zoom` (clamped to
    ``MIN_ZOOM``..``MAX_ZOOM``) and ``zoom_changed`` reports the new factor.
    Ctrl+wheel zooms about the cursor. Ctrl+0 fits, Ctrl+1 shows actual pixels,
    Ctrl+= / Ctrl++ and Ctrl+- step the zoom.

Fit
    :meth:`fit_to_view` fits the content rect into the viewport minus insets
    (the host's floating overlays, see :meth:`set_fit_insets_provider`) and
    centres it there. A fitted view re-fits on every resize until the user
    zooms or pans.

Pan
    Sticky pan mode (the rail's hand button), holding Space, or dragging with
    the middle button, whatever the tool. Scroll bars stay hidden. PageUp and
    PageDown are left to the parent widgets instead of scrolling the canvas.

Viewport
    One OpenGL policy (:mod:`app.gui.canvas_gl`). After every viewport swap the
    update mode (Full for OpenGL, Smart for the raster viewport), mouse tracking
    and cursor are applied again.

Each subclass calls :meth:`_init_canvas` and then
``configure_canvas_background(self)`` in ``__init__``, and receives tool input
through the ``_tool_*`` hooks. The hooks only see events that are not pans.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import QMargins, QPoint, QPointF, QRectF, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCursor, QKeyEvent, QMouseEvent, QTransform, QWheelEvent
from PyQt6.QtWidgets import QGraphicsView, QWidget

from . import canvas_gl

_LEFT = Qt.MouseButton.LeftButton
_MIDDLE = Qt.MouseButton.MiddleButton
_PLAIN = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)


class CanvasViewMixin:
    """Zoom, fit, pan and viewport management for a ``QGraphicsView`` subclass."""

    zoom_changed = pyqtSignal(float)

    MIN_ZOOM = 0.1
    MAX_ZOOM = 20.0
    WHEEL_STEP = 1.2  # zoom per wheel notch (120 angle-delta units)
    KEY_STEP = 1.25  # zoom per Ctrl+= / Ctrl+- press (and the editors' zoom buttons)
    FIT_MARGIN = 24  # default inset on every side when fitting
    PAN_PADDING = 0.5  # scene padding around the content, as a fraction of its size

    # ------------------------------------------------------------------ setup
    def _init_canvas(self, *, pan_mode: bool = False) -> None:
        """Set up the shared state and viewport; call right after ``super().__init__``."""
        self.pan_mode = pan_mode  # sticky pan (the rail's hand button)
        self.space_pan = False  # temporary pan while Space is held
        self.drawing = False  # a tool drag is in progress
        self._pan_origin: QPointF | None = None
        self._pan_button = Qt.MouseButton.NoButton
        self._content_rect: QRectF | None = None
        self._fit_insets_provider: Callable[[], QMargins] | None = None
        self._auto_fit = False
        self._auto_fit_insets: QMargins | None = None
        self._opengl_viewport_enabled = False

        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_opengl_viewport_enabled(True)

    def set_opengl_viewport_enabled(self, enabled: bool) -> None:
        """Use a ``QOpenGLWidget`` viewport when ``enabled`` and allowed, else a raster one.

        MainWindow turns OpenGL off while a QtWebEngine dashboard is shown (the
        two cannot share a window) and asks for it again afterwards. Asking is a
        no-op when the env opt-out or the capability probe says no.
        """
        want_gl = bool(enabled) and canvas_gl.opengl_viewports_allowed()
        if want_gl != self._opengl_viewport_enabled:
            self.setViewport(self._new_gl_viewport() if want_gl else QWidget())
            self._opengl_viewport_enabled = want_gl
        self._configure_viewport()

    @staticmethod
    def _new_gl_viewport() -> QWidget:
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget

        return QOpenGLWidget()

    def _configure_viewport(self) -> None:
        """Apply what every fresh viewport widget needs (runs after each swap)."""
        # A QOpenGLWidget cannot do partial updates; the raster viewport can,
        # which keeps hover and stroke repaints to the dirty region only.
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate
            if self._opengl_viewport_enabled
            else QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
        )
        viewport = self.viewport()
        viewport.setMouseTracking(True)  # hover drives brush rings and previews
        viewport.setCursor(self._cursor_for_state())

    # ------------------------------------------------------------------- zoom
    @property
    def zoom_factor(self) -> float:
        """Current zoom (1.0 = actual pixels), read from the view transform."""
        return self.transform().m11()

    @property
    def min_zoom(self) -> float:
        return self.MIN_ZOOM

    @property
    def max_zoom(self) -> float:
        return self.MAX_ZOOM

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        """Set an absolute zoom, keeping the scene point under ``anchor`` in place.

        ``anchor`` is a viewport position; ``None`` anchors on the viewport
        centre. A zoom ends the fitted state (no more re-fit on resize).
        """
        self._auto_fit = False
        factor = self._clamp_zoom(factor)
        if anchor is None:
            anchor = QPointF(self.viewport().rect().center())
        anchor_pt = anchor.toPoint()
        scene_pt = self.mapToScene(anchor_pt)
        self.setTransform(QTransform.fromScale(factor, factor))
        drift = self.mapFromScene(scene_pt) - anchor_pt
        self._scroll_by(drift.x(), drift.y())
        self.zoom_changed.emit(self.zoom_factor)

    def zoom_by(self, step: float, anchor: QPointF | None = None) -> None:
        """Multiply the zoom by ``step`` (see :meth:`set_zoom`)."""
        self.set_zoom(self.zoom_factor * step, anchor)

    def _clamp_zoom(self, factor: float) -> float:
        return max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(factor)))

    # -------------------------------------------------------------------- fit
    def set_content_rect(self, rect: QRectF) -> None:
        """Set the image bounds; the scene rect gets padding so any edge can be panned to centre."""
        self._content_rect = QRectF(rect)
        pad_x = rect.width() * self.PAN_PADDING
        pad_y = rect.height() * self.PAN_PADDING
        self.setSceneRect(rect.adjusted(-pad_x, -pad_y, pad_x, pad_y))

    def content_rect(self) -> QRectF:
        """The rect :meth:`fit_to_view` fits: the content rect, else the scene rect."""
        if self._content_rect is not None:
            return QRectF(self._content_rect)
        return self.sceneRect()

    def clear_content_rect(self) -> None:
        """Nothing to fit (e.g. a message is shown): fits are no-ops until the next content."""
        self._content_rect = QRectF()
        self._auto_fit = False

    def set_fit_insets_provider(self, provider: Callable[[], QMargins] | None) -> None:
        """Let the host supply the insets a fit keeps clear (its floating overlays).

        The provider may run inside this view's resize event, before the host has
        moved its overlays, so it should derive insets from overlay sizes and
        margins rather than from their current positions.
        """
        self._fit_insets_provider = provider

    def fit_insets(self) -> QMargins:
        """Insets used by :meth:`fit_to_view` when none are given."""
        if self._fit_insets_provider is not None:
            return self._fit_insets_provider()
        m = self.FIT_MARGIN
        return QMargins(m, m, m, m)

    # As a slot (e.g. a Fit button's ``clicked``) it takes no arguments, so the
    # ``checked`` flag is never taken for ``insets``.
    @pyqtSlot()
    def fit_to_view(self, insets: QMargins | None = None) -> None:
        """Fit the content into the viewport minus ``insets`` and centre it there.

        ``insets`` defaults to :meth:`fit_insets`. The view then stays fitted:
        it re-fits on every resize until the user zooms or pans.
        """
        self._auto_fit = True
        self._auto_fit_insets = insets
        self._fit(insets)

    def _fit(self, insets: QMargins | None) -> None:
        rect = self.content_rect()
        m = insets if insets is not None else self.fit_insets()
        vp = self.viewport().rect()
        avail_w = vp.width() - m.left() - m.right()
        avail_h = vp.height() - m.top() - m.bottom()
        if rect.isEmpty() or avail_w < 2 or avail_h < 2:
            return  # not laid out yet: the next resize fits
        factor = self._clamp_zoom(min(avail_w / rect.width(), avail_h / rect.height()))
        self.setTransform(QTransform.fromScale(factor, factor))
        # centerOn() centres on the whole viewport; shift so the content is
        # centred in the inset rect instead.
        shift = QPointF(m.right() - m.left(), m.bottom() - m.top()) / (2 * factor)
        self.centerOn(rect.center() + shift)
        self.zoom_changed.emit(self.zoom_factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._auto_fit:
            self._fit(self._auto_fit_insets)

    # ------------------------------------------------------------------ wheel
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.drawing:
            event.ignore()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120.0  # fractions from touchpads
            if notches:
                self.zoom_by(self.WHEEL_STEP**notches, event.position())
            event.accept()
            return
        self._auto_fit = False  # a plain wheel scrolls the view
        super().wheelEvent(event)

    # --------------------------------------------------------------- keyboard
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._canvas_key_press(event):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and event.modifiers() in _PLAIN:
            if not event.isAutoRepeat():
                self.set_space_pan(False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _canvas_key_press(self, event: QKeyEvent) -> bool:
        """Handle the canvas keys; ``True`` when ``event`` was consumed (or ignored on purpose)."""
        key, mods = event.key(), event.modifiers()
        ctrl_only = (mods & Qt.KeyboardModifier.ControlModifier) and not (
            mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if ctrl_only:
            action = {
                Qt.Key.Key_0: self.fit_to_view,
                Qt.Key.Key_1: lambda: self.set_zoom(1.0),
                Qt.Key.Key_Equal: lambda: self.zoom_by(self.KEY_STEP),
                Qt.Key.Key_Plus: lambda: self.zoom_by(self.KEY_STEP),
                Qt.Key.Key_Minus: lambda: self.zoom_by(1 / self.KEY_STEP),
            }.get(key)
            if action is not None:
                action()
                event.accept()
                return True
        if key == Qt.Key.Key_Space and mods in _PLAIN:
            if not event.isAutoRepeat():
                self.set_space_pan(True)
            event.accept()
            return True
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            # Not a canvas scroll (the scroll bars are hidden): leave the key to
            # the parent widgets, e.g. for next / previous image.
            event.ignore()
            return True
        return False

    # -------------------------------------------------------------------- pan
    def is_panning_mode(self) -> bool:
        """Whether a left drag pans (sticky pan mode or Space held)."""
        return self.pan_mode or self.space_pan

    def set_mode(self, pan_mode: bool) -> None:
        """Turn the sticky pan mode (the rail's hand button) on or off."""
        self.pan_mode = bool(pan_mode)
        self.drawing = False
        self.refresh_cursor()

    def set_pan_mode(self, enabled: bool) -> None:
        """Alias of :meth:`set_mode` (the skeleton editor's name)."""
        self.set_mode(enabled)

    def set_space_pan(self, active: bool) -> None:
        """Start or stop the temporary pan of a held Space key."""
        self.space_pan = bool(active)
        if not active and self._pan_button == _LEFT and not self.pan_mode:
            self._pan_origin = None  # the Space-drag ends with the key
            self._pan_button = Qt.MouseButton.NoButton
        self.refresh_cursor()

    def _pan_press(self, event: QMouseEvent) -> bool:
        if self._pan_origin is not None:
            # Other buttons do nothing mid-pan: a stroke started now would block
            # the scrolling (see the mask view) and desync the view.
            event.accept()
            return True
        button = event.button()
        pans = button == _MIDDLE or (button == _LEFT and self.is_panning_mode())
        if not pans or self.drawing:
            return False
        self._pan_origin = QPointF(event.position())
        self._pan_button = button
        self._auto_fit = False
        self.refresh_cursor()
        event.accept()
        return True

    def _pan_move(self, event: QMouseEvent) -> bool:
        if self._pan_origin is None:
            return False
        delta = event.position() - self._pan_origin
        dx, dy = round(delta.x()), round(delta.y())
        # Advance the origin by the whole pixels scrolled only, so sub-pixel
        # (high-DPI) motion accumulates instead of being rounded away.
        self._pan_origin += QPointF(dx, dy)
        self._scroll_by(-dx, -dy)
        event.accept()
        return True

    def _pan_release(self, event: QMouseEvent) -> bool:
        if self._pan_origin is None or event.button() != self._pan_button:
            return False
        self._pan_origin = None
        self._pan_button = Qt.MouseButton.NoButton
        self.refresh_cursor()
        event.accept()
        return True

    def _scroll_by(self, dx: float, dy: float) -> None:
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + round(dx))
        v.setValue(v.value() + round(dy))

    # ----------------------------------------------------------------- cursor
    def refresh_cursor(self) -> None:
        """Show the cursor for the current state: grabbing, pan, or the tool's."""
        self.viewport().setCursor(self._cursor_for_state())

    def update_cursor(self) -> None:
        """Alias of :meth:`refresh_cursor` (the skeleton editor's name)."""
        self.refresh_cursor()

    def _cursor_for_state(self) -> QCursor | Qt.CursorShape:
        if self._pan_origin is not None:
            return Qt.CursorShape.ClosedHandCursor
        if self.is_panning_mode():
            return Qt.CursorShape.OpenHandCursor
        return self._tool_cursor()

    def _tool_cursor(self) -> QCursor | Qt.CursorShape:
        """Cursor while no pan is active; subclasses may show a tool cursor."""
        return Qt.CursorShape.CrossCursor

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not (self._pan_press(event) or self._tool_press(event)):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (self._pan_move(event) or self._tool_move(event)):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not (self._pan_release(event) or self._tool_release(event)):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # The second press of a fast double-click arrives here, not in
        # mousePressEvent: it can start a pan too.
        if self._pan_press(event):
            return
        if self.is_panning_mode() or not self._tool_double_click(event):
            super().mouseDoubleClickEvent(event)

    # Tool hooks: each returns True when it consumed the event. They only see
    # events that are not pans.
    def _tool_press(self, event: QMouseEvent) -> bool:
        return False

    def _tool_move(self, event: QMouseEvent) -> bool:
        return False

    def _tool_release(self, event: QMouseEvent) -> bool:
        return False

    def _tool_double_click(self, event: QMouseEvent) -> bool:
        return False

    def _scene_pixel(self, pos: QPointF) -> QPoint:
        """The (unclamped) image pixel under viewport position ``pos``."""
        scene_pos = self.mapToScene(pos.toPoint())
        return QPoint(math.floor(scene_pos.x()), math.floor(scene_pos.y()))

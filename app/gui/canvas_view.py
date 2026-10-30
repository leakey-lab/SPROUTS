"""Shared zoom / pan / fit / viewport behaviour of every image canvas.

``CanvasViewMixin`` is mixed into each image ``QGraphicsView`` as
``class X(CanvasViewMixin, QGraphicsView)``: the Library display and the Correct
canvas. So both behave the same way:

Zoom
    The view transform is the single source of truth. ``zoom_factor`` is read
    from it, every zoom goes through :meth:`set_zoom` (clamped to
    ``MIN_ZOOM``..``MAX_ZOOM``) and ``zoom_changed`` reports the new factor.
    Ctrl+wheel zooms about the cursor, continuously (``WHEEL_STEP`` per notch,
    fractions from touchpads). Ctrl+0 fits, Ctrl+1 shows actual pixels, and
    Ctrl+= / Ctrl++ and Ctrl+- step along the ``ZOOM_STOPS`` ladder
    (:meth:`zoom_step`: round percentages, 80 % and 125 % around 100 %, integer
    stops from 200 %, so exact-pixel layers show uniform blocks). A trackpad pinch
    (macOS and Linux native gestures) zooms about the fingers, continuously, and a
    smart zoom (two-finger double tap) toggles fit and 100 %.

    Step zooms (a wheel notch, Ctrl+= / Ctrl+-, the zoom buttons) glide over
    ``ZOOM_ANIMATION_MS`` when the platform animates its UI (see
    :meth:`zoom_animation_ms`): geometric and eased out, so the zoom never passes
    its target, with the scene point under the anchor kept in place on every
    frame. A step during a glide goes on from the glide's target; ``set_zoom``,
    fits and new content stop a glide, a mouse press or a plain scroll freezes it.
    Touchpad fractions of a notch zoom at once.

    Interactive zoom-out stops at :attr:`zoom_out_limit`, ``min(100 %,
    FIT_ZOOM_OUT x the fit zoom)``: the image never shrinks below half its fitted
    size, and a small image can always go down to actual pixels. A fit is never
    clamped up to ``MIN_ZOOM``, so a very large image still fits.

Fit
    :meth:`fit_to_view` fits the content rect into the viewport minus insets
    (the host's floating overlays, see :meth:`set_fit_insets_provider`) and
    centres it there. A fitted view re-fits on every resize until the user
    zooms or pans.

Pan
    Sticky pan mode (the rail's hand button), holding Space, or dragging with
    the middle button, whatever the tool. Scroll bars stay hidden. PageUp and
    PageDown are left to the parent widgets instead of scrolling the canvas. A held
    Space ends when the view loses the keyboard (its release goes elsewhere) or on
    :meth:`end_holds`.

Pixel grid
    Off by default; :meth:`set_pixel_grid` / :meth:`toggle_pixel_grid` (the
    shortcut's slot) turn it on. From ``PIXEL_GRID_MIN_ZOOM`` (800 %) up it draws a
    faint one-device-pixel line on the first device column and row of every image
    pixel, where the exact-pixel layers start each pixel block. Display only.

Viewport
    One OpenGL policy (:mod:`app.gui.canvas_gl`); a GL viewport requests
    ``canvas_gl.gl_samples()`` MSAA samples. After every viewport swap the
    update mode (Full for OpenGL, Smart for the raster viewport), mouse tracking
    and cursor are applied again. ``viewport_changed`` reports a new viewport size
    or device pixel ratio.

Each subclass calls :meth:`_init_canvas` and then
``configure_canvas_background(self)`` in ``__init__``, and receives tool input
through the ``_tool_*`` hooks. The hooks only see events that are not pans.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QMargins,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtWidgets import QApplication, QGraphicsView, QWidget

from app.design import tokens

from . import canvas_gl

_LEFT = Qt.MouseButton.LeftButton
_MIDDLE = Qt.MouseButton.MiddleButton
_PLAIN = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)


#: What the pixel-grid key says (the grid only shows from ``PIXEL_GRID_MIN_ZOOM``).
PIXEL_GRID_ON = "Pixel grid on (it shows at 800 % and above)"
PIXEL_GRID_OFF = "Pixel grid off"


def draw_pixel_grid(painter: QPainter, area: QRectF, colour: QColor) -> None:
    """A one-device-pixel line on the first device column and row of each pixel in ``area``.

    ``area`` is in the painter's (scene) coordinates, one unit per image pixel. A pixel's
    first device column is where a nearest mapping enters it (as the raster layer snaps its
    tiles), so the lines sit on the edges of exact pixel blocks. Only a scale + translate
    painter is drawn on. The vertical lines are one ``width x 1`` row image filled down the
    area as a texture: long spans, about 40x faster on the raster engine than a translucent
    line per column (thousands of one-pixel spans).

    The device columns include everything between the painter and the device: the
    devicePixelRatio and, on a raster viewport, the viewport's offset in its window (Qt's
    paint redirection). The lines are drawn with that whole mapping undone, so they land
    on exactly those columns (undoing the ratio alone added the offset a second time).
    """
    dev = painter.deviceTransform()
    sx, sy, dx, dy = dev.m11(), dev.m22(), dev.dx(), dev.dy()
    if area.isEmpty() or sx <= 0 or sy <= 0 or dev.m12() or dev.m21():
        return
    left, right = math.ceil(area.left() * sx + dx - 0.5), math.ceil(area.right() * sx + dx - 0.5)
    top, bottom = math.ceil(area.top() * sy + dy - 0.5), math.ceil(area.bottom() * sy + dy - 0.5)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return
    row = QImage(width, 1, QImage.Format.Format_ARGB32_Premultiplied)
    row.fill(Qt.GlobalColor.transparent)
    for x in range(math.ceil(area.left()), math.floor(area.right()) + 1):
        column = math.ceil(x * sx + dx - 0.5) - left
        if 0 <= column < width:
            row.setPixelColor(column, 0, colour)
    painter.save()
    # From here on one unit is one device pixel: world x (the rest of the mapping) = identity.
    painter.setTransform(dev.inverted()[0] * painter.worldTransform())
    painter.setBrushOrigin(left, 0)
    painter.fillRect(QRect(left, top, width, height), QBrush(row))
    for y in range(math.ceil(area.top()), math.floor(area.bottom()) + 1):
        line = math.ceil(y * sy + dy - 0.5)
        if top <= line < bottom:
            painter.fillRect(QRect(left, line, width, 1), colour)
    painter.restore()


class CanvasViewMixin:
    """Zoom, fit, pan and viewport management for a ``QGraphicsView`` subclass."""

    zoom_changed = pyqtSignal(float)
    pixel_grid_changed = pyqtSignal(bool)
    viewport_changed = pyqtSignal()  # the viewport's size or device pixel ratio changed

    MIN_ZOOM = 0.1  # set_zoom's floor (lower only as far as a huge image's fit needs)
    MAX_ZOOM = 20.0
    FIT_ZOOM_OUT = 0.5  # interactive zoom-out stops at this fraction of the fit zoom
    ZOOM_ANIMATION_MS = 120  # how long a step zoom glides (when the platform animates)
    WHEEL_STEP = 1.2  # zoom per wheel notch (120 angle-delta units)
    # Stops of Ctrl+= / Ctrl+- and the zoom buttons (zoom_step), MIN_ZOOM to MAX_ZOOM.
    ZOOM_STOPS: tuple[float, ...] = (
        0.1, 0.125, 0.167, 0.25, 0.333, 0.5, 0.667, 0.8, 1.0, 1.25,
        1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0,
    )  # fmt: skip
    ZOOM_STOP_TOLERANCE = 1e-3  # a zoom this close to a stop counts as on it
    FIT_MARGIN = 24  # default inset on every side when fitting
    PAN_PADDING = 0.5  # scene padding around the content, as a fraction of its size
    PIXEL_GRID_MIN_ZOOM = 8.0  # the pixel grid (when on) shows from 800 %
    PIXEL_GRID_ALPHA = 70  # of tokens.TEXT: faint on dark soil, still seen on the mask

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
        # Step-zoom glide: None follows the platform (see zoom_animation_ms).
        self._zoom_animation_ms: int | None = None
        self._glide: QVariantAnimation | None = None  # made at the first glide
        self._glide_from = self._glide_to = 1.0
        self._glide_anchor = QPoint()  # viewport pixel the scene point stays under
        self._glide_scene = QPointF()
        # (anchor pixel, view state, scene point) of the last zoom; see _anchor_scene.
        self._anchor_memo: tuple[QPoint, tuple, QPointF] | None = None
        self._pixel_grid = False

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
        """A ``QOpenGLWidget`` asking for ``canvas_gl.gl_samples()`` MSAA samples."""
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget

        viewport = QOpenGLWidget()
        surface = viewport.format()
        surface.setSamples(canvas_gl.gl_samples())
        viewport.setFormat(surface)
        return viewport

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
        """The absolute floor of :meth:`set_zoom` (see :attr:`zoom_out_limit` for zoom-out)."""
        return self.MIN_ZOOM

    @property
    def max_zoom(self) -> float:
        return self.MAX_ZOOM

    @property
    def zoom_out_limit(self) -> float:
        """Where interactive zoom-out stops: ``min(1.0, FIT_ZOOM_OUT * fit zoom)``.

        Half the fitted size, so the image never shrinks to a stamp in the canvas, and at
        most 100 %, so a small image can always be zoomed out to actual pixels. The fit
        zoom is that of :meth:`fit_to_view` in the current viewport; ``MIN_ZOOM`` while
        there is nothing to fit or the view is not laid out.
        """
        fit = self._fit_zoom(None)
        return self.MIN_ZOOM if fit is None else min(1.0, self.FIT_ZOOM_OUT * fit)

    @property
    def target_zoom(self) -> float:
        """Where the zoom is heading: a glide's target, else the current zoom."""
        return self._glide_to if self.zoom_animating() else self.zoom_factor

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        """Set an absolute zoom at once, keeping the scene point under ``anchor`` in place.

        ``anchor`` is a viewport position; ``None`` anchors on the viewport
        centre. A zoom ends the fitted state (no more re-fit on resize) and a glide.
        """
        self._stop_zoom_glide()
        self._auto_fit = False
        anchor_pt = self._anchor_point(anchor)
        self._apply_zoom(self._clamp_zoom(factor), anchor_pt, self._anchor_scene(anchor_pt))

    def zoom_by(self, step: float, anchor: QPointF | None = None) -> None:
        """Multiply the zoom by ``step`` at once (see :meth:`set_zoom`)."""
        self.set_zoom(self.zoom_factor * step, anchor)

    def zoom_step(self, direction: int, anchor: QPointF | None = None) -> None:
        """Zoom to the next stop of ``ZOOM_STOPS``: up for ``direction > 0``, else down.

        A zoom within ``ZOOM_STOP_TOLERANCE`` of a stop counts as on it; past the last
        stop the zoom stays clamped to ``MAX_ZOOM``, and zooming out stops at
        :attr:`zoom_out_limit` (a zoom already below it stays). Steps from the target of
        a glide in progress and glides there (see :meth:`zoom_animation_ms`). ``anchor``
        as in :meth:`set_zoom`.
        """
        if not direction:
            return
        zoom, tol = self.target_zoom, self.ZOOM_STOP_TOLERANCE
        if direction > 0:
            target = next((s for s in self.ZOOM_STOPS if s > zoom + tol), self.MAX_ZOOM)
        else:
            target = next((s for s in reversed(self.ZOOM_STOPS) if s < zoom - tol), 0.0)
            target = max(target, min(self.zoom_out_limit, zoom))
        self._step_zoom(target, anchor)

    def zoom_animation_ms(self) -> int:
        """How long a step zoom glides; 0 means it jumps.

        The value given to :meth:`set_zoom_animation`, else ``ZOOM_ANIMATION_MS`` when the
        platform animates its UI (``QApplication.isEffectEnabled(UI_General)``: on Windows the
        system's UI effects switch, SPI_GETUIEFFECTS) and 0 when it does not (the offscreen
        platform).
        """
        if self._zoom_animation_ms is not None:
            return self._zoom_animation_ms
        if QApplication.isEffectEnabled(Qt.UIEffect.UI_General):
            return self.ZOOM_ANIMATION_MS
        return 0

    def set_zoom_animation(self, ms: int | None) -> None:
        """Glide step zooms over ``ms`` milliseconds (0: jump); None follows the platform."""
        self._zoom_animation_ms = None if ms is None else max(0, int(ms))
        if self._zoom_animation_ms == 0:
            self._stop_zoom_glide()

    def zoom_animating(self) -> bool:
        """True while a step zoom glides."""
        glide = self._glide
        return glide is not None and glide.state() == QAbstractAnimation.State.Running

    def _clamp_zoom(self, factor: float) -> float:
        """``factor`` within ``MIN_ZOOM``..``MAX_ZOOM``; the floor is lower only where the
        zoom-out limit of a very large image is (so it can still fit)."""
        floor = min(self.MIN_ZOOM, self.zoom_out_limit)
        return max(floor, min(self.MAX_ZOOM, float(factor)))

    def _anchor_point(self, anchor: QPointF | None) -> QPoint:
        """The viewport pixel of ``anchor``; the viewport centre for None."""
        if anchor is None:
            return self.viewport().rect().center()
        return anchor.toPoint()

    def _apply_zoom(self, factor: float, anchor_pt: QPoint, scene_pt: QPointF) -> None:
        """Show ``factor`` with ``scene_pt`` under viewport pixel ``anchor_pt``; report it."""
        self.setTransform(QTransform.fromScale(factor, factor))
        drift = self.mapFromScene(scene_pt) - anchor_pt
        self._scroll_by(drift.x(), drift.y())
        # Remembered only where it landed: near the zoom-out limit the view centres the
        # scene and cannot scroll, and the anchor then shows another point.
        rest = self.mapFromScene(scene_pt) - anchor_pt
        placed = abs(rest.x()) <= 1 and abs(rest.y()) <= 1
        memo = (QPoint(anchor_pt), self._view_state(), QPointF(scene_pt))
        self._anchor_memo = memo if placed else None
        self.zoom_changed.emit(self.zoom_factor)

    def _anchor_scene(self, anchor_pt: QPoint) -> QPointF:
        """The scene point a zoom keeps under viewport pixel ``anchor_pt``.

        The view scrolls in whole pixels, so after a zoom its anchored point sits up to half
        a pixel off the anchor. While nothing else has moved the view since (same anchor,
        scroll, zoom, viewport and scene rect), the next zoom aims at that same point again
        instead of the one now under the pixel: over a run of wheel notches the errors do not
        add up (a deep zoom magnified them to 5 screen px). A zoom that could not put its
        point under the anchor (see :meth:`_apply_zoom`) leaves nothing to aim at again.
        """
        memo = self._anchor_memo
        if memo is not None and memo[0] == anchor_pt and memo[1] == self._view_state():
            return QPointF(memo[2])
        return self.mapToScene(anchor_pt)

    def _view_state(self) -> tuple:
        """What maps viewport pixels to the scene: scroll, zoom, viewport and scene rect."""
        viewport, rect = self.viewport(), self.sceneRect()
        return (
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
            self.transform().m11(),
            viewport.width(),
            viewport.height(),
            rect.x(),
            rect.y(),
            rect.width(),
            rect.height(),
        )

    def _step_zoom(self, target: float, anchor: QPointF | None, *, glide: bool = True) -> None:
        """A step zoom to ``target``: a glide when animations are on, else at once."""
        target = self._clamp_zoom(target)
        ms = self.zoom_animation_ms() if glide else 0
        if ms <= 0 or not self.isVisible() or target == self.zoom_factor:
            self.set_zoom(target, anchor)
            return
        self._auto_fit = False
        animation = self._glide
        if animation is None:
            animation = self._glide = QVariantAnimation(self)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
            animation.valueChanged.connect(self._glide_frame)
        animation.stop()
        self._glide_from, self._glide_to = self.zoom_factor, target
        self._glide_anchor = self._anchor_point(anchor)
        self._glide_scene = self._anchor_scene(self._glide_anchor)
        animation.setDuration(ms)
        animation.start()

    def _glide_frame(self, value) -> None:
        """One frame of a glide at eased progress ``value`` (0..1): geometric in the zoom,
        so it only approaches the target and ends exactly on it."""
        progress = min(1.0, max(0.0, float(value)))
        start, end = self._glide_from, self._glide_to
        factor = end if progress >= 1.0 else start * (end / start) ** progress
        self._apply_zoom(factor, self._glide_anchor, self._glide_scene)

    def _stop_zoom_glide(self) -> None:
        """Leave a glide where it is (the zoom shown stays)."""
        if self.zoom_animating():
            self._glide.stop()

    # -------------------------------------------------------------------- fit
    def set_content_rect(self, rect: QRectF) -> None:
        """Set the image bounds; the scene rect gets padding so any edge can be panned to centre.

        A glide in progress stops (it was anchored on the previous content).
        """
        self._stop_zoom_glide()
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
        self._stop_zoom_glide()
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
        it re-fits on every resize until the user zooms or pans. A glide stops.
        """
        self._stop_zoom_glide()
        self._auto_fit = True
        self._auto_fit_insets = insets
        self._fit(insets)

    def _fit_zoom(self, insets: QMargins | None) -> float | None:
        """The zoom that fits the content into the viewport minus ``insets`` (default
        :meth:`fit_insets`); None when there is nothing to fit or no room yet."""
        rect = self.content_rect()
        m = insets if insets is not None else self.fit_insets()
        vp = self.viewport().rect()
        avail_w = vp.width() - m.left() - m.right()
        avail_h = vp.height() - m.top() - m.bottom()
        if rect.isEmpty() or avail_w < 2 or avail_h < 2:
            return None
        return min(avail_w / rect.width(), avail_h / rect.height())

    def _fit(self, insets: QMargins | None) -> None:
        m = insets if insets is not None else self.fit_insets()
        fit = self._fit_zoom(m)
        if fit is None:
            return  # not laid out yet: the next resize fits
        factor = min(self.MAX_ZOOM, fit)  # never clamped up: a very large image still fits
        self.setTransform(QTransform.fromScale(factor, factor))
        # centerOn() centres on the whole viewport; shift so the content is
        # centred in the inset rect instead.
        shift = QPointF(m.right() - m.left(), m.bottom() - m.top()) / (2 * factor)
        self.centerOn(self.content_rect().center() + shift)
        self.zoom_changed.emit(self.zoom_factor)

    def resizeEvent(self, event) -> None:
        # A view's resizeEvent is its viewport's (QAbstractScrollArea), sized already.
        super().resizeEvent(event)
        if self._auto_fit:
            self._fit(self._auto_fit_insets)
        self.viewport_changed.emit()

    # ------------------------------------------------------------- pixel grid
    def pixel_grid(self) -> bool:
        """True while the pixel grid is on (it shows from ``PIXEL_GRID_MIN_ZOOM`` up)."""
        return self._pixel_grid

    def set_pixel_grid(self, on: bool) -> None:
        """Turn the pixel grid on or off (display only); ``pixel_grid_changed`` reports it."""
        on = bool(on)
        if on == self._pixel_grid:
            return
        self._pixel_grid = on
        self.viewport().update()
        self.pixel_grid_changed.emit(on)

    def toggle_pixel_grid(self) -> bool:
        """Turn the pixel grid on when it is off and off when it is on; the new state."""
        self.set_pixel_grid(not self._pixel_grid)
        return self._pixel_grid

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """The items' foreground, then the pixel grid when it is on and the zoom is high."""
        super().drawForeground(painter, rect)
        if self._pixel_grid and self.zoom_factor >= self.PIXEL_GRID_MIN_ZOOM - 1e-9:
            colour = QColor(tokens.TEXT)
            colour.setAlpha(self.PIXEL_GRID_ALPHA)
            draw_pixel_grid(painter, rect.intersected(self.content_rect()), colour)

    # ------------------------------------------------------------------ wheel
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.drawing:
            event.ignore()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120.0  # fractions from touchpads
            if notches:
                self._wheel_zoom(notches, event.position())
            event.accept()
            return
        self._stop_zoom_glide()  # the glide's anchor would undo the scroll
        self._auto_fit = False  # a plain wheel scrolls the view
        super().wheelEvent(event)

    def _wheel_zoom(self, notches: float, anchor: QPointF) -> None:
        """Ctrl+wheel: ``WHEEL_STEP`` per notch about ``anchor``, from a glide's target.

        Whole notches glide; a touchpad's fraction of a notch zooms at once (its input is
        already smooth) unless a glide is under way, which then heads further. Zooming out
        stops at :attr:`zoom_out_limit`.
        """
        zoom = self.target_zoom
        target = zoom * self.WHEEL_STEP**notches
        if notches < 0:
            target = max(target, min(self.zoom_out_limit, zoom))
        self._step_zoom(target, anchor, glide=abs(notches) >= 1 or self.zoom_animating())

    # ---------------------------------------------------------------- gestures
    def viewportEvent(self, event: QEvent) -> bool:
        """Native trackpad gestures (macOS, Linux), then the view's own handling."""
        kind = event.type()
        if kind == QEvent.Type.NativeGesture and self._native_gesture(event):
            return True
        if kind == QEvent.Type.DevicePixelRatioChange:  # another screen, same logical size
            self.viewport_changed.emit()
        return super().viewportEvent(event)

    def _native_gesture(self, event: QNativeGestureEvent) -> bool:
        """A pinch zooms about the fingers; a smart zoom toggles fit and 100 %.

        Both are taken (and do nothing) while a tool drag is in progress; True when
        ``event`` was taken. Windows precision touchpads send Ctrl+wheel instead.
        """
        kind = event.gestureType()
        if kind == Qt.NativeGestureType.ZoomNativeGesture:
            if not self.drawing and event.value():
                self._pinch_zoom(event.value(), event.position())
        elif kind == Qt.NativeGestureType.SmartZoomNativeGesture:  # two-finger double tap
            if not self.drawing:
                if abs(self.zoom_factor - 1.0) <= self.ZOOM_STOP_TOLERANCE:
                    self.fit_to_view()
                else:
                    self.set_zoom(1.0, event.position())
        else:
            return False
        event.accept()
        return True

    def _pinch_zoom(self, value: float, anchor: QPointF) -> None:
        """A pinch step about ``anchor``: ``value`` is the zoom increment (0.25 = 25 % bigger).

        From a glide's target, at once (the gesture is already smooth; ``set_zoom`` ends
        the glide). Zooming out stops at :attr:`zoom_out_limit`, as :meth:`_wheel_zoom`.
        """
        zoom = self.target_zoom
        target = zoom * max(1.0 + value, 0.05)
        if value < 0:
            target = max(target, min(self.zoom_out_limit, zoom))
        self.set_zoom(target, anchor)

    # --------------------------------------------------------------- keyboard
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._canvas_key_press(event):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        # A pan ends on the release of its Space whatever else is held by then (Space, then
        # Ctrl, then Space up first); only a plain Space starts one (_canvas_key_press).
        if event.key() == Qt.Key.Key_Space and (self.space_pan or event.modifiers() in _PLAIN):
            if not event.isAutoRepeat():
                self.set_space_pan(False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        # The keyboard left (another widget, a popup, another window): a held Space is
        # released there, so this view would keep panning.
        super().focusOutEvent(event)
        self.end_holds()

    def end_holds(self) -> None:
        """End the holds of keys whose release this view will not see (Space)."""
        if self.space_pan:
            self.set_space_pan(False)

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
                Qt.Key.Key_Equal: lambda: self.zoom_step(+1),
                Qt.Key.Key_Plus: lambda: self.zoom_step(+1),
                Qt.Key.Key_Minus: lambda: self.zoom_step(-1),
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
        self._stop_zoom_glide()  # the press lands where the user sees it
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
        self._stop_zoom_glide()
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

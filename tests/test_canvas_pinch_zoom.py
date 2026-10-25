"""Trackpad pinch and smart zoom (app/gui/canvas_view.py).

macOS and Linux (Wayland pointer gestures, X11 XInput 2.4) trackpads send
``QNativeGestureEvent`` instead of Ctrl+wheel. A ``ZoomNativeGesture`` of value v multiplies
the zoom by ``1 + v`` about the fingers, at once (no glide), within the zoom-out limit and
``MAX_ZOOM``; a ``SmartZoomNativeGesture`` (two-finger double tap) toggles fit and 100 %.
Both are ignored mid-stroke. Every canvas has it, the editor canvas without asking its sink.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QNativeGestureEvent, QPixmap, QPointingDevice  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

ZOOM = Qt.NativeGestureType.ZoomNativeGesture
SMART_ZOOM = Qt.NativeGestureType.SmartZoomNativeGesture
FINGERS = QPointF(150, 120)


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()


class _WheelEatingSink:
    """An InputSink that consumes every wheel turn and records whether it was asked."""

    def __init__(self):
        self.wheels = 0

    def canvas_size(self) -> QSize:
        return QSize(800, 600)

    def canvas_press(self, pos, event) -> bool:
        return True

    def canvas_move(self, pos, event, dragging) -> None:
        pass

    def canvas_release(self, pos, event) -> None:
        pass

    def canvas_double_click(self, pos, event) -> None:
        pass

    def canvas_wheel(self, event) -> bool:
        self.wheels += 1
        return True

    def canvas_key_press(self, event) -> bool:
        return False

    def canvas_key_release(self, event) -> bool:
        return False

    def canvas_hover_left(self) -> None:
        pass

    def canvas_pan_changed(self, panning) -> None:
        pass


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_view(app, monkeypatch):
    """A shown ``size`` view over a ``w`` x ``h`` content rect (a small pixmap stands in)."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    made = []

    def make(w=800, h=600, size=(400, 300), cls=_View):
        view = cls()
        scene = QGraphicsScene(view)
        pm = QPixmap(64, 48)
        pm.fill(QColor(90, 90, 90))
        scene.addItem(QGraphicsPixmapItem(pm))
        view.setScene(scene)
        view.set_content_rect(QRectF(0, 0, w, h))
        view.resize(*size)
        view.show()
        app.processEvents()
        made.append(view)
        return view

    yield make
    delete_widgets(made)


def gesture(view, kind, value: float = 0.0, pos: QPointF = FINGERS) -> bool:
    """Send a native gesture of ``kind`` at viewport position ``pos``; whether it was taken."""
    vp = view.viewport()
    event = QNativeGestureEvent(
        kind,
        QPointingDevice.primaryPointingDevice(),
        2,
        pos,
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        value,
        QPointF(0, 0),
    )
    return QApplication.sendEvent(vp, event)


def test_pinch_multiplies_the_zoom_and_keeps_the_anchor(make_view):
    view = make_view()
    view.set_zoom(0.5)
    before = view.mapToScene(FINGERS.toPoint())
    assert gesture(view, ZOOM, 0.25)
    assert view.zoom_factor == pytest.approx(0.625)
    after = view.mapToScene(FINGERS.toPoint())
    assert after.x() == pytest.approx(before.x(), abs=0.5)
    assert after.y() == pytest.approx(before.y(), abs=0.5)


def test_pinch_out_stops_at_the_zoom_out_limit(make_view):
    view = make_view()
    for _ in range(60):
        gesture(view, ZOOM, -0.10)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)


def test_pinch_in_stops_at_max_zoom(make_view):
    view = make_view()
    for _ in range(80):
        gesture(view, ZOOM, 0.20)
    assert view.zoom_factor == pytest.approx(view.MAX_ZOOM)


def test_pinch_is_ignored_while_drawing(make_view):
    view = make_view()
    view.set_zoom(0.5)
    view.drawing = True
    gesture(view, ZOOM, 0.25)
    assert view.zoom_factor == pytest.approx(0.5)


def test_smart_zoom_toggles_fit_and_actual_pixels(make_view):
    view = make_view()
    view.fit_to_view()
    fitted = view.zoom_factor
    assert fitted != pytest.approx(1.0)
    gesture(view, SMART_ZOOM)
    assert view.zoom_factor == pytest.approx(1.0)
    gesture(view, SMART_ZOOM)
    assert view.zoom_factor == pytest.approx(fitted)


def test_pinch_ends_a_glide(make_view):
    view = make_view()
    view.set_zoom(1.0)
    view.set_zoom_animation(120)
    view.zoom_step(+1)  # glides to 125 %
    assert view.zoom_animating()
    gesture(view, ZOOM, 0.25)
    assert not view.zoom_animating()
    assert view.zoom_factor == pytest.approx(1.25 * 1.25)  # on from the glide's target


def test_pinch_reaches_the_editor_canvas(make_view):
    view = make_view(cls=EditorCanvasView)
    sink = _WheelEatingSink()
    view.set_sink(sink)
    view.set_zoom(0.5)
    gesture(view, ZOOM, 0.25)
    assert view.zoom_factor == pytest.approx(0.625)
    assert sink.wheels == 0

"""How far the canvas zooms out (app/gui/canvas_view.py).

Interactive zoom-out (Ctrl+wheel, Ctrl+- and the zoom-out button) stops at the zoom-out
limit, ``min(100 %, half the fit zoom)``: the image never shrinks below half its fitted
size, and a small image can always go down to actual pixels. Without an image (or before
the view is laid out) the absolute ``MIN_ZOOM`` applies. A fit always fits, however large
the image: the fit zoom is never clamped up to ``MIN_ZOOM``. Programmatic ``set_zoom`` keeps
the absolute limits.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMargins, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap, QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_view(app, monkeypatch):
    """A shown ``size`` view over a ``w`` x ``h`` content rect (a small pixmap stands in)."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    made = []

    def make(w=800, h=600, size=(400, 300)):
        view = _View()
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
    for view in made:
        view.close()
        view.deleteLater()


def fit_zoom(view, w, h, margin=CanvasViewMixin.FIT_MARGIN) -> float:
    vp = view.viewport().rect()
    return min((vp.width() - 2 * margin) / w, (vp.height() - 2 * margin) / h)


def wheel(view, delta: int, pos: QPointF | None = None) -> None:
    pos = QPointF(150, 120) if pos is None else pos
    vp = view.viewport()
    event = QWheelEvent(
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        CTRL,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, event)


def test_the_zoom_out_limit_is_half_the_fit_zoom(make_view):
    view = make_view()
    assert view.zoom_out_limit == pytest.approx(fit_zoom(view, 800, 600) / 2)


def test_zoom_steps_down_stop_at_the_limit(make_view):
    view = make_view()
    view.fit_to_view()
    for _ in range(12):
        view.zoom_step(-1)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)
    view.zoom_step(-1)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)


def test_ctrl_wheel_out_stops_at_the_limit(make_view):
    view = make_view()
    view.fit_to_view()
    for _ in range(20):
        wheel(view, -120)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)


def test_a_small_image_can_always_zoom_out_to_actual_pixels(make_view):
    view = make_view(64, 48)  # fits at about 5x
    assert fit_zoom(view, 64, 48) / 2 > 1.0
    assert view.zoom_out_limit == 1.0
    view.fit_to_view()
    for _ in range(12):
        view.zoom_step(-1)
    assert view.zoom_factor == pytest.approx(1.0)


def test_below_the_limit_a_step_down_stays_and_a_step_up_zooms_in(make_view):
    view = make_view()
    view.set_zoom(0.11)  # programmatic: below the interactive limit
    view.zoom_step(-1)
    assert view.zoom_factor == pytest.approx(0.11)
    view.zoom_step(+1)
    assert view.zoom_factor > 0.11


def test_the_limit_follows_the_viewport_size(app, make_view):
    view = make_view()
    small = view.zoom_out_limit
    view.resize(800, 600)
    app.processEvents()
    assert view.zoom_out_limit == pytest.approx(fit_zoom(view, 800, 600) / 2)
    assert view.zoom_out_limit > small


def test_without_content_the_absolute_minimum_applies(make_view):
    view = make_view()
    view.clear_content_rect()
    assert view.zoom_out_limit == CanvasViewMixin.MIN_ZOOM


def test_programmatic_zoom_keeps_the_absolute_limits(make_view):
    view = make_view()
    view.set_zoom(0.0001)
    assert view.zoom_factor == pytest.approx(CanvasViewMixin.MIN_ZOOM)
    view.zoom_by(0.5)
    assert view.zoom_factor == pytest.approx(CanvasViewMixin.MIN_ZOOM)


def test_a_huge_image_fits_below_the_absolute_minimum(make_view):
    """A 6000 x 4000 photo in a 600 px canvas fits at about 0.09x: Ctrl+0 must show all of it
    (the fit used to be clamped up to MIN_ZOOM = 0.1, cutting the image off)."""
    view = make_view(6000, 4000, size=(600, 400))
    expected = fit_zoom(view, 6000, 4000)
    assert expected < CanvasViewMixin.MIN_ZOOM

    view.fit_to_view()

    assert view.zoom_factor == pytest.approx(expected)
    shown = view.viewportTransform().mapRect(QRectF(0, 0, 6000, 4000))
    vp = view.viewport().rect()
    assert shown.left() >= -1 and shown.right() <= vp.width() + 1
    assert shown.top() >= -1 and shown.bottom() <= vp.height() + 1
    view.zoom_step(-1)  # and zooming out from there stops at half of it
    assert view.zoom_out_limit == pytest.approx(expected / 2)
    assert view.zoom_factor < expected


def test_a_fit_with_insets_bigger_than_the_margin_keeps_the_limit_at_half_of_it(make_view):
    view = make_view()
    insets = QMargins(10, 20, 150, 40)
    view.set_fit_insets_provider(lambda: insets)
    vp = view.viewport().rect()
    fit = min((vp.width() - 160) / 800, (vp.height() - 60) / 600)
    assert view.zoom_out_limit == pytest.approx(fit / 2)

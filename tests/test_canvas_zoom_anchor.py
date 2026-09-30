"""The point under the cursor stays under it through a run of Ctrl+wheel notches
(app/gui/canvas_view.py).

The view scrolls in whole pixels, so after each zoom the anchored scene point sits up to half
a pixel off the cursor. Re-reading the point under the cursor at the next notch took that error
as the new target, and a run of notches into a deep zoom magnified the earlier errors: the
image slid by up to 5 screen px under a still cursor (270 % -> 1160 %). While nothing else
moves the view, each zoom now aims at the point the run started from.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap, QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app, monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    view = _View()
    scene = QGraphicsScene(view)
    pm = QPixmap(2000, 1500)
    pm.fill(QColor(90, 90, 90))
    scene.addItem(QGraphicsPixmapItem(pm))
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, 2000, 1500))
    view.resize(900, 700)
    view.show()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    del view, scene
    flush_deleted()


def wheel(view, anchor: QPointF, delta: int) -> None:
    vp = view.viewport()
    event = QWheelEvent(
        anchor,
        QPointF(vp.mapToGlobal(anchor.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        CTRL,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, event)


def screen_drift(view, anchor: QPointF, start: QPointF) -> float:
    """How far (screen px) the scene point ``start`` now is from the cursor pixel."""
    now = view.mapFromScene(start)
    pixel = anchor.toPoint()
    return max(abs(now.x() - pixel.x()), abs(now.y() - pixel.y()))


@pytest.mark.parametrize("zoom", [0.3, 1.0, 2.7])
@pytest.mark.parametrize("anchor", [QPointF(123.4, 321.7), QPointF(700.2, 90.6), QPointF(450, 350)])
def test_a_run_of_wheel_notches_keeps_the_point_under_the_cursor(view, zoom, anchor):
    view.set_zoom(zoom)
    view.centerOn(1000, 750)
    start = view.mapToScene(anchor.toPoint())
    worst = 0.0
    for delta in [120] * 8 + [-120] * 8:
        wheel(view, anchor, delta)
        worst = max(worst, screen_drift(view, anchor, start))
    assert worst <= 1.0
    assert view.zoom_factor == pytest.approx(zoom)


def test_a_pan_between_notches_anchors_on_the_new_point(view):
    anchor = QPointF(300.3, 200.6)
    view.set_zoom(2.0)
    wheel(view, anchor, 120)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 57)  # a pan
    start = view.mapToScene(anchor.toPoint())
    wheel(view, anchor, 120)
    assert screen_drift(view, anchor, start) <= 1.0


def test_another_cursor_position_anchors_on_the_point_under_it(view):
    view.set_zoom(2.0)
    wheel(view, QPointF(100, 100), 120)
    anchor = QPointF(600.4, 500.2)
    start = view.mapToScene(anchor.toPoint())
    wheel(view, anchor, 120)
    assert screen_drift(view, anchor, start) <= 1.0

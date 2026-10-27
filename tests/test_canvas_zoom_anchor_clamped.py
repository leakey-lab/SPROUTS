"""A zoom the view could not anchor does not steer the next one (app/gui/canvas_view.py).

A zoom keeps the scene point under its anchor by scrolling. Near the zoom-out limit the
padded scene is no larger than the viewport: the view centres it and cannot scroll, so the
anchored point is not under the anchor any more. The next zoom at the same anchor must then
start from the point shown there, not from the one the earlier zooms aimed at (the view
jumped back to that one: ~500 px at 300 % in the probe below).
"""

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui import canvas_gl
from app.gui.canvas_view import CanvasViewMixin
from tests.qt_teardown import flush_deleted

W, H = 400, 300


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()
        self.setFrameShape(QFrame.Shape.NoFrame)


@pytest.fixture
def view(app, monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    view = _View()
    scene = QGraphicsScene(view)
    pm = QPixmap(W, H)
    pm.fill(QColor(90, 90, 90))
    item = QGraphicsPixmapItem(pm)
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, W, H))
    view.resize(W, H)
    view.show()
    app.processEvents()
    view.fit_to_view()
    yield view
    scene.removeItem(item)
    view.close()
    view.deleteLater()
    del view, scene
    flush_deleted()


def _off_anchor(view, scene_pt: QPointF, anchor: QPoint) -> int:
    shown = view.mapFromScene(scene_pt)
    return max(abs(shown.x() - anchor.x()), abs(shown.y() - anchor.y()))


def _zoom_out_to_the_limit(view, anchor: QPointF) -> None:
    for _ in range(12):  # Ctrl+wheel notches out, well past the limit
        view._wheel_zoom(-1.0, anchor)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)


def test_the_setup_clamps_the_zoom_out(view):
    """At the limit the view centres the scene: the anchored point is off the cursor."""
    anchor = QPointF(60, 50)
    view.set_zoom(2.0, anchor)
    aimed = view.mapToScene(anchor.toPoint())
    _zoom_out_to_the_limit(view, anchor)
    assert _off_anchor(view, aimed, anchor.toPoint()) > 50


def test_a_zoom_after_a_clamped_zoom_out_keeps_the_point_shown_under_the_cursor(view):
    anchor = QPointF(60, 50)
    view.set_zoom(2.0, anchor)
    _zoom_out_to_the_limit(view, anchor)
    shown = view.mapToScene(anchor.toPoint())  # what the still cursor is over now

    view.set_zoom(3.0, anchor)

    assert _off_anchor(view, shown, anchor.toPoint()) <= 1


def test_a_wheel_notch_after_a_clamped_zoom_out_starts_from_the_point_shown(view):
    anchor = QPointF(60, 50)
    view.set_zoom(2.0, anchor)
    _zoom_out_to_the_limit(view, anchor)
    view.set_zoom(0.9, anchor)  # a zoom the view can anchor again (scrollable)
    shown = view.mapToScene(anchor.toPoint())

    view._wheel_zoom(4.0, anchor)  # four notches in, at once (animations are off)

    assert _off_anchor(view, shown, anchor.toPoint()) <= 1


def test_keyboard_steps_in_from_the_limit_zoom_about_the_centre_shown(view):
    """Ctrl+- to the limit, then Ctrl+=: the zoom goes on about what the centre shows."""
    view.set_zoom(4.0)
    view.centerOn(40.0, 30.0)  # zoomed into the top-left corner
    for _ in range(10):
        view.zoom_step(-1)
    assert view.zoom_factor == pytest.approx(view.zoom_out_limit)
    centre = view.viewport().rect().center()
    shown = view.mapToScene(centre)

    view.set_zoom(3.0)

    assert _off_anchor(view, shown, centre) <= 1

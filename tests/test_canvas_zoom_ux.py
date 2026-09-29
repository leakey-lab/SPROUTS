"""Zoom UX of the shared canvas view (app/gui/canvas_view.py, spec section 10.5) and the
GL viewport's MSAA request (app/gui/canvas_gl.py, spec section 10.4).

Keys and buttons step along a ladder of round zoom stops (80 % and 125 % around 100 %,
integer stops from 2x), the wheel stays continuous (1.2 per notch), and GL viewports ask
for ``gl_samples()`` MSAA samples (``ROOT_VIEWER_GL_SAMPLES``, default 4, 0 = off).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap, QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
    QMessageBox,
    QWidget,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402
from app.gui.widgets import ZoomCluster  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier
IMAGE_W, IMAGE_H = 800, 600


class _View(CanvasViewMixin, QGraphicsView):
    """The smallest canvas view: the mixin on a plain QGraphicsView."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_message_boxes(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("unexpected QMessageBox.exec in a zoom test")

    monkeypatch.setattr(QMessageBox, "exec", fail)


@pytest.fixture
def view(app, monkeypatch):
    """A shown 400x300 raster view over an 800x600 image at 100 %."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    view = _View()
    scene = QGraphicsScene(view)
    pm = QPixmap(IMAGE_W, IMAGE_H)
    pm.fill(QColor(90, 90, 90))
    scene.addItem(QGraphicsPixmapItem(pm))
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, IMAGE_W, IMAGE_H))
    view.resize(400, 300)
    view.show()
    app.processEvents()
    view.set_zoom(1.0)
    yield view
    view.close()


# --------------------------------------------------------------------------- ladder
def test_zoom_stops_are_sorted_round_numbers_within_the_limits():
    stops = CanvasViewMixin.ZOOM_STOPS
    assert list(stops) == sorted(stops)
    assert stops[0] == CanvasViewMixin.MIN_ZOOM == 0.1
    assert stops[-1] == CanvasViewMixin.MAX_ZOOM == 20.0
    for stop in (0.8, 1.0, 1.25, 2.0, 3.0, 4.0, 5.0, 6.0):
        assert stop in stops


@pytest.mark.parametrize(
    ("start", "direction", "expected"),
    [
        (1.0, +1, 1.25),
        (1.0, -1, 0.8),
        (1.63, +1, 2.0),
        (1.63, -1, 1.5),
        (2.0, +1, 3.0),
        (3.0, -1, 2.0),
        (0.9995, +1, 1.25),  # within the 1e-3 tolerance of 100 %: the next stop up
        (1.0005, -1, 0.8),
        (20.0, +1, 20.0),  # clamped at MAX_ZOOM
        (0.1, -1, 0.1),  # clamped at MIN_ZOOM
        (0.11, -1, 0.1),
        (19.0, +1, 20.0),
    ],
)
def test_zoom_step_moves_to_the_neighbouring_stop(view, start, direction, expected):
    view.set_zoom(start)
    view.zoom_step(direction)
    assert view.zoom_factor == pytest.approx(expected)


def test_zoom_step_keeps_the_anchor_point_in_place(view):
    anchor = QPointF(120, 80)
    before = view.mapToScene(anchor.toPoint())
    view.zoom_step(+1, anchor)
    after = view.mapToScene(anchor.toPoint())
    assert view.zoom_factor == pytest.approx(1.25)
    assert abs(after.x() - before.x()) <= 1.0
    assert abs(after.y() - before.y()) <= 1.0


def test_zoom_step_reports_the_new_zoom(view):
    seen = []
    view.zoom_changed.connect(seen.append)
    view.zoom_step(+1)
    assert seen == [pytest.approx(1.25)]


@pytest.mark.parametrize(
    ("key", "start", "expected"),
    [
        (Qt.Key.Key_Equal, 1.63, 2.0),
        (Qt.Key.Key_Plus, 1.63, 2.0),
        (Qt.Key.Key_Minus, 1.63, 1.5),
        (Qt.Key.Key_Equal, 4.0, 5.0),
        (Qt.Key.Key_Minus, 0.5, 0.333),
    ],
)
def test_ctrl_zoom_keys_follow_the_ladder(view, key, start, expected):
    view.set_zoom(start)
    QTest.keyClick(view, key, CTRL)
    assert view.zoom_factor == pytest.approx(expected)


def test_one_wheel_notch_still_zooms_by_1_2(view):
    vp = view.viewport()
    pos = QPointF(100, 100)
    event = QWheelEvent(
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        CTRL,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, event)
    assert view.zoom_factor == pytest.approx(1.2)


# ------------------------------------------------------------------------ zoom cluster
@pytest.fixture
def cluster(view):
    host = QWidget()
    zoom = ZoomCluster(host)
    zoom.bind(view)
    yield zoom
    host.deleteLater()


def _menu_entries(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def test_zoom_cluster_buttons_step_along_the_ladder(view, cluster):
    view.set_zoom(1.63)
    cluster.zoom_in_button.click()
    assert view.zoom_factor == pytest.approx(2.0)
    cluster.zoom_in_button.click()
    assert view.zoom_factor == pytest.approx(3.0)
    cluster.zoom_out_button.click()
    cluster.zoom_out_button.click()
    assert view.zoom_factor == pytest.approx(1.5)
    assert cluster.zoom_pct_label.text() == "150%"


def test_zoom_readout_menu_lists_fit_then_the_presets(view, cluster):
    menu = cluster.preset_menu()
    assert _menu_entries(menu) == ["Fit", "50%", "100%", "200%", "400%", "800%", "1600%"]
    presets = [a for a in menu.actions() if not a.isSeparator() and a.text() != "Fit"]
    assert all(action.isCheckable() for action in presets)


@pytest.mark.parametrize(("zoom", "checked"), [(2.0, ["200%"]), (1.0, ["100%"]), (1.63, [])])
def test_zoom_readout_menu_checks_the_current_zoom(view, cluster, zoom, checked):
    view.set_zoom(zoom)
    menu = cluster.preset_menu()
    assert [a.text() for a in menu.actions() if a.isChecked()] == checked


@pytest.mark.parametrize(
    ("entry", "zoom"),
    [("50%", 0.5), ("100%", 1.0), ("200%", 2.0), ("400%", 4.0), ("800%", 8.0), ("1600%", 16.0)],
)
def test_zoom_readout_presets_set_that_zoom(view, cluster, entry, zoom):
    view.set_zoom(1.63)
    menu = cluster.preset_menu()
    next(a for a in menu.actions() if a.text() == entry).trigger()
    assert view.zoom_factor == pytest.approx(zoom)


def test_zoom_readout_menu_fit_fits_the_view(view, cluster):
    view.fit_to_view()
    fitted = view.zoom_factor
    view.set_zoom(8.0)
    menu = cluster.preset_menu()
    next(a for a in menu.actions() if a.text() == "Fit").trigger()
    assert view.zoom_factor == pytest.approx(fitted)


def test_zoom_readout_menu_fit_uses_the_editor_fit(view):
    host = QWidget()
    zoom = ZoomCluster(host)
    fits = []
    zoom.bind(view, fit=lambda: fits.append("fit"))
    next(a for a in zoom.preset_menu().actions() if a.text() == "Fit").trigger()
    assert fits == ["fit"]
    host.deleteLater()


def test_right_click_on_the_readout_opens_the_presets(view, cluster, monkeypatch):
    shown = []
    monkeypatch.setattr(QMenu, "exec", lambda menu, *args: shown.append(_menu_entries(menu)))
    cluster.zoom_pct_label.customContextMenuRequested.emit(QPoint(5, 5))
    assert shown == [["Fit", "50%", "100%", "200%", "400%", "800%", "1600%"]]
    assert cluster.zoom_pct_label.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


# ----------------------------------------------------------------------------- MSAA
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 4),
        ("0", 0),
        ("8", 8),
        (" 2 ", 2),
        ("abc", 4),
        ("", 4),
        ("4.5", 4),
        ("64", 16),
        ("-3", 0),
    ],
)
def test_gl_samples_reads_the_env_var_at_call_time(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(canvas_gl.SAMPLES_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(canvas_gl.SAMPLES_ENV_VAR, value)
    assert canvas_gl.gl_samples() == expected


@pytest.mark.parametrize("value", [None, "0", "8"])
def test_gl_viewport_requests_the_msaa_samples(app, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(canvas_gl.SAMPLES_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(canvas_gl.SAMPLES_ENV_VAR, value)
    viewport = CanvasViewMixin._new_gl_viewport()  # constructed, never shown
    try:
        assert viewport.format().samples() == canvas_gl.gl_samples()
    finally:
        viewport.deleteLater()

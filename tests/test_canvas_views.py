"""Shared canvas behaviour of every image view (app/gui/canvas_view.py, AR-10).

The Library display and the editor canvas of Correct and Trace GT
(``EditorCanvasView``) share one zoom/pan/fit model: zoom read from the transform
and clamped to the same limits, Ctrl+wheel zoom about the cursor, Ctrl+0/1/=/- keys,
fit inside overlay insets (UXE-08) that is kept on resize until the user zooms or
pans (PG-13), Space-hold and middle-drag pan (UXE-05), and one OpenGL viewport
policy with partial updates and mouse tracking on the raster viewport (PE-05,
PE-12, UI-16). The Correct page's layers get the canvas input behind the view.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QMargins, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPixmap, QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QPushButton,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.display_controller import MagnifyingGraphicsView  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402

VIEWS = [MagnifyingGraphicsView, EditorCanvasView]
CTRL = Qt.KeyboardModifier.ControlModifier
NO_MOD = Qt.KeyboardModifier.NoModifier
IMAGE_W, IMAGE_H = 800, 600


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def raster_only(monkeypatch):
    """No OpenGL: the documented env opt-out (also what offscreen CI ends up with)."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")


@pytest.fixture
def make_view(app, raster_only):
    """A shown top-level view holding an 800x600 image, 100 % zoom."""
    made = []

    def make(view_cls, size=(400, 300)):
        view = view_cls(None)
        scene = QGraphicsScene()
        pm = QPixmap(IMAGE_W, IMAGE_H)
        pm.fill(QColor(255, 0, 255))
        scene.addItem(QGraphicsPixmapItem(pm))
        view.setScene(scene)
        view.set_content_rect(QRectF(0, 0, IMAGE_W, IMAGE_H))
        view.resize(*size)
        view.show()
        app.processEvents()
        view.set_zoom(1.0)
        made.append((view, scene))
        return view

    yield make
    for view, _scene in made:
        view.close()


def _wheel(view, pos: QPointF, delta: int, modifiers=CTRL) -> None:
    vp = view.viewport()
    event = QWheelEvent(
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, event)


def _mouse(view, kind, pos: QPointF, button, buttons=None) -> None:
    vp = view.viewport()
    if buttons is None:
        buttons = button
    event = QMouseEvent(kind, pos, QPointF(vp.mapToGlobal(pos.toPoint())), button, buttons, NO_MOD)
    QApplication.sendEvent(vp, event)


def _drag(view, button, start: QPointF, end: QPointF) -> None:
    _mouse(view, QEvent.Type.MouseButtonPress, start, button)
    _mouse(view, QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, button)
    _mouse(view, QEvent.Type.MouseButtonRelease, end, button, Qt.MouseButton.NoButton)


def _scroll(view) -> tuple[int, int]:
    return view.horizontalScrollBar().value(), view.verticalScrollBar().value()


def _content_on_viewport(view) -> QRectF:
    return view.viewportTransform().mapRect(QRectF(0, 0, IMAGE_W, IMAGE_H))


# ----------------------------------------------------------------------------- zoom model
@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_zoom_is_read_from_the_transform_and_clamped_to_shared_limits(make_view, view_cls):
    view = make_view(view_cls)
    view.set_zoom(1000.0)
    assert view.zoom_factor == pytest.approx(20.0)
    assert view.transform().m11() == pytest.approx(20.0)
    view.set_zoom(0.0001)
    assert view.zoom_factor == pytest.approx(0.1)
    assert (view.min_zoom, view.max_zoom) == (view.MIN_ZOOM, view.MAX_ZOOM) == (0.1, 20.0)


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_ctrl_wheel_zooms_about_the_cursor_and_reports_it(make_view, view_cls):
    view = make_view(view_cls)
    seen = []
    view.zoom_changed.connect(seen.append)
    anchor = QPointF(120, 90)
    before = view.mapToScene(anchor.toPoint())

    _wheel(view, anchor, 120)

    assert view.zoom_factor == pytest.approx(1.2)
    assert view.transform().m11() == pytest.approx(view.zoom_factor)
    assert seen and seen[-1] == pytest.approx(1.2)
    after = view.mapToScene(anchor.toPoint())
    # The scene point under the cursor stays under it (to within a screen pixel).
    assert abs(after.x() - before.x()) <= 1.0
    assert abs(after.y() - before.y()) <= 1.0


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_high_resolution_wheel_deltas_zoom_proportionally(make_view, view_cls):
    view = make_view(view_cls)
    _wheel(view, QPointF(100, 100), 60)  # half a notch (touchpads send many small deltas)
    assert view.zoom_factor == pytest.approx(1.2**0.5)
    _wheel(view, QPointF(100, 100), 0)  # horizontal-only wheel: no zoom
    assert view.zoom_factor == pytest.approx(1.2**0.5)


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
@pytest.mark.parametrize(
    ("key", "start", "expected"),
    [
        (Qt.Key.Key_1, 3.0, 1.0),
        (Qt.Key.Key_Equal, 1.0, 1.25),
        (Qt.Key.Key_Plus, 1.0, 1.25),
        (Qt.Key.Key_Minus, 1.0, 0.8),
    ],
)
def test_ctrl_zoom_keys(make_view, view_cls, key, start, expected):
    view = make_view(view_cls)
    view.set_zoom(start)
    QTest.keyClick(view, key, CTRL)
    assert view.zoom_factor == pytest.approx(expected)


# ------------------------------------------------------------------------------ fit
@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_fit_centres_the_image_inside_the_insets(make_view, view_cls):
    view = make_view(view_cls)
    insets = QMargins(10, 20, 30, 40)
    seen = []
    view.zoom_changed.connect(seen.append)

    view.fit_to_view(insets)

    vw, vh = view.viewport().width(), view.viewport().height()
    avail = QRectF(10, 20, vw - 40, vh - 60)
    expected = min(avail.width() / IMAGE_W, avail.height() / IMAGE_H)
    assert view.zoom_factor == pytest.approx(expected)
    assert seen[-1] == pytest.approx(expected)
    shown = _content_on_viewport(view)
    assert abs(shown.center().x() - avail.center().x()) <= 1.0
    assert abs(shown.center().y() - avail.center().y()) <= 1.0
    assert shown.top() >= avail.top() - 1 and shown.bottom() <= avail.bottom() + 1
    assert shown.left() >= avail.left() - 1 and shown.right() <= avail.right() + 1


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_ctrl_0_fits_with_the_host_insets_provider(make_view, view_cls):
    view = make_view(view_cls)
    view.set_fit_insets_provider(lambda: QMargins(0, 0, 0, 100))
    view.set_zoom(5.0)

    QTest.keyClick(view, Qt.Key.Key_0, CTRL)

    vh = view.viewport().height()
    shown = _content_on_viewport(view)
    assert shown.bottom() <= vh - 100 + 1  # clear of the (pretend) dock
    assert view.zoom_factor < 5.0


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_fit_to_view_works_as_a_button_slot(make_view, monkeypatch, view_cls):
    """Editors wire ``fit_button.clicked.connect(view.fit_to_view)``; ``clicked``
    carries a ``checked`` bool that must not be taken for the insets."""
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc[1]))
    view = make_view(view_cls)
    view.set_zoom(5.0)
    button = QPushButton()
    button.clicked.connect(view.fit_to_view)

    button.click()

    assert errors == []
    assert view.zoom_factor < 5.0


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_fitted_view_refits_on_resize_until_the_user_zooms(app, make_view, view_cls):
    view = make_view(view_cls)
    view.fit_to_view(QMargins())
    small = view.zoom_factor

    view.resize(800, 600)
    app.processEvents()
    grown = view.zoom_factor
    vw, vh = view.viewport().width(), view.viewport().height()
    assert grown == pytest.approx(min(vw / IMAGE_W, vh / IMAGE_H))
    assert grown > small

    view.zoom_by(1.25)
    zoomed = view.zoom_factor
    view.resize(500, 400)
    app.processEvents()
    assert view.zoom_factor == pytest.approx(zoomed)


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_panning_stops_the_resize_refit(app, make_view, view_cls):
    view = make_view(view_cls)
    view.fit_to_view(QMargins())
    fitted = view.zoom_factor
    _drag(view, Qt.MouseButton.MiddleButton, QPointF(200, 150), QPointF(190, 140))
    view.resize(800, 600)
    app.processEvents()
    assert view.zoom_factor == pytest.approx(fitted)


# ------------------------------------------------------------------------------ pan
@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_middle_drag_pans_by_the_drag_distance(make_view, view_cls):
    view = make_view(view_cls)
    h0, v0 = _scroll(view)
    _drag(view, Qt.MouseButton.MiddleButton, QPointF(200, 150), QPointF(150, 120))
    h1, v1 = _scroll(view)
    assert (h1 - h0, v1 - v0) == (50, 30)


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_space_hold_turns_left_drag_into_a_pan_with_hand_cursors(make_view, view_cls):
    view = make_view(view_cls)
    vp = view.viewport()
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert vp.cursor().shape() == Qt.CursorShape.OpenHandCursor

    h0, v0 = _scroll(view)
    _mouse(view, QEvent.Type.MouseButtonPress, QPointF(200, 150), Qt.MouseButton.LeftButton)
    assert vp.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    _mouse(
        view,
        QEvent.Type.MouseMove,
        QPointF(230, 170),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    _mouse(
        view,
        QEvent.Type.MouseButtonRelease,
        QPointF(230, 170),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
    )
    assert _scroll(view) == (h0 - 30, v0 - 20)

    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert not view.space_pan


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_page_keys_do_not_scroll_the_canvas(make_view, view_cls):
    view = make_view(view_cls)
    view.set_zoom(3.0)
    before = _scroll(view)
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    QTest.keyClick(view, Qt.Key.Key_PageUp)
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert _scroll(view) == before


# ------------------------------------------------------------------ viewport policy
@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_raster_viewport_gets_partial_updates_and_mouse_tracking(app, raster_only, view_cls):
    view = view_cls(None)
    for _ in range(2):
        assert not view.viewport().inherits("QOpenGLWidget")
        mode = view.viewportUpdateMode()
        assert mode == QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
        assert view.viewport().hasMouseTracking()
        view.set_opengl_viewport_enabled(True)  # opted out by env: stays raster
        view.set_opengl_viewport_enabled(False)


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_gl_viewport_swaps_keep_full_updates_tracking_and_cursor(app, monkeypatch, view_cls):
    monkeypatch.setattr(canvas_gl, "opengl_viewports_allowed", lambda: True)
    view = view_cls(None)  # not shown: offscreen cannot render GL
    cursor = view.viewport().cursor().shape()
    assert view.viewport().inherits("QOpenGLWidget")
    assert view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    assert view.viewport().hasMouseTracking()

    view.set_opengl_viewport_enabled(False)
    assert not view.viewport().inherits("QOpenGLWidget")
    assert view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
    assert view.viewport().hasMouseTracking()
    assert view.viewport().cursor().shape() == cursor

    view.set_opengl_viewport_enabled(True)
    assert view.viewport().inherits("QOpenGLWidget")
    assert view.viewport().cursor().shape() == cursor


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_env_opt_out_wins_over_a_later_gl_request(app, monkeypatch, view_cls):
    """UI-16: closing a dashboard calls set_opengl_viewport_enabled(True) on every view."""
    monkeypatch.setattr(canvas_gl, "opengl_support", lambda: canvas_gl.GLSupport(True, "fake"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    view = view_cls(None)
    view.set_opengl_viewport_enabled(False)
    view.set_opengl_viewport_enabled(True)
    assert not view.viewport().inherits("QOpenGLWidget")


# ------------------------------------------------------------- editors behind the views
def _write_image(path, w=64, h=48) -> str:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 70, 50))
    assert img.save(str(path))
    return str(path)


@pytest.fixture
def correct_page(app, raster_only):
    """``open(image_path, size=(800, 600))``: a shown Correct page with the image open;
    every page made is deleted deterministically afterwards."""
    from app.gui.correct.editor import CorrectEditor

    made = []

    def open_page(image_path, size=(800, 600)):
        page = CorrectEditor()
        made.append(page)
        page.resize(*size)
        page.show()
        app.processEvents()
        page.load_image(image_path)
        app.processEvents()
        return page

    yield open_page
    for page in made:
        page.layers["skeleton"].cancel_scheduled_render()
        page.close()
        page.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_mask_hover_reaches_the_brush_ring_on_the_raster_viewport(correct_page, tmp_path):
    """PE-12: with GL opted out, hover moves must still drive the brush ring."""
    page = correct_page(_write_image(tmp_path / "a.png"))
    ring = page.layers["mask"].ring  # the mask is the edited layer when a page opens

    view = page.graphics_view
    target = QPointF(view.mapFromScene(QPointF(20.5, 30.5)))
    _mouse(view, QEvent.Type.MouseMove, target, Qt.MouseButton.NoButton)

    assert not view.viewport().inherits("QOpenGLWidget")  # the opt-out was honoured
    assert view.viewport().hasMouseTracking()
    assert ring.is_visible() and ring.rect().center() == QPointF(20.5, 30.5)


def test_mask_layer_pans_instead_of_painting_while_space_is_held(correct_page, tmp_path):
    page = correct_page(_write_image(tmp_path / "a.png", 400, 300))
    view = page.graphics_view
    view.set_zoom(4.0)

    view.set_space_pan(True)
    before = _scroll(view)
    _drag(view, Qt.MouseButton.LeftButton, QPointF(300, 300), QPointF(280, 290))
    view.set_space_pan(False)

    assert not page.layers["mask"].document.history.plane.any()  # nothing painted
    assert not page.session.can_undo()
    assert _scroll(view) == (before[0] + 20, before[1] + 10)


def test_mask_layer_ignores_a_left_press_during_a_middle_drag(correct_page, tmp_path):
    """A stroke must not start mid-pan: painting blocks scrolling, the view would desync."""
    page = correct_page(_write_image(tmp_path / "a.png", 400, 300))
    view = page.graphics_view
    view.set_zoom(4.0)
    middle, left = Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton
    both = middle | left

    before = _scroll(view)
    _mouse(view, QEvent.Type.MouseButtonPress, QPointF(300, 300), middle)
    _mouse(view, QEvent.Type.MouseButtonPress, QPointF(300, 300), left, both)
    _mouse(view, QEvent.Type.MouseMove, QPointF(280, 290), Qt.MouseButton.NoButton, both)
    _mouse(view, QEvent.Type.MouseButtonRelease, QPointF(280, 290), left, middle)
    _mouse(view, QEvent.Type.MouseButtonRelease, QPointF(280, 290), middle, Qt.MouseButton.NoButton)

    assert not page.layers["mask"].document.history.plane.any()  # no stroke started
    assert not page.session.can_undo()
    assert not view.drawing
    assert _scroll(view) == (before[0] + 20, before[1] + 10)


def test_skeleton_layer_gets_tools_in_image_pixels_but_not_while_panning(correct_page, tmp_path):
    page = correct_page(_write_image(tmp_path / "s.png", 400, 300), size=(900, 700))
    page.set_active_layer("skeleton")
    view = page.graphics_view
    view.set_zoom(4.0)
    view.centerOn(QPointF(200, 150))
    skeleton = page.layers["skeleton"]
    calls = []
    skeleton.press = lambda pos, modifiers: calls.append(("press", QPoint(pos)))
    skeleton.release = lambda pos: calls.append(("release", QPoint(pos)))

    at = QPointF(view.mapFromScene(QPointF(150.5, 120.5)))
    _mouse(view, QEvent.Type.MouseButtonPress, at, Qt.MouseButton.LeftButton)
    _mouse(
        view, QEvent.Type.MouseButtonRelease, at, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    )
    assert calls == [("press", QPoint(150, 120)), ("release", QPoint(150, 120))]

    calls.clear()
    _drag(view, Qt.MouseButton.MiddleButton, at, at + QPointF(10, 10))
    QTest.keyPress(view, Qt.Key.Key_Space)
    _drag(view, Qt.MouseButton.LeftButton, at, at + QPointF(10, 10))
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert calls == []

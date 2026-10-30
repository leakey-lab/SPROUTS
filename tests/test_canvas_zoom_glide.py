"""Step zooms glide (app/gui/canvas_view.py): a wheel notch, Ctrl+= / Ctrl+- and the zoom
buttons animate the zoom over ``ZOOM_ANIMATION_MS`` (120 ms) instead of jumping.

The zoom moves geometrically with an ease-out curve, so it only ever approaches the target
(no overshoot) and ends exactly on it; the scene point under the anchor (the cursor, else the
view centre) stays put on every frame. A step during a glide goes on from the glide's
target (three quick notches zoom by 1.2 ** 3). ``set_zoom`` and fits are immediate and stop
a glide; a mouse press or a plain wheel scroll freezes it where it is. Touchpad fractions of a
notch zoom at once (the input is already smooth). Glides follow the platform's UI-animation
setting (``QApplication.isEffectEnabled(UI_General)``: Windows' UI effects switch, off on the
offscreen platform) unless ``set_zoom_animation`` overrides it; a hidden view jumps.
"""

import time

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from app.gui import canvas_gl
from app.gui.canvas_view import CanvasViewMixin
from app.gui.widgets import ZoomCluster
from tests.qt_teardown import flush_deleted

CTRL = Qt.KeyboardModifier.ControlModifier
IMAGE_W, IMAGE_H = 800, 600


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()


def _build(app, monkeypatch) -> _View:
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
    return view


@pytest.fixture
def view(app, monkeypatch):
    """A shown 400x300 view over an 800x600 image at 100 %, glides forced on."""
    view = _build(app, monkeypatch)
    view.set_zoom_animation(CanvasViewMixin.ZOOM_ANIMATION_MS)
    yield view
    view.close()
    view.deleteLater()
    del view
    flush_deleted()


def settle(view, timeout_s: float = 3.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while view.zoom_animating() and time.perf_counter() < deadline:
        QTest.qWait(5)
    assert not view.zoom_animating()


def play(view, frames: int = 8) -> None:
    """Play the glide just started frame by frame (deterministic frames, whatever the load)."""
    glide = view._glide
    glide.pause()
    for i in range(1, frames + 1):
        glide.setCurrentTime(round(glide.duration() * i / frames))
    glide.stop()


def wheel(view, delta: int, pos: QPointF | None = None, modifiers=CTRL) -> None:
    pos = QPointF(150, 120) if pos is None else pos
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


def mouse(view, kind, pos: QPointF, button, buttons) -> None:
    vp = view.viewport()
    event = QMouseEvent(
        kind, pos, QPointF(vp.mapToGlobal(pos.toPoint())), button, buttons,
        Qt.KeyboardModifier.NoModifier,
    )  # fmt: skip
    QApplication.sendEvent(vp, event)


def test_a_step_zoom_glides_to_the_next_stop_without_overshooting(view):
    seen = []
    view.zoom_changed.connect(seen.append)

    view.zoom_step(+1)

    assert view.zoom_animating() and view.zoom_factor < 1.25
    assert view.target_zoom == pytest.approx(1.25)
    play(view)
    assert view.zoom_factor == pytest.approx(1.25, abs=1e-12)
    assert len(set(seen)) >= 8  # animated frames, not one jump
    assert seen == sorted(seen) and max(seen) <= 1.25 + 1e-12
    assert seen[-1] == pytest.approx(1.25, abs=1e-12)


def test_a_step_down_glides_down_without_undershooting(view):
    seen = []
    view.zoom_changed.connect(seen.append)
    view.zoom_step(-1)
    play(view)
    assert view.zoom_factor == pytest.approx(0.8, abs=1e-12)
    assert len(set(seen)) >= 8
    assert seen == sorted(seen, reverse=True) and min(seen) >= 0.8 - 1e-12


def test_a_glide_lasts_about_the_animation_time(view):
    t0 = time.perf_counter()
    view.zoom_step(+1)
    settle(view)
    elapsed = time.perf_counter() - t0
    assert 0.8 * CanvasViewMixin.ZOOM_ANIMATION_MS / 1000 <= elapsed < 1.5


def test_the_anchor_point_stays_put_on_every_frame(view):
    anchor = QPointF(120, 80)
    before = view.mapToScene(anchor.toPoint())
    drift = []
    view.zoom_changed.connect(lambda _z: drift.append(view.mapToScene(anchor.toPoint()) - before))
    view.zoom_step(+1, anchor)
    play(view)
    assert len(drift) >= 8 and all(abs(d.x()) <= 1.0 and abs(d.y()) <= 1.0 for d in drift)


def test_steps_during_a_glide_go_on_from_its_target(view):
    seen = []
    view.zoom_changed.connect(seen.append)
    view.zoom_step(+1)
    QTest.qWait(30)
    view.zoom_step(+1)  # 1.25 -> 1.5, not the stop after the half-way zoom
    assert view.target_zoom == pytest.approx(1.5)
    settle(view)
    assert view.zoom_factor == pytest.approx(1.5, abs=1e-12)
    assert seen == sorted(seen)


def test_wheel_notches_glide_and_add_up(view):
    for _ in range(3):
        wheel(view, 120)
    assert view.zoom_animating()
    settle(view)
    assert view.zoom_factor == pytest.approx(1.2**3)


def test_a_touchpad_fraction_of_a_notch_zooms_at_once(view):
    wheel(view, 40)
    assert not view.zoom_animating()
    assert view.zoom_factor == pytest.approx(1.2 ** (1 / 3))


def test_set_zoom_and_fit_are_immediate_and_stop_a_glide(view):
    view.zoom_step(+1)
    view.set_zoom(3.0)
    assert not view.zoom_animating() and view.zoom_factor == pytest.approx(3.0)
    QTest.qWait(200)
    assert view.zoom_factor == pytest.approx(3.0)

    view.zoom_step(+1)
    view.fit_to_view()
    fitted = view.zoom_factor
    assert not view.zoom_animating()
    QTest.qWait(200)
    assert view.zoom_factor == pytest.approx(fitted)


@pytest.mark.parametrize("button", [Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton])
def test_a_press_freezes_a_glide_where_it_is(view, button):
    view.zoom_step(+1)
    QTest.qWait(30)
    mouse(view, QEvent.Type.MouseButtonPress, QPointF(200, 150), button, button)
    frozen = view.zoom_factor
    assert not view.zoom_animating() and 1.0 <= frozen <= 1.25
    QTest.qWait(200)
    assert view.zoom_factor == frozen
    mouse(view, QEvent.Type.MouseButtonRelease, QPointF(200, 150), button, Qt.MouseButton.NoButton)


def test_a_plain_wheel_scroll_freezes_a_glide(view):
    view.zoom_step(+1)
    QTest.qWait(30)
    wheel(view, -120, modifiers=Qt.KeyboardModifier.NoModifier)
    frozen = view.zoom_factor
    assert not view.zoom_animating()
    QTest.qWait(200)
    assert view.zoom_factor == frozen


def test_new_content_stops_a_glide(view):
    view.zoom_step(+1)
    view.set_content_rect(QRectF(0, 0, 640, 480))
    assert not view.zoom_animating()


def test_ctrl_keys_and_zoom_buttons_glide(view):
    QTest.keyClick(view, Qt.Key.Key_Equal, CTRL)
    assert view.zoom_animating()
    settle(view)
    assert view.zoom_factor == pytest.approx(1.25, abs=1e-12)

    host = QWidget()
    cluster = ZoomCluster(host)
    cluster.bind(view)
    cluster.zoom_out_button.click()
    assert view.zoom_animating()
    settle(view)
    assert view.zoom_factor == pytest.approx(1.0, abs=1e-12)
    assert cluster.zoom_pct_label.text() == "100%"
    host.deleteLater()


def test_a_hidden_view_jumps(view):
    view.hide()
    view.zoom_step(+1)
    assert not view.zoom_animating() and view.zoom_factor == pytest.approx(1.25)


def test_glides_follow_the_platform_animation_setting(app, monkeypatch):
    view = _build(app, monkeypatch)
    try:
        assert not QApplication.isEffectEnabled(Qt.UIEffect.UI_General)  # offscreen
        assert view.zoom_animation_ms() == 0
        view.zoom_step(+1)
        assert not view.zoom_animating() and view.zoom_factor == pytest.approx(1.25)

        monkeypatch.setattr(QApplication, "isEffectEnabled", staticmethod(lambda effect: True))
        assert view.zoom_animation_ms() == CanvasViewMixin.ZOOM_ANIMATION_MS
        view.set_zoom_animation(0)
        assert view.zoom_animation_ms() == 0
        view.set_zoom_animation(None)
        assert view.zoom_animation_ms() == CanvasViewMixin.ZOOM_ANIMATION_MS
    finally:
        view.close()
        view.deleteLater()
        flush_deleted()

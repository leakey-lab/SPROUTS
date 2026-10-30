"""EditorCanvasView (C8): the one canvas of every editor. Zoom, pan and fit come from the
shared mixin; tool input goes to an InputSink in image pixels (the Correct router, the
Trace GT editor), keys and the wheel reach the sink first, and a drag stops the view from
scrolling. ``end_gesture()`` forgets a drag the editor has finished on its own.
"""

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFocusEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
)

from app.gui.editor_canvas import EditorCanvasView
from app.gui.widgets import tokens

LEFT, MIDDLE = Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton
NO_BUTTON = Qt.MouseButton.NoButton
CTRL, NO_MOD = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.NoModifier
IMAGE = QSize(200, 150)
# Only an exception of the code under test fails the test (``slot_errors``); another
# module's leftover widget (e.g. a queued repaint) only gets its traceback printed.
SLOT_ERRORS_FROM = ("editor_canvas.py", "test_editor_canvas.py")

# No modal prompt, no GL, and no exception of the code under test hidden in an event handler.
pytestmark = pytest.mark.usefixtures("no_prompts", "gl_off", "slot_errors")


class FakeSink:
    """An InputSink that records every call (positions copied) and answers as scripted."""

    def __init__(self, size: QSize = IMAGE, *, accept_press=True, consume_wheel=False):
        self.size = QSize(size)
        self.accept_press = accept_press
        self.consume_wheel = consume_wheel
        self.consume_keys: set[Qt.Key] = set()
        self.calls: list[tuple] = []

    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]

    def canvas_size(self) -> QSize:
        return QSize(self.size)

    def canvas_press(self, pos, event) -> bool:
        self.calls.append(("press", QPoint(pos)))
        return self.accept_press

    def canvas_move(self, pos, event, dragging) -> None:
        self.calls.append(("move", QPoint(pos), dragging))

    def canvas_release(self, pos, event) -> None:
        self.calls.append(("release", QPoint(pos)))

    def canvas_double_click(self, pos, event) -> None:
        self.calls.append(("double_click", QPoint(pos)))

    def canvas_wheel(self, event) -> bool:
        self.calls.append(("wheel",))
        return self.consume_wheel

    def canvas_key_press(self, event) -> bool:
        self.calls.append(("key", event.key()))
        return event.key() in self.consume_keys

    def canvas_key_release(self, event) -> bool:
        self.calls.append(("key_release", event.key()))
        return event.key() in self.consume_keys

    def canvas_hover_left(self) -> None:
        self.calls.append(("left",))

    def canvas_pan_changed(self, panning) -> None:
        self.calls.append(("pan", panning))

    def canvas_focus_lost(self) -> None:
        self.calls.append(("focus_lost",))


@pytest.fixture
def make_view(app):
    """A shown 400x300 view over a 200x150 image at 400 %, centred; the sink starts empty."""
    made = []

    def make(sink=None, zoom=4.0):
        view = EditorCanvasView(sink)
        scene = QGraphicsScene(view)
        pixmap = QPixmap(IMAGE.width(), IMAGE.height())
        pixmap.fill(QColor(tokens.BG_2))
        scene.addItem(QGraphicsPixmapItem(pixmap))
        view.setScene(scene)
        view.set_content_rect(QRectF(0, 0, IMAGE.width(), IMAGE.height()))
        view.resize(400, 300)
        view.show()
        app.processEvents()
        view.set_zoom(zoom)
        view.centerOn(100, 75)
        if sink is not None:
            sink.calls.clear()
        made.append(view)
        return view

    yield make
    for view in made:
        view.close()


def at(view, x: float, y: float) -> QPointF:
    """The viewport position of scene point (x, y)."""
    return QPointF(view.mapFromScene(QPointF(x, y)))


def mouse(view, kind, pos: QPointF, button=NO_BUTTON, buttons=None, modifiers=NO_MOD) -> None:
    vp = view.viewport()
    held = button if buttons is None else buttons
    global_pos = QPointF(vp.mapToGlobal(pos.toPoint()))
    QApplication.sendEvent(vp, QMouseEvent(kind, pos, global_pos, button, held, modifiers))


def press(view, pos, button=LEFT, buttons=None):
    mouse(view, QEvent.Type.MouseButtonPress, pos, button, buttons)


def drag_to(view, pos, buttons=LEFT):
    mouse(view, QEvent.Type.MouseMove, pos, NO_BUTTON, buttons)


def hover(view, pos):
    mouse(view, QEvent.Type.MouseMove, pos, NO_BUTTON, NO_BUTTON)


def release(view, pos, button=LEFT):
    mouse(view, QEvent.Type.MouseButtonRelease, pos, button, NO_BUTTON)


def wheel(view, pos: QPointF, delta=120, modifiers=CTRL) -> None:
    vp = view.viewport()
    event = QWheelEvent(
        pos,
        QPointF(vp.mapToGlobal(pos.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        NO_BUTTON,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vp, event)


def scroll(view) -> tuple[int, int]:
    return view.horizontalScrollBar().value(), view.verticalScrollBar().value()


# --------------------------------------------------------------------------- pointer
def test_a_press_reaches_the_sink_as_the_floored_image_pixel(make_view):
    sink = FakeSink()
    view = make_view(sink)
    press(view, at(view, 20.75, 30.25))
    assert sink.calls == [("press", QPoint(20, 30))]
    assert view.drawing


def test_drawing_follows_the_sinks_answer_and_a_refused_drag_is_not_forwarded(make_view):
    sink = FakeSink(accept_press=False)
    view = make_view(sink)
    press(view, at(view, 10.5, 10.5))
    assert not view.drawing
    drag_to(view, at(view, 30.5, 10.5))
    release(view, at(view, 30.5, 10.5))
    assert sink.kinds() == ["press"]
    hover(view, at(view, 40.5, 12.5))  # after the release, hover is forwarded again
    assert sink.calls[-1] == ("move", QPoint(40, 12), False)


def test_hover_resumes_when_the_release_of_a_refused_drag_went_elsewhere(make_view):
    """E.g. a modal box took the release: the next move without buttons is hover again."""
    sink = FakeSink(accept_press=False)
    view = make_view(sink)
    press(view, at(view, 10.5, 10.5))
    drag_to(view, at(view, 20.5, 10.5))  # still held: not ours
    hover(view, at(view, 30.5, 11.5))  # no button any more: the release was lost
    hover(view, at(view, 31.5, 11.5))
    assert sink.calls == [
        ("press", QPoint(10, 10)),
        ("move", QPoint(30, 11), False),
        ("move", QPoint(31, 11), False),
    ]


def test_a_double_click_drag_is_not_forwarded(make_view):
    """The second press of a double-click starts no drag, so its drag is not the tool's."""
    sink = FakeSink()
    view = make_view(sink)
    press(view, at(view, 10.5, 10.5))
    release(view, at(view, 10.5, 10.5))
    mouse(view, QEvent.Type.MouseButtonDblClick, at(view, 10.5, 10.5), LEFT)
    drag_to(view, at(view, 25.5, 10.5))
    release(view, at(view, 25.5, 10.5))
    assert sink.kinds() == ["press", "release", "double_click"]


def test_a_drag_moves_and_releases_through_the_sink(make_view):
    sink = FakeSink()
    view = make_view(sink)
    hover(view, at(view, 5.5, 6.5))
    press(view, at(view, 10.5, 10.5))
    drag_to(view, at(view, 12.5, 11.5))
    drag_to(view, at(view, 14.5, 12.5))
    release(view, at(view, 15.5, 12.5))
    assert sink.calls == [
        ("move", QPoint(5, 6), False),
        ("press", QPoint(10, 10)),
        ("move", QPoint(12, 11), True),
        ("move", QPoint(14, 12), True),
        ("release", QPoint(15, 12)),
    ]
    assert not view.drawing


def test_a_double_click_reaches_the_sink(make_view):
    sink = FakeSink()
    view = make_view(sink)
    mouse(view, QEvent.Type.MouseButtonDblClick, at(view, 50.5, 60.5), LEFT)
    assert sink.calls == [("double_click", QPoint(50, 60))]
    assert not view.drawing


def test_positions_are_clamped_to_the_image(make_view):
    sink = FakeSink()
    view = make_view(sink, zoom=1.0)
    assert view.map_to_image(at(view, -30.0, -12.0)) == QPoint(0, 0)
    assert view.map_to_image(at(view, 250.0, 180.0)) == QPoint(199, 149)
    sink.size = QSize()
    assert view.map_to_image(at(view, 50.0, 50.0)) == QPoint(0, 0)


def test_nothing_is_forwarded_while_no_image_is_open(make_view):
    sink = FakeSink(QSize())
    view = make_view(sink)
    hover(view, at(view, 10.5, 10.5))
    press(view, at(view, 10.5, 10.5))
    release(view, at(view, 10.5, 10.5))
    assert sink.calls == [] and not view.drawing


# --------------------------------------------------------------------------- pans
def test_space_turns_a_left_drag_into_a_pan_the_tools_never_see(make_view):
    sink = FakeSink()
    view = make_view(sink)
    QTest.keyPress(view, Qt.Key.Key_Space)
    before = scroll(view)
    press(view, QPointF(200, 150))
    drag_to(view, QPointF(230, 170))
    release(view, QPointF(230, 170))
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert scroll(view) == (before[0] - 30, before[1] - 20)
    assert not {"press", "move", "release", "double_click"} & set(sink.kinds())
    assert ("key", Qt.Key.Key_Space) in sink.calls  # keys still reach the sink first
    assert sink.calls[-1] == ("pan", False)


def test_a_middle_drag_pans_and_a_left_press_during_it_is_ignored(make_view):
    sink = FakeSink()
    view = make_view(sink)
    before = scroll(view)
    press(view, QPointF(200, 150), MIDDLE)
    press(view, QPointF(210, 150), LEFT, MIDDLE | LEFT)
    drag_to(view, QPointF(150, 120), MIDDLE | LEFT)
    release(view, QPointF(150, 120), MIDDLE)
    assert scroll(view) == (before[0] + 50, before[1] + 30)
    assert not {"press", "move", "release"} & set(sink.kinds())
    assert not view.drawing


def test_the_pan_mode_is_reported_to_the_sink(make_view):
    sink = FakeSink()
    view = make_view(sink)
    view.set_mode(True)
    view.set_mode(False)
    assert [c for c in sink.calls if c[0] == "pan"] == [("pan", True), ("pan", False)]


def test_leaving_the_canvas_is_reported(make_view):
    sink = FakeSink()
    view = make_view(sink)
    QApplication.sendEvent(view, QEvent(QEvent.Type.Leave))
    assert sink.calls == [("left",)]


# --------------------------------------------------------------------------- holds
def test_a_lost_keyboard_ends_the_sinks_holds_then_space(make_view):
    """The releases will not come: the sink ends its holds (B), then the view its Space."""
    sink = FakeSink()
    view = make_view(sink)
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert view.space_pan
    sink.calls.clear()
    QApplication.sendEvent(
        view, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason)
    )
    assert sink.calls == [("focus_lost",), ("pan", False)] and not view.space_pan


def test_end_holds_ends_the_sinks_holds_and_space_on_request(make_view):
    sink = FakeSink()
    view = make_view(sink)
    view.end_holds()  # nothing held here: the sink's own hold decides
    assert sink.calls == [("focus_lost",)] and not view.space_pan
    QTest.keyPress(view, Qt.Key.Key_Space)
    sink.calls.clear()
    view.end_holds()
    assert sink.calls == [("focus_lost",), ("pan", False)] and not view.space_pan


# --------------------------------------------------------------------------- wheel
def test_a_wheel_the_sink_consumes_does_not_zoom(make_view):
    sink = FakeSink(consume_wheel=True)
    view = make_view(sink, zoom=1.0)
    wheel(view, QPointF(200, 150))
    assert sink.calls == [("wheel",)] and view.zoom_factor == pytest.approx(1.0)


def test_an_unconsumed_ctrl_wheel_zooms(make_view):
    sink = FakeSink()
    view = make_view(sink, zoom=1.0)
    wheel(view, QPointF(200, 150))
    assert sink.calls == [("wheel",)] and view.zoom_factor == pytest.approx(1.2)


def test_the_wheel_is_ignored_while_drawing(make_view):
    sink = FakeSink(consume_wheel=True)
    view = make_view(sink, zoom=1.0)
    press(view, at(view, 10.5, 10.5))
    wheel(view, QPointF(200, 150))
    assert "wheel" not in sink.kinds() and view.zoom_factor == pytest.approx(1.0)


# --------------------------------------------------------------------------- keys
def test_keys_reach_the_sink_before_the_canvas_keys(make_view):
    sink = FakeSink()
    view = make_view(sink)
    QTest.keyClick(view, Qt.Key.Key_0, CTRL)  # not consumed: the canvas fits
    assert view.zoom_factor < 4.0
    assert ("key", Qt.Key.Key_0) in sink.calls and ("key_release", Qt.Key.Key_0) in sink.calls
    fitted = view.zoom_factor
    view.set_zoom(4.0)
    sink.consume_keys.add(Qt.Key.Key_0)
    QTest.keyClick(view, Qt.Key.Key_0, CTRL)  # consumed: no fit
    assert view.zoom_factor == pytest.approx(4.0) != pytest.approx(fitted)


def test_key_releases_reach_the_sink(make_view):
    sink = FakeSink()
    view = make_view(sink)
    QTest.keyRelease(view, Qt.Key.Key_B)
    assert sink.calls == [("key_release", Qt.Key.Key_B)]


# --------------------------------------------------------------------------- scrolling
def test_the_view_does_not_scroll_while_drawing(make_view):
    sink = FakeSink()
    view = make_view(sink)
    press(view, at(view, 100.5, 75.5))
    before, origin = scroll(view), view.mapToScene(QPoint(0, 0))
    view.ensureVisible(QRectF(190, 140, 5, 5))
    assert scroll(view) == before
    view.horizontalScrollBar().setValue(before[0] + 40)  # e.g. an auto-scroll mid-stroke
    assert view.mapToScene(QPoint(0, 0)) == origin
    release(view, at(view, 100.5, 75.5))
    view.ensureVisible(QRectF(0, 0, 5, 5))
    assert view.mapToScene(QPoint(0, 0)) != origin


def test_end_gesture_turns_the_rest_of_the_drag_into_hover(make_view):
    sink = FakeSink()
    view = make_view(sink, zoom=2.0)
    press(view, at(view, 10.5, 10.5))
    view.end_gesture()
    assert not view.drawing
    drag_to(view, at(view, 12.5, 10.5))
    release(view, at(view, 13.5, 10.5))
    assert sink.calls == [("press", QPoint(10, 10)), ("move", QPoint(12, 10), False)]
    wheel(view, QPointF(200, 150))  # the wheel works again
    assert view.zoom_factor == pytest.approx(2.4)
    before = scroll(view)
    view.ensureVisible(QRectF(195, 145, 4, 4))  # and so does scrolling
    assert scroll(view) != before


# --------------------------------------------------------------------------- set-up
def test_render_hints_antialias_decorations_and_leave_pixmap_smoothing_to_the_items(app):
    hints = EditorCanvasView().renderHints()
    assert hints & QPainter.RenderHint.Antialiasing
    assert hints & QPainter.RenderHint.TextAntialiasing
    assert not hints & QPainter.RenderHint.SmoothPixmapTransform


def test_the_background_is_the_opaque_canvas_colour(app):
    brush = EditorCanvasView().backgroundBrush()
    assert brush.isOpaque() and brush.color().name() == QColor(tokens.BG_0).name()


def test_without_a_sink_it_is_a_plain_canvas_that_pans_and_zooms(make_view):
    view = make_view(None, zoom=1.0)
    press(view, QPointF(100, 100))
    drag_to(view, QPointF(120, 110))
    release(view, QPointF(120, 110))
    mouse(view, QEvent.Type.MouseButtonDblClick, QPointF(120, 110), LEFT)
    QTest.keyClick(view, Qt.Key.Key_B)
    QApplication.sendEvent(view, QEvent(QEvent.Type.Leave))
    assert not view.drawing
    wheel(view, QPointF(200, 150))
    assert view.zoom_factor == pytest.approx(1.2)
    before = scroll(view)
    press(view, QPointF(200, 150), MIDDLE)
    drag_to(view, QPointF(180, 140), MIDDLE)
    release(view, QPointF(180, 140), MIDDLE)
    assert scroll(view) == (before[0] + 20, before[1] + 10)


def test_a_new_sink_forgets_the_drag_of_the_old_one(make_view):
    first, second = FakeSink(), FakeSink()
    view = make_view(first)
    press(view, at(view, 10.5, 10.5))
    view.set_sink(second)
    assert not view.drawing
    release(view, at(view, 11.5, 10.5))
    assert first.kinds() == ["press"] and second.calls == []

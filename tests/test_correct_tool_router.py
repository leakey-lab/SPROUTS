"""ToolRouter: canvas input of the Correct workspace (C16), driven with fake layers.

Offscreen Qt only for the event objects; the layers are ``tests.correct_fakes.FakeLayer``.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication

from app.gui.correct.layer import MASK, SKELETON
from app.gui.correct.tool_router import ToolRouter
from tests.correct_fakes import FakeLayer

NO_MOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
KEYPAD = Qt.KeyboardModifier.KeypadModifier


@pytest.fixture(scope="module", autouse=True)
def qapp():
    """One QApplication for the module, kept referenced."""
    return QApplication.instance() or QApplication([])


class _Rig:
    """A router over an open fake mask and skeleton, recording its callbacks."""

    def __init__(self, active: str = MASK) -> None:
        self.mask = FakeLayer(MASK)
        self.skeleton = FakeLayer(SKELETON)
        self.mask.opened = self.skeleton.opened = True
        self.requests: list[tuple[str, str]] = []
        self.holds: list[bool] = []
        self.shown: list[str] = []
        self.router = ToolRouter(
            {MASK: self.mask, SKELETON: self.skeleton},
            canvas_size=lambda: QSize(640, 480),
            on_tool_request=lambda layer_id, tool_id: self.requests.append((layer_id, tool_id)),
            on_b_hold=self.holds.append,
            on_layer_shown=self.shown.append,
            active=active,
        )
        self.mask.calls.clear()
        self.skeleton.calls.clear()


def _mouse(kind=QEvent.Type.MouseButtonPress, modifiers=NO_MOD) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    return QMouseEvent(kind, QPointF(5, 6), QPointF(5, 6), button, button, modifiers)


def _key(key, *, release=False, modifiers=NO_MOD, auto_repeat=False) -> QKeyEvent:
    kind = QEvent.Type.KeyRelease if release else QEvent.Type.KeyPress
    return QKeyEvent(kind, key, modifiers, "", auto_repeat)


def _wheel(dy: int, dx: int = 0) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 6),
        QPointF(5, 6),
        QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        NO_MOD,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


# ------------------------------------------------------------------------ pointer
def test_a_press_on_the_open_active_layer_starts_a_drag():
    rig = _Rig()
    pos = QPoint(5, 6)

    assert rig.router.canvas_press(pos, _mouse(modifiers=CTRL))

    assert rig.mask.calls == [("press", pos, CTRL)]
    assert rig.skeleton.calls == [] and rig.shown == []


def test_a_press_with_nothing_open_starts_nothing():
    rig = _Rig()
    rig.mask.opened = False

    assert not rig.router.canvas_press(QPoint(1, 1), _mouse())

    assert rig.mask.calls == []


def test_a_press_on_a_hidden_active_layer_shows_it_first():
    rig = _Rig(active=SKELETON)
    rig.skeleton.visible = False
    pos = QPoint(2, 3)

    assert rig.router.canvas_press(pos, _mouse())

    assert rig.skeleton.calls == [("set_visible", True), ("press", pos, NO_MOD)]
    assert rig.shown == [SKELETON]


def test_moves_releases_and_double_clicks_go_to_the_active_layer():
    rig = _Rig(active=SKELETON)
    a, b = QPoint(1, 2), QPoint(3, 4)

    rig.router.canvas_move(a, _mouse(QEvent.Type.MouseMove), False)
    rig.router.canvas_move(b, _mouse(QEvent.Type.MouseMove), True)
    rig.router.canvas_release(b, _mouse(QEvent.Type.MouseButtonRelease))
    rig.router.canvas_double_click(b, _mouse(QEvent.Type.MouseButtonDblClick))

    assert rig.skeleton.calls == [
        ("move", a, False),
        ("move", b, True),
        ("release", b),
        ("double_click", b),
    ]
    assert rig.mask.calls == []


def test_leaving_the_canvas_or_starting_a_pan_hides_the_ring():
    rig = _Rig()

    rig.router.canvas_hover_left()
    rig.router.canvas_pan_changed(True)
    rig.router.canvas_pan_changed(False)

    assert rig.mask.calls == [("hover", None), ("hover", None)]


def test_the_canvas_size_comes_from_the_editor():
    assert _Rig().router.canvas_size() == QSize(640, 480)


# --------------------------------------------------------------------------- B key
def test_a_b_tap_asks_for_the_mask_brush_from_either_layer():
    rig = _Rig(active=SKELETON)

    assert rig.router.canvas_key_press(_key(Qt.Key.Key_B))
    assert rig.router.b_held and rig.holds == [True]
    assert rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True))

    assert not rig.router.b_held
    assert rig.holds == [True, False]
    assert rig.requests == [(MASK, "brush")]


def test_hold_b_and_scroll_sizes_the_mask_brush_and_asks_for_it_once():
    rig = _Rig(active=SKELETON)
    rig.router.canvas_key_press(_key(Qt.Key.Key_B))

    assert rig.router.canvas_wheel(_wheel(120))
    assert rig.router.canvas_wheel(_wheel(-240))
    rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True))

    assert rig.mask.calls == [("step_size", 1), ("step_size", -1)]
    assert rig.skeleton.calls == []
    assert rig.requests == [(MASK, "brush")]  # the first notch: its slider shows the size


@pytest.mark.parametrize(("tool", "requests"), [("brush", []), ("fill", [(MASK, "brush")])])
def test_hold_b_and_scroll_on_the_mask_asks_for_the_brush_only_from_a_sizeless_tool(tool, requests):
    rig = _Rig()
    rig.mask.current_tool = tool
    rig.router.canvas_key_press(_key(Qt.Key.Key_B))

    rig.router.canvas_wheel(_wheel(120))
    rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True))

    assert rig.requests == requests
    assert rig.mask.calls == [("step_size", 1)]


def test_a_sideways_scroll_while_holding_b_neither_resizes_nor_cancels_the_tap():
    rig = _Rig()
    rig.router.canvas_key_press(_key(Qt.Key.Key_B))

    assert rig.router.canvas_wheel(_wheel(0, dx=120))
    rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True))

    assert rig.mask.calls == []
    assert rig.requests == [(MASK, "brush")]


def test_the_wheel_without_b_is_left_to_the_view():
    rig = _Rig()

    assert not rig.router.canvas_wheel(_wheel(120))
    assert rig.mask.calls == []


def test_auto_repeated_and_modified_b_do_not_start_a_hold():
    rig = _Rig()

    assert rig.router.canvas_key_press(_key(Qt.Key.Key_B, auto_repeat=True))
    ctrl_b = _key(Qt.Key.Key_B, modifiers=CTRL)
    rig.router.canvas_key_press(ctrl_b)

    assert not rig.router.b_held and rig.holds == []
    assert rig.mask.calls == [("key", ctrl_b)]  # any other key goes to the layer


def test_keypad_b_holds_and_auto_repeated_releases_do_not_end_the_hold():
    rig = _Rig()

    rig.router.canvas_key_press(_key(Qt.Key.Key_B, modifiers=KEYPAD))
    assert rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True, auto_repeat=True))

    assert rig.router.b_held
    assert rig.holds == [True] and rig.requests == []


def test_releasing_b_with_a_modifier_down_still_ends_the_hold():
    rig = _Rig()
    rig.router.canvas_key_press(_key(Qt.Key.Key_B))

    assert rig.router.canvas_key_release(_key(Qt.Key.Key_B, release=True, modifiers=CTRL))

    assert not rig.router.b_held
    assert rig.holds == [True, False]


# ---------------------------------------------------------------------- other keys
def test_esc_cancels_the_transient_state_of_the_active_layer():
    rig = _Rig(active=SKELETON)

    assert rig.router.canvas_key_press(_key(Qt.Key.Key_Escape))

    assert rig.skeleton.calls == [("cancel_transient",)]
    assert rig.mask.calls == []


@pytest.mark.parametrize(
    "key",
    [Qt.Key.Key_S, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Delete, Qt.Key.Key_Backspace],
)
def test_gesture_keys_go_to_the_active_layer(key):
    rig = _Rig(active=SKELETON)
    event = _key(key)

    rig.skeleton.key_result = True
    assert rig.router.canvas_key_press(event)
    rig.skeleton.key_result = False
    assert not rig.router.canvas_key_press(event)  # unconsumed: the view handles it

    assert rig.skeleton.calls == [("key", event), ("key", event)]
    assert rig.mask.calls == []


def test_space_is_never_consumed():
    rig = _Rig()
    rig.mask.key_result = True  # a layer that would take any key

    assert not rig.router.canvas_key_press(_key(Qt.Key.Key_Space))
    assert not rig.router.canvas_key_release(_key(Qt.Key.Key_Space, release=True))

    assert rig.mask.calls == []


def test_keys_with_nothing_open_are_left_to_the_view():
    rig = _Rig()
    rig.mask.opened = False
    rig.mask.key_result = True

    assert not rig.router.canvas_key_press(_key(Qt.Key.Key_Delete))
    assert not rig.router.canvas_key_press(_key(Qt.Key.Key_Escape))

    assert rig.mask.calls == []


def test_releases_of_other_keys_are_not_consumed():
    assert not _Rig().router.canvas_key_release(_key(Qt.Key.Key_S, release=True))


# -------------------------------------------------------------------- active layer
def test_the_router_starts_with_the_given_active_layer():
    rig = _Rig(active=SKELETON)

    assert rig.router.active_id == SKELETON
    assert rig.skeleton.is_active() and not rig.mask.is_active()


def test_set_active_finishes_a_drag_then_switches():
    rig = _Rig()
    rig.mask.start_gesture()

    assert rig.router.set_active(SKELETON)

    assert rig.router.active_id == SKELETON
    assert rig.mask.calls == [("finish_gesture",), ("set_active", False)]
    assert rig.skeleton.calls == [("set_active", True)]
    assert not rig.mask.gesture_active()


def test_set_active_on_the_active_layer_changes_nothing():
    rig = _Rig()
    rig.mask.start_gesture()

    assert not rig.router.set_active(MASK)

    assert rig.mask.calls == [] and rig.skeleton.calls == []
    assert rig.mask.gesture_active()


def test_input_follows_the_active_layer():
    rig = _Rig()
    rig.router.set_active(SKELETON)
    rig.skeleton.calls.clear()
    pos = QPoint(4, 4)

    rig.router.canvas_press(pos, _mouse())
    rig.router.canvas_key_press(_key(Qt.Key.Key_Escape))

    assert rig.skeleton.calls == [("press", pos, NO_MOD), ("cancel_transient",)]


def test_the_new_layer_shows_its_ring_where_the_pointer_is():
    rig = _Rig()
    pos = QPoint(7, 8)
    rig.router.canvas_move(pos, _mouse(QEvent.Type.MouseMove), False)

    rig.router.set_active(SKELETON)

    assert rig.skeleton.calls == [("set_active", True), ("hover", pos)]


def test_no_ring_is_restored_once_the_pointer_left_the_canvas():
    rig = _Rig()
    rig.router.canvas_move(QPoint(7, 8), _mouse(QEvent.Type.MouseMove), False)
    rig.router.canvas_hover_left()

    rig.router.set_active(SKELETON)

    assert rig.skeleton.calls == [("set_active", True)]


def test_an_unknown_layer_cannot_become_active():
    with pytest.raises(KeyError):
        _Rig().router.set_active("gt")

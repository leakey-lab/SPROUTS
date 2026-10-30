"""Canvas input of the Correct workspace: ``ToolRouter``, the canvas view's input sink.

Pointer events go to the active layer (a hidden active layer is shown before it is
edited). B is a hold key: hold B and scroll to resize the brush of the edited raster layer
(the mask or the class labels; from the skeleton, the mask's: :attr:`ToolRouter.raster_id`;
the scroll asks for the brush, so its slider shows the size), tap B to ask for it. Esc
cancels the active layer's transient state (a raster layer commits its stroke); every other
key goes to the active layer (its tool's gesture keys: S, Enter, Delete, Backspace). Space
is never consumed, so the view keeps its hold-to-pan. A layer switch first finishes a drag
in progress (the mid-gesture rule of ``app.gui.correct.layer``). Uses Qt only for key and
modifier values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from app.gui.correct.layer import MASK, EditLayer

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtCore import QPoint, QSize
    from PyQt6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent

# A B press with one of these starts the hold (as today's mask editor).
_HOLD_MODIFIERS = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)


class ToolRouter:
    """Canvas input of the Correct workspace: pointer to the active layer, B hold/tap,
    gesture keys to the active layer's tool."""

    def __init__(
        self,
        layers: Mapping[str, EditLayer],
        *,
        canvas_size: Callable[[], QSize],
        on_tool_request: Callable[[str, str], None],
        on_b_hold: Callable[[bool], None],
        on_layer_shown: Callable[[str], None],
        active: str = MASK,
    ) -> None:
        self._layers = dict(layers)
        self._canvas_size = canvas_size
        self._on_tool_request = on_tool_request
        self._on_b_hold = on_b_hold
        self._on_layer_shown = on_layer_shown
        self._active_id = active
        self._active = self._layers[active]
        self._b_held = False
        self._b_wheel_used = False
        self._last_pos: QPoint | None = None  # pointer on the canvas, image pixels
        for layer_id, layer in self._layers.items():
            layer.set_active(layer_id == active)

    @property
    def active_id(self) -> str:
        """Id of the layer that receives the input."""
        return self._active_id

    @property
    def b_held(self) -> bool:
        """True while B is held (the wheel then sizes the brush of :attr:`raster_id`)."""
        return self._b_held

    @property
    def raster_id(self) -> str:
        """The layer B and F work on: the edited layer when it paints (it has a brush), else
        the mask (from the skeleton)."""
        if any(spec.tool_id == "brush" for spec in self._active.tools):
            return self._active_id
        return MASK

    def set_active(self, layer_id: str) -> bool:
        """Make ``layer_id`` the edited layer; False when it already is.

        A drag in progress is finished first. The old layer becomes the dimmed
        reference (``set_active(False)``); the new one is told it is active and shows
        its ring where the pointer is.
        """
        layer = self._layers[layer_id]
        if layer_id == self._active_id:
            return False
        for other in self._layers.values():
            if other.gesture_active():
                other.finish_gesture()
        self._active.set_active(False)
        self._active_id, self._active = layer_id, layer
        layer.set_active(True)
        if self._last_pos is not None:
            layer.hover(self._last_pos)
        return True

    # ------------------------------------------------------------------ InputSink
    def canvas_size(self) -> QSize:
        """The image size; empty when nothing is open."""
        return self._canvas_size()

    def canvas_press(self, pos: QPoint, event: QMouseEvent) -> bool:
        """Left press at ``pos``: True when the active layer is open and takes the drag."""
        layer = self._active
        if not layer.is_open():
            return False
        if not layer.is_visible():
            layer.set_visible(True)
            self._on_layer_shown(self._active_id)
        self._last_pos = pos
        layer.press(pos, event.modifiers())
        return True

    def canvas_move(self, pos: QPoint, event: QMouseEvent, dragging: bool) -> None:
        """Hover (``dragging`` False) or drag to ``pos`` on the active layer."""
        self._last_pos = pos
        self._active.move(pos, dragging)

    def canvas_release(self, pos: QPoint, event: QMouseEvent) -> None:
        """End of the drag at ``pos``."""
        self._last_pos = pos
        self._active.release(pos)

    def canvas_double_click(self, pos: QPoint, event: QMouseEvent) -> None:
        """Double-click at ``pos`` (the polyline finishes; the mask ignores it)."""
        self._active.double_click(pos)

    def canvas_wheel(self, event: QWheelEvent) -> bool:
        """While B is held the wheel sizes the brush of :attr:`raster_id` (consumed); else the
        view scrolls. The first notch asks for that brush unless the layer's sized tool is
        edited, so the dock's slider and the ring show the size being set."""
        if not self._b_held:
            return False
        dy = event.angleDelta().y()
        if dy:
            target = self.raster_id
            layer = self._layers[target]
            if not self._b_wheel_used and (self._active is not layer or layer.size_spec() is None):
                self._on_tool_request(target, "brush")
            layer.step_size(1 if dy > 0 else -1)
            self._b_wheel_used = True
        return True

    def canvas_key_press(self, event: QKeyEvent) -> bool:
        """B starts the hold, Esc cancels, other keys go to the active layer; True = consumed."""
        key = event.key()
        if key == Qt.Key.Key_Space:
            return False
        if key == Qt.Key.Key_B and event.modifiers() in _HOLD_MODIFIERS:
            if not event.isAutoRepeat():
                self._b_held, self._b_wheel_used = True, False
                self._on_b_hold(True)
            return True
        layer = self._active
        if not layer.is_open():
            return False
        if key == Qt.Key.Key_Escape:
            layer.cancel_transient()
            return True
        return layer.key(event)

    def canvas_key_release(self, event: QKeyEvent) -> bool:
        """Releasing B ends the hold; without a scroll meanwhile it was a tap (the brush of
        :attr:`raster_id`)."""
        if event.key() != Qt.Key.Key_B:
            return False
        if self._b_held and not event.isAutoRepeat():
            tap = not self._b_wheel_used
            self._b_held = False
            if tap:
                self._on_tool_request(self.raster_id, "brush")
            self._on_b_hold(False)
            return True
        return event.modifiers() in _HOLD_MODIFIERS

    def canvas_hover_left(self) -> None:
        """The pointer left the canvas: hide the ring."""
        self._last_pos = None
        self._active.hover(None)

    def canvas_focus_lost(self) -> None:
        """The canvas lost the keyboard (another window, a dialog): a held B will not see
        its release, so the hold ends here (not a tap: the tool stays)."""
        if self._b_held:
            self._b_held = False
            self._on_b_hold(False)

    def canvas_pan_changed(self, panning: bool) -> None:
        """A pan started (hide the ring) or ended (the next move shows it again)."""
        if panning:
            self._last_pos = None
            self._active.hover(None)

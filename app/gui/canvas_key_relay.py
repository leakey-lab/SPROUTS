"""Hand an editor canvas its own keys while the library tree has the keyboard (W1).

The library tree keeps the keyboard while the arrow keys browse. The editors' shortcuts
are window-wide (see :mod:`app.gui.shortcuts`), so they work from there; the keys a
canvas handles in its own key events (the registry's ``key`` rows: hold B to size the
brush, hold Space to pan, and Correct's S, Enter, Esc, Delete) went to the tree's
type-ahead instead, so Space + drag painted where it should have panned. The relay sends
those keys to the canvas of the editor on screen; the tree keeps the keyboard, so the
arrows go on browsing.

A key held when the tree loses the keyboard ends its hold the way the canvas ends one
when it loses the keyboard itself (a ``FocusOut``), unless the canvas took the keyboard
(a press on it during a Space pan): the release then reaches the canvas.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QApplication, QWidget

from app.gui import shortcuts

__all__ = ["CanvasKeyRelay", "canvas_keys"]

#: ``target()``: the canvas of the editor on screen and its registry scope, or None.
Target = Callable[[], "tuple[QWidget, str] | None"]

_COMMAND = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)
_KEY_EVENTS = (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)


@cache
def canvas_keys(scope: str) -> frozenset[int]:
    """Qt key codes of the registry's ``key`` rows of ``scope`` (Enter includes Return)."""
    codes: set[int] = set()
    for spec in shortcuts.specs_for(scope):
        if spec.kind != shortcuts.KEY:
            continue
        for key in spec.keys:
            sequence = QKeySequence(key)
            if sequence.count() != 1:
                continue
            combo = sequence[0]
            if combo.key() != Qt.Key.Key_unknown and combo.keyboardModifiers() == (
                Qt.KeyboardModifier.NoModifier
            ):
                codes.add(int(combo.key()))
    if Qt.Key.Key_Enter in codes:
        codes.add(int(Qt.Key.Key_Return))
    return frozenset(codes)


class CanvasKeyRelay(QObject):
    """Sends ``source``'s canvas keys (see the module docstring) to ``target()``'s canvas."""

    def __init__(self, source: QWidget, target: Target) -> None:
        super().__init__(source)
        self._source = source
        self._target = target
        self._held: set[int] = set()  # keys pressed on the tree, relayed, not released yet
        self._held_by: QWidget | None = None  # the canvas they went to
        source.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self._source:
            kind = event.type()
            if kind in _KEY_EVENTS:
                return self._relay(event)
            if kind == QEvent.Type.FocusOut:
                self._release_holds()
        return False

    def _relay(self, event: QKeyEvent) -> bool:
        """Send ``event`` to the editor canvas when it is one of its keys; True = consumed."""
        found = self._target()
        if found is None:
            return False
        canvas, scope = found
        key = event.key()
        if not canvas.isVisible() or event.modifiers() & _COMMAND or key not in canvas_keys(scope):
            return False
        press = event.type() == QEvent.Type.KeyPress
        copy = QKeyEvent(
            event.type(),
            key,
            event.modifiers(),
            event.text(),
            event.isAutoRepeat(),
            event.count(),
        )
        QApplication.sendEvent(canvas, copy)
        if not event.isAutoRepeat():
            if press:
                self._held.add(key)
                self._held_by = canvas
            else:
                self._held.discard(key)
        event.accept()
        return True

    def _release_holds(self) -> None:
        """The tree lost the keyboard: the releases of held keys will not come through it."""
        canvas, held = self._held_by, bool(self._held)
        self._held.clear()
        self._held_by = None
        if not held or canvas is None or sip.isdeleted(canvas):
            return  # nothing held (or the window is being torn down)
        if QApplication.focusWidget() is canvas:
            return  # the canvas has the keyboard: it gets the releases
        QApplication.sendEvent(
            canvas, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)
        )
        if getattr(canvas, "space_pan", False):
            canvas.set_space_pan(False)

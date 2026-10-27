"""Hand an editor canvas its own keys while the library tree has the keyboard (W1).

The library tree keeps the keyboard while the arrow keys browse. The editors' shortcuts
are window-wide (see :mod:`app.gui.shortcuts`), so they work from there; the keys a
canvas handles in its own key events (the registry's ``key`` rows: hold B to size the
brush, hold Space to pan, and Correct's S, Enter, Esc, Delete) went to the tree's
type-ahead instead, so Space + drag painted where it should have panned. The relay sends
those keys to the canvas of the editor on screen; the tree keeps the keyboard, so the
arrows go on browsing. Other typing selects nothing there: the tree's type-ahead is off
while an editor page is on screen (``ui_panels.LibraryTree``, V5).

A key held when the tree loses the keyboard ends its hold the way the canvas ends one
when it loses the keyboard itself (a ``FocusOut``), unless the canvas took the keyboard
(a press on it during a Space pan): the release then reaches the canvas. The release of a
relayed key goes to its canvas whatever is held by then (Ctrl down before Space up); when
that canvas's page left the screen meanwhile (a stage switch leaves the keyboard on the
tree), it ends the holds with a ``FocusOut`` too.
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
        key = event.key()
        press = event.type() == QEvent.Type.KeyPress
        if not press and key in self._held and not event.isAutoRepeat():
            return self._relay_held_release(event)
        found = self._target()
        if found is None:
            return False
        canvas, scope = found
        if not canvas.isVisible() or event.modifiers() & _COMMAND or key not in canvas_keys(scope):
            return False
        self._send(canvas, event)
        if press and not event.isAutoRepeat():
            self._held.add(key)
            self._held_by = canvas
        return True

    def _relay_held_release(self, event: QKeyEvent) -> bool:
        """The release of a key whose press went to a canvas: to that canvas, with the
        modifiers held now; while its page is off screen it ends the canvas's holds instead
        (as a lost keyboard does: no B tap). True = consumed."""
        self._held.discard(event.key())
        canvas = self._held_by
        if canvas is None or sip.isdeleted(canvas):
            return False
        if canvas.isVisible():
            self._send(canvas, event)
            return True
        self._held.clear()
        self._held_by = None
        self._end_holds(canvas)
        event.accept()
        return True

    @staticmethod
    def _send(canvas: QWidget, event: QKeyEvent) -> None:
        """A copy of ``event`` to ``canvas``; ``event`` is accepted (the tree never sees it)."""
        copy = QKeyEvent(
            event.type(),
            event.key(),
            event.modifiers(),
            event.text(),
            event.isAutoRepeat(),
            event.count(),
        )
        QApplication.sendEvent(canvas, copy)
        event.accept()

    def _release_holds(self) -> None:
        """The tree lost the keyboard: the releases of held keys will not come through it."""
        canvas, held = self._held_by, bool(self._held)
        self._held.clear()
        self._held_by = None
        if not held or canvas is None or sip.isdeleted(canvas):
            return  # nothing held (or the window is being torn down)
        if QApplication.focusWidget() is not canvas:  # else the canvas gets the releases
            self._end_holds(canvas)

    @staticmethod
    def _end_holds(canvas: QWidget) -> None:
        """End ``canvas``'s holds (B, Space) the way it ends them when it loses the keyboard."""
        QApplication.sendEvent(
            canvas, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)
        )
        if getattr(canvas, "space_pan", False):
            canvas.set_space_pan(False)

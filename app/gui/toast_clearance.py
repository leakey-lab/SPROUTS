"""Keep the toast stack clear of a window's bottom chrome (KNOWN_ISSUES #5).

Toasts stack bottom-right (:class:`~app.gui.widgets.toasts.ToastManager`). They used to
keep clear only of the status line and a Correct dock, so on the Library page they
covered the metrics bar, and in an editor they could cover the zoom cluster.
:class:`ToastClearance` lifts the stack above every piece of bottom chrome that is on
screen under the toast column, and repositions the stack whenever that chrome moves,
resizes, shows or hides (a page switch shows the metrics bar; an editor places its
overlays a moment after it was resized), not only when the window resizes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer
from PyQt6.QtWidgets import QWidget

from .widgets.toasts import ToastManager

__all__ = ["ChromeProvider", "FLOATING_GAP", "ToastClearance"]

#: Gap kept between the stack and a floating overlay (an editor's dock or zoom cluster).
FLOATING_GAP = 8

#: ``() -> [(widget or None, gap px), ...]``: the chrome to keep clear of, right now.
ChromeProvider = Callable[[], Iterable[tuple["QWidget | None", int]]]


class ToastClearance(QObject):
    """The bottom offset of ``toasts`` in ``window``, from the chrome ``chrome()`` names.

    ``offset()`` is what :meth:`ToastManager.set_bottom_offset` reads on every
    reposition; :meth:`watch` makes the stack follow the given widgets.
    """

    _EVENTS = frozenset({QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide})

    def __init__(self, window: QWidget, toasts: ToastManager, chrome: ChromeProvider) -> None:
        super().__init__(window)
        self._window = window
        self._toasts = toasts
        self._chrome = chrome
        # One reposition per event-loop turn, after the layouts placed the chrome.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(toasts.reposition)
        toasts.set_bottom_offset(self.offset)

    def offset(self) -> int:
        """Pixels the stack keeps above the window's bottom edge: above every visible
        piece of chrome that reaches into the toast column (plus its gap)."""
        window = self._window
        column_left = self._toasts.column_left()
        offset = 0
        for widget, gap in self._chrome():
            if widget is None or not widget.isVisible():
                continue
            top_left = widget.mapTo(window, QPoint(0, 0))
            if top_left.x() + widget.width() <= column_left:
                continue  # entirely left of the toasts
            offset = max(offset, window.height() - top_left.y() + gap)
        return offset

    def watch(self, *widgets: QWidget | None) -> None:
        """Reposition the stack after any of ``widgets`` moved, resized, showed or hid."""
        for widget in widgets:
            if widget is not None:
                widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() in self._EVENTS:
            self._timer.start()
        return False

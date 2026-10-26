"""Keep the toast stack clear of a window's bottom and side chrome (KNOWN_ISSUES #5).

Toasts stack bottom-right (:class:`~app.gui.widgets.toasts.ToastManager`). They used to
keep clear only of the status line and a Correct dock, so on the Library page they
covered the metrics bar, and in an editor they could cover the zoom cluster.
:class:`ToastClearance` lifts the stack above every piece of bottom chrome that is on
screen under the toast column, and repositions the stack whenever that chrome moves,
resizes, shows or hides (a page switch shows the metrics bar; an editor places its
overlays a moment after it was resized), not only when the window resizes.

Lifting the stack above an editor's dock and zoom cluster moves it up the right edge,
where the editor's side panel sits (Correct's Layers and Classes). The optional
``side`` chrome keeps the stack in its corner while it fits below such a panel and moves
it to the panel's left when it does not (more toasts, longer ones, a shorter window).

A screen can name chrome of its own with :meth:`ToastClearance.keep_clear_of`: the Welcome
screen's About button, which the toast column reaches on a 1024 x 640 window. Such chrome
counts while it is visible, so it stops counting when its screen is left.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterable

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QTimer
from PyQt6.QtWidgets import QWidget

from .widgets.toasts import ToastManager

__all__ = ["ChromeProvider", "FLOATING_GAP", "ToastClearance"]

#: Gap kept between the stack and a floating overlay (an editor's dock or zoom cluster).
FLOATING_GAP = 8

#: ``() -> [(widget or None, gap px), ...]``: the chrome to keep clear of, right now.
ChromeProvider = Callable[[], Iterable[tuple["QWidget | None", int]]]


class ToastClearance(QObject):
    """The bottom (and right) offset of ``toasts`` in ``window``, from the chrome
    ``chrome()`` (bottom) and ``side()`` (right-hand panels) name.

    ``offset()`` / ``right_offset()`` are what :meth:`ToastManager.set_bottom_offset` /
    :meth:`ToastManager.set_right_offset` read on every reposition; :meth:`watch` makes
    the stack follow the given widgets.
    """

    _EVENTS = frozenset({QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide})

    def __init__(
        self,
        window: QWidget,
        toasts: ToastManager,
        chrome: ChromeProvider,
        *,
        side: ChromeProvider | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._toasts = toasts
        self._chrome = chrome
        self._side = side
        # Chrome named by keep_clear_of (weak: a deleted screen takes its chrome with it).
        self._kept: list[tuple[weakref.ref, int]] = []
        # One reposition per event-loop turn, after the layouts placed the chrome.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(toasts.reposition)
        toasts.set_bottom_offset(self.offset)
        if side is not None:
            toasts.set_right_offset(self.right_offset)

    def offset(self) -> int:
        """Pixels the stack keeps above the window's bottom edge: above every visible
        piece of chrome that reaches into the toast column (plus its gap)."""
        return self._bottom_offset(self.right_offset())

    def right_offset(self) -> int:
        """Pixels the stack keeps left of its corner: 0 while it clears every visible side
        panel in its column (it fits below or above it), else enough to pass its left edge."""
        if self._side is None:
            return 0
        window, toasts = self._window, self._toasts
        margin = toasts.margin()
        column = QRect(0, 0, 0, 0)
        column.setLeft(toasts.column_left(0))
        column.setRight(window.width() - margin - 1)
        column.setBottom(window.height() - margin - self._bottom_offset(0) - 1)
        column.setTop(column.bottom() + 1 - toasts.stack_height())
        right = 0
        for widget, gap in self._side():
            if widget is None or not widget.isVisible():
                continue
            box = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
            if column.intersects(box.adjusted(-gap, -gap, gap, gap)):
                right = max(right, window.width() - margin - box.left() + gap)
        return right

    def watch(self, *widgets: QWidget | None) -> None:
        """Reposition the stack after any of ``widgets`` moved, resized, showed or hid."""
        for widget in widgets:
            if widget is not None:
                widget.installEventFilter(self)

    def keep_clear_of(self, widget: QWidget, gap: int = FLOATING_GAP) -> None:
        """Count ``widget`` as bottom chrome too, while it is visible, and follow it.
        Naming the same widget again changes nothing."""
        if any(ref() is widget for ref, _gap in self._kept):
            return
        self._kept.append((weakref.ref(widget), int(gap)))
        self.watch(widget)
        self._timer.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() in self._EVENTS:
            self._timer.start()
        return False

    def _bottom_offset(self, right_offset: int) -> int:
        """The bottom offset for the column ``right_offset`` px left of the corner."""
        window = self._window
        column_left = self._toasts.column_left(right_offset)
        column_right = window.width() - self._toasts.margin() - right_offset
        offset = 0
        for widget, gap in [*self._chrome(), *self._kept_chrome()]:
            if widget is None or not widget.isVisible():
                continue
            top_left = widget.mapTo(window, QPoint(0, 0))
            if top_left.x() + widget.width() <= column_left or top_left.x() >= column_right:
                continue  # entirely left or right of the toasts
            offset = max(offset, window.height() - top_left.y() + gap)
        return offset

    def _kept_chrome(self) -> list[tuple[QWidget, int]]:
        """``(widget, gap)`` of the chrome named by :meth:`keep_clear_of` that still exists."""
        kept = [(ref(), gap) for ref, gap in self._kept]
        return [
            (widget, gap)
            for widget, gap in kept
            if widget is not None and not sip.isdeleted(widget)
        ]

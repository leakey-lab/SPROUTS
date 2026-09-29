"""Toasts: transient feedback stacked bottom-right of a host window (audit UXS-07).

- At most ``max_visible`` toasts are shown; a new one pushes out the oldest, but a
  ``danger`` toast (e.g. "Could not save mask ...") outlives lower-severity ones.
- ``show(..., key=...)`` updates a live toast with the same key in place (the GT autosave
  on every PgDn used to stack a new toast each time).
- Text wraps at ``min(460, host width - 2 * margin)``; an unbreakable token such as a path
  is elided in the middle, and the full message is in the tooltip and accessible name.
- Lifetimes by kind: success / info expire (3.2 s by default), warn lasts at least 6 s,
  danger stays until closed. Warn and danger toasts have a close button; a click on any
  toast dismisses it.
- ``set_bottom_offset`` keeps the stack above bottom chrome (status bars, a dock).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .controls import IconButton
from .icons import load_pixmap
from .text_fit import elide_long_words, wrap_label

DEFAULT_TIMEOUT_MS = 3200
WARN_MIN_TIMEOUT_MS = 6000
MAX_TOAST_WIDTH = 460
_KIND_COLOR = {
    "success": tokens.OK,
    "info": tokens.INFO,
    "warn": tokens.WARN,
    "danger": tokens.DANGER,
}
_CLOSABLE = ("warn", "danger")


def toast_timeout(kind: str, requested: int | None) -> int | None:
    """Lifetime of a ``kind`` toast in ms; ``None`` = stays until closed."""
    if kind == "danger":
        return None
    timeout = DEFAULT_TIMEOUT_MS if requested is None else int(requested)
    return max(timeout, WARN_MIN_TIMEOUT_MS) if kind == "warn" else timeout


class Toast(QWidget):
    """One toast: kind colour bar, icon, wrapped text, close button for warn / danger.

    Fades via a QGraphicsOpacityEffect (windowOpacity is unreliable on some Qt platforms).
    It carries no drop shadow: Qt cannot nest graphics effects, and a shadow on the frame
    inside the faded widget made every partial repaint log seven QPainter warnings. The
    strong border and the kind colour bar provide the elevation instead.
    ``dismissed`` fires when it starts fading out, ``closed`` when it is gone.
    """

    closed = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(
        self,
        parent,
        message: str,
        kind: str = "success",
        timeout: int | None = DEFAULT_TIMEOUT_MS,
        *,
        margin: int = 16,
        max_width: int = MAX_TOAST_WIDTH,
    ):
        super().__init__(parent)
        self._margin = margin
        self._max_width = max_width
        self._kind = kind
        self._message = ""
        self._timeout = toast_timeout(kind, timeout)
        self._dismissing = False
        color = _KIND_COLOR.get(kind, tokens.OK)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        frame = QFrame(self)
        frame.setObjectName("toastFrame")
        frame.setStyleSheet(f"""
            QFrame#toastFrame {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-left: 3px solid {color};
                border-radius: 10px;
            }}
            QLabel {{ color: {tokens.TEXT}; background: transparent; font-size: 12.5px; }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(13, 10, 10, 10)
        row.setSpacing(9)
        self._icon = QLabel()
        self._icon.setPixmap(load_pixmap("check" if kind == "success" else "info", color, 15))
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._label = QLabel()
        self._label.setObjectName("toastText")
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._label, 1)
        self.close_button: IconButton | None = None
        if kind in _CLOSABLE:
            self.close_button = IconButton("xmark", "Dismiss", size=22, icon_px=13)
            self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.close_button.clicked.connect(self.dismiss)
            row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        self._frame, self._row = frame, row

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.set_message(message)

    # ------------------------------------------------------------------ API
    def kind(self) -> str:
        return self._kind

    def message(self) -> str:
        return self._message

    def set_message(self, message: str) -> None:
        """Show ``message`` (wrapped, long words elided); full text in tooltip and name."""
        self._message = message
        self.setToolTip(message)
        self.setAccessibleName(message)
        self._layout_text()

    def set_max_width(self, width: int) -> None:
        self._max_width = max(120, int(width))
        self._layout_text()

    def restart(self, timeout: int | None = None) -> None:
        """Restart the lifetime (``timeout`` as in the constructor; ``None`` keeps it)."""
        if timeout is not None:
            self._timeout = toast_timeout(self._kind, timeout)
        self._timer.stop()
        if self._timeout is not None:
            self._timer.start(self._timeout)

    def show_toast(self):
        self.reposition()
        self.raise_()
        self.show()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self.restart()

    def reposition(self):
        """Bottom-right of the parent (a ToastManager stacks several instead)."""
        if self.parent() is None:
            return
        self._adjust_size()
        pw, ph = self.parent().width(), self.parent().height()
        self.move(pw - self.width() - self._margin, ph - self.height() - self._margin)

    def dismiss(self):
        if self._dismissing:
            return
        self._dismissing = True
        self._timer.stop()
        self.dismissed.emit()
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish)
        self._fade.start()

    # ------------------------------------------------------------- internals
    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mousePressEvent(event)

    def _text_room(self) -> int:
        """Width left for the text once the frame, margins, icon and close button fit."""
        self._frame.ensurePolished()  # its QSS border is part of the contents margins
        frame, margins = self._frame.contentsMargins(), self._row.contentsMargins()
        chrome = frame.left() + frame.right() + margins.left() + margins.right()
        for widget in (self._icon, self.close_button):
            if widget is not None:
                chrome += widget.sizeHint().width() + self._row.spacing()
        return max(60, self._max_width - chrome)

    def _layout_text(self) -> None:
        """Fit the text into ``max_width``. The chrome estimate can miss a few px of
        style-dependent padding, so the measured overshoot is taken off in a second pass."""
        room = self._text_room()
        self._fit_text(room)
        overshoot = self.width() - self._max_width
        if overshoot > 0:
            self._fit_text(max(60, room - overshoot))

    def _fit_text(self, room: int) -> None:
        self._label.ensurePolished()
        metrics = self._label.fontMetrics()
        self._label.setText(
            elide_long_words(
                self._message,
                lambda word: metrics.horizontalAdvance(word) <= room,
                lambda word: metrics.elidedText(word, Qt.TextElideMode.ElideMiddle, room),
            )
        )
        wrap_label(self._label, room)
        self._adjust_size()

    def _adjust_size(self) -> None:
        """Resize to the current text (the frame's layout is refreshed first)."""
        self._row.activate()
        self.layout().activate()
        self.adjustSize()

    def _finish(self):
        self.closed.emit()
        self.deleteLater()


class ToastManager:
    """Owns the stack of toasts anchored bottom-right of a host widget.

    Wire it on ``MainWindow`` and route transient feedback through :meth:`show`; hard
    errors that need a decision keep using ``QMessageBox``.
    """

    def __init__(self, host: QWidget, *, gap: int = 10, margin: int = 16, max_visible: int = 3):
        self._host = host
        self._gap = gap
        self._margin = margin
        self._max_visible = max(1, max_visible)
        self._toasts: list[Toast] = []
        self._keys: dict[str, Toast] = {}
        self._bottom_offset: Callable[[], int] = lambda: 0

    def show(
        self,
        message: str,
        kind: str = "success",
        timeout: int | None = None,
        *,
        key: str | None = None,
    ) -> Toast:
        """Show ``message``; ``kind`` in success / info / warn / danger (see module doc)."""
        live = self._keys.get(key) if key is not None else None
        if live is not None and live in self._toasts and live.kind() == kind:
            live.set_message(message)
            live.restart(timeout)
            self._toasts.remove(live)
            self._toasts.append(live)  # newest again: bottom of the stack
            self.reposition()
            return live
        if live is not None:
            live.dismiss()

        toast = Toast(
            self._host,
            message,
            kind=kind,
            timeout=timeout,
            margin=self._margin,
            max_width=self._max_width(),
        )
        toast.dismissed.connect(lambda t=toast: self._remove(t))
        self._toasts.append(toast)
        if key is not None:
            self._keys[key] = toast
        self._evict()
        toast.show_toast()
        self.reposition()
        return toast

    def toasts(self) -> list[Toast]:
        """The live toasts, oldest first."""
        return list(self._toasts)

    def set_bottom_offset(self, offset: int | Callable[[], int]) -> None:
        """Keep the stack ``offset`` px (or ``offset()`` px, read on every reposition)
        above the host's bottom edge, e.g. clear of status bars or an editor's dock."""
        self._bottom_offset = offset if callable(offset) else (lambda: int(offset))
        self.reposition()

    def reposition(self):
        width, height = self._host.width(), self._host.height()
        y = height - self._margin - max(0, int(self._bottom_offset()))
        for toast in reversed(self._toasts):
            toast.set_max_width(self._max_width())
            y -= toast.height()
            toast.move(width - toast.width() - self._margin, y)
            y -= self._gap

    # ------------------------------------------------------------- internals
    def _max_width(self) -> int:
        return min(MAX_TOAST_WIDTH, self._host.width() - 2 * self._margin)

    def _evict(self) -> None:
        """Keep ``max_visible`` toasts. The newest always stays; of the older ones the
        oldest non-danger toast goes first, else the oldest."""
        while len(self._toasts) > self._max_visible:
            older = self._toasts[:-1]
            victim = next((t for t in older if t.kind() != "danger"), older[0])
            victim.dismiss()

    def _remove(self, toast: Toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        for key, keyed in list(self._keys.items()):
            if keyed is toast:
                del self._keys[key]
        self.reposition()

"""
Task progress widget — a single-row progress indicator for the statusline.

Layout: ``<operation> — <progress> · ETA <n> min   [=====     ]   [Cancel]`` in
one 22 px row, so it can live inline in the shell's 28 px statusline (see
``shell_chrome.StatusLine.adopt``) instead of inflating a second status band.

This widget knows nothing about handlers. It is driven through its small public
API (``app.gui.ui_feedback.QtUiFeedback`` adapts ``UiFeedback`` onto it):

    start(operation, cancelable=False, on_cancel=None)
    set_progress(value: int)             # percent, 0..100
    set_counts(done: int, total: int)    # "1,234 / 46,178"
    set_operation(operation: str)
    finish(message: str | None = None)
    fail(message: str)

It is hidden by default; ``start`` shows it, ``finish``/``fail`` hide it after a
short confirmation. ``state_changed`` reports "working" / "idle" / "error" for
the statusline's state dot. The remaining time comes from :class:`EtaEstimator`
(pure Python, injectable clock).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QWidget

from app.gui.feedback import progress_percent
from app.gui.widgets import tokens

Clock = Callable[[], float]

ROW_HEIGHT = 22
BAR_WIDTH = 140

_LABEL_QSS = f"color: {tokens.TEXT}; font-size: 11.5px; font-weight: 600; background: transparent;"

_PROGRESS_QSS = f"""
QProgressBar {{
    background-color: {tokens.BG_3};
    border: none;
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background-color: {tokens.ACCENT};
    border-radius: 3px;
}}
"""

_CANCEL_QSS = f"""
QToolButton {{
    background-color: {tokens.BG_3};
    color: {tokens.TEXT};
    border: 1px solid {tokens.BORDER_STRONG};
    border-radius: 6px;
    padding: 0px 8px;
    font-size: 11px;
}}
QToolButton:hover {{
    background-color: {tokens.BORDER};
    border: 1px solid {tokens.DANGER};
    color: {tokens.DANGER};
}}
QToolButton:disabled {{
    color: {tokens.TEXT_FAINT};
}}
"""


# --------------------------------------------------------------------------- #
#  Remaining-time estimate (Qt-free)
# --------------------------------------------------------------------------- #
class EtaEstimator:
    """Remaining seconds from progress fractions, smoothed by an EMA of the rate.

    ``update(fraction)`` returns the estimate, or ``None`` until at least
    ``min_elapsed`` seconds and ``min_fraction`` of the work have passed (early
    rates are noise), and ``None`` once the work is complete. Progress that goes
    backwards (a new phase) restarts the estimate.
    """

    def __init__(
        self,
        clock: Clock = time.monotonic,
        *,
        alpha: float = 0.3,
        min_elapsed: float = 2.0,
        min_fraction: float = 0.02,
    ) -> None:
        self._clock = clock
        self._alpha = alpha
        self._min_elapsed = min_elapsed
        self._min_fraction = min_fraction
        self.reset()

    def reset(self) -> None:
        now = self._clock()
        self._start = now
        self._last = (now, 0.0)
        self._rate: float | None = None

    def update(self, fraction: float) -> float | None:
        now = self._clock()
        fraction = min(1.0, max(0.0, float(fraction)))
        last_t, last_f = self._last
        if fraction < last_f:
            self.reset()
            self._last = (self._start, fraction)
            return None
        dt, df = now - last_t, fraction - last_f
        if dt > 0 and df > 0:
            rate = df / dt
            self._rate = (
                rate
                if self._rate is None
                else (self._alpha * rate + (1 - self._alpha) * self._rate)
            )
            self._last = (now, fraction)
        if (
            self._rate is None
            or now - self._start < self._min_elapsed
            or fraction < self._min_fraction
            or fraction >= 1.0
        ):
            return None
        return (1.0 - fraction) / self._rate


def format_eta(seconds: float) -> str:
    """``"ETA <1 min"``, ``"ETA 12 min"`` or ``"ETA 2 h 5 min"``."""
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "ETA <1 min"
    if minutes < 60:
        return f"ETA {minutes} min"
    return f"ETA {minutes // 60} h {minutes % 60} min"


# --------------------------------------------------------------------------- #
#  Widget
# --------------------------------------------------------------------------- #
class TaskProgressWidget(QWidget):
    """Single-row progress: ``<operation> — <progress>[ · ETA]``, a bar, optional Cancel.

    Signals:
        canceled: the user clicked Cancel (the ``on_cancel`` callback runs too).
        state_changed(str): "working" on start, "idle" on finish, "error" on fail.
    """

    canceled = pyqtSignal()
    state_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, clock: Clock = time.monotonic) -> None:
        super().__init__(parent)
        self._operation = ""
        self._on_cancel: Callable[[], None] | None = None
        self._eta = EtaEstimator(clock)
        # One timer, so a pending hide from the previous task never hides a new one.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_and_reset)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.label = QLabel("")
        self.label.setStyleSheet(_LABEL_QSS)
        row.addWidget(self.label)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedSize(BAR_WIDTH, 6)
        self.bar.setStyleSheet(_PROGRESS_QSS)
        row.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)

        self.cancel_button = QToolButton()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setToolTip("Cancel the current operation")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setFixedHeight(ROW_HEIGHT - 2)
        self.cancel_button.setStyleSheet(_CANCEL_QSS)
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)

        self.setFixedHeight(ROW_HEIGHT)
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(
        self,
        operation: str,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Begin an operation at 0%; show Cancel when ``cancelable`` or ``on_cancel``."""
        self._hide_timer.stop()
        self._operation = operation or ""
        self._on_cancel = on_cancel
        self._eta.reset()
        self.bar.setValue(0)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(bool(cancelable or on_cancel is not None))
        self._render("0%")
        self.show()
        self.state_changed.emit("working")

    def set_progress(self, value: int) -> None:
        """Update the percentage (clamped to 0..100) and refresh the label."""
        try:
            pct = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return
        self._advance(pct, f"{pct}%", pct / 100.0)

    def set_counts(self, done: int, total: int) -> None:
        """Show ``done / total`` items (thousands separators) and the matching percent."""
        try:
            done, total = int(done), int(total)
        except (TypeError, ValueError):
            return
        pct = progress_percent(done, total)
        fraction = done / total if total > 0 else 1.0
        self._advance(pct, f"{done:,} / {total:,}", fraction)

    def set_operation(self, operation: str) -> None:
        """Change the operation name shown in the label (progress unchanged)."""
        self._operation = operation or ""
        self._render(f"{self.bar.value()}%")

    def finish(self, message: str | None = None) -> None:
        """Complete the operation: snap to 100%, then hide.

        If ``message`` is given it is briefly shown in the label before hiding so
        a glance confirms success; otherwise the widget hides immediately.
        """
        self.bar.setValue(100)
        self.cancel_button.hide()
        self.state_changed.emit("idle")
        if message:
            self.label.setText(message)
            self._hide_timer.start(1500)
        else:
            self._hide_and_reset()

    def fail(self, message: str) -> None:
        """Mark the operation as failed: show ``message`` briefly, then hide."""
        self.cancel_button.hide()
        self.label.setText(message or "Failed")
        self.state_changed.emit("error")
        self._hide_timer.start(2500)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _advance(self, pct: int, progress_text: str, fraction: float) -> None:
        if self.isHidden():
            self.show()
        self.bar.setValue(pct)
        remaining = self._eta.update(fraction)
        suffix = f" · {format_eta(remaining)}" if remaining is not None else ""
        self._render(progress_text + suffix)

    def _render(self, progress_text: str) -> None:
        if self._operation:
            self.label.setText(f"{self._operation} — {progress_text}")
        else:
            self.label.setText(progress_text)

    def _cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.label.setText(f"Cancelling {self._operation or 'task'}…")
        if self._on_cancel is not None:
            self._on_cancel()
        self.canceled.emit()

    def _hide_and_reset(self) -> None:
        self._hide_timer.stop()
        self.hide()
        self.bar.setValue(0)
        self.label.setText("")
        self._operation = ""
        self._on_cancel = None

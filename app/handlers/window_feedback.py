"""``UiFeedback`` over the window's existing chrome (task progress, status bar, toasts).

Handlers talk to :class:`app.gui.feedback.UiFeedback` only. Until the main
window exposes its own ``feedback`` object, :class:`WindowFeedback` adapts the
widgets it already has; every part is optional, so a partial window (tests,
the UI harness) works too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.gui.feedback import NoticeKind, progress_percent


class WindowFeedback:
    """Drive ``window.task_progress`` / ``status_bar`` / ``notify`` as a ``UiFeedback``."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._label = ""
        self._total: int | None = None

    def _part(self, name: str) -> Any:
        return getattr(self._window, name, None)

    def task_started(
        self,
        label: str,
        *,
        total: int | None = None,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._label, self._total = label, total
        progress = self._part("task_progress")
        if progress is not None:
            progress.start(label, cancelable=cancelable, on_cancel=on_cancel)
        self.status(f"{label}…", 0)

    def task_progress(self, done: int, total: int | None = None) -> None:
        total = total if total is not None else self._total
        progress = self._part("task_progress")
        if progress is not None:
            if total is None:
                progress.set_progress(done)
            else:
                progress.set_counts(done, total)
        self.status(f"{self._label}… {progress_percent(done, total)}%", 0)

    def task_finished(self, message: str | None = None) -> None:
        progress = self._part("task_progress")
        if progress is not None:
            progress.finish(message)
        self.status(message or "Done")

    def task_failed(self, message: str) -> None:
        progress = self._part("task_progress")
        if progress is not None:
            progress.fail(message)
        self.status(message)

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        bar = self._part("status_bar")
        if bar is not None:
            bar.showMessage(message, timeout_ms)

    def notify(self, message: str, kind: NoticeKind = "success") -> None:
        notify = self._part("notify")
        if callable(notify):
            notify(message, kind)
        else:
            self.status(message)

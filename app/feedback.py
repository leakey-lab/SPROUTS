"""User-feedback port for handlers and workers (Qt-free, layer-neutral).

Handlers report long-running work through ``UiFeedback`` instead of reaching into
``main_window`` widgets (task progress bar, status bar, toasts). The Qt
implementation lives with the shell chrome and is exposed as
``main_window.feedback``; this module holds only the protocol and two doubles:

* ``NullFeedback`` - discards everything (a safe default when no window exists);
* ``RecordingFeedback`` - records every call and tracks the task state (for tests).

Progress semantics: ``task_started(label, total=N)`` makes later
``task_progress(done)`` calls count items out of ``N``; an explicit
``task_progress(done, total)`` overrides that total; with no total anywhere,
``done`` is already a percent (0..100). Implementations convert with
``progress_percent`` (re-exported from ``app.inference.batch_pipeline``).

Layering: this port sits below both ``app.gui`` and ``app.handlers`` so handlers
never depend on the GUI package.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, NamedTuple, Protocol, runtime_checkable

from app.inference.batch_pipeline import progress_percent

__all__ = [
    "FeedbackCall",
    "NoticeKind",
    "NullFeedback",
    "RecordingFeedback",
    "UiFeedback",
    "progress_percent",
]

NoticeKind = Literal["success", "info", "warn", "danger"]


@runtime_checkable
class UiFeedback(Protocol):
    """What a handler may tell the user about its work."""

    def task_started(
        self,
        label: str,
        *,
        total: int | None = None,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Show a running task named ``label`` (with Cancel wired to ``on_cancel``)."""

    def task_progress(self, done: int, total: int | None = None) -> None:
        """Advance the running task (see the module docstring for the units)."""

    def task_finished(self, message: str | None = None) -> None:
        """Complete the running task, optionally confirming with ``message``."""

    def task_failed(self, message: str) -> None:
        """End the running task with a user-facing failure ``message``."""

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        """Show a transient status-line message."""

    def notify(self, message: str, kind: NoticeKind = "success") -> None:
        """Show a toast (``kind``: success / info / warn / danger)."""


class NullFeedback:
    """``UiFeedback`` that discards every call."""

    def task_started(
        self,
        label: str,
        *,
        total: int | None = None,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        pass

    def task_progress(self, done: int, total: int | None = None) -> None:
        pass

    def task_finished(self, message: str | None = None) -> None:
        pass

    def task_failed(self, message: str) -> None:
        pass

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        pass

    def notify(self, message: str, kind: NoticeKind = "success") -> None:
        pass


class FeedbackCall(NamedTuple):
    """One recorded ``UiFeedback`` call."""

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class RecordingFeedback:
    """``UiFeedback`` test double: records calls and tracks the task state.

    State: ``busy`` (between task_started and task_finished/failed), ``label``,
    ``percent`` and ``last_message`` (finish/fail/status/notify text).
    ``cancel()`` simulates the user pressing Cancel on the running task.
    """

    def __init__(self) -> None:
        self.calls: list[FeedbackCall] = []
        self.busy = False
        self.label = ""
        self.percent = 0
        self.last_message: str | None = None
        self._total: int | None = None
        self._on_cancel: Callable[[], None] | None = None

    def names(self) -> list[str]:
        """Names of the recorded calls, in order."""
        return [call.name for call in self.calls]

    def cancel(self) -> None:
        """Invoke the ``on_cancel`` of the latest ``task_started`` (if any)."""
        if self._on_cancel is not None:
            self._on_cancel()

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(FeedbackCall(name, args, kwargs))

    def task_started(
        self,
        label: str,
        *,
        total: int | None = None,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._record(
            "task_started",
            label,
            total=total,
            cancelable=cancelable,
            on_cancel=on_cancel,
        )
        self.busy, self.label, self.percent = True, label, 0
        self._total, self._on_cancel = total, on_cancel

    def task_progress(self, done: int, total: int | None = None) -> None:
        self._record("task_progress", done, total)
        self.percent = progress_percent(done, total if total is not None else self._total)

    def task_finished(self, message: str | None = None) -> None:
        self._record("task_finished", message)
        self.busy, self.percent, self.last_message = False, 100, message

    def task_failed(self, message: str) -> None:
        self._record("task_failed", message)
        self.busy, self.last_message = False, message

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        self._record("status", message, timeout_ms)
        self.last_message = message

    def notify(self, message: str, kind: NoticeKind = "success") -> None:
        self._record("notify", message, kind)
        self.last_message = message

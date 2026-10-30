"""Qt implementation of the ``UiFeedback`` port (``app.feedback``).

Handlers report long-running work through ``main_window.feedback`` instead of
reaching into window widgets. :class:`QtUiFeedback` maps the protocol onto the
shell's surfaces:

* task_* -> the statusline's :class:`~app.gui.task_progress.TaskProgressWidget`
  (item counts with ETA when a total is known, else a percent; Cancel wired to
  ``on_cancel``);
* status -> ``QStatusBar.showMessage`` (mirrored in the statusline);
* notify / task_failed -> toasts through an injected ``notifier(message, kind)``.

Every collaborator is injected, so the class is testable without a MainWindow.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QStatusBar

from app.feedback import NoticeKind, progress_percent
from app.gui.task_progress import TaskProgressWidget

Notifier = Callable[[str, str], None]


class QtUiFeedback:
    """``UiFeedback`` over a TaskProgressWidget, a QStatusBar and a toast notifier.

    ``on_task_end`` (optional) runs after every finished or failed task; the
    shell uses it to refresh the runtime chip once inference has resolved its
    device.
    """

    def __init__(
        self,
        progress: TaskProgressWidget,
        *,
        status_bar: QStatusBar | None = None,
        notifier: Notifier | None = None,
        on_task_end: Callable[[], None] | None = None,
    ) -> None:
        self._progress = progress
        self._status_bar = status_bar
        self._notifier = notifier
        self._on_task_end = on_task_end
        self._total: int | None = None

    def task_started(
        self,
        label: str,
        *,
        total: int | None = None,
        cancelable: bool = False,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._total = total
        self._progress.start(label, cancelable=cancelable, on_cancel=on_cancel)

    def task_progress(self, done: int, total: int | None = None) -> None:
        total = total if total is not None else self._total
        if total is None:
            self._progress.set_progress(progress_percent(done, None))
        else:
            self._progress.set_counts(done, total)

    def task_finished(self, message: str | None = None) -> None:
        self._total = None
        self._progress.finish(message)
        self._task_ended()

    def task_failed(self, message: str) -> None:
        self._total = None
        self._progress.fail(message)
        self.notify(message, "danger")
        self._task_ended()

    def status(self, message: str, timeout_ms: int = 5000) -> None:
        if self._status_bar is not None:
            self._status_bar.showMessage(message, timeout_ms)

    def notify(self, message: str, kind: NoticeKind = "success") -> None:
        if self._notifier is not None:
            self._notifier(message, kind)

    def _task_ended(self) -> None:
        if self._on_task_end is not None:
            self._on_task_end()

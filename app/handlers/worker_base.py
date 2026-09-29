"""Uniform error boundary for background ``QThread`` workers.

PyQt6 calls ``qFatal`` (the process aborts) when an exception escapes a
reimplemented ``QThread.run`` and no ``sys.excepthook`` is installed; even with a
hook, a worker that dies silently leaves the task UI spinning forever. Workers
therefore subclass ``GuardedThread`` and implement ``work()``:

* ``work()`` returns normally -> the job completed; the subclass emits its own
  result signal (a custom ``finished = pyqtSignal(...)`` may shadow QThread's);
* ``work()`` raises ``JobCancelled`` (``check_cancelled()`` does that once
  ``cancel()`` was requested) -> ``cancelled`` is emitted;
* ``work()`` raises anything else -> the traceback is logged and ``failed`` is
  emitted with a user-facing one-line message (``describe_error``).

Exactly one of those outcomes happens per run.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised inside ``GuardedThread.work()`` to stop a cancelled job early."""


def describe_error(exc: BaseException) -> str:
    """User-facing one-line description of a worker exception.

    ``app.io_utils.FileLockedError`` already carries a user-facing message and is
    returned verbatim; anything else becomes ``"<ExceptionType>: <message>"``.
    """
    try:  # lazy: io_utils is optional here and must never break the error path
        from app.io_utils import FileLockedError
    except Exception:
        FileLockedError = None  # noqa: N806
    name = type(exc).__name__
    try:
        text = str(exc)
    except Exception:  # a broken __str__ must not break the error path
        return name
    if FileLockedError is not None and isinstance(exc, FileLockedError):
        return text
    return f"{name}: {text}" if text else name


class GuardedThread(QThread):
    """``QThread`` whose ``run()`` never lets an exception escape.

    Signals:
        failed(str): ``work()`` raised; the argument is a user-facing message.
        cancelled(): ``work()`` stopped early after ``cancel()``.
    """

    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def work(self) -> None:
        """The job body, run on the worker thread. Subclasses must implement it."""
        raise NotImplementedError(f"{type(self).__name__} must implement work()")

    def run(self) -> None:
        """Run ``work()`` inside the error boundary (do not override)."""
        try:
            self.work()
        except JobCancelled:
            logger.info("%s cancelled", type(self).__name__)
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("%s failed", type(self).__name__)
            self.failed.emit(describe_error(exc))

    def cancel(self) -> None:
        """Ask the job to stop at its next ``check_cancelled()`` (thread-safe)."""
        self.requestInterruption()

    def is_cancelled(self) -> bool:
        """True once ``cancel()`` was requested."""
        return self.isInterruptionRequested()

    def check_cancelled(self) -> None:
        """Raise ``JobCancelled`` if ``cancel()`` was requested (call from ``work()``)."""
        if self.isInterruptionRequested():
            raise JobCancelled

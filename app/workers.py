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

Layering: this module is layer-neutral (it imports only PyQt6 and, lazily,
``app.io_utils``) so ``app.inference`` threads and ``app.handlers`` workers can
both build on it; ``app.handlers.worker_base`` is a compatibility re-export.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

# Stable logger name: log filters and tests key on the original module path.
LOGGER_NAME = "app.handlers.worker_base"
logger = logging.getLogger(LOGGER_NAME)

__all__ = [
    "GuardedThread",
    "JobCancelled",
    "LOGGER_NAME",
    "describe_error",
    "is_user_condition",
]


class JobCancelled(Exception):
    """Raised inside ``GuardedThread.work()`` to stop a cancelled job early."""


def is_user_condition(exc: BaseException) -> bool:
    """True for errors that are an expected user condition, not a bug.

    ``app.io_utils.FileLockedError`` (an output file open in another program)
    carries its own user-facing message and needs no traceback in the log.
    """
    try:  # lazy: io_utils is optional here and must never break the error path
        from app.io_utils import FileLockedError
    except Exception:
        return False
    return isinstance(exc, FileLockedError)


def describe_error(exc: BaseException) -> str:
    """User-facing one-line description of a worker exception.

    A user condition (:func:`is_user_condition`) is returned verbatim; anything
    else becomes ``"<ExceptionType>: <message>"``.
    """
    name = type(exc).__name__
    try:
        text = str(exc)
    except Exception:  # a broken __str__ must not break the error path
        return name
    if is_user_condition(exc):
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
            if is_user_condition(exc):
                logger.warning("%s failed: %s", type(self).__name__, exc)
            else:
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

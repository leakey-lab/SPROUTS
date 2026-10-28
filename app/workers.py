"""Background threads: one error boundary for ``QThread`` workers, the thread keeper and
the one way to start a pool job.

PyQt6 calls ``qFatal`` (the process aborts) when an exception escapes a
reimplemented ``QThread.run`` and no ``sys.excepthook`` is installed; even with a
hook, a worker that dies silently leaves the task UI spinning forever. Workers
therefore subclass ``GuardedThread`` and implement ``work()``:

* ``work()`` returns normally -> the job completed; the subclass emits its own
  result signal (a custom ``finished = pyqtSignal(...)`` may shadow QThread's);
* ``work()`` raises ``JobCancelled`` (``check_cancelled()`` does that once
  ``cancel()`` was requested) -> ``cancelled`` is emitted;
* ``work()`` raises anything else (``SystemExit`` and ``KeyboardInterrupt`` too)
  -> the traceback is logged and ``failed`` is emitted with a user-facing one-line
  message (``describe()``: ``describe_error`` unless the subclass words its own).

Exactly one of those outcomes happens per run.

The thread keeper. A ``QThread`` whose last reference goes while it runs aborts the
process ("QThread: Destroyed while thread ... is still running": exit 0xC0000409, no
Python traceback; it ended a real session, app.log.1:7831), and so does deleting the
parent of a running one. So a thread is held from ``start()`` until Qt's own
``finished`` (:func:`keep`; ``GuardedThread.start`` does it), it never has a parent,
:func:`release_qthread` deletes one once it has finished without blocking the caller,
and :func:`stop_all` runs when the application quits.

Pool jobs: :func:`start_pool_job` is the one way to start a job on a ``QThreadPool``. It
marks the job (:func:`in_pool_job`), so torch can refuse to run there
(:func:`refuse_pool_thread`): on a reused pool thread it waited forever for the
interpreter lock it held (dc11edf).

Layering: this module is layer-neutral (it imports only PyQt6 and, lazily,
``app.io_utils``) so ``app.inference`` threads and ``app.handlers`` workers can
both build on it.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections.abc import Callable
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QThread, QThreadPool, pyqtSignal

# Stable logger name: log filters and tests key on the original module path.
LOGGER_NAME = "app.handlers.worker_base"
logger = logging.getLogger(LOGGER_NAME)

__all__ = [
    "GuardedThread",
    "JobCancelled",
    "LOGGER_NAME",
    "STOP_ALL_TIMEOUT_MS",
    "base_finished",
    "describe_error",
    "in_pool_job",
    "is_user_condition",
    "keep",
    "kept_threads",
    "refuse_pool_thread",
    "release_qthread",
    "start_pool_job",
    "stop_all",
]

#: How long the quit waits, in all, for the kept threads that still run (:func:`stop_all`).
STOP_ALL_TIMEOUT_MS = 2000
#: Application property: its ``aboutToQuit`` runs :func:`stop_all` already.
_STOP_ON_QUIT = "spr_stop_threads_on_quit"

# The kept threads (strong references) -> delete it once Qt's own finished came (it was
# released). Touched on the GUI thread only.
_kept: dict[QThread, bool] = {}
# Set for the whole run of a job started by start_pool_job (per Python thread state).
_pool_job = threading.local()


class JobCancelled(Exception):
    """Raised inside ``GuardedThread.work()`` to stop a cancelled job early."""


def is_user_condition(exc: BaseException) -> bool:
    """True for errors that are an expected user condition, not a bug.

    ``app.io_utils.FileLockedError`` (an output file open in another program) and
    ``app.inference.batch_engine.FixedBatchOutOfMemory`` (a fixed-batch generation run
    stopped for GPU memory, O4; covers ``FixedBatchMemoryTooLow``) carry their own
    user-facing message and need no traceback in the log.
    """
    try:  # lazy: io_utils is optional here and must never break the error path
        from app.io_utils import FileLockedError
    except Exception:
        FileLockedError = None
    try:  # lazy as well; batch_engine imports no torch at import
        from app.inference.batch_engine import FixedBatchOutOfMemory
    except Exception:
        FixedBatchOutOfMemory = None
    kinds = tuple(kind for kind in (FileLockedError, FixedBatchOutOfMemory) if kind is not None)
    return bool(kinds) and isinstance(exc, kinds)


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

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        """Start the thread, held until Qt's own finished and unparented (:func:`keep`)."""
        keep(self)
        super().start(priority)

    def work(self) -> None:
        """The job body, run on the worker thread. Subclasses must implement it."""
        raise NotImplementedError(f"{type(self).__name__} must implement work()")

    def describe(self, exc: BaseException) -> str:
        """The ``failed`` message for ``exc``; a subclass may word its own."""
        return describe_error(exc)

    def run(self) -> None:
        """Run ``work()`` inside the error boundary (do not override)."""
        try:
            self.work()
        except JobCancelled:
            logger.info("%s cancelled", type(self).__name__)
            self.cancelled.emit()
        except BaseException as exc:  # noqa: BLE001 - SystemExit too: it must report back
            # Unreported, the job would stay running for good and block every later one.
            if is_user_condition(exc):
                logger.warning("%s failed: %s", type(self).__name__, exc)
            else:
                logger.exception("%s failed", type(self).__name__)
            try:
                message = self.describe(exc)
            except BaseException:  # noqa: BLE001 - a broken describe() must not escape run()
                message = describe_error(exc)
            self.failed.emit(message)

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


# --------------------------------------------------------------------------- the keeper
def base_finished(thread: QThread):
    """Qt's own ``QThread.finished`` of ``thread``, emitted after ``run()`` has returned
    (subclasses shadow ``finished`` with a result signal of their own)."""
    return QThread.finished.__get__(thread, QThread)


def keep(thread: QThread) -> None:
    """Hold ``thread`` until Qt's own finished and take it off its parent (a parent deleted
    while it runs would destroy it). Call it right before ``start()``; ``GuardedThread.start``
    does. GUI thread only."""
    if thread.parent() is not None:
        thread.setParent(None)
    if thread in _kept:
        return
    _forget_deleted()
    _kept[thread] = False
    # A weak reference: the connection must not keep a thread its owner has let go.
    base_finished(thread).connect(partial(_on_finished, weakref.ref(thread)))
    _stop_on_quit()


def kept_threads() -> tuple[QThread, ...]:
    """The threads held now (started or released, not finished yet): a leak census."""
    _forget_deleted()
    return tuple(_kept)


def _forget_deleted() -> None:
    """Drop the kept threads an owner deleted itself (a deleted thread no longer runs)."""
    for thread in [t for t in _kept if sip.isdeleted(t)]:
        del _kept[thread]


def release_qthread(thread: QThread | None) -> None:
    """Delete ``thread`` once it has finished, without blocking the caller.

    Safe in a slot of a custom "finished" signal emitted at the end of ``run()`` (RB-08):
    that thread is still running when the slot runs. A thread kept since it started, or one
    still running, is deleted when Qt's own finished arrives; one that never ran, or ended
    before it was kept, is deleted at once.
    """
    if thread is None or sip.isdeleted(thread):
        return
    if thread in _kept and (thread.isRunning() or thread.isFinished()):
        # Kept since before it ran: Qt's own finished reaches the keeper, no need to wait.
        _kept[thread] = True
        return
    if thread.isRunning():
        keep(thread)
        _kept[thread] = True
        if thread.isRunning():
            return
        # It finished between the check and the connect: Qt's finished may be gone.
    thread.wait()  # not running: returns at once (at most the end of finish())
    _kept.pop(thread, None)
    thread.deleteLater()


def _on_finished(ref: weakref.ref) -> None:
    """Qt's own finished of a kept thread: let it go (and delete it when released)."""
    thread = ref()
    if thread is None or thread not in _kept:
        return  # gone, or let go already
    if sip.isdeleted(thread):
        del _kept[thread]
    elif not thread.isRunning() and _kept.pop(thread):  # running: started again
        thread.deleteLater()


def _stop_on_quit() -> None:
    """Connect :func:`stop_all` to the application's ``aboutToQuit``, once per application."""
    app = QCoreApplication.instance()
    if app is None or app.property(_STOP_ON_QUIT):
        return
    app.aboutToQuit.connect(stop_all)
    app.setProperty(_STOP_ON_QUIT, True)


def stop_all(timeout_ms: int = STOP_ALL_TIMEOUT_MS) -> bool:
    """Ask every kept thread that still runs to stop, then wait up to ``timeout_ms`` in all.

    Runs when the application quits, so the exit never meets a running thread that a
    parent or a last reference could destroy. A thread still running after the wait is
    logged and left to the process exit (unparented, it ends with the process). True when
    none is left running.
    """
    _forget_deleted()
    running = [t for t in list(_kept) if t.isRunning()]
    for thread in running:
        _ask_to_stop(thread)
    deadline = time.perf_counter() + max(0, timeout_ms) / 1000
    left = []
    for thread in running:
        remaining_ms = max(0, int((deadline - time.perf_counter()) * 1000))
        if not thread.wait(remaining_ms):
            left.append(thread)
    if left:
        names = ", ".join(t.objectName() or type(t).__name__ for t in left)
        logger.warning("Quitting with %d thread(s) still running: %s", len(left), names)
    return not left


def _ask_to_stop(thread: QThread) -> None:
    """``stop()`` (a server loop), else ``cancel()`` (a GuardedThread), else an interruption
    request and the end of its event loop. Never raises."""
    try:
        stop = getattr(thread, "stop", None) or getattr(thread, "cancel", None)
        if callable(stop):
            stop()
        else:
            thread.requestInterruption()
            thread.quit()
    except Exception:  # noqa: BLE001 - the quit goes on
        logger.exception("Could not ask %s to stop", thread.objectName() or type(thread).__name__)


# ------------------------------------------------------------------------ pool jobs
def start_pool_job(job: Callable[[], object], pool: QThreadPool | None = None) -> None:
    """Run ``job`` on ``pool`` (the global ``QThreadPool`` when None), marked as a pool job
    for its whole run (:func:`in_pool_job`). ``job`` must not raise: nothing reports it."""

    def marked() -> None:
        _pool_job.active = True
        try:
            job()
        finally:
            _pool_job.active = False

    (pool if pool is not None else QThreadPool.globalInstance()).start(marked)


def in_pool_job() -> bool:
    """True inside a job started by :func:`start_pool_job`."""
    return getattr(_pool_job, "active", False)


def refuse_pool_thread(what: str = "torch") -> None:
    """Raise ``RuntimeError`` inside a pool job: ``what`` must run on a thread of its own.

    PyQt gives every job on a reused ``QThreadPool`` thread a fresh Python thread state;
    torch's C++ code kept the first one, and the next job that entered it waited forever for
    the interpreter lock (dc11edf: 4 of 4 runs, never on a fresh thread).
    """
    if in_pool_job():
        raise RuntimeError(
            f"{what} must not run on a QThreadPool thread (it deadlocks there); "
            "start it on a thread of its own"
        )

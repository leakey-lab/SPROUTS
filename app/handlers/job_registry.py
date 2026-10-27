"""App-wide registry that serializes batch jobs (mask/skeleton generation, measuring).

Only one batch job runs at a time: ``try_start`` refuses a second one, so e.g.
Measure cannot read a half-written ``skeletons/`` folder while generation runs.
The registry clears itself on ``QThread``'s own ``finished`` signal, which Qt
emits for every thread, including subclasses that shadow ``finished`` with a
custom signature. ``stop_all`` is meant for ``closeEvent``: it requests a
cooperative stop and waits, so the window never destroys a running thread;
``shutdown`` (called first) makes ``try_start`` refuse every later job.

Use from the GUI thread only.
"""

from __future__ import annotations

import logging
from functools import partial

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)


class JobRegistry(QObject):
    """Tracks the single running batch job.

    Signals:
        busy_changed(bool): True when a job starts, False when it ends.
    """

    busy_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name: str | None = None
        self._ended_name: str | None = None  # the job that ended last (ended_name)
        self._thread: QThread | None = None
        self._generation = 0  # identifies the current job's finished connection
        self._closing = False  # set by shutdown(): no new job may start

    def try_start(self, name: str, thread: QThread) -> bool:
        """Start ``thread`` as job ``name`` unless another job is running.

        Returns:
            True if the job was registered and started, False if another job runs.
        """
        if self._closing:
            logger.info("Job %r not started: the application is closing", name)
            return False
        if self._thread is not None:
            if self._thread.isRunning():
                logger.info("Job %r not started: %r is running", name, self._name)
                return False
            self._release()  # ended; its queued finished signal is still in flight
        self._generation += 1
        # Bind QThread's own finished: subclasses often shadow ``finished``.
        base_finished = QThread.finished.__get__(thread, QThread)
        base_finished.connect(partial(self._on_thread_finished, self._generation))
        self._name, self._thread = name, thread
        logger.info("Job started: %s", name)
        thread.start()
        self.busy_changed.emit(True)
        return True

    def shutdown(self) -> None:
        """Refuse every later ``try_start`` (call from ``closeEvent`` before ``stop_all``).

        A job chained to the one being stopped (Generate: masks, then skeletons)
        must not start while, or after, the window closes.
        """
        self._closing = True

    def is_closing(self) -> bool:
        """True once :meth:`shutdown` was called."""
        return self._closing

    def running_name(self) -> str | None:
        """Name of the running job, or None when idle."""
        return self._name

    def ended_name(self) -> str | None:
        """Name of the job that ended last (None before any): which job a
        ``busy_changed(False)`` slot follows, since :meth:`running_name` is None by then."""
        return self._ended_name

    def is_busy(self) -> bool:
        """True while a job is registered."""
        return self._thread is not None

    def cancel_all(self) -> None:
        """Request a cooperative stop of the running job (non-blocking).

        Calls the thread's ``cancel()`` when it has one (``GuardedThread``),
        otherwise ``requestInterruption()``.
        """
        thread = self._thread
        if thread is None:
            return
        logger.info("Cancelling job: %s", self._name)
        cancel = getattr(thread, "cancel", None)
        if callable(cancel):
            cancel()
        else:
            thread.requestInterruption()

    def stop_all(self, timeout_ms: int = 5000) -> bool:
        """Cancel the running job and wait up to ``timeout_ms`` for it to end.

        Returns:
            True when no job is left running (the registry is then idle and
            ``busy_changed(False)`` has been emitted); False if the job is still
            running after the timeout.
        """
        thread = self._thread
        if thread is None:
            return True
        self.cancel_all()
        if not thread.wait(timeout_ms):
            logger.warning("Job %r did not stop within %d ms", self._name, timeout_ms)
            return False
        self._release()
        return True

    def _on_thread_finished(self, generation: int) -> None:
        if generation == self._generation and self._thread is not None:
            self._release()

    def _release(self) -> None:
        logger.info("Job finished: %s", self._name)
        self._ended_name = self._name
        self._name = None
        self._thread = None
        self.busy_changed.emit(False)

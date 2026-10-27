"""
Tests for the worker error boundary (``app/workers.py``) and the
app-wide batch-job registry (``app/handlers/job_registry.py``).

Offscreen Qt. Threads are real ``QThread`` s; tests that act while a job runs
hold it on a ``threading.Event`` so they never race it. Queued signals are
flushed with ``_pump_until``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
import sys
import threading
import time
import types

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.handlers.job_registry import JobRegistry
from app.workers import GuardedThread, JobCancelled, describe_error


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump_until(predicate, timeout_s: float = 5.0) -> None:
    """Process queued events until ``predicate()`` is true (or fail after the timeout)."""
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.002)


def _flush() -> None:
    for _ in range(5):
        QCoreApplication.processEvents()


class _Recorder:
    """Collects a GuardedThread's terminal signals."""

    def __init__(self, thread: GuardedThread) -> None:
        self.failed: list[str] = []
        self.cancelled = 0
        thread.failed.connect(self.failed.append)
        thread.cancelled.connect(self._on_cancelled)

    def _on_cancelled(self) -> None:
        self.cancelled += 1


class _RaisingThread(GuardedThread):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self._exc = exc

    def work(self) -> None:
        raise self._exc


class _ResultThread(GuardedThread):
    """Shadows QThread.finished with a custom signature, like the app's workers."""

    finished = pyqtSignal(str)

    def work(self) -> None:
        self.finished.emit("ok")


class _BlockingThread(GuardedThread):
    """Waits on ``gate``; stops early (JobCancelled) when cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.started_work = threading.Event()

    def work(self) -> None:
        self.started_work.set()
        while not self.gate.wait(0.005):
            self.check_cancelled()


class _StubbornThread(QThread):
    """Plain QThread (custom finished) that ignores interruption until released."""

    finished = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def run(self) -> None:
        self.gate.wait(10)


class _PoliteThread(QThread):
    """Plain QThread that honours requestInterruption()."""

    def run(self) -> None:
        while not self.isInterruptionRequested():
            time.sleep(0.002)


# --- describe_error ----------------------------------------------------------------


def test_describe_error_formats_type_and_message():
    assert describe_error(ValueError("bad value")) == "ValueError: bad value"
    assert describe_error(MemoryError()) == "MemoryError"


def test_describe_error_survives_a_broken_str():
    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("cannot render")

    assert describe_error(Unprintable()) == "Unprintable"


def test_describe_error_uses_file_locked_message(monkeypatch):
    class FileLockedError(OSError):
        pass

    fake = types.ModuleType("app.io_utils")
    fake.FileLockedError = FileLockedError
    monkeypatch.setitem(sys.modules, "app.io_utils", fake)
    exc = FileLockedError("root_lengths.csv is open in another program. Close it and retry.")
    assert describe_error(exc) == (
        "root_lengths.csv is open in another program. Close it and retry."
    )


def test_describe_error_with_the_real_file_locked_error(tmp_path):
    io_utils = pytest.importorskip("app.io_utils")
    exc = io_utils.FileLockedError(tmp_path / "root_lengths.csv")
    message = describe_error(exc)
    assert message == str(exc)
    assert message.startswith("root_lengths.csv")
    assert not message.startswith("FileLockedError")


# --- GuardedThread -----------------------------------------------------------------


def test_exception_in_work_emits_failed_and_logs_traceback(qapp, caplog):
    thread = _RaisingThread(PermissionError(13, "Permission denied", "root_lengths.csv"))
    rec = _Recorder(thread)
    with caplog.at_level(logging.ERROR, logger="app.handlers.worker_base"):
        thread.start()
        assert thread.wait(5000)
        _pump_until(lambda: rec.failed)
    assert rec.failed == ["PermissionError: [Errno 13] Permission denied: 'root_lengths.csv'"]
    assert rec.cancelled == 0
    records = [r for r in caplog.records if r.exc_info]
    assert records and records[0].exc_info[0] is PermissionError
    assert "_RaisingThread" in records[0].getMessage()


def test_normal_completion_emits_only_the_subclass_signal(qapp):
    thread = _ResultThread()
    rec = _Recorder(thread)
    results: list[str] = []
    thread.finished.connect(results.append)
    thread.start()
    assert thread.wait(5000)
    _pump_until(lambda: results)
    _flush()
    assert results == ["ok"]
    assert (rec.failed, rec.cancelled) == ([], 0)


def test_cancel_stops_work_and_emits_cancelled(qapp):
    thread = _BlockingThread()
    rec = _Recorder(thread)
    thread.start()
    assert thread.started_work.wait(5)
    assert thread.is_cancelled() is False
    thread.cancel()
    assert thread.is_cancelled() is True
    assert thread.wait(5000)
    _pump_until(lambda: rec.cancelled)
    _flush()
    assert (rec.failed, rec.cancelled) == ([], 1)


def test_raising_job_cancelled_directly_counts_as_cancel(qapp):
    thread = _RaisingThread(JobCancelled())
    rec = _Recorder(thread)
    thread.start()
    assert thread.wait(5000)
    _pump_until(lambda: rec.cancelled)
    assert rec.failed == []


class _Abort(BaseException):
    """A BaseException that is not an Exception, SystemExit or KeyboardInterrupt."""


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (_Abort("stopped"), "_Abort: stopped"),
        (KeyboardInterrupt(), "KeyboardInterrupt"),
        (SystemExit(3), "SystemExit: 3"),
    ],
)
def test_a_base_exception_in_work_emits_failed(qapp, caplog, exc, message):
    # Unreported, the job registry would hold the job for good and refuse every later one.
    thread = _RaisingThread(exc)
    rec = _Recorder(thread)
    with caplog.at_level(logging.ERROR, logger="app.handlers.worker_base"):
        thread.start()
        assert thread.wait(5000)
        _pump_until(lambda: rec.failed)
    assert rec.failed == [message]
    assert rec.cancelled == 0
    assert any(r.exc_info and r.exc_info[0] is type(exc) for r in caplog.records)


def test_work_must_be_implemented(qapp):
    thread = GuardedThread()
    rec = _Recorder(thread)
    thread.start()
    assert thread.wait(5000)
    _pump_until(lambda: rec.failed)
    assert rec.failed[0].startswith("NotImplementedError")


class _WordedThread(_RaisingThread):
    """Words its own failure message, like the dashboard build worker."""

    def describe(self, exc: BaseException) -> str:
        return f"Could not finish: {exc}"


class _BrokenWordingThread(_RaisingThread):
    def describe(self, exc: BaseException) -> str:
        raise SystemExit("describe() itself failed")


def test_the_default_message_is_describe_error(qapp):
    exc = ValueError("bad value")
    assert GuardedThread().describe(exc) == describe_error(exc) == "ValueError: bad value"


@pytest.mark.parametrize("exc", [OSError("disk gone"), SystemExit("disk gone")])
def test_a_subclass_words_its_own_failure_message(qapp, caplog, exc):
    thread = _WordedThread(exc)
    rec = _Recorder(thread)
    with caplog.at_level(logging.ERROR, logger="app.handlers.worker_base"):
        thread.start()
        assert thread.wait(5000)
        _pump_until(lambda: rec.failed)
    assert rec.failed == ["Could not finish: disk gone"]
    assert rec.cancelled == 0
    assert any(r.exc_info and r.exc_info[0] is type(exc) for r in caplog.records)


def test_a_broken_describe_falls_back_to_describe_error(qapp):
    thread = _BrokenWordingThread(RuntimeError("boom"))
    rec = _Recorder(thread)
    thread.run()  # on this thread: whatever escapes run() would escape the worker thread
    _pump_until(lambda: rec.failed)
    assert rec.failed == ["RuntimeError: boom"]


# --- JobRegistry -------------------------------------------------------------------


@pytest.fixture
def registry(qapp):
    reg = JobRegistry()
    states: list[bool] = []
    reg.busy_changed.connect(states.append)
    reg.states = states  # test-only attribute for assertions
    return reg


def test_try_start_runs_one_job_and_rejects_a_second(registry):
    first, second = _BlockingThread(), _BlockingThread()
    assert registry.try_start("Generating masks", first) is True
    assert first.started_work.wait(5)
    assert registry.is_busy() is True
    assert registry.running_name() == "Generating masks"
    assert registry.try_start("Measuring", second) is False
    assert second.isRunning() is False
    first.gate.set()
    assert first.wait(5000)
    _pump_until(lambda: not registry.is_busy())
    assert registry.running_name() is None
    assert registry.states == [True, False]


def test_registry_clears_when_subclass_shadows_finished(registry):
    thread = _StubbornThread()  # custom finished(dict) that is never emitted
    assert registry.try_start("Loading images", thread)
    thread.gate.set()
    assert thread.wait(5000)
    _pump_until(lambda: not registry.is_busy())
    assert registry.try_start("Next job", _ResultThread()) is True
    _pump_until(lambda: not registry.is_busy())


def test_cancel_all_requests_interruption(registry):
    guarded, plain = _BlockingThread(), _PoliteThread()
    rec = _Recorder(guarded)
    assert registry.try_start("Generating skeletons", guarded)
    assert guarded.started_work.wait(5)
    registry.cancel_all()
    assert guarded.wait(5000)
    _pump_until(lambda: rec.cancelled and not registry.is_busy())

    assert registry.try_start("Measuring", plain)
    registry.cancel_all()
    assert plain.wait(5000)
    _pump_until(lambda: not registry.is_busy())


def test_stop_all_waits_and_clears_synchronously(registry):
    thread = _BlockingThread()
    assert registry.try_start("Generating masks", thread)
    assert thread.started_work.wait(5)
    assert registry.stop_all(timeout_ms=5000) is True
    assert thread.isFinished()
    assert registry.is_busy() is False
    assert registry.states == [True, False]
    # The queued finished of the stopped job must not clear a job started after it.
    follow_up = _BlockingThread()
    assert registry.try_start("Measuring", follow_up)
    _flush()
    assert registry.running_name() == "Measuring"
    follow_up.gate.set()
    assert follow_up.wait(5000)
    _pump_until(lambda: not registry.is_busy())


def test_stop_all_reports_a_job_that_does_not_stop(registry):
    thread = _StubbornThread()
    assert registry.try_start("Stubborn", thread)
    try:
        assert registry.stop_all(timeout_ms=50) is False
        assert registry.running_name() == "Stubborn"
    finally:
        thread.gate.set()
        assert thread.wait(5000)
    _pump_until(lambda: not registry.is_busy())


def test_stop_all_without_a_job_is_true(registry):
    assert registry.stop_all() is True
    assert registry.states == []

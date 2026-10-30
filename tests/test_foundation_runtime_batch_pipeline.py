"""
Tests for the Qt-free batch pipeline helpers (``app/inference/batch_pipeline.py``).

Fake work only (sleeps, events, counters): no GPU, no torch, no image files.
Tasks that must still be running when the test acts are held on a
``threading.Event`` so assertions never race them.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import sys
import threading
import time

import pytest

from app.inference import batch_pipeline
from app.inference.batch_pipeline import (
    FAILURE_LOG_LIMIT,
    AsyncWriter,
    LogLimiter,
    PrefetchIterator,
    default_io_threads,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- default_io_threads ------------------------------------------------------------


@pytest.mark.parametrize(("cpus", "expected"), [(None, 2), (1, 2), (4, 2), (8, 4), (32, 6)])
def test_default_io_threads_is_half_the_cpus_clamped(monkeypatch, cpus, expected):
    monkeypatch.setattr(batch_pipeline.os, "cpu_count", lambda: cpus)
    assert default_io_threads() == expected


def test_module_import_does_not_pull_in_torch():
    code = "import sys, app.inference.batch_pipeline; print('torch' in sys.modules)"
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


# --- PrefetchIterator --------------------------------------------------------------


def _sleepy(value: int, delay: float):
    def task():
        time.sleep(delay)
        return value

    return task


def test_prefetch_yields_results_in_submission_order():
    rng = random.Random(0)
    tasks = [_sleepy(i, rng.uniform(0, 0.01)) for i in range(40)]
    with PrefetchIterator(tasks, depth=4, workers=4) as it:
        assert list(it) == list(range(40))


def test_prefetch_runs_tasks_ahead_on_background_threads():
    started = [threading.Event() for _ in range(3)]
    thread_names: list[str] = []

    def make(i):
        def task():
            thread_names.append(threading.current_thread().name)
            started[i].set()
            return i

        return task

    with PrefetchIterator([make(i) for i in range(3)], depth=2, workers=2) as it:
        assert next(it) == 0
        # Items 1 and 2 are computed before the consumer asks for them.
        assert started[1].wait(2) and started[2].wait(2)
        assert list(it) == [1, 2]
    assert threading.main_thread().name not in thread_names


def test_prefetch_never_runs_more_than_depth_ahead():
    started = 0
    lock = threading.Lock()

    def make(i):
        def task():
            nonlocal started
            with lock:
                started += 1
            return i

        return task

    depth = 3
    with PrefetchIterator([make(i) for i in range(30)], depth=depth, workers=3) as it:
        for consumed, _ in enumerate(it, start=1):
            time.sleep(0.002)  # slow consumer: the pool would race ahead if unbounded
            with lock:
                assert started <= consumed + depth


def test_prefetch_propagates_worker_exceptions_and_closes():
    def boom():
        raise ValueError("corrupt image")

    tasks = [_sleepy(0, 0), _sleepy(1, 0), boom, _sleepy(3, 0)]
    it = PrefetchIterator(tasks, depth=2, workers=2)
    assert next(it) == 0
    assert next(it) == 1
    with pytest.raises(ValueError, match="corrupt image"):
        next(it)
    with pytest.raises(StopIteration):
        next(it)


def test_prefetch_should_stop_ends_iteration_early():
    executed = []
    stop = threading.Event()

    def make(i):
        def task():
            executed.append(i)
            return i

        return task

    got = []
    with PrefetchIterator(
        [make(i) for i in range(100)], depth=2, workers=2, should_stop=stop.is_set
    ) as it:
        for value in it:
            got.append(value)
            if value == 4:
                stop.set()
    assert got == [0, 1, 2, 3, 4]
    assert len(executed) <= len(got) + 2


def test_prefetch_close_cancels_pending_and_waits_for_running():
    gate = threading.Event()
    running_done = threading.Event()
    executed = []

    def slow_first():
        gate.wait(5)
        time.sleep(0.05)
        running_done.set()
        return "first"

    def make(i):
        def task():
            executed.append(i)
            return i

        return task

    it = PrefetchIterator([slow_first] + [make(i) for i in range(50)], depth=3, workers=1)
    threading.Timer(0.05, gate.set).start()
    it.close()
    assert running_done.is_set()  # close() returned only after the running task ended
    assert len(executed) < 50  # queued tasks were cancelled
    it.close()  # idempotent
    with pytest.raises(StopIteration):
        next(it)


def test_prefetch_map_applies_fn_in_order():
    with PrefetchIterator.map(lambda x: x * x, range(10), depth=3) as it:
        assert list(it) == [x * x for x in range(10)]


def test_prefetch_empty_input():
    with PrefetchIterator([], depth=2) as it:
        assert list(it) == []


def test_prefetch_rejects_bad_depth():
    with pytest.raises(ValueError):
        PrefetchIterator([], depth=0)


# --- AsyncWriter -------------------------------------------------------------------


def test_writer_runs_every_task_with_args():
    out: dict[str, tuple] = {}
    lock = threading.Lock()

    def write(key, a, b=0):
        with lock:
            out[key] = (a, b, threading.current_thread().name)

    with AsyncWriter(max_workers=3, max_pending=4) as writer:
        for i in range(20):
            writer.submit(f"k{i}", write, f"k{i}", i, b=i * 2)
        writer.wait()
        assert sorted(out) == sorted(f"k{i}" for i in range(20))
    assert out["k7"][:2] == (7, 14)
    assert all(name != threading.main_thread().name for _, _, name in out.values())


def test_writer_collects_errors_by_key():
    def write(i):
        if i % 5 == 0:
            raise OSError(f"disk full at {i}")

    writer = AsyncWriter(max_workers=2)
    for i in range(12):
        writer.submit(f"img{i}", write, i)
    writer.wait()
    errors = writer.errors()
    writer.close()
    assert sorted(key for key, _ in errors) == ["img0", "img10", "img5"]
    assert all(isinstance(exc, OSError) for _, exc in errors)


def test_writer_bounds_pending_tasks():
    gate = threading.Event()
    writer = AsyncWriter(max_workers=2, max_pending=2)
    writer.submit("a", gate.wait, 5)
    writer.submit("b", gate.wait, 5)
    third_returned = threading.Event()
    submitter = threading.Thread(
        target=lambda: (writer.submit("c", lambda: None), third_returned.set())
    )
    submitter.start()
    assert not third_returned.wait(0.15)  # blocked: two tasks already outstanding
    gate.set()
    assert third_returned.wait(5)
    submitter.join(5)
    writer.close()
    assert writer.errors() == []


def test_writer_rejects_submit_after_close():
    writer = AsyncWriter(max_workers=1)
    writer.close()
    with pytest.raises(RuntimeError):
        writer.submit("late", lambda: None)


def test_writer_logs_the_first_failures_then_a_count(caplog):
    def write(i):
        raise OSError(f"share gone at {i}")

    with caplog.at_level(logging.WARNING, logger=batch_pipeline.logger.name):
        writer = AsyncWriter(max_workers=2)
        for i in range(FAILURE_LOG_LIMIT + 7):
            writer.submit(f"img{i}", write, i)
        writer.close()
    assert len(writer.errors()) == FAILURE_LOG_LIMIT + 7  # every failure is still collected
    logged = [r for r in caplog.records if "Write failed" in r.getMessage()]
    assert len(logged) == FAILURE_LOG_LIMIT
    assert sum(r.exc_info is not None for r in logged) == 1  # only the first has a traceback
    [more] = [r for r in caplog.records if "more failed writes" in r.getMessage()]
    assert more.getMessage().startswith("7 more")


# --- LogLimiter --------------------------------------------------------------------


def test_log_limiter_logs_the_first_records_then_one_count(caplog):
    log = logging.getLogger("tests.limiter")
    limiter = LogLimiter(log, "broken things", limit=3)
    quiet = LogLimiter(log, "quiet things", limit=3)
    with caplog.at_level(logging.INFO, logger=log.name):
        for i in range(5):
            limiter.log(logging.ERROR, "thing %d broke", i)
        limiter.summary()
        quiet.log(logging.WARNING, "only once")
        quiet.summary()  # nothing held back: no count
    assert [r.getMessage() for r in caplog.records] == [
        "thing 0 broke",
        "thing 1 broke",
        "thing 2 broke",
        "2 more broken things not logged one by one",
        "only once",
    ]
    assert [r.levelno for r in caplog.records[:3]] == [logging.ERROR] * 3
    passed_on = [caplog.records[i] for i in (0, 1, 2, 4)]  # the caller's line, not the limiter's
    assert {os.path.basename(r.pathname) for r in passed_on} == {os.path.basename(__file__)}

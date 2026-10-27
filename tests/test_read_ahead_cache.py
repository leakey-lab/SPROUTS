"""ReadAheadCache, the bounded cache of the Library's and the Correct page's read-aheads.

The rules both read-aheads rely on: a job's result counts once it ends, a queued or running
job holds nothing, the least wanted go first (outside the plan oldest first, then the plan
from its end) and the last of that order stays, a cancelled job's callback (run by
``cancel`` with the lock held) never takes the lock, and a result kept without a job is a
finished entry. Qt-free, standard library only.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from concurrent.futures import Executor, Future
from pathlib import Path

import pytest

from app.read_ahead_cache import ReadAheadCache, finished

ROOT = Path(__file__).resolve().parents[1]


class Sized:
    """A result that reports its bytes."""

    def __init__(self, nbytes: int) -> None:
        self.nbytes = nbytes


class ManualExecutor(Executor):
    """Queues the jobs; ``run_next()`` / ``run_all()`` run them, in order, on this thread."""

    def __init__(self) -> None:
        self.queue: list[tuple[Future, object]] = []

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        self.queue.append((future, lambda: fn(*args, **kwargs)))
        return future

    def run_next(self) -> None:
        future, job = self.queue.pop(0)
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(job())
            except BaseException as exc:  # noqa: BLE001 - the future carries it
                future.set_exception(exc)

    def run_all(self) -> None:
        while self.queue:
            self.run_next()


class Cache(ReadAheadCache):
    """The smallest subclass: jobs and kept results by key, as the read-aheads use them."""

    def __init__(self, **kwargs) -> None:
        super().__init__(thread_name="test-read-ahead", **kwargs)

    def request(self, key: str, fn, *args) -> Future:
        with self._lock:
            future = self._submit(key, fn, *args)
        self._watch(key, future)
        return future

    def keep(self, key: str, result: object) -> None:
        with self._lock:
            self._put(key, result)

    def set_plan(self, keys: list[str]) -> None:
        with self._lock:
            self._plan = list(keys)


def test_the_module_loads_the_standard_library_only():
    probe = (
        "import importlib, sys; importlib.import_module('app.read_ahead_cache'); "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'PyQt6', 'torch', 'numpy', 'cv2'} or (m.startswith('app.') "
        "and m != 'app.read_ahead_cache')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_at_least_one_entry_is_kept():
    with pytest.raises(ValueError):
        Cache(max_images=0, max_bytes=100, executor=ManualExecutor())


def test_finished_hands_over_only_a_job_that_ended_with_a_result():
    queued, running, done, failed, cancelled = (Future() for _ in range(5))
    running.set_running_or_notify_cancel()
    done.set_result("photo")
    failed.set_exception(OSError("disk gone"))
    cancelled.cancel()

    found = [finished(f) for f in (queued, running, done, failed, cancelled)]

    assert found == [None, None, "photo", None, None]


def test_a_result_kept_without_a_job_is_a_finished_entry():
    cache = Cache(max_images=3, max_bytes=100, executor=ManualExecutor())
    cache.keep("a", Sized(10))

    (entry,) = cache._jobs.values()

    assert entry.done() and not entry.cancelled() and entry.exception() is None
    assert not entry.cancel()  # nothing to cancel, as with a finished Future
    assert finished(entry).nbytes == 10 and cache.pending() == ["a"]


def test_the_least_wanted_go_first_outside_the_plan_then_the_plan_from_its_end():
    executor = ManualExecutor()
    cache = Cache(max_images=5, max_bytes=100, executor=executor)
    cache.set_plan(["p1", "p2"])  # the most wanted first
    for key in ("x", "p2", "y", "p1"):
        cache.request(key, Sized, 40)  # queued: nothing held yet
    assert cache.pending() == ["x", "p2", "y", "p1"]

    executor.run_all()  # each end re-checks the bytes: x goes, then y

    assert cache.pending() == ["p2", "p1"]
    cache.keep("q", Sized(40))  # 120 > 100: q is outside the plan, so it goes, newest or not
    assert cache.pending() == ["p2", "p1"]
    cache.set_plan([])
    cache.keep("q", Sized(40))  # without a plan the oldest go first and the newest stays
    assert cache.pending() == ["p1", "q"]


def test_without_a_plan_the_newest_stays_even_alone_over_the_budget():
    executor = ManualExecutor()
    cache = Cache(max_images=3, max_bytes=100, executor=executor)
    cache.request("a", Sized, 60)
    cache.request("b", Sized, 500)

    executor.run_all()

    assert cache.pending() == ["b"]
    assert finished(cache._jobs["b"]).nbytes == 500


def test_the_count_bound_drops_the_oldest_when_a_job_is_queued():
    cache = Cache(max_images=2, max_bytes=100, executor=ManualExecutor())
    first = cache.request("a", Sized, 1)
    cache.request("b", Sized, 1)
    cache.request("c", Sized, 1)

    assert cache.pending() == ["b", "c"] and first.cancelled()


def test_cancelling_queued_jobs_with_the_lock_held_does_not_deadlock():
    """A deadlock fails the test instead of hanging the run: the clearing thread is a daemon,
    and no thread is left waiting for its lock."""
    release = threading.Event()
    cache = Cache(max_images=3, max_bytes=100)  # its own one-thread pool
    clearing = threading.Thread(target=cache.clear, daemon=True)  # cancels b with the lock held
    try:
        # Occupies the worker. Not an entry: its end would wait for a deadlocked lock, and
        # the pool's thread would keep the process from ending.
        cache._executor.submit(release.wait, 5)
        queued = cache.request("b", Sized, 1)

        clearing.start()
        clearing.join(5)

        assert not clearing.is_alive(), "clear() deadlocked"
        assert queued.cancelled() and cache.pending() == []
    finally:
        release.set()
        if not clearing.is_alive():  # else close() would wait for the same lock
            cache.close()


def test_a_failed_job_is_logged_and_holds_nothing(caplog):
    executor = ManualExecutor()
    cache = Cache(max_images=3, max_bytes=100, executor=executor)

    def broken():
        raise OSError("disk gone")

    cache.request("a", broken)
    with caplog.at_level(logging.WARNING, logger="app.read_ahead_cache"):
        executor.run_all()

    assert "A read-ahead job failed: disk gone" in caplog.text
    assert cache.pending() == ["a"] and finished(cache._jobs["a"]) is None


def test_close_cancels_a_queued_job():
    executor = ManualExecutor()
    cache = Cache(max_images=3, max_bytes=100, executor=executor)
    future = cache.request("a", Sized, 500)

    cache.close()
    executor.run_all()

    assert future.cancelled() and cache.pending() == []


def test_a_job_ending_after_close_changes_nothing():
    ended = []

    class Watched(Cache):
        def _ended(self, key, future):
            ended.append(key)
            super()._ended(key, future)

    executor = ManualExecutor()
    cache = Watched(max_images=3, max_bytes=100, executor=executor)
    cache.request("a", Sized, 1)
    executor.run_next()
    assert ended == ["a"]  # before close, a job's end is checked
    future = cache.request("b", Sized, 500)
    assert future.set_running_or_notify_cancel()  # running: close() cannot cancel it

    cache.close()
    future.set_result(Sized(500))  # the job ends after close

    assert not future.cancelled() and ended == ["a"] and cache.pending() == []

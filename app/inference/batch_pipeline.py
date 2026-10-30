"""Thread-based overlap helpers for batch inference (Qt-free, torch-free).

A batch run is decode -> host-to-device copy -> forward -> device-to-host copy ->
PNG write. Run serially on one thread, the GPU idles while images are decoded
and written. These helpers overlap the CPU stages with the forward pass without
changing what is computed:

* ``PrefetchIterator`` runs the next ``depth`` load tasks (decode, transform,
  collate, pin) on a thread pool while the consumer runs the model, and yields
  the results in exactly the submission order, so batch composition is unchanged;
* ``AsyncWriter`` runs save tasks on a bounded pool and collects per-item errors
  instead of aborting the run;
* ``LogLimiter`` logs the first records of one kind of per-item failure and counts
  the rest, so a run in which every input fails does not flood the log (C6-02).

Threads, not ``DataLoader`` worker processes: PIL decoding and torch tensor ops
release the GIL, and worker processes are unsafe to spawn from a ``QThread`` on
Windows. Each thread that runs a torch CPU op starts its own OpenMP team, so a GPU
run gives these threads one torch thread (``batch_engine.run_batches``'
``torch_threads``, S12-01), and ``main.py`` sets ``KMP_BLOCKTIME=0`` so that idle team
workers sleep instead of spinning against the forward (P3-01: the CPU fallback,
whose forward needs every core). ``PrefetchIterator`` honours a ``should_stop`` callable
(e.g. a worker's ``is_cancelled``) so a cancelled run stops scheduling new work
promptly; a task that is already running is never interrupted, so no file is left
half-written. ``AsyncWriter`` runs every save it was given (PI-08: a cancelled run
keeps the outputs it computed).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

#: Default bound on AsyncWriter tasks submitted but not yet finished.
DEFAULT_MAX_PENDING = 256

#: (C6-02) Records of one kind of per-item failure logged one by one in a run.
FAILURE_LOG_LIMIT = 20


def default_io_threads() -> int:
    """Thread count for decode/write pools: half the CPUs, clamped to 2..6."""
    return max(2, min(6, (os.cpu_count() or 1) // 2))


def progress_percent(done: int, total: int | None) -> int:
    """Percent complete, clamped to 0..100.

    ``total`` None means ``done`` is already a percent; a non-positive ``total``
    (nothing to do) counts as complete.
    """
    if total is None:
        pct = int(done)
    elif total <= 0:
        pct = 100
    else:
        pct = int(done) * 100 // int(total)
    return max(0, min(100, pct))


class PrefetchIterator[T](Iterator[T]):
    """Run zero-argument tasks ahead of the consumer; yield results in order.

    Up to ``depth`` tasks run (or wait) on ``workers`` background threads while
    the consumer processes the current result; prefetching starts on
    construction. A task's exception is re-raised by ``next()`` in the consumer
    thread, and the iterator is then closed. When ``should_stop()`` turns true,
    iteration ends as if the input were exhausted and queued tasks are
    cancelled; call the owner's own cancel check after the loop to tell the two
    apart. Always ``close()`` it (or use it as a context manager).

    The ``tasks`` iterable itself is consumed on the consumer thread, so it
    should be cheap (e.g. a generator of ``functools.partial`` objects).
    """

    def __init__(
        self,
        tasks: Iterable[Callable[[], T]],
        *,
        depth: int = 2,
        workers: int | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        if depth < 1:
            raise ValueError(f"depth must be >= 1, got {depth}")
        n_workers = workers if workers is not None else min(depth, default_io_threads())
        if n_workers < 1:
            raise ValueError(f"workers must be >= 1, got {n_workers}")
        self._tasks = iter(tasks)
        self._depth = depth
        self._should_stop = should_stop
        self._pool = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="prefetch")
        self._pending: deque[Future[T]] = deque()
        self._exhausted = False
        self._closed = False
        try:
            self._fill()
        except BaseException:
            self.close()
            raise

    @classmethod
    def map(
        cls, fn: Callable[[Any], T], items: Iterable[Any], **kwargs: Any
    ) -> PrefetchIterator[T]:
        """Prefetch ``fn(item)`` for each item (same keyword options as the constructor)."""
        return cls((partial(fn, item) for item in items), **kwargs)

    def __iter__(self) -> PrefetchIterator[T]:
        return self

    def __next__(self) -> T:
        if self._closed or self._stop_requested() or not self._pending:
            self.close()
            raise StopIteration
        future = self._pending.popleft()
        try:
            self._fill()  # keep ``depth`` tasks in flight while we wait
            return future.result()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Cancel queued tasks and wait for running ones (idempotent)."""
        if self._closed:
            return
        self._closed = True
        for future in self._pending:
            future.cancel()
        self._pending.clear()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> PrefetchIterator[T]:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _stop_requested(self) -> bool:
        return self._should_stop is not None and bool(self._should_stop())

    def _fill(self) -> None:
        while not self._exhausted and len(self._pending) < self._depth:
            if self._stop_requested():
                return
            try:
                task = next(self._tasks)
            except StopIteration:
                self._exhausted = True
                return
            self._pending.append(self._pool.submit(task))


class LogLimiter:
    """Log the first ``limit`` records of one kind, then only count them (C6-02).

    A run in which every input fails (a network share that dropped, a protected
    output folder) would otherwise log one record per input and rotate the
    session's history out of ``app.log``; the run record lists every failure
    anyway. ``log`` keeps a record's level, message and source line (the
    caller's); ``summary()`` logs how many were held back. Thread-safe; one per run.
    """

    def __init__(self, log: logging.Logger, what: str, limit: int = FAILURE_LOG_LIMIT) -> None:
        self._log = log
        self._what = what  # plural noun for the summary, e.g. "unreadable inputs"
        self._limit = limit
        self._count = 0
        self._lock = threading.Lock()

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        """``logger.log(level, msg, *args, **kwargs)`` for the first ``limit`` calls."""
        with self._lock:
            self._count += 1
            if self._count > self._limit:
                return
        self._log.log(level, msg, *args, stacklevel=2, **kwargs)

    def summary(self) -> None:
        """One warning with the number of records held back, if any."""
        with self._lock:
            held_back = self._count - self._limit
        if held_back > 0:
            self._log.warning("%d more %s not logged one by one", held_back, self._what)


class AsyncWriter:
    """Bounded background pool for save tasks, with per-item error collection.

    ``submit`` blocks while ``max_pending`` tasks are outstanding (back-pressure
    keeps memory bounded when writes fall behind). A failing task is recorded in
    ``errors()``; the others keep running. The first ``FAILURE_LOG_LIMIT`` failures
    are logged (the first with its traceback), and ``close()`` logs how many more
    there were. Every submitted task runs. Submit from one thread.
    """

    def __init__(self, max_workers: int | None = None, max_pending: int | None = None) -> None:
        workers = max_workers if max_workers is not None else default_io_threads()
        pending = max_pending if max_pending is not None else DEFAULT_MAX_PENDING
        if workers < 1 or pending < 1:
            raise ValueError("max_workers and max_pending must be >= 1")
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="writer")
        self._slots = threading.Semaphore(pending)
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._outstanding = 0
        self._errors: list[tuple[Hashable, BaseException]] = []
        self._failures = LogLimiter(logger, "failed writes")
        self._closed = False

    def submit(self, key: Hashable, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        """Queue ``fn(*args, **kwargs)``; ``key`` labels it in ``errors()``.

        Blocks while ``max_pending`` tasks are outstanding.

        Raises:
            RuntimeError: the writer was closed.
        """
        if self._closed:
            raise RuntimeError("AsyncWriter is closed")
        self._slots.acquire()
        with self._lock:
            self._outstanding += 1
        self._pool.submit(self._run, key, fn, args, kwargs)

    def wait(self) -> None:
        """Block until every submitted task has finished."""
        with self._idle:
            while self._outstanding:
                self._idle.wait()

    def errors(self) -> list[tuple[Hashable, BaseException]]:
        """``(key, exception)`` for each failed task, in completion order."""
        with self._lock:
            return list(self._errors)

    def close(self) -> None:
        """Refuse new work, wait for outstanding tasks, stop the pool (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self.wait()
        self._pool.shutdown(wait=True)
        self._failures.summary()

    def __enter__(self) -> AsyncWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _run(
        self, key: Hashable, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                self._errors.append((key, exc))
                first = len(self._errors) == 1
            self._failures.log(
                logging.WARNING,
                "Write failed for %s: %s",
                key,
                exc,
                exc_info=exc if first else None,
            )
        finally:
            self._task_finished()

    def _task_finished(self) -> None:
        self._slots.release()
        with self._idle:
            self._outstanding -= 1
            if not self._outstanding:
                self._idle.notify_all()

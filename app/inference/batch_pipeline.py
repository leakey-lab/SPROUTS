"""Thread-based overlap helpers for batch inference (Qt-free, torch-free).

A batch run is decode -> host-to-device copy -> forward -> device-to-host copy ->
PNG write. Run serially on one thread, the GPU idles while images are decoded
and written. These helpers overlap the CPU stages with the forward pass without
changing what is computed:

* ``PrefetchIterator`` runs the next ``depth`` load tasks (decode, transform,
  collate, pin) on a thread pool while the consumer runs the model, and yields
  the results in exactly the submission order, so batch composition is unchanged;
* ``AsyncWriter`` runs save tasks on a bounded pool, collects per-item errors
  instead of aborting the run, and reports completions for progress;
* ``PercentThrottle`` turns completion counts into one progress update per
  percent.

Threads, not ``DataLoader`` worker processes: PIL decoding and torch tensor ops
release the GIL, and worker processes are unsafe to spawn from a ``QThread`` on
Windows. Both classes honour a ``should_stop`` callable (e.g. a worker's
``is_cancelled``) so a cancelled run stops scheduling new work promptly; a task
that is already running is never interrupted, so no file is left half-written.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Default bound on AsyncWriter tasks submitted but not yet finished.
DEFAULT_MAX_PENDING = 256

#: How often a blocked ``AsyncWriter.submit`` re-checks ``should_stop`` (seconds).
_STOP_POLL_S = 0.05


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


class PrefetchIterator(Iterator[T], Generic[T]):
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


class AsyncWriter:
    """Bounded background pool for save tasks, with per-item error collection.

    ``submit`` blocks while ``max_pending`` tasks are outstanding (back-pressure
    keeps memory bounded when writes fall behind). A failing task is logged
    (the first with its traceback) and recorded in ``errors()``; the others keep
    running. ``on_done(completed)`` is called after every finished task with the
    running count; calls are serialized and the counts increase by one, so it
    can drive a progress bar directly (see ``PercentThrottle``). Once
    ``should_stop()`` is true, ``submit`` refuses new work and queued tasks are
    skipped; a running task always completes. Submit from one thread.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        max_pending: int | None = None,
        *,
        on_done: Callable[[int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
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
        self._done_lock = threading.Lock()  # serializes on_done
        self._completed = 0
        self._on_done = on_done
        self._should_stop = should_stop
        self._closed = False

    def submit(self, key: Hashable, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> bool:
        """Queue ``fn(*args, **kwargs)``; ``key`` labels it in ``errors()``.

        Returns:
            True if queued; False if ``should_stop()`` is (or became) true.

        Raises:
            RuntimeError: the writer was closed.
        """
        if self._closed:
            raise RuntimeError("AsyncWriter is closed")
        while not self._slots.acquire(timeout=_STOP_POLL_S):
            if self._stop_requested():
                return False
        if self._stop_requested():
            self._slots.release()
            return False
        with self._lock:
            self._outstanding += 1
        self._pool.submit(self._run, key, fn, args, kwargs)
        return True

    def wait(self) -> None:
        """Block until every submitted task has finished (or been skipped)."""
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

    def __enter__(self) -> AsyncWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _stop_requested(self) -> bool:
        return self._should_stop is not None and bool(self._should_stop())

    def _run(
        self, key: Hashable, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        try:
            if not self._stop_requested():
                fn(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                self._errors.append((key, exc))
                first = len(self._errors) == 1
            logger.warning("Write failed for %s: %s", key, exc, exc_info=exc if first else None)
        finally:
            self._task_finished()

    def _task_finished(self) -> None:
        with self._done_lock:
            self._completed += 1
            if self._on_done is not None:
                try:
                    self._on_done(self._completed)
                except Exception:
                    logger.exception("AsyncWriter on_done callback failed")
        self._slots.release()
        with self._idle:
            self._outstanding -= 1
            if not self._outstanding:
                self._idle.notify_all()


class PercentThrottle:
    """Forward progress to ``emit(percent)`` only when the integer percent grows.

    ``update(done)`` may be called for every finished item (e.g. as an
    ``AsyncWriter`` ``on_done``); ``emit`` then runs at most 101 times per job
    and never reports a lower percent than before. Thread-safe.
    """

    def __init__(self, total: int, emit: Callable[[int], None]) -> None:
        self._total = total
        self._emit = emit
        self._last = -1
        self._lock = threading.Lock()

    def update(self, done: int) -> None:
        """Report ``done`` finished items out of the total."""
        pct = progress_percent(done, self._total)
        with self._lock:
            if pct <= self._last:
                return
            self._last = pct
            self._emit(pct)

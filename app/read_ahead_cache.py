"""The bounded cache both read-aheads share, the Library's and the Correct page's (Qt-free).

:class:`ReadAheadCache` keeps entries by key (the subclasses use :func:`~app.io_utils.path_key`),
oldest first: the futures of the jobs it runs on one worker thread, and results kept without a
job (a photo shown, an image handed back). They are bounded by count and by the bytes of the
finished results:

- a result counts towards the bytes as soon as its job ends, and the bounds are enforced
  then too; a job queued or running holds nothing yet, so it is never dropped for bytes;
- what goes first: the entries outside the plan (the keys most wanted, first first), oldest
  first, then the plan's from its end; the last of that order always stays (without a plan,
  the newest);
- ``cancel`` calls a cancelled job's done-callback at once, with the lock held, so
  :meth:`ReadAheadCache._completed` returns for it before taking the lock.

Only the thread that owns the object (the GUI thread) calls it; the worker runs the jobs, and
the end of one only re-checks the bounds, under the lock. What an entry holds and how it is
handed over is the subclasses': :class:`app.gui.display_read_ahead.DisplayReadAhead` (the
Library) and :class:`app.gui.correct.read_ahead.ReadAhead` (the Correct page).
"""

from __future__ import annotations

import functools
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor

logger = logging.getLogger(__name__)

__all__ = ["ReadAheadCache", "finished"]


def finished(future: Future) -> object | None:
    """A job's result; None while it is queued or running, or when it was cancelled or
    raised (never waits). ``future`` may also be a result kept without a job."""
    if not future.done():
        return None
    try:
        error = future.exception()  # a cancelled job raises: one call less per entry trimmed
    except CancelledError:
        return None
    return future.result() if error is None else None


class _Kept:
    """A result kept without a job (:meth:`ReadAheadCache._put`). It answers what the caches
    ask a finished Future, without the Future's lock: every bound check reads each entry, and
    a Future per kept photo made a Library step about 15 us slower."""

    __slots__ = ("_result",)

    def __init__(self, result: object) -> None:
        self._result = result

    def done(self) -> bool:
        return True

    def cancelled(self) -> bool:
        return False

    def cancel(self) -> bool:
        return False  # finished: nothing to cancel, as with a finished Future

    def exception(self) -> BaseException | None:
        return None

    def result(self) -> object:
        return self._result


class ReadAheadCache:
    """Jobs run on one worker thread and their results, keyed, bounded (see the module).

    ``executor`` is for tests; by default the object owns a one-thread pool named
    ``thread_name``, stopped by :meth:`close`.
    """

    def __init__(
        self,
        *,
        max_images: int,
        max_bytes: int,
        thread_name: str,
        executor: Executor | None = None,
    ) -> None:
        if max_images < 1:
            raise ValueError(f"max_images must be at least 1, got {max_images}")
        self._max_images = int(max_images)
        self._max_bytes = int(max_bytes)
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=thread_name
        )
        self._jobs: OrderedDict[str, Future | _Kept] = OrderedDict()  # oldest first
        self._plan: list[str] = []  # the keys most wanted, first first
        self._lock = threading.Lock()  # the end of a job trims on the worker
        self._closed = False

    def pending(self) -> list[str]:
        """Keys (normalised paths) of the entries kept, finished or not, oldest first."""
        with self._lock:
            return list(self._jobs)

    def clear(self) -> None:
        """Forget every entry and the plan (a new folder); a queued job is cancelled."""
        with self._lock:
            for key in list(self._jobs):
                self._drop(key)
            self._plan = []

    def close(self) -> None:
        """Forget everything and stop the worker (a running job ends on its own)."""
        self.clear()
        with self._lock:
            self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- internals
    def _submit(self, key: str, fn: Callable[..., object], *args: object) -> Future:
        """Queue ``fn(*args)`` as ``key``'s entry, the newest (lock held). The caller hands
        the future to :meth:`_watch` once it has unlocked."""
        self._jobs.pop(key, None)
        future = self._jobs[key] = self._executor.submit(fn, *args)
        self._trim()
        return future

    def _watch(self, key: str, future: Future) -> None:
        """Re-check the bounds when ``key``'s job ends. Unlocked: a job done already calls
        back at once, on this thread."""
        future.add_done_callback(functools.partial(self._completed, key))

    def _put(self, key: str, result: object, plan: Sequence[str] | None = None) -> None:
        """Keep ``result`` as ``key``'s entry, finished and the newest (lock held); the
        entry it replaces is not cancelled. ``plan`` as in :meth:`_trim`."""
        self._jobs.pop(key, None)
        self._jobs[key] = _Kept(result)
        self._trim(plan)

    def _completed(self, key: str, future: Future) -> None:
        """``key``'s job ended (on the worker, or at once): a failure is logged; a result
        now counts towards the bytes. A cancelled job holds nothing, and ``cancel`` calls
        this with the lock held: it must return before taking the lock."""
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            logger.warning("A read-ahead job failed: %s", error)
            return
        with self._lock:
            if not self._closed:
                self._ended(key, future)

    def _ended(self, key: str, future: Future) -> None:
        """``key``'s job ended with a result (lock held, not closed): re-check the bounds,
        even when the entry is not that job any more (the Library's override checks)."""
        self._trim()

    def _drop(self, key: str) -> None:
        """Forget ``key`` (lock held); a queued job is cancelled."""
        future = self._jobs.pop(key, None)
        if future is not None:
            future.cancel()

    def _nbytes(self, result: object) -> int:
        """Bytes a finished entry's result holds: its ``nbytes`` (0 for None)."""
        return 0 if result is None else int(result.nbytes)

    def _trim(self, plan: Sequence[str] | None = None) -> None:
        """Keep at most ``max_images`` entries and ``max_bytes`` of finished results (lock
        held), in the order of the module docstring; ``plan`` (by default the last set)
        lists the keys most wanted, first first."""
        wanted = self._plan if plan is None else plan
        planned = [key for key in reversed(wanted) if key in self._jobs]
        chosen = set(planned)
        order = [key for key in self._jobs if key not in chosen] + planned
        while len(self._jobs) > self._max_images:
            self._drop(order.pop(0))
        held = {key: self._nbytes(finished(self._jobs[key])) for key in order}
        total = sum(held.values())
        for key in order[:-1]:
            if total <= self._max_bytes:
                break
            if held[key]:
                total -= held[key]
                self._drop(key)

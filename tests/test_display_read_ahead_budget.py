"""The Library read-ahead counts a decode against its bytes once it finishes, shown or not.

``DisplayReadAhead`` keeps the photo shown and its neighbours decoded ahead, bounded by
count and by bytes (192 MiB). A decode used to count only once it was shown (``get``), so
reads that finished but were never shown could hold more than the budget. Now a read
counts when it completes, and the bounds are enforced then: the oldest go first (the photo
shown before its neighbours were asked for, then the reads in the order asked), a read
still queued or running holds nothing, and the photo just decoded, kept or asked for stays.
"""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from app.gui.display_read_ahead import DisplayReadAhead, file_stamp

SHAPE = (30, 40, 3)
ONE = int(np.prod(SHAPE))  # bytes of one decoded photo
BUDGET = int(ONE * 2.5)  # room for two photos, not three


class StepExecutor:
    """Runs the queued jobs one at a time, when the test says so."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Future, object]] = []

    def submit(self, fn, *args):
        future: Future = Future()
        self.jobs.append((future, lambda: fn(*args)))
        return future

    def run_next(self) -> None:
        future, job = self.jobs.pop(0)
        if future.set_running_or_notify_cancel():
            future.set_result(job())

    def shutdown(self, wait=True, cancel_futures=False) -> None:
        pass


def _decode(path: str) -> np.ndarray:
    return np.zeros(SHAPE, np.uint8)


def _photos(tmp_path, n: int) -> list[str]:
    paths = []
    for i in range(n):
        path = tmp_path / f"{i}.png"
        path.write_bytes(b"photo")  # only stamped; _decode never reads it
        paths.append(str(path))
    return paths


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _held(ahead: DisplayReadAhead, paths: list[str]) -> int:
    """Bytes of the decoded photos held (every photo here is ONE bytes)."""
    return ONE * sum(1 for path in paths if ahead.is_ready(path))


def test_reads_never_shown_count_against_the_budget_once_they_finish(tmp_path):
    executor = StepExecutor()
    ahead = DisplayReadAhead(_decode, max_images=4, max_bytes=BUDGET, executor=executor)
    paths = _photos(tmp_path, 4)
    ahead.request(paths)

    for _ in paths:
        executor.run_next()
        assert _held(ahead, paths) <= BUDGET

    # The reads asked for first went first; the two decoded last are kept, ready.
    assert ahead.pending() == [_key(path) for path in paths[2:]]
    assert all(ahead.is_ready(path) for path in paths[2:])


def test_the_photo_shown_goes_before_the_neighbours_read_for_it(tmp_path):
    executor = StepExecutor()
    ahead = DisplayReadAhead(_decode, max_images=3, max_bytes=BUDGET, executor=executor)
    shown, following, previous = _photos(tmp_path, 3)
    ahead.keep(shown, file_stamp(shown), _decode(shown))
    ahead.request([following, previous])  # the display's order: next, then previous

    executor.run_next()
    assert ahead.pending() == [_key(shown), _key(following), _key(previous)]  # two fit

    executor.run_next()
    assert _held(ahead, [shown, following, previous]) <= BUDGET
    assert ahead.pending() == [_key(following), _key(previous)]
    # Both neighbours are handed over without a decode on the GUI thread.
    assert ahead.get(following) is not None and ahead.get(previous) is not None


def test_a_read_still_queued_holds_nothing_and_is_not_dropped_for_bytes(tmp_path):
    executor = StepExecutor()
    budget = int(ONE * 1.5)  # room for one photo
    ahead = DisplayReadAhead(_decode, max_images=3, max_bytes=budget, executor=executor)
    first, second, third = _photos(tmp_path, 3)
    ahead.request([first, second, third])

    executor.run_next()
    executor.run_next()  # the second tips the bound: the first goes, the third waits

    assert ahead.pending() == [_key(second), _key(third)]
    assert not executor.jobs[0][0].cancelled()
    assert _held(ahead, [first, second, third]) <= budget

    executor.run_next()
    assert ahead.pending() == [_key(third)]


def test_a_photo_over_the_budget_on_its_own_is_still_kept(tmp_path):
    executor = StepExecutor()
    ahead = DisplayReadAhead(_decode, max_images=3, max_bytes=ONE // 2, executor=executor)
    (path,) = _photos(tmp_path, 1)
    ahead.request([path])
    executor.run_next()

    assert ahead.pending() == [_key(path)]
    assert ahead.get(path) is not None


def test_the_bound_holds_when_reads_finish_on_the_worker_thread(tmp_path):
    pool = ThreadPoolExecutor(max_workers=1)
    ahead = DisplayReadAhead(_decode, max_images=5, max_bytes=BUDGET, executor=pool)
    paths = _photos(tmp_path, 5)
    try:
        ahead.request(paths)
        pool.shutdown(wait=True)  # every read finished, its bound check included

        assert _held(ahead, paths) <= BUDGET
        assert ahead.pending() == [_key(path) for path in paths[3:]]
    finally:
        ahead.close()

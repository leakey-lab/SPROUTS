"""ReadAhead keeps its byte budget when reads complete, and keeps the nearest neighbour.

``MAX_BYTES`` used to be checked only when a read was queued or put back: a queued read
holds nothing yet, so two neighbours of 8000 x 6000 (about 240 MB each: photo, mask and
skeleton) were both kept against a 256 MiB budget once they finished. The budget is now
also enforced when a read completes. What goes first under the budget is the read least
likely to be opened: entries outside the last plan (``keep_only``), then the plan's from its
end, so the nearest neighbour (the next image, which PgDn opens) stays. Qt-free.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import Executor, Future

import numpy as np

from app.gui.correct.read_ahead import AheadImage, ReadAhead, file_stamp
from tests.test_correct_read_ahead import InlineExecutor, Payload, photo


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


def entry(path: str, nbytes: int) -> AheadImage:
    return AheadImage(path, file_stamp(path), np.zeros((1, 1, 3), np.uint8), {"x": Payload(nbytes)})


def key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def test_reads_that_complete_over_the_budget_are_not_all_kept(tmp_path):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.request(a, lambda: entry(a, 60))
    ahead.request(b, lambda: entry(b, 60))  # both queued: nothing held yet

    executor.run_all()  # 120 bytes > 100 once both are done

    assert len(ahead.pending()) == 1


def test_the_nearest_neighbour_stays_when_both_do_not_fit(tmp_path):
    following, previous = photo(tmp_path, "c.png"), photo(tmp_path, "a.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.keep_only([following, previous])  # the plan, nearest first
    ahead.request(following, lambda: entry(following, 60))
    ahead.request(previous, lambda: entry(previous, 60))

    executor.run_all()

    assert ahead.pending() == [key(following)]
    assert ahead.take(following) is not None


def test_a_nearest_neighbour_read_last_still_stays(tmp_path):
    following, previous = photo(tmp_path, "c.png"), photo(tmp_path, "a.png")
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=InlineExecutor())
    ahead.put(entry(previous, 60))  # e.g. the image just left, handed back
    ahead.keep_only([following, previous])  # the next plan: the following image first

    ahead.request(following, lambda: entry(following, 60))  # done at once: 120 > 100

    assert ahead.pending() == [key(following)]


def test_without_a_plan_the_oldest_read_goes_first(tmp_path):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.request(a, lambda: entry(a, 60))
    ahead.request(b, lambda: entry(b, 60))

    executor.run_all()

    assert ahead.pending() == [key(b)]  # the newest stays, as when a read is queued


def test_a_put_back_read_is_the_newest_again(tmp_path):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=InlineExecutor())
    ahead.keep_only([a, b])
    ahead.request(a, lambda: entry(a, 60))

    ahead.put(entry(b, 60))  # a read the open did not use: kept, the plan is over

    assert ahead.pending() == [key(b)]


def test_a_read_alone_over_the_budget_is_kept(tmp_path):
    a = photo(tmp_path, "a.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.request(a, lambda: entry(a, 500))

    executor.run_all()

    assert ahead.take(a) is not None


def test_a_queued_read_is_not_dropped_for_bytes_it_does_not_hold(tmp_path):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.keep_only([a, b])
    ahead.request(a, lambda: entry(a, 500))
    executor.run_next()  # a is done, alone over the budget

    ahead.request(b, lambda: entry(b, 10))  # queued: it holds nothing yet

    assert ahead.pending() == [key(a), key(b)]
    executor.run_all()
    assert ahead.pending() == [key(a)]  # done: now it counts, and the plan keeps a


def test_reads_completing_on_the_worker_keep_the_budget(tmp_path):
    paths = [photo(tmp_path, f"{name}.png") for name in "ab"]
    release = threading.Event()
    ahead = ReadAhead(max_images=3, max_bytes=100)
    try:
        ahead.keep_only(paths)
        for path in paths:
            ahead.request(path, lambda path=path: release.wait(5) and entry(path, 60))
        release.set()
        deadline = time.perf_counter() + 5
        while len(ahead.pending()) > 1 and time.perf_counter() < deadline:
            time.sleep(0.005)
        assert ahead.pending() == [key(paths[0])]
    finally:
        release.set()
        ahead.close()


def test_a_read_completing_after_close_changes_nothing(tmp_path):
    a = photo(tmp_path, "a.png")
    executor = ManualExecutor()
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=executor)
    ahead.request(a, lambda: entry(a, 500))
    future = executor.queue[0][0]
    ahead.close()

    executor.run_all()  # cancelled by close: nothing runs, nothing is kept

    assert future.cancelled() and ahead.pending() == []


def test_a_failed_read_is_logged_once(tmp_path, caplog):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    ahead = ReadAhead(executor=InlineExecutor())

    def broken():
        raise OSError("disk gone")

    with caplog.at_level(logging.WARNING):
        ahead.request(a, broken)
        ahead.request(b, lambda: entry(b, 1))  # trims again: a is still failed
        ahead.put(entry(b, 1))

    assert caplog.text.count("read-ahead job failed") == 1
    assert ahead.take(a) is None

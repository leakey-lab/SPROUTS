"""R5: the out-of-memory backoff never retries a slice at a length that ran out of memory.

Before R5, ``OomBackoff`` retried the sibling half of a split even when it was as long as
the half that had just run out of memory (a batch of 16 that fits 4 attempted 16, 8, 4,
4, 8, 4, 4), and a lone image that ran out of memory was recorded as "ItemOutOfMemory:
OutOfMemoryError: ...". Now a slice of more than one image that is at least as long as
one that already ran out of memory is split untried; every shorter slice is tried as
before. Under a fixed out-of-memory threshold the slices that run, the lone-image
failures and the size kept for later batches are exactly the old halving's, so outputs
stay bit-identical. CPU only: a fake out-of-memory error is injected via ``is_oom``.
"""

from __future__ import annotations

import pytest
import torch

from app.inference.batch_engine import OomBackoff, run_batches
from tests.test_generation_batch_engine import _make_images, _spec


class _FakeOom(Exception):
    """Stands in for ``torch.cuda.OutOfMemoryError`` (injected through ``is_oom``)."""


def _backoff(batch_size):
    return OomBackoff(batch_size, is_oom=lambda exc: isinstance(exc, _FakeOom), on_oom=lambda: None)


def _step_fitting(limit, attempts):
    """A step that runs out of memory on more than ``limit`` items; records each attempt.

    It also fails the test when a slice of more than one item is tried although a slice
    at most as long already ran out of memory (a wasted retry).
    """
    shortest_oom = float("inf")

    def step(batch):
        nonlocal shortest_oom
        attempts.append(len(batch))
        assert len(batch) == 1 or len(batch) < shortest_oom, f"retried {len(batch)} items"
        if len(batch) > limit:
            shortest_oom = min(shortest_oom, len(batch))
            raise _FakeOom(f"{len(batch)} items do not fit")
        return list(batch)

    return step


def _legacy_halving(n, limit):
    """What the pre-R5 halving did with ``n`` items when ``limit`` of them fit at a time.

    Returns ``(slices that ran, items that failed alone, final size)``.
    """
    ran, failed, size = [], [], n

    def visit(start, stop):
        nonlocal size
        if stop - start <= limit:
            ran.append((start, stop))
        elif stop - start == 1:
            failed.append(start)
        else:
            half = (stop - start + 1) // 2
            size = min(size, half)
            visit(start, start + half)
            visit(start + half, stop)

    visit(0, n)
    return ran, failed, size


def test_the_sibling_half_is_not_retried_at_a_size_that_ran_out_of_memory():
    attempts = []
    backoff = _backoff(16)
    results = backoff.run(_step_fitting(4, attempts), list(range(16)))

    assert attempts == [16, 8, 4, 4, 4, 4]  # before R5: [16, 8, 4, 4, 8, 4, 4]
    assert [(s.start, s.stop) for s, _ in results] == [(0, 4), (4, 8), (8, 12), (12, 16)]
    assert backoff.size == 4


def test_a_shorter_sibling_half_is_still_tried_so_the_old_slices_run():
    attempts = []
    results = _backoff(7).run(_step_fitting(3, attempts), list(range(7)))

    assert [s.stop - s.start for s, _ in results] == [2, 2, 3]  # as before R5
    assert attempts == [7, 4, 2, 2, 3]  # 3 is shorter than the 4 that ran out of memory


def test_an_odd_batch_covers_every_item_once_in_order():
    attempts = []
    results = _backoff(7).run(_step_fitting(2, attempts), list(range(7)))

    assert [(s.start, s.stop) for s, _ in results] == [(0, 2), (2, 4), (4, 6), (6, 7)]
    assert [item for _, items in results for item in items] == list(range(7))
    assert attempts == [7, 4, 2, 2, 3, 2, 1]


@pytest.mark.parametrize("n", range(1, 41))
def test_under_a_fixed_threshold_the_old_halving_runs_unchanged(n):
    for limit in range(n + 1):  # 0: not even a single item fits
        backoff = _backoff(n)
        results = backoff.run(_step_fitting(limit, []), list(range(n)))

        ran = [(s.start, s.stop) for s, out in results if not isinstance(out, BaseException)]
        failed = [s.start for s, out in results if isinstance(out, BaseException)]
        assert [i for s, _ in results for i in range(s.start, s.stop)] == list(range(n)), limit
        assert (ran, failed, backoff.size) == _legacy_halving(n, limit), limit


def test_a_lone_image_out_of_memory_is_recorded_once_with_the_original_reason(tmp_path):
    paths = _make_images(str(tmp_path / "in"), ["a.png", "b.png", "c.png"])

    def step(x):
        raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")

    report = run_batches(paths, _spec(str(tmp_path / "out"), step=step), batch_size=2)

    reason = "OutOfMemoryError: CUDA out of memory (stub)"  # not "ItemOutOfMemory: ..."
    assert report.failed == [("a.png", reason), ("b.png", reason), ("c.png", reason)]
    assert report.written == []
    assert report.effective_batch_size == 1

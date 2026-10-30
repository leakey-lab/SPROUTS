"""R3: a progress callback that raises never changes what a batch run reports.

``batch_engine.run_batches`` counts progress once an output is on disk. The count ran
inside the save task's ``finally``, so a progress callback that raised (a UI object
already gone, a bug in a listener) made that task fail after its file was written:
the input was listed as both written and failed. The same callback raising while an
unreadable input was recorded ended the whole run with the callback's error, and in
Measure (``metrics.run_metric_pool``) it failed the run after every image had been
measured. Progress is advisory: a failing callback is now logged and ignored.
"""

from __future__ import annotations

import logging
import time

import pytest

from app.inference import batch_engine, metrics
from tests.test_generation_batch_engine import NAMES, _make_images, _spec


class _BrokenProgress:
    """A progress callback that raises on its first ``fail_first`` calls."""

    def __init__(self, fail_first=1):
        self.fail_first = fail_first
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("progress listener is gone (simulated)")


def test_a_raising_progress_callback_leaves_a_written_input_written(tmp_path, caplog):
    paths = _make_images(str(tmp_path / "in"), NAMES[:3])
    progress = _BrokenProgress()

    with caplog.at_level(logging.WARNING):
        report = batch_engine.run_batches(
            paths, _spec(str(tmp_path / "out")), batch_size=3, progress=progress
        )

    assert report.failed == []  # never both written and failed
    assert report.written == sorted(NAMES[:3])
    assert report.done == report.total == 3
    assert len(progress.calls) == 3  # later updates still reach the callback
    assert "progress listener is gone" in caplog.text  # logged, not swallowed silently


def test_a_raising_progress_callback_never_ends_the_run_on_an_unreadable_input(tmp_path):
    names = [*NAMES[:3], "bad.jpg"]
    paths = _make_images(str(tmp_path / "in"), names, corrupt={"bad.jpg"})
    progress = _BrokenProgress(fail_first=10)  # every call raises

    report = batch_engine.run_batches(
        paths, _spec(str(tmp_path / "out")), batch_size=2, progress=progress
    )

    assert report.failed_names == ["bad.jpg"]
    assert report.written == sorted(NAMES[:3])
    assert report.status == "completed" and report.done == report.total == 4


def test_a_raising_progress_callback_never_fails_a_measure_run(tmp_path):
    images = {f"img{i}": str(tmp_path / f"img{i}.png") for i in range(4)}
    progress = _BrokenProgress()

    rows = metrics.run_metric_pool(
        images, lambda name, path: {"Image": name, "Tube": None}, progress_cb=progress
    )

    assert sorted(row["Image"] for row in rows) == sorted(images)
    assert progress.calls[-1] == (100,)  # progress still reaches 100 %


def test_progress_updates_arrive_in_order_across_writer_threads(tmp_path):
    # Saves finish on several writer threads. The count was taken under a lock but the
    # callback ran after releasing it, so a slow delivery of an early count could land
    # after later ones: the progress bar went backwards and could end on "1 of 7".
    paths = _make_images(str(tmp_path / "in"), NAMES)
    applied = []

    def slow_first_update(done, total):
        if done == 1:
            time.sleep(0.2)  # e.g. the GUI thread busy when the first signal is emitted
        applied.append(done)

    batch_engine.run_batches(
        paths,
        _spec(str(tmp_path / "out")),
        batch_size=len(NAMES),
        io_threads=4,
        progress=slow_first_update,
    )

    assert applied == sorted(applied) and applied[-1] == len(NAMES)


@pytest.mark.parametrize("fail_first", [1, 3])
def test_progress_still_ends_at_the_total_after_a_failing_update(tmp_path, fail_first):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    progress = _BrokenProgress(fail_first=fail_first)

    batch_engine.run_batches(paths, _spec(str(tmp_path / "out")), batch_size=3, progress=progress)

    dones = [done for done, _total in progress.calls]
    assert dones == sorted(dones) and dones[-1] == len(NAMES)

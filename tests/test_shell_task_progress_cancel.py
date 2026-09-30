"""The status line keeps saying "Cancelling …" until a cancelled job has stopped (R-SHELL-B).

A worker checks for Cancel between batches, so the batch in flight still reports progress
after the click. Each report used to overwrite "Cancelling Generating masks…" with
"Generating masks — 1,235 / 46,178 · ETA 12 min" (Cancel greyed out), which read as if the
click had been ignored. Mask generation also restarts the task at each stage
(``begin_task`` per stage): a stage reported after the click re-enabled Cancel and dropped
the "Cancelling" text. Both now keep the cancelling state until the job ends.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.task_progress import TaskProgressWidget  # noqa: E402


class _Worker:
    def __init__(self) -> None:
        self.cancels = 0

    def cancel(self) -> None:
        self.cancels += 1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def progress(app):
    widget = TaskProgressWidget()
    yield widget
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_progress_after_cancel_keeps_the_cancelling_text(progress):
    worker = _Worker()
    progress.start("Generating masks", cancelable=True, on_cancel=worker.cancel)
    progress.set_counts(1_234, 46_178)
    progress.cancel_button.click()
    assert progress.label.text() == "Cancelling Generating masks…"

    progress.set_counts(1_235, 46_178)  # the batch in flight still reports
    progress.set_progress(3)
    assert progress.label.text() == "Cancelling Generating masks…"
    assert progress.bar.value() == 3  # the bar still follows the last batch
    assert not progress.cancel_button.isEnabled()
    assert worker.cancels == 1


def test_a_new_stage_of_the_cancelled_job_stays_cancelling(progress):
    worker = _Worker()
    progress.start("Loading the mask model", cancelable=True, on_cancel=worker.cancel)
    progress.cancel_button.click()
    # The worker reports its next stage before it sees the cancel (same job, same cancel).
    progress.start("Generating masks", cancelable=True, on_cancel=worker.cancel)
    assert progress.label.text() == "Cancelling Generating masks…"
    assert not progress.cancel_button.isEnabled()
    assert not progress.cancel_button.isHidden()
    assert worker.cancels == 1


def test_the_end_of_a_cancelled_job_is_shown_and_the_next_job_starts_clean(progress):
    worker = _Worker()
    progress.start("Generating skeletons", cancelable=True, on_cancel=worker.cancel)
    progress.cancel_button.click()
    progress.finish("Skeleton generation cancelled after 12 of 40 images")
    assert progress.label.text() == "Skeleton generation cancelled after 12 of 40 images"

    other = _Worker()
    progress.start("Measuring root length", cancelable=True, on_cancel=other.cancel)
    assert progress.cancel_button.isEnabled()
    progress.set_progress(5)
    assert progress.label.text() == "Measuring root length — 5%"


def test_another_job_after_a_cancel_is_not_shown_as_cancelling(progress):
    first, second = _Worker(), _Worker()
    progress.start("Generating masks", cancelable=True, on_cancel=first.cancel)
    progress.cancel_button.click()
    progress.fail("Mask generation failed")
    progress.start("Measuring root area", cancelable=True, on_cancel=second.cancel)
    assert progress.cancel_button.isEnabled()
    progress.set_counts(1, 10)
    assert progress.label.text().startswith("Measuring root area — 1 / 10")

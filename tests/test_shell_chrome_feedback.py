"""QtUiFeedback: the Qt UiFeedback over TaskProgressWidget / status bar / toasts."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QStatusBar  # noqa: E402

from app.gui.feedback import UiFeedback  # noqa: E402
from app.gui.task_progress import TaskProgressWidget  # noqa: E402
from app.gui.ui_feedback import QtUiFeedback  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def parts(app):
    tp = TaskProgressWidget()
    bar = QStatusBar()
    toasts = []
    refreshed = []
    fb = QtUiFeedback(
        tp,
        status_bar=bar,
        notifier=lambda msg, kind: toasts.append((msg, kind)),
        on_task_end=lambda: refreshed.append(1),
    )
    return fb, tp, bar, toasts, refreshed


def test_is_a_ui_feedback(parts):
    assert isinstance(parts[0], UiFeedback)


def test_counts_mode(parts):
    fb, tp, *_ = parts
    fb.task_started("Generating masks", total=46_178)
    fb.task_progress(1_234)
    assert tp.label.text().startswith("Generating masks — 1,234 / 46,178")
    assert tp.bar.value() == 2


def test_percent_mode(parts):
    fb, tp, *_ = parts
    fb.task_started("Calculating root length")
    fb.task_progress(42)
    assert tp.label.text() == "Calculating root length — 42%"


def test_explicit_total_wins(parts):
    fb, tp, *_ = parts
    fb.task_started("x", total=10)
    fb.task_progress(5, 20)
    assert tp.bar.value() == 25


def test_cancel_reaches_the_handler(parts):
    fb, tp, *_ = parts
    cancelled = []
    fb.task_started("Generating masks", cancelable=True, on_cancel=lambda: cancelled.append(1))
    tp.cancel_button.click()
    assert cancelled == [1]


def test_failure_is_shown_and_toasted(parts):
    fb, tp, _bar, toasts, refreshed = parts
    fb.task_started("x")
    fb.task_failed("Model weights missing")
    assert tp.label.text() == "Model weights missing"
    assert toasts == [("Model weights missing", "danger")]
    assert refreshed == [1]


def test_finish_status_and_notify(parts):
    fb, tp, bar, toasts, refreshed = parts
    fb.task_started("x")
    fb.task_finished("Done")
    assert tp.label.text() == "Done"
    fb.status("Saved", 1000)
    assert bar.currentMessage() == "Saved"
    fb.notify("Hi", "info")
    assert toasts == [("Hi", "info")]
    assert refreshed == [1]

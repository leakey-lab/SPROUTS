"""After Cancel the status line reads "<operation> — cancelling…" (KNOWN_ISSUES #7).

It read "Cancelling Generating masks…": the operation's own capital in mid-sentence. The
row already shows "<operation> — <progress>", so the cancelling state takes the place of
the progress.
"""

from __future__ import annotations

import pytest

from app.gui.task_progress import TaskProgressWidget
from tests.qt_teardown import delete_widgets


@pytest.fixture
def progress(app):
    made = [TaskProgressWidget()]
    yield made[0]
    delete_widgets(made, close=False)


def test_cancelling_follows_the_operation(progress):
    progress.start("Generating masks", cancelable=True, on_cancel=lambda: None)
    progress.set_counts(1_234, 46_178)
    progress.cancel_button.click()

    assert progress.label.text() == "Generating masks — cancelling…"


def test_cancelling_without_an_operation_name(progress):
    progress.start("", cancelable=True, on_cancel=lambda: None)
    progress.cancel_button.click()

    assert progress.label.text() == "Cancelling…"

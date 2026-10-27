"""tests/qt_wait.py: the shared wait and settle helpers of the GUI tests."""

import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QTimer

from tests.qt_wait import settle, wait_until

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_wait_until_returns_once_the_event_loop_makes_the_predicate_hold(app):
    fired = []
    QTimer.singleShot(30, lambda: fired.append(True))
    assert wait_until(lambda: fired) is True


def test_wait_until_gives_the_last_answer_after_the_timeout(app):
    started = time.monotonic()
    assert wait_until(lambda: [], timeout=0.05) == []
    assert time.monotonic() - started >= 0.05


def test_settle_runs_posted_events_and_the_zero_timers_they_start(app):
    ran = []
    QTimer.singleShot(0, lambda: QTimer.singleShot(0, lambda: ran.append(2)) or ran.append(1))
    settle()
    assert ran == [1, 2]


def test_importing_the_helpers_makes_no_application_and_no_window():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import tests.qt_wait, tests.window_support, tests.guards; "
        "from PyQt6.QtCore import QCoreApplication; "
        "print(QCoreApplication.instance() is None, 'app.gui.main_window' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "False"]

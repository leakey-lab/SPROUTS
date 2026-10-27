"""The status line tells the truth during and after long jobs (H6).

Before (1280 x 800, offscreen screenshots):

* after a folder loaded, the line kept saying "Ready" (the idle text is only re-read
  when a status message clears), and after a second folder it kept the first folder's
  count ("Ready · 40 images" with 768 loaded);
* while a job ran, the line said "Ready" next to the purple "working" dot and the job's
  own progress;
* after a failed job, the red dot stayed next to "Ready" once the failure row hid (the
  sticky danger toast already keeps the message).
"""

from __future__ import annotations

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QStatusBar

from app.gui import task_progress
from app.gui.shell_chrome import WORKING_TEXT, StatusLine
from app.gui.task_progress import TaskProgressWidget
from tests.qt_teardown import delete_widgets
from tests.qt_wait import wait_until as _wait
from tests.window_support import build_window, write_photos

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


def _line(idle=None):
    win = QMainWindow()
    line = StatusLine(idle_text=idle)
    win.setCentralWidget(line)
    bar = QStatusBar()
    win.setStatusBar(bar)
    progress = TaskProgressWidget()
    bar.addPermanentWidget(progress)
    line.adopt(bar, progress)
    return win, line, bar, progress


# ------------------------------------------------------------------ the widgets
def test_a_running_task_does_not_say_ready(app):
    _win, line, bar, progress = _line(idle=lambda: "Ready · 40 images")
    progress.start("Generating masks", cancelable=True)
    assert line.message.text() == WORKING_TEXT
    bar.showMessage("Loading the mask model…")
    assert line.message.text() == "Loading the mask model…"  # messages still win
    bar.clearMessage()
    assert line.message.text() == WORKING_TEXT
    progress.finish("Masks generated")
    assert line.message.text() == "Ready · 40 images"


def test_the_dot_returns_to_idle_when_a_failure_row_hides(app, monkeypatch):
    monkeypatch.setattr(task_progress, "FAIL_HIDE_MS", 40)
    _win, line, _bar, progress = _line()
    progress.start("Measuring")
    progress.fail("Measure failed: disk full")
    assert line.state() == "error"
    assert _wait(lambda: progress.isHidden())
    assert line.state() == "idle"


def test_a_new_task_before_the_failure_row_hides_keeps_working(app, monkeypatch):
    monkeypatch.setattr(task_progress, "FAIL_HIDE_MS", 40)
    _win, line, _bar, progress = _line()
    states = []
    progress.state_changed.connect(states.append)
    progress.start("first")
    progress.fail("nope")
    progress.start("second")
    QTest.qWait(150)
    QApplication.processEvents()
    assert states == ["working", "error", "working"]
    assert line.state() == "working"


def test_refresh_idle_rereads_the_provider_without_a_message(app):
    count = [40]
    _win, line, bar, _progress = _line(idle=lambda: f"Ready · {count[0]} images")
    count[0] = 768
    line.refresh_idle()
    assert line.message.text() == "Ready · 768 images"
    bar.showMessage("Saving…")
    count[0] = 5
    line.refresh_idle()
    assert line.message.text() == "Saving…"  # a message on show is kept


# ------------------------------------------------------------------ the window
def _folder(root, name: str, count: int) -> str:
    stems = [f"24EFCAM1_T001_L0{10 + i}_2024.07.18_1115{10 + i}_001_SMM" for i in range(count)]
    return str(write_photos(root / name, stems))


def test_the_window_title_names_the_app_and_the_project(app, tmp_path, no_prompts):
    """The title bar said "Root Viewer" (the app's old name) whatever was open."""
    window = build_window(tmp_path / "t.ini", size=None, show=False)
    try:
        assert window.windowTitle() == "SPROUTS[*]"
        window.open_path(_folder(tmp_path, "trial_B", 2))
        assert _wait(lambda: len(window.library_index.leaves()) == 2)
        assert window.windowTitle() == "trial_B — SPROUTS[*]"
    finally:
        delete_widgets([window])


def test_the_idle_text_follows_the_loaded_folder(app, tmp_path, no_prompts):
    window = build_window(tmp_path / "s.ini", size=None, show=False)
    try:
        window.show()
        window.open_path(_folder(tmp_path, "a", 3))
        assert _wait(lambda: len(window.library_index.leaves()) == 3)
        assert _wait(lambda: window.statusline_message.text() == "Ready · 3 images")

        window.open_path(_folder(tmp_path, "b", 5))
        assert _wait(lambda: len(window.library_index.leaves()) == 5)
        assert _wait(lambda: window.statusline_message.text() == "Ready · 5 images")
    finally:
        delete_widgets([window])

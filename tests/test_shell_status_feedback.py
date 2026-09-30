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

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStatusBar  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import task_progress  # noqa: E402
from app.gui.shell_chrome import WORKING_TEXT, StatusLine  # noqa: E402
from app.gui.task_progress import TaskProgressWidget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    """A loaded project schedules its stale-temp sweep 2 s later (R8) on an untracked timer;
    here it is a no-op, so a window of this test never sweeps inside a later test."""
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


def _wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


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
    folder = root / name
    folder.mkdir()
    img = QImage(32, 24, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 60, 30))
    for i in range(count):
        stem = f"24EFCAM1_T001_L0{10 + i}_2024.07.18_1115{10 + i}_001_SMM"
        assert img.save(str(folder / f"{stem}.jpg"))
    return str(folder)


def test_the_window_title_names_the_app_and_the_project(app, tmp_path, monkeypatch):
    """The title bar said "Root Viewer" (the app's old name) whatever was open."""
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    settings = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    try:
        assert window.windowTitle() == "SPROUTS[*]"
        window.open_path(_folder(tmp_path, "trial_B", 2))
        assert _wait(lambda: len(window.library_index.leaves()) == 2)
        assert window.windowTitle() == "trial_B — SPROUTS[*]"
    finally:
        window.close()
        window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_idle_text_follows_the_loaded_folder(app, tmp_path, monkeypatch):
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    try:
        window.show()
        window.open_path(_folder(tmp_path, "a", 3))
        assert _wait(lambda: len(window.library_index.leaves()) == 3)
        assert _wait(lambda: window.statusline_message.text() == "Ready · 3 images")

        window.open_path(_folder(tmp_path, "b", 5))
        assert _wait(lambda: len(window.library_index.leaves()) == 5)
        assert _wait(lambda: window.statusline_message.text() == "Ready · 5 images")
    finally:
        window.close()
        window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

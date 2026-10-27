"""OverlayLayout at teardown: Qt events that reach the layout while Python collects it.

A page, its canvas host and the host's OverlayLayout form a reference cycle (the layout
keeps ``_host``; the page keeps the chrome that keeps the layout). When Python's garbage
collector breaks that cycle it clears the layout wrapper's ``__dict__`` (sip ``tp_clear``)
while the C++ object, owned by the host, still exists and is still installed as an event
filter on the host and the overlays. Events delivered then (the host's resize, an overlay
hidden while the host is destroyed, a relayout queued just before) used to run
``eventFilter`` / ``_run_pending`` on an empty wrapper and print
``AttributeError: 'OverlayLayout' object has no attribute '_host'``. They must be ignored.

The same teardown order is why ``main.main`` deletes the window itself once the event loop
has returned, while the QApplication still exists: a window (and its editor pages) left to
the collector after the QApplication is gone sometimes died at interpreter exit with an
access violation (0xC0000005, about 2-5 % of runs with a Correct page open).
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QSize
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication, QFrame, QWidget

from app.gui.widgets import OverlayLayout


@pytest.fixture
def slot_errors(monkeypatch):
    """Exceptions raised in Qt virtuals / slots (PyQt hands them to ``sys.excepthook``)."""
    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: errors.append(value))
    return errors


def _host_with_layout():
    host = QWidget()
    host.resize(400, 300)
    dock = QFrame(host)
    dock.setMinimumSize(120, 30)
    layout = OverlayLayout(host)
    layout.register(dock=dock)
    host.show()
    QApplication.processEvents()
    return host, dock, layout


def _events_after_collection(host, dock) -> None:
    """What Qt delivers to the filter while the page is being torn down."""
    QApplication.sendEvent(host, QResizeEvent(QSize(410, 310), QSize(400, 300)))
    dock.hide()
    dock.show()
    QApplication.sendEvent(dock, QEvent(QEvent.Type.LayoutRequest))
    QApplication.processEvents()  # a relayout queued by the show / hide


def test_events_after_the_collector_cleared_the_layout_are_ignored(app, slot_errors):
    host, dock, layout = _host_with_layout()
    dock.hide()  # queues a relayout on the next loop pass (_run_pending)
    layout.__dict__.clear()  # what the collector's tp_clear does to the wrapper

    _events_after_collection(host, dock)

    assert slot_errors == []
    host.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_collecting_a_page_cycle_while_its_host_is_shown_raises_nothing(app, slot_errors):
    class Page(QWidget):
        """A page that keeps its canvas host's layout (the editors' chrome does)."""

    for _ in range(20):  # the collector's order within the cycle varies
        page = Page()
        page.resize(400, 300)
        page.host = QWidget(page)
        page.host.resize(400, 300)
        page.dock = QFrame(page.host)
        page.dock.setMinimumSize(120, 30)
        page.layout_ = OverlayLayout(page.host)
        page.layout_.register(dock=page.dock)
        page.layout_.page = page  # the cycle: page -> layout -> page
        page.show()
        QApplication.processEvents()
        page.dock.hide()  # a relayout still queued when the page goes
        del page
        gc.collect()
        QApplication.processEvents()

    assert slot_errors == []


def test_a_live_layout_still_follows_its_host(app, slot_errors):
    host, dock, layout = _host_with_layout()
    before = dock.geometry()

    host.resize(800, 600)
    QApplication.processEvents()

    assert dock.geometry() != before  # the dock was placed again for the new size
    assert slot_errors == []
    host.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ------------------------------------------------------------------ main.main teardown
REPO_ROOT = Path(__file__).resolve().parents[1]

_TEARDOWN_DRIVER = """
import sys
sys.path.insert(0, {repo!r})
import main
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow

class StubWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        QTimer.singleShot(50, self.close)  # the user closes the window

main.MainWindow = StubWindow
rc = main.main(["sprouts"])
app, window = main._session
print("RC", rc)
print("APP ALIVE", QApplication.instance() is app and not sip.isdeleted(app))
print("WINDOW DELETED", sip.isdeleted(window))
"""


def _run_main(code: str, tmp_path, timeout: int = 120) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    env.update(
        SPROUTS_LOG_DIR=str(tmp_path / "logs"),
        SPROUTS_CONFIG_DIR=str(tmp_path / "cfg"),
        ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT="0",
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_main_deletes_the_window_while_the_application_still_exists(tmp_path):
    proc = _run_main(_TEARDOWN_DRIVER.format(repo=str(REPO_ROOT)), tmp_path)

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert "RC 0" in out
    assert "APP ALIVE True" in out
    assert "WINDOW DELETED True" in out


_EXIT_DRIVER = """
import sys
sys.path.insert(0, {repo!r})
from tests.correct_goldens_support import make_synth_project
folder, image = make_synth_project({root!r}, "synth_model_mask_and_skeleton")
import main
from PyQt6.QtCore import QTimer

class Window(main.MainWindow):
    def __init__(self):
        super().__init__()
        QTimer.singleShot(0, self._open_then_close)

    def _open_then_close(self):
        self.correct_editor.load_image(image, folder)
        self.toggle_correct()
        QTimer.singleShot(30, self.close)

main.MainWindow = Window
sys.exit(main.main(["sprouts"]))
"""


@pytest.mark.skipif(
    os.environ.get("SPROUTS_PERF_TESTS") != "1",
    reason="about 2 s per fresh app; opt-in with SPROUTS_PERF_TESTS=1",
)
def test_closing_the_app_with_a_correct_page_open_never_crashes_at_exit(tmp_path):
    runs = int(os.environ.get("SPROUTS_EXIT_RUNS", "40"))
    codes = []
    for index in range(runs):
        root = tmp_path / f"run{index}"
        root.mkdir()
        code = _EXIT_DRIVER.format(repo=str(REPO_ROOT), root=str(root))
        proc = _run_main(code, root)
        codes.append(proc.returncode)
        assert "Traceback" not in proc.stderr, proc.stderr
    assert codes == [0] * runs  # 0xC0000005 is 3221225477

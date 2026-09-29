"""Welcome screen: dropped folder opens, recents scroll, stale recents, real stages, version."""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PyQt6.QtGui import QDropEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

from app.gui.recent_projects import RecentProjectsStore  # noqa: E402
from app.gui.welcome_screen import WelcomeWidget  # noqa: E402


class _FakeSettings:
    def __init__(self, entries=()):
        self.data = {"recent_projects": json.dumps(list(entries))}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.data[key] = value


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _store(paths):
    entries = [
        {"name": os.path.basename(p), "path": p, "count": 10, "ts": "2026-09-01T10:00:00"}
        for p in paths
    ]
    return RecentProjectsStore(_FakeSettings(entries))


class _Spy:
    def __init__(self):
        self.started = 0
        self.opened: list[str] = []

    def start(self):
        self.started += 1

    def open(self, path):
        self.opened.append(path)


def _welcome(spy, store=None, **kw):
    return WelcomeWidget(
        spy.start,
        spy.open,
        recent_store=store or _store([]),
        probe_async=False,
        **kw,
    )


def _drop(widget, urls):
    mime = QMimeData()
    mime.setUrls(urls)
    event = QDropEvent(
        QPointF(QPoint(5, 5)),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)
    return event


def test_dropping_a_folder_opens_that_folder(app, tmp_path):
    """UI-13 / PG-11: the drop used to open the Browse dialog and ignore the folder."""
    spy = _Spy()
    w = _welcome(spy)
    _drop(w.dropzone, [QUrl.fromLocalFile(str(tmp_path))])
    assert [os.path.normcase(p) for p in spy.opened] == [os.path.normcase(str(tmp_path))]
    assert spy.started == 0


def test_dropping_a_file_opens_its_folder(app, tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    spy = _Spy()
    _drop(_welcome(spy).dropzone, [QUrl.fromLocalFile(str(f))])
    assert [os.path.normcase(p) for p in spy.opened] == [os.path.normcase(str(tmp_path))]


def test_dropping_a_remote_url_is_ignored(app):
    spy = _Spy()
    _drop(_welcome(spy).dropzone, [QUrl("https://example.org/x")])
    assert spy.opened == [] and spy.started == 0


def test_eight_recents_do_not_raise_the_minimum_height(app, tmp_path):
    """UXS-14: 8 rows pushed the Welcome minimum height to 823 px."""
    spy = _Spy()
    w = _welcome(spy, _store([str(tmp_path / f"p{i}") for i in range(8)]))
    assert w.findChild(QScrollArea, "recentScroll") is not None
    assert w.minimumSizeHint().height() <= 560


def test_recent_rows_are_accessible_and_open(app, tmp_path):
    spy = _Spy()
    w = _welcome(spy, _store([str(tmp_path)]))
    rows = w.recent_rows()
    assert len(rows) == 1
    assert rows[0].open_button.accessibleName()
    assert str(tmp_path) in rows[0].open_button.toolTip()
    rows[0].open_button.click()
    assert spy.opened == [str(tmp_path)]


def test_missing_recent_is_flagged_and_removable(app, tmp_path):
    gone = str(tmp_path / "gone")
    store = _store([gone, str(tmp_path)])
    w = _welcome(_Spy(), store)
    missing, present = w.recent_rows()
    assert missing.is_missing() is True and not missing.open_button.isEnabled()
    assert present.is_missing() is False and present.open_button.isEnabled()
    missing.remove_button.click()
    assert [e.path for e in store.entries()] == [str(tmp_path)]
    assert len(w.recent_rows()) == 1


def test_existence_probe_runs_off_the_gui_thread(app, tmp_path):
    import threading
    import time

    seen_threads = []

    def exists(path):
        seen_threads.append(threading.current_thread())
        return False

    w = WelcomeWidget(
        lambda: None,
        lambda p: None,
        recent_store=_store([str(tmp_path)]),
        path_exists=exists,
    )
    deadline = time.monotonic() + 5
    while not w.recent_rows()[0].is_missing() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert w.recent_rows()[0].is_missing() is True
    assert seen_threads and seen_threads[0] is not threading.main_thread()


def test_stage_chips_match_the_ribbon(app):
    from app.gui import shell_chrome

    w = _welcome(_Spy())
    assert w.stage_labels() == [s[1] for s in shell_chrome._STAGES]


def test_footer_shows_the_real_version_and_no_hardcoded_gpu(app):
    from app import __version__

    w = _welcome(_Spy())
    footer = w.findChild(QLabel, "welcomeFooter").text()
    assert f"v{__version__}" in footer
    assert "v2.0" not in footer and "CUDA 12.8" not in footer


def test_deleting_the_welcome_while_probing_is_safe(app, tmp_path):
    import threading
    import time

    from PyQt6 import sip

    release = threading.Event()

    def slow_exists(path):
        release.wait(5)
        return True

    w = WelcomeWidget(
        lambda: None, lambda p: None, recent_store=_store([str(tmp_path)]), path_exists=slow_exists
    )
    sip.delete(w)
    release.set()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

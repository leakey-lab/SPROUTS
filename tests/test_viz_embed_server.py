"""viz-embed: the Dash server thread and QThread disposal (app/visualization/_viz_server.py).

A tiny WSGI callable stands in for the Dash app (no dash import); the server runs
on real loopback sockets. Checks that the server binds 127.0.0.1, falls back to a
free port when the preferred one is taken (PV-10), reports failures instead of
dying silently, stops without the 0.5 s accept-timeout stall (PV-03), and that
``release_qthread`` never destroys a running QThread (RB-08).
"""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QThread

from app.visualization import _viz_server
from app.visualization._viz_server import _DashServerThreadBase, join_qthread

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def wait_until(predicate, timeout: float = 5.0) -> bool:
    """Pump Qt events (and deferred deletes) until ``predicate()`` or the timeout."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class _StubDash:
    """What the server thread uses of a DashApp: ``.app.server``, a WSGI callable."""

    class app:  # noqa: N801 - mirrors DashApp.app
        @staticmethod
        def server(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2")])
            return [b"ok"]


def _get(port: int) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _closed_port() -> int:
    """A port nothing listens on (bound once, then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def start_server(qapp):
    started = []

    def start(port: int):
        thread = _DashServerThreadBase(_StubDash(), port)
        events = {"ports": [], "errors": []}
        thread.port_assigned.connect(events["ports"].append)
        thread.error.connect(events["errors"].append)
        thread.start()
        started.append(thread)
        return thread, events

    yield start
    for thread in started:
        thread.stop()
        join_qthread(thread, 5000)


def test_server_reports_the_port_it_actually_bound(start_server):
    thread, events = start_server(0)  # 0 = let the OS choose

    assert wait_until(lambda: events["ports"] or events["errors"])
    assert events["errors"] == []
    port = events["ports"][0]
    assert port > 0
    assert thread.port == port
    assert _get(port) == (200, b"ok")


def test_busy_preferred_port_falls_back_to_a_free_port(start_server):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        thread, events = start_server(busy)

        assert wait_until(lambda: events["ports"] or events["errors"])
        assert events["errors"] == []
        port = events["ports"][0]
        assert port not in (0, busy)
        assert _get(port) == (200, b"ok")
    finally:
        blocker.close()


def test_port_held_by_another_werkzeug_server_is_not_shared(start_server):
    """Another Dash/werkzeug app (or a second SPROUTS) on the preferred port.

    werkzeug sets SO_REUSEADDR, which on Windows lets a second socket bind a
    port that another SO_REUSEADDR socket already listens on; the two servers
    then share the port and the dashboard page came from the wrong one.
    """
    from werkzeug.serving import make_server

    def other_app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "5")])
        return [b"other"]

    other = make_server("127.0.0.1", 0, other_app, threaded=True)
    busy = other.server_port
    try:
        thread, events = start_server(busy)

        assert wait_until(lambda: events["ports"] or events["errors"])
        assert events["errors"] == []
        port = events["ports"][0]
        assert port != busy
        assert _get(port) == (200, b"ok")
    finally:
        other.server_close()


def test_unbindable_server_reports_an_error_and_finishes(start_server, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise SystemExit(1)  # what werkzeug's make_server does when bind() fails

    monkeypatch.setattr(_viz_server, "_make_server", refuse)
    thread, events = start_server(0)

    assert wait_until(lambda: events["errors"])
    assert thread.wait(2000)
    assert events["ports"] == []


def test_stop_wakes_the_accept_loop_promptly(start_server):
    thread, events = start_server(_closed_port())  # a fixed free port, like 8050
    assert wait_until(lambda: events["ports"])

    t0 = time.perf_counter()
    thread.stop()
    assert join_qthread(thread, 5000)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2  # was ~0.5 s: the wake-up connect hit an already closed socket
    assert events["errors"] == []


def test_stop_before_the_server_listens_returns_at_once(qapp):
    thread = _DashServerThreadBase(_StubDash(), _closed_port())  # never started

    t0 = time.perf_counter()
    thread.stop()

    assert time.perf_counter() - t0 < 0.1


def test_release_qthread_deletes_a_finished_thread(qapp):
    thread = QThread()
    thread.start()
    thread.quit()
    assert thread.wait(2000)
    destroyed = []
    thread.destroyed.connect(lambda *_: destroyed.append(True))

    _viz_server.release_qthread(thread)

    assert wait_until(lambda: destroyed, 2.0)


def _run_python(code: str, timeout: float = 120) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_release_qthread_does_not_block_and_outlives_dropped_references():
    # In a subprocess: destroying a running QThread aborts the whole process.
    result = _run_python("""
        import sys, time
        from PyQt6.QtCore import QCoreApplication, QThread, QTimer, pyqtSignal
        from app.visualization._viz_server import release_qthread

        app = QCoreApplication(sys.argv)

        class Sleeper(QThread):
            finished = pyqtSignal(str)  # shadows QThread.finished, like the app's workers

            def run(self):
                time.sleep(0.3)
                self.finished.emit("done")

        destroyed = []
        thread = Sleeper()
        thread.destroyed.connect(lambda *_: destroyed.append(1))
        thread.destroyed.connect(app.quit)  # quits once deleted (after the append above)
        thread.start()
        t0 = time.perf_counter()
        release_qthread(thread)
        blocked = time.perf_counter() - t0
        del thread  # the helper must keep the running thread alive itself
        QTimer.singleShot(1500, app.quit)  # the deadline: a leaked thread ends with destroyed=0
        app.exec()
        print(f"blocked={blocked:.3f} destroyed={len(destroyed)}")
    """)
    assert result.returncode == 0, result.stderr[-2000:]
    blocked, destroyed = result.stdout.split()
    assert float(blocked.split("=")[1]) < 0.05
    assert destroyed == "destroyed=1"


def test_releasing_in_a_custom_finished_slot_never_aborts():
    # RB-08: the custom finished signal is the last statement of run(); dropping the
    # only reference in its slot raced QThread shutdown (~1 abort per 8,000 runs).
    result = _run_python("""
        import sys
        from PyQt6.QtCore import QCoreApplication, QThread, QTimer, pyqtSignal
        from app.visualization._viz_server import release_qthread

        app = QCoreApplication(sys.argv)
        N = 2000

        class Quick(QThread):
            finished = pyqtSignal(str)

            def run(self):
                self.finished.emit("x")

        class Owner:
            thread = None
            done = 0

            def launch(self):
                if self.done >= N:
                    print("survived", self.done)
                    app.quit()
                    return
                self.thread = Quick()
                self.thread.finished.connect(self.on_finished)
                self.thread.start()

            def on_finished(self, _value):
                release_qthread(self.thread)
                self.thread = None
                self.done += 1
                QTimer.singleShot(0, self.launch)

        owner = Owner()
        QTimer.singleShot(0, owner.launch)
        app.exec()
    """)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "survived 2000"

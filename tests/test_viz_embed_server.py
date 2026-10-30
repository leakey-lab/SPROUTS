"""viz-embed: the Dash server thread (app/visualization/_viz_server.py).

A tiny WSGI callable stands in for the Dash app (no dash import); the server runs
on real loopback sockets. Checks that the server binds 127.0.0.1, falls back to a
free port when the preferred one is taken (PV-10), reports failures instead of
dying silently (anything its serve loop raises, through ``GuardedThread``), and
stops without the 0.5 s accept-timeout stall (PV-03). ``release_qthread`` is
tested with the thread keeper (tests/test_workers_thread_keeper.py).
"""

from __future__ import annotations

import http.client
import socket
import threading
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt

from app.visualization import _viz_server
from app.visualization._viz_server import _DashServerThreadBase, join_qthread
from app.workers import GuardedThread, kept_threads


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
        events = {"ports": [], "errors": [], "emitted_on": []}
        thread.port_assigned.connect(events["ports"].append)
        thread.error.connect(events["errors"].append)
        thread.error.connect(  # where ``error`` is emitted (its slots above are queued)
            lambda _message: events["emitted_on"].append(threading.current_thread()),
            Qt.ConnectionType.DirectConnection,
        )
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
    assert events["errors"] == ["no free port on 127.0.0.1 for the dashboard server"]
    assert events["emitted_on"] != [threading.main_thread()]  # on the server thread


def test_the_server_thread_runs_inside_the_worker_error_boundary():
    """One error boundary for every QThread of the app, and the keeper from its start."""
    assert issubclass(_DashServerThreadBase, GuardedThread)
    assert _DashServerThreadBase.run is GuardedThread.run


class _ServerThatRaises:
    """What the serve loop uses of werkzeug's server; ``handle_request`` raises ``exc``."""

    server_port = 4321

    def __init__(self, exc: BaseException) -> None:
        self.exc, self.timeout, self.closed = exc, None, False

    def handle_request(self) -> None:
        raise self.exc

    def server_close(self) -> None:
        self.closed = True


class _Odd(BaseException):
    """Neither an Exception nor an interrupt, as a library may raise."""


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), SystemExit(3), KeyboardInterrupt(), _Odd("odd")],
    ids=["error", "exit", "interrupt", "other"],
)
def test_anything_the_serve_loop_raises_is_reported_and_the_thread_ends(
    start_server, monkeypatch, slot_errors, exc
):
    """Its run() caught ``Exception`` only: anything else went to ``sys.excepthook`` and the
    dashboard waited for the start-up timeout. The message is the exception's text, else
    its type name; the socket is closed."""
    server = _ServerThatRaises(exc)
    monkeypatch.setattr(_viz_server, "_make_server", lambda *_args, **_kwargs: server)
    thread, events = start_server(0)

    assert wait_until(lambda: events["errors"])
    assert thread.wait(2000)
    assert events["ports"] == [4321]  # it listened, then the loop raised
    assert events["errors"] == [str(exc) or type(exc).__name__]
    assert events["emitted_on"] != [threading.main_thread()]
    assert server.closed


def test_the_server_thread_is_kept_from_its_start_until_qt_says_it_finished(start_server):
    thread, events = start_server(0)
    assert wait_until(lambda: events["ports"])
    assert thread in kept_threads() and thread.parent() is None

    thread.stop()
    assert join_qthread(thread, 5000)
    assert wait_until(lambda: thread not in kept_threads())


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

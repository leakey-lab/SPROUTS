"""
Server thread and QThread lifetime helpers for the embedded Dash dashboards.

``_DashServerThreadBase`` serves one Dash app with werkzeug on ``DASH_HOST``
(loopback only) from a QThread; the per-metric subclasses live next to their
windows. ``join_qthread`` and ``release_qthread`` end QThreads without ever
destroying one that is still running.

Names here are private to :mod:`app.visualization`; the public classes stay in
their own modules.
"""

from __future__ import annotations

import logging
import socket
import threading

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from app.config import DASH_HOST
except ImportError:  # pragma: no cover - config predating DASH_HOST
    DASH_HOST = "127.0.0.1"

logger = logging.getLogger(__name__)

#: Seconds the idle accept loop waits before it re-checks the stop flag.
_ACCEPT_TIMEOUT_S = 0.5
#: Upper bound for the wake-up connection that stop() makes on the GUI thread.
_WAKE_TIMEOUT_S = 0.25


#: Windows-only option that forbids any other socket from binding our address.
_EXCLUSIVE_ADDR = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)


def _make_server(host: str, port: int, wsgi_app):
    """werkzeug's threaded WSGI server bound to ``host:port`` (listening on return).

    Imported here, on the server thread, so starting the app never pays for
    werkzeug. On a bind failure werkzeug prints the reason and raises
    ``SystemExit`` instead of ``OSError``.

    The server is threaded, which is load-bearing for a clean shutdown, not
    just speed: QWebEngineView keeps a keep-alive connection open, and a
    single-threaded server would sit in that connection's readline, where no
    stop request can reach it. With threads each connection has its own daemon
    thread and the accept loop stays responsive to
    :meth:`_DashServerThreadBase.stop`.

    werkzeug sets ``SO_REUSEADDR``, which on Windows means "share this port":
    a second server could bind a port another SO_REUSEADDR server (any Dash
    app, or a second SPROUTS) already listens on, and pages then came from the
    wrong server. On Windows the socket takes ``SO_EXCLUSIVEADDRUSE`` instead,
    so a taken port is refused and :meth:`_DashServerThreadBase._bind` moves
    to a free one. Elsewhere SO_REUSEADDR never shares a listening port.
    """
    from werkzeug.serving import ThreadedWSGIServer

    class _LoopbackWSGIServer(ThreadedWSGIServer):
        allow_reuse_address = _EXCLUSIVE_ADDR is None

        def server_bind(self) -> None:
            if _EXCLUSIVE_ADDR is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, _EXCLUSIVE_ADDR, 1)
            super().server_bind()

    return _LoopbackWSGIServer(host, port, wsgi_app)


def join_qthread(thread: QThread | None, timeout_ms: int = 10000) -> bool:
    """Block until *thread* finishes; return False if it is still running."""
    if thread is None:
        return True
    if thread.isRunning() and not thread.wait(timeout_ms):
        logger.warning(
            "%s did not stop within %dms",
            thread.objectName() or "QThread",
            timeout_ms,
        )
        return False
    return True


# Threads handed to release_qthread() while still running, held (strong
# references) until Qt reports them finished. Touched on the GUI thread only.
_finishing: set[QThread] = set()


def release_qthread(thread: QThread | None) -> None:
    """Delete *thread* once it has finished, without blocking the caller.

    Safe in a slot of a custom "finished" signal emitted at the end of
    ``run()`` (RB-08): that thread is still running when the slot runs, and
    dropping its last reference there destroyed a running QThread ("QThread:
    Destroyed while thread '' is still running", then abort). A running thread
    is unparented and kept alive here until Qt's own ``QThread.finished`` fires
    (reached through the base class, because subclasses shadow ``finished``
    with their own signal); a thread that is not running is deleted at once.
    """
    if thread is None or thread in _finishing:
        return
    if thread.isRunning():
        thread.setParent(None)
        _finishing.add(thread)
        QThread.finished.__get__(thread, QThread).connect(lambda: _dispose(thread))
        if thread.isRunning():
            return
        # It finished between the check and the connect; the signal may be gone.
    thread.wait()  # not running: returns at once (at most the end of finish())
    if thread in _finishing:
        _dispose(thread)
    else:
        thread.deleteLater()


def _dispose(thread: QThread) -> None:
    """Drop a released thread once; later calls are no-ops."""
    if thread in _finishing:
        _finishing.discard(thread)
        thread.deleteLater()


class _DashServerThreadBase(QThread):
    """Serves one Dash app on ``host`` (loopback) until :meth:`stop`.

    ``port`` is the preferred port. When it is taken (another app, or a second
    SPROUTS) the server binds any free port instead (PV-10). ``port_assigned``
    announces the port actually bound; the socket already listens then, so the
    page can be loaded at once, with no readiness polling (PV-02). ``error``
    reports a server that could not start or stopped unexpectedly.
    """

    error = pyqtSignal(str)
    port_assigned = pyqtSignal(int)

    def __init__(
        self,
        dash_app,
        port: int,
        *,
        host: str = DASH_HOST,
        object_name: str = "DashServerThread",
    ):
        super().__init__()
        self.setObjectName(object_name)
        self.dash_app = dash_app
        self.host = host
        self.port = port
        self.server = None
        self._stop_event = threading.Event()
        self._listening = threading.Event()

    def run(self) -> None:
        try:
            self._serve()
        except Exception as exc:  # noqa: BLE001 -- a thread reports errors, never raises them
            logger.exception("%s failed", self.objectName())
            self.error.emit(str(exc) or type(exc).__name__)

    def _serve(self) -> None:
        server = self._bind()
        self.server = server
        server.timeout = _ACCEPT_TIMEOUT_S
        self.port = server.server_port
        self._listening.set()
        try:
            self.port_assigned.emit(self.port)
            while not self._stop_event.is_set():
                server.handle_request()
        finally:
            self._listening.clear()
            server.server_close()

    def _bind(self):
        """A server on the preferred port, else on a free one; ``OSError`` if neither."""
        for port in (self.port, 0) if self.port else (0,):
            try:
                return _make_server(self.host, port, self.dash_app.app.server)
            except (OSError, SystemExit) as exc:
                logger.warning("Dash server cannot bind %s:%d (%r)", self.host, port, exc)
        raise OSError(f"no free port on {self.host} for the dashboard server")

    def stop(self) -> None:
        """Ask the serve loop to exit; returns at once (join with :func:`join_qthread`)."""
        logger.debug("Stopping %s", self.objectName())
        self._stop_event.set()
        if not self._listening.is_set():
            return  # not listening (yet or any more): the loop sees the flag by itself
        # Wake handle_request() out of its accept wait while the socket still
        # listens (PV-03). Closing the socket first, as before, made this
        # connect fail, so every stop waited out the 0.5 s accept timeout.
        # run() closes the socket itself once the loop has exited.
        try:
            with socket.create_connection((self.host, self.port), timeout=_WAKE_TIMEOUT_S):
                pass
        except OSError as exc:
            logger.debug("Wake-up connection to %s:%d failed: %s", self.host, self.port, exc)

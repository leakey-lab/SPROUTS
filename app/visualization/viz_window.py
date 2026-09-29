"""
Embedded Dash dashboard window: the lifecycle shared by root length and area.

A dashboard opens in three steps, none of which blocks the GUI thread:

1. a worker thread reads the CSV and builds the Dash app, so dash, plotly and
   pandas are imported off the GUI thread and only when a dashboard opens;
2. a server thread serves the app on ``DASH_HOST`` (its preferred port, or any
   free one) and reports the port it bound;
3. the web view loads ``http://DASH_HOST:port/`` at once (the socket already
   listens) and replaces the status text when the page has loaded.

A failure at any step replaces the page with a message and sets ``failed``;
the panel stays open so the message can be read. Subclasses only state what
differs: title, export prefix, preferred port and server-thread class, and how
to build ``(processor, dash_app)``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from app.gui.widgets import tokens

from ._viz_server import DASH_HOST, _DashServerThreadBase, join_qthread, release_qthread

logger = logging.getLogger(__name__)

#: Runs on the worker thread and returns ``(processor, dash_app)``.
Builder = Callable[[], tuple[object, object]]

#: Longest wait (GUI thread) for a stopped server thread before it is left to finish alone.
_SERVER_JOIN_MS = 2000


def require_rows(processor):
    """Return *processor*, or raise when its CSV has no usable rows."""
    if processor.df.empty:
        raise ValueError("No data found in CSV file")
    return processor


def unique_path(directory: str, filename: str) -> str:
    """``directory/filename``, or ``stem_1.ext``, ``stem_2.ext``, ... when taken."""
    path = os.path.join(directory, filename)
    stem, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = f"{stem}_{counter}{ext}"
        counter += 1
    return path


def _disconnect(signal, slot=None) -> None:
    """Disconnect *slot* (or every slot) from *signal*; a no-op when not connected."""
    try:
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


class DashInitWorker(QThread):
    """Runs a dashboard build (CSV -> processor -> Dash app) off the GUI thread."""

    built = pyqtSignal(object, object)  # processor, dash_app
    failed = pyqtSignal(str)

    def __init__(self, build: Builder, parent: QObject | None = None):
        super().__init__(parent)
        self._build = build

    def run(self) -> None:
        try:
            processor, dash_app = self._build()
        except Exception as exc:  # noqa: BLE001 -- reported to the window, never raised
            logger.exception("Dashboard initialisation failed")
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.built.emit(processor, dash_app)


class _Stage(Enum):
    BUILDING = auto()  # the worker reads the CSV and builds the Dash app
    STARTING = auto()  # the server thread binds its port
    LOADING = auto()  # the web view loads the page
    SHOWN = auto()
    FAILED = auto()
    CLOSED = auto()


class DashVizWindow(QMainWindow):
    """One embedded Dash dashboard, from reading its CSV to stopping its server."""

    #: Emitted once by :meth:`cleanup_server` when it stopped a running server.
    server_closed = pyqtSignal()
    #: Path of an export (Plotly camera button) saved next to the CSV.
    export_saved = pyqtSignal(str)

    window_title = "Visualization"
    download_prefix = "plot"
    #: Preferred server port (another free port is used when it is taken).
    default_port = 0
    server_thread_class: type[_DashServerThreadBase] = _DashServerThreadBase
    startup_timeout_ms = 15000

    def __init__(self, csv_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path
        self.save_directory = os.path.dirname(os.path.abspath(csv_path))
        self.port = self.default_port
        self.processor = None
        self.dash_app = None
        self.init_worker: DashInitWorker | None = None
        self.server_thread: _DashServerThreadBase | None = None
        self.web_view: QWidget | None = None
        self._page = None
        self._exports: dict[QObject, str] = {}
        self._stage = _Stage.BUILDING
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._on_startup_timeout)
        self.setWindowTitle(self.window_title)
        self._init_ui()
        if self._stage is _Stage.BUILDING:
            self._start_build()

    # ------------------------------------------------------------------ hooks
    def _dashboard_builder(self) -> Builder:
        """Callable run on the worker thread returning ``(processor, dash_app)``.

        It must capture plain values only and never touch Qt widgets.
        """
        raise NotImplementedError

    def _create_web_view(self) -> QWidget:
        """The browser widget that shows the dashboard page."""
        return QWebEngineView()

    # ------------------------------------------------------------------ state
    @property
    def url(self) -> str:
        """Address of this dashboard's page (valid once the server has bound)."""
        return f"http://{DASH_HOST}:{self.port}/"

    @property
    def failed(self) -> bool:
        """True when the dashboard could not be shown (the message is on the page)."""
        return self._stage is _Stage.FAILED

    # ------------------------------------------------------------------ ui
    def _init_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        self.loading_label = QLabel(central)
        self.loading_label.setObjectName("dashboardStatus")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setTextFormat(Qt.TextFormat.PlainText)
        self.loading_label.setWordWrap(True)
        self.loading_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.loading_label)
        self._set_status("Loading data…")

        try:
            view = self._create_web_view()
        except Exception as exc:  # noqa: BLE001 -- WebEngine can fail to start (GPU, sandbox)
            logger.exception("Qt WebEngine could not start")
            self._fail(f"The embedded browser (Qt WebEngine) could not start:\n{exc}")
            return
        view.hide()
        self._page = view.page()
        # Paint the page background dark before the first frame (no white flash).
        self._page.setBackgroundColor(QColor(tokens.BG_0))
        self._page.profile().downloadRequested.connect(self.handle_download)
        view.loadFinished.connect(self._on_load_finished)
        layout.addWidget(view)
        self.web_view = view

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = tokens.TEXT if error else tokens.TEXT_MUTED
        self.loading_label.setStyleSheet(f"color: {color}; font-size: 14px; padding: 24px;")
        self.loading_label.setText(text)
        self.loading_label.show()

    # ------------------------------------------------------------------ start-up
    def _start_build(self) -> None:
        worker = DashInitWorker(self._dashboard_builder(), self)
        worker.setObjectName(f"{type(self).__name__}Init")
        worker.built.connect(self._on_built)
        worker.failed.connect(self._on_build_failed)
        self.init_worker = worker
        worker.start()

    def _on_built(self, processor, dash_app) -> None:
        self._release_init_worker()
        if self._stage is not _Stage.BUILDING:
            return  # closed while the data was loading
        self.processor = processor
        self.dash_app = dash_app
        self._stage = _Stage.STARTING
        self._set_status("Starting the dashboard server…")
        thread = self.server_thread_class(dash_app, self.port)
        thread.setParent(self)
        thread.port_assigned.connect(self._on_server_ready)
        thread.error.connect(self._on_server_error)
        self.server_thread = thread
        self._startup_timer.start(self.startup_timeout_ms)
        thread.start()

    def _on_build_failed(self, message: str) -> None:
        self._release_init_worker()
        if self._stage is _Stage.BUILDING:
            self._fail(f"Could not read {os.path.basename(self.csv_path)}:\n{message}")

    def _on_server_ready(self, port: int) -> None:
        self._startup_timer.stop()
        if self._stage is not _Stage.STARTING:
            return
        self.port = port
        self._stage = _Stage.LOADING
        self._set_status("Loading the dashboard…")
        self.web_view.load(QUrl(self.url))

    def _on_server_error(self, message: str) -> None:
        if self._stage not in (_Stage.FAILED, _Stage.CLOSED):
            self._fail(f"The dashboard server stopped:\n{message}")

    def _on_startup_timeout(self) -> None:
        if self._stage is _Stage.STARTING:
            seconds = self.startup_timeout_ms / 1000
            self._fail(f"The dashboard server did not start within {seconds:g} s.")

    def _on_load_finished(self, ok: bool) -> None:
        if self._stage is not _Stage.LOADING:
            return  # in-page navigation after the first load, or the blank page of cleanup
        if not ok:
            self._fail(f"The dashboard page did not load from {self.url}.")
            return
        self._stage = _Stage.SHOWN
        self.loading_label.hide()
        self.web_view.show()

    def _fail(self, message: str) -> None:
        """Show *message* instead of the page and stop background work."""
        logger.error("%s: %s", self.window_title, message)
        self._stage = _Stage.FAILED
        self._startup_timer.stop()
        self._stop_background_work()
        if self.web_view is not None:
            self.web_view.hide()
        self._set_status(
            f"Could not open the {self.window_title.lower()}.\n\n{message}\n\n"
            "Open the dashboard again to retry.",
            error=True,
        )

    # ------------------------------------------------------------------ shutdown
    def _release_init_worker(self) -> None:
        worker, self.init_worker = self.init_worker, None
        if worker is None:
            return
        _disconnect(worker.built)
        _disconnect(worker.failed)
        release_qthread(worker)  # never blocks: a build still running finishes on its own

    def _stop_background_work(self) -> bool:
        """Release the init worker and stop the server; True if a server was stopped."""
        self._release_init_worker()
        thread, self.server_thread = self.server_thread, None
        if thread is None:
            return False
        _disconnect(thread.port_assigned)
        _disconnect(thread.error)
        thread.stop()
        join_qthread(thread, _SERVER_JOIN_MS)
        release_qthread(thread)
        return True

    def cleanup_server(self) -> None:
        """Stop the dashboard for good: blank the page and release its threads.

        Idempotent. Emits ``server_closed`` once, when it stopped a running server.
        """
        self._stage = _Stage.CLOSED
        self._startup_timer.stop()
        if self.web_view is not None:
            _disconnect(self._page.profile().downloadRequested, self.handle_download)
            self.web_view.setUrl(QUrl("about:blank"))
            self.web_view.hide()
        if self._stop_background_work():
            self.server_closed.emit()

    def closeEvent(self, event) -> None:
        try:
            self.cleanup_server()
        except Exception:  # noqa: BLE001 -- never block closing the window
            logger.exception("Dashboard cleanup on close failed")
        event.accept()

    # ------------------------------------------------------------------ exports
    def handle_download(self, download) -> None:
        """Save an export from this dashboard next to its CSV, under a free name.

        Every view shares the default QWebEngineProfile, so each open dashboard
        sees every download; only the one whose page started it answers (PV-11).
        """
        if self._page is None or download.page() is not self._page:
            return
        name = os.path.basename(download.suggestedFileName() or "")
        if not name:
            name = f"{self.download_prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        path = unique_path(self.save_directory, name)
        download.setDownloadDirectory(os.path.dirname(path))
        download.setDownloadFileName(os.path.basename(path))
        self._exports[download] = path
        download.isFinishedChanged.connect(self._on_export_finished)
        download.accept()

    def _on_export_finished(self) -> None:
        download = self.sender()
        path = self._exports.pop(download, None)
        if path is None:
            return
        if download.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.export_saved.emit(path)
        else:
            logger.warning("Export to %s failed: %s", path, download.interruptReasonString())

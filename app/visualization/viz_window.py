"""
Embedded Dash dashboard window: the lifecycle shared by root length and area.

A dashboard opens in three steps, none of which blocks the GUI thread:

1. a worker thread reads the CSV and builds the Dash app, so dash, plotly and
   pandas are imported off the GUI thread and only when a dashboard opens;
2. a server thread serves the app on ``DASH_HOST`` (its preferred port, or any
   free one) and reports the port it bound;
3. the web view loads ``http://DASH_HOST:port/`` at once (the socket already
   listens) under the status text, which is removed when the page has loaded;
   the page lays out and draws at its real size meanwhile.

A failure at any step replaces the page with a message and sets ``failed``;
the panel stays open so the message can be read. Subclasses only state what
differs: title, export prefix, preferred port and server-thread class, and how
to build ``(processor, dash_app)``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QSurface
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QLabel, QMainWindow, QStackedLayout, QWidget

from app.design import tokens
from app.qt_environment import windows_quick_uses_direct3d
from app.workers import GuardedThread, release_qthread

from ._viz_server import DASH_HOST, _DashServerThreadBase, join_qthread

logger = logging.getLogger(__name__)

#: Runs on the worker thread and returns ``(processor, dash_app)``.
Builder = Callable[[], tuple[object, object]]

#: Longest wait (GUI thread) for a stopped server thread before it is left to finish alone.
_SERVER_JOIN_MS = 2000


def prewarm_imports() -> None:
    """Import the dashboard stack (dash, plotly, pandas; 0.7-1 s): run it off the GUI thread.

    A dashboard's build worker imports these on the first open of a session.
    """
    import app.data_processing.data_processor  # noqa: F401
    import app.data_processing.data_processor_area  # noqa: F401

    from . import dash_app, dash_app_area  # noqa: F401


def native_child_surface(
    platform: str | None = None, environ: Mapping[str, str] | None = None
) -> QSurface.SurfaceType | None:
    """The surface of a dashboard's own native child window, or None to keep a plain child widget.

    A dashboard's browser draws through a ``QQuickWidget``. On Windows that needs a Direct3D 11
    surface (the RHI backend ``app/qt_environment.py`` sets), and Qt cannot change the surface
    of a window on screen. As a plain child widget the dashboard made Qt recreate the
    main window (gone for 43-98 ms at the first open, the release and each reopen), and the
    canvases' OpenGL viewports had to be off while it lived. In a native child window of its own
    the dashboard has its own Direct3D surface and the main window keeps the canvases' OpenGL
    surface: route (b) of the dashboard blink (``docs/KNOWN_ISSUES.md`` D8), measured at the RTX
    5070 Ti's own screen on 2026-10-29. Linux and macOS were not checked, nor a browser on another
    RHI or a Qt Quick backend other than the RHI one: they keep plain child widgets.

    Args:
        platform: A Qt platform plugin name; default ``QGuiApplication.platformName()``.
        environ: The process environment; default ``os.environ``.
    """
    if platform is None:
        platform = QGuiApplication.platformName()
    if environ is None:
        environ = os.environ
    if platform != "windows" or not windows_quick_uses_direct3d(environ):
        return None
    return QSurface.SurfaceType.Direct3DSurface


def require_rows(processor):
    """Return *processor*; raise with the processor's reason when its CSV has no usable rows."""
    if processor.df.empty:
        reason = getattr(processor, "empty_reason", None)
        raise ValueError((reason() if callable(reason) else None) or "No data found in CSV file")
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
    except TypeError, RuntimeError:
        pass


def _error_text(exc: BaseException) -> str:
    """The message of *exc*, or its type name when it has none (or cannot be printed)."""
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 -- a broken __str__ must not break the error path
        text = ""
    return text or type(exc).__name__


class DashInitWorker(GuardedThread):
    """Runs a dashboard build (CSV -> processor -> Dash app) off the GUI thread.

    Any failure, ``SystemExit`` too, is logged with its traceback and sent as
    ``failed(text)``; unreported, the window would show "Loading data…" until it
    was released.
    """

    built = pyqtSignal(object, object)  # processor, dash_app

    def __init__(self, build: Builder, parent: QObject | None = None):
        super().__init__(parent)
        self._build = build

    def work(self) -> None:
        processor, dash_app = self._build()
        self.built.emit(processor, dash_app)

    def describe(self, exc: BaseException) -> str:
        return _error_text(exc)


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
        self._embed_natively()

    def _embed_natively(self) -> None:
        """Give the dashboard a native window of its own where :func:`native_child_surface`
        names a surface for it (Windows). It becomes a native child of the panel it is added to,
        and Qt makes the panel's ancestors and their siblings native too (16-18 widgets)."""
        surface = native_child_surface()
        if surface is None:
            return
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        handle = self.windowHandle()
        if handle is not None:
            handle.setSurfaceType(surface)

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
        # The status label is stacked over the web view (both fill the window), so
        # the page lays out and draws its first chart at its real size while it
        # loads. A hidden view kept QWidget's default 640x480: the first chart was
        # drawn at 640x262, then everything again once the view was shown.
        layout = QStackedLayout(central)
        layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(central)

        self.loading_label = QLabel(central)
        self.loading_label.setObjectName("dashboardStatus")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setTextFormat(Qt.TextFormat.PlainText)
        self.loading_label.setWordWrap(True)
        self.loading_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._set_status("Loading data…")

        try:
            view = self._create_web_view()
        except Exception as exc:  # noqa: BLE001 -- WebEngine can fail to start (GPU, sandbox)
            logger.exception("Qt WebEngine could not start")
            layout.addWidget(self.loading_label)
            self._fail(f"The embedded browser (Qt WebEngine) could not start:\n{exc}")
            return
        self._page = view.page()
        # Paint the page background dark before the first frame (no white flash).
        self._page.setBackgroundColor(QColor(tokens.BG_0))
        self._page.profile().downloadRequested.connect(self.handle_download)
        self._page.renderProcessTerminated.connect(self._on_render_process_terminated)
        view.loadFinished.connect(self._on_load_finished)
        layout.addWidget(view)
        layout.addWidget(self.loading_label)  # added last: stacked on top
        layout.setCurrentWidget(self.loading_label)
        self.web_view = view

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = tokens.TEXT if error else tokens.TEXT_MUTED
        # Opaque: the label covers the page while it loads.
        self.loading_label.setStyleSheet(
            f"color: {color}; background-color: {tokens.BG_0}; font-size: 14px; padding: 24px;"
        )
        self.loading_label.setText(text)
        self.loading_label.show()
        self.loading_label.raise_()

    # ------------------------------------------------------------------ start-up
    # Neither thread has a parent: deleting a dashboard while one runs would destroy it
    # (the abort of app.log.1:7831); the keeper of app.workers holds them until they end.
    def _start_build(self) -> None:
        worker = DashInitWorker(self._dashboard_builder())
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
        thread.port_assigned.connect(self._on_server_ready)
        thread.error.connect(self._on_server_error)
        self.server_thread = thread
        self._startup_timer.start(self.startup_timeout_ms)
        thread.start()  # a GuardedThread: start() keeps it until Qt's own finished

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

    def _on_render_process_terminated(self, status, exit_code: int) -> None:
        """A crashed or killed page renderer: say so instead of a blank page.

        Marking the window failed makes the next open rebuild it; before, the dead
        page was brought forward on every visit. A normal exit is not an error.
        """
        normal = QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus
        if status == normal or self._stage not in (_Stage.LOADING, _Stage.SHOWN):
            return
        self._fail(
            "The dashboard page stopped: its browser process ended unexpectedly "
            f"({getattr(status, 'name', status)}, exit code {exit_code})."
        )

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

        Idempotent. Emits ``server_closed`` once, when it stopped a running server. The
        threads are stopped even when blanking the page fails (the error is raised after).
        """
        self._stage = _Stage.CLOSED
        self._startup_timer.stop()
        try:
            if self.web_view is not None:
                _disconnect(self._page.profile().downloadRequested, self.handle_download)
                self.web_view.setUrl(QUrl("about:blank"))
                self.web_view.hide()
        finally:
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

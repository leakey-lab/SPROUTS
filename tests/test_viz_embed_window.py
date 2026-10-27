"""viz-embed: the shared dashboard window lifecycle (app/visualization/viz_window.py).

No browser is started: QtWebEngine cannot run in headless CI, so the web view is a
small fake exposing the calls DashVizWindow makes. The "Dash app" is a WSGI
stand-in served by the real server thread on 127.0.0.1.
"""

from __future__ import annotations

import http.client
import logging
import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl, pyqtSignal  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.visualization._viz_server import _DashServerThreadBase  # noqa: E402
from app.visualization.viz_window import DashVizWindow, require_rows  # noqa: E402
from tests.qt_teardown import delete_widgets, flush_deleted  # noqa: E402

DownloadState = QWebEngineDownloadRequest.DownloadState
TerminationStatus = QWebEnginePage.RenderProcessTerminationStatus


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QCoreApplication.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def _status(port: int) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/")
        return conn.getresponse().status
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Fakes for the QtWebEngine parts
# --------------------------------------------------------------------------- #
class FakeProfile(QObject):
    """Stands in for QWebEngineProfile.defaultProfile(), which every view shares."""

    downloadRequested = pyqtSignal(object)


class FakePage(QObject):
    #: QWebEnginePage.renderProcessTerminated(status, exit code)
    renderProcessTerminated = pyqtSignal(object, int)

    def __init__(self, profile: FakeProfile):
        super().__init__()
        self._profile = profile

    def profile(self) -> FakeProfile:
        return self._profile

    def setBackgroundColor(self, _color) -> None:
        pass


class FakeWebView(QWidget):
    """The QWebEngineView calls DashVizWindow makes; records every load()."""

    loadFinished = pyqtSignal(bool)

    def __init__(self, profile: FakeProfile):
        super().__init__()
        self._page = FakePage(profile)
        self._url = QUrl()
        self.loads: list[str] = []

    def page(self) -> FakePage:
        return self._page

    def load(self, url: QUrl) -> None:
        self.loads.append(url.toString())
        self._url = QUrl(url)

    def setUrl(self, url: QUrl) -> None:
        self._url = QUrl(url)

    def url(self) -> QUrl:
        return QUrl(self._url)


class FakeDownload(QObject):
    """The QWebEngineDownloadRequest calls DashVizWindow makes."""

    isFinishedChanged = pyqtSignal()

    def __init__(self, page, suggested: str = "root_length_plot.svg"):
        super().__init__()
        self._page = page
        self._suggested = suggested
        self._directory = ""
        self._file_name = suggested
        self._state = DownloadState.DownloadRequested
        self.accepted = False

    def page(self):
        return self._page

    def suggestedFileName(self) -> str:
        return self._suggested

    def downloadDirectory(self) -> str:
        return self._directory

    def setDownloadDirectory(self, directory: str) -> None:
        self._directory = directory

    def downloadFileName(self) -> str:
        return self._file_name

    def setDownloadFileName(self, name: str) -> None:
        self._file_name = name

    def accept(self) -> None:
        self.accepted = True
        self._state = DownloadState.DownloadInProgress

    def state(self):
        return self._state

    def interruptReasonString(self) -> str:
        return "" if self._state == DownloadState.DownloadCompleted else "Network failed"

    def finish(self, state=DownloadState.DownloadCompleted) -> None:
        self._state = state
        self.isFinishedChanged.emit()


class StubDash:
    """What the server thread uses of a DashApp: ``.app.server``, a WSGI callable."""

    class app:  # noqa: N801 - mirrors DashApp.app
        @staticmethod
        def server(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/html")])
            return [b"<html><body>dashboard</body></html>"]


class AnyPortServer(_DashServerThreadBase):
    def __init__(self, dash_app, port: int = 0):
        super().__init__(dash_app, port, object_name="TestDashServer")


class _Dashboard(DashVizWindow):
    window_title = "Test dashboard"
    download_prefix = "test_plot"
    server_thread_class = AnyPortServer

    def __init__(self, csv_path, profile, build=None, **overrides):
        self._build = build or (lambda: (object(), StubDash()))
        self._profile = profile
        for name, value in overrides.items():  # per-test hooks, e.g. server_thread_class
            setattr(self, name, value)
        super().__init__(csv_path)

    def _dashboard_builder(self):
        return self._build

    def _create_web_view(self):
        return FakeWebView(self._profile)


@pytest.fixture
def profile(qapp):
    return FakeProfile()


@pytest.fixture
def make_window(qapp, tmp_path, profile):
    windows = []

    def make(folder=None, **kwargs):
        folder = folder or tmp_path
        os.makedirs(folder, exist_ok=True)
        csv_path = os.path.join(folder, "root_lengths.csv")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("Image,Length (mm)\n")
        window = _Dashboard(csv_path, profile, **kwargs)
        windows.append(window)
        return window

    yield make
    delete_widgets(windows, close=False, prepare=lambda window: window.cleanup_server())
    pump(0.2)


# --------------------------------------------------------------------------- #
#  Start-up
# --------------------------------------------------------------------------- #
def test_page_loads_from_ipv4_loopback_as_soon_as_the_server_listens(make_window):
    window = make_window()

    assert wait_until(lambda: window.web_view.loads)
    assert window.port > 0
    assert window.web_view.loads == [f"http://127.0.0.1:{window.port}/"]
    assert window.url == window.web_view.loads[0]
    assert _status(window.port) == 200


def test_dashboard_is_revealed_only_once_the_page_has_loaded(make_window):
    window = make_window()
    assert wait_until(lambda: window.web_view.loads)
    assert _covered_by_status(window)

    window.web_view.loadFinished.emit(True)

    assert not window.web_view.isHidden()
    assert window.loading_label.isHidden()
    assert not window.failed


def _covered_by_status(window) -> bool:
    """The status label is shown and stacked above the (loading) page."""
    stack = window.centralWidget().layout()
    return (
        not window.loading_label.isHidden()
        and stack.indexOf(window.loading_label) > stack.indexOf(window.web_view)
        and stack.currentWidget() is window.loading_label
    )


def test_the_page_loads_at_the_window_size_under_the_status(make_window):
    # A hidden view kept QWidget's default 640x480, so the page was laid out and its
    # first chart drawn at 640x262, then everything again once the view was shown.
    window = make_window()
    window.resize(1000, 700)
    window.show()
    assert wait_until(lambda: window.web_view.loads)
    pump(0.05)

    assert not window.web_view.isHidden()
    assert window.web_view.size() == window.centralWidget().size()
    assert _covered_by_status(window)
    window.hide()


def test_page_that_fails_to_load_is_reported_in_place(make_window):
    window = make_window()
    assert wait_until(lambda: window.web_view.loads)

    window.web_view.loadFinished.emit(False)

    assert window.failed
    assert window.url in window.loading_label.text()
    assert window.web_view.isHidden() and not window.loading_label.isHidden()


@pytest.mark.parametrize("status", ["CrashedTerminationStatus", "KilledTerminationStatus"])
def test_a_page_whose_renderer_died_is_reported_and_marked_failed(make_window, status):
    # Before, the dead page stayed blank and every later visit brought it forward
    # (not failed, CSV unchanged); now the message shows and the next open rebuilds.
    window = make_window()
    assert wait_until(lambda: window.web_view.loads)
    window.web_view.loadFinished.emit(True)

    window.web_view.page().renderProcessTerminated.emit(getattr(TerminationStatus, status), 1)

    assert window.failed
    assert "stopped" in window.loading_label.text()
    assert window.web_view.isHidden() and not window.loading_label.isHidden()
    assert window.server_thread is None


def test_a_renderer_that_ends_normally_or_after_cleanup_is_no_error(make_window):
    window = make_window()
    assert wait_until(lambda: window.web_view.loads)
    window.web_view.loadFinished.emit(True)
    page = window.web_view.page()

    page.renderProcessTerminated.emit(TerminationStatus.NormalTerminationStatus, 0)
    assert not window.failed
    window.cleanup_server()
    page.renderProcessTerminated.emit(TerminationStatus.CrashedTerminationStatus, 1)
    assert not window.failed


def test_unreadable_data_is_reported_in_place_and_starts_no_server(make_window):
    def broken():
        raise ValueError("No data found in CSV file")

    window = make_window(build=broken)

    assert wait_until(lambda: window.failed)
    assert "No data found in CSV file" in window.loading_label.text()
    assert window.server_thread is None
    assert window.web_view.loads == []


class _NotAnError(BaseException):
    """A BaseException that is neither SystemExit nor KeyboardInterrupt."""


class _Unprintable(Exception):
    def __str__(self):
        raise RuntimeError("no text")


@pytest.mark.parametrize(
    "exc, text",
    [
        (SystemExit("a library called sys.exit"), "a library called sys.exit"),
        (SystemExit(), "SystemExit"),
        (KeyboardInterrupt(), "KeyboardInterrupt"),
        (_NotAnError(), "_NotAnError"),
        (_Unprintable(), "_Unprintable"),
    ],
    ids=["exit", "bare-exit", "interrupt", "other", "unprintable"],
)
def test_a_build_ending_in_any_exception_is_reported(qapp, exc, text):
    # Only an Exception was caught: a SystemExit (from a library) left run(), neither built
    # nor failed was sent, and the dashboard said "Loading data…" until it was released.
    from app.visualization.viz_window import DashInitWorker

    def build():
        raise exc

    worker = DashInitWorker(build)
    reports = []
    worker.failed.connect(reports.append)
    escaped = None
    try:
        worker.run()  # on this thread: what escapes here escaped the worker thread
    except BaseException as error:  # noqa: BLE001 -- the test reports it instead
        escaped = error

    assert escaped is None, f"{type(escaped).__name__} escaped the worker"
    assert reports == [text]


def test_a_build_that_exits_is_reported_in_place_with_its_traceback(make_window, caplog):
    def exiting():
        raise SystemExit("a library called sys.exit")

    with caplog.at_level(logging.ERROR, logger="app.visualization.viz_window"):
        window = make_window(build=exiting)
        assert wait_until(lambda: window.failed)

    assert "a library called sys.exit" in window.loading_label.text()
    assert window.server_thread is None
    assert "Traceback" in caplog.text


def test_an_unchartable_csv_is_reported_with_the_processors_reason():
    # "No data found in CSV file" was all a user saw, also for a CSV whose
    # measurements had all failed or that lacked a column.
    processor = SimpleNamespace(
        df=SimpleNamespace(empty=True), empty_reason=lambda: "The CSV has no 'Date' column."
    )
    with pytest.raises(ValueError, match="The CSV has no 'Date' column."):
        require_rows(processor)
    with pytest.raises(ValueError, match="No data found in CSV file"):
        require_rows(SimpleNamespace(df=SimpleNamespace(empty=True)))  # an older processor


def test_server_failure_is_reported_in_place(make_window):
    class FailingServer(AnyPortServer):
        def run(self):
            self.error.emit("no free port on 127.0.0.1")

    window = make_window(server_thread_class=FailingServer)

    assert wait_until(lambda: window.failed)
    assert "no free port on 127.0.0.1" in window.loading_label.text()
    assert window.web_view.loads == []


def test_server_that_never_listens_times_out_with_an_error(make_window):
    class SilentServer(AnyPortServer):
        def run(self):
            self._stop_event.wait(10)  # never binds; returns when stopped

    window = make_window(server_thread_class=SilentServer, startup_timeout_ms=200)

    assert wait_until(lambda: window.failed, 3.0)
    assert window.web_view.loads == []
    assert window.server_thread is None  # stopped and joined


def test_browser_that_cannot_start_is_reported_without_background_work(qapp, tmp_path, profile):
    class NoBrowser(_Dashboard):
        def _create_web_view(self):
            raise RuntimeError("Qt WebEngine is unavailable")

    csv_path = os.path.join(tmp_path, "root_lengths.csv")
    window = NoBrowser(csv_path, profile)
    try:
        assert window.failed
        assert "Qt WebEngine is unavailable" in window.loading_label.text()
        assert window.init_worker is None and window.server_thread is None
    finally:
        window.cleanup_server()
        window.deleteLater()
        flush_deleted()


# --------------------------------------------------------------------------- #
#  Shutdown
# --------------------------------------------------------------------------- #
def test_cleanup_stops_the_server_and_emits_server_closed_once(make_window):
    window = make_window()
    assert wait_until(lambda: window.web_view.loads)
    closed = []
    window.server_closed.connect(lambda: closed.append(1))

    t0 = time.perf_counter()
    window.cleanup_server()
    elapsed = time.perf_counter() - t0
    window.cleanup_server()

    assert elapsed < 0.3
    assert closed == [1]
    assert window.server_thread is None
    assert window.web_view.url().toString() == "about:blank"


def test_cleanup_while_data_loads_neither_blocks_nor_starts_a_server(make_window):
    release = threading.Event()

    def slow_build():
        release.wait(5)
        return object(), StubDash()

    window = make_window(build=slow_build)

    t0 = time.perf_counter()
    window.cleanup_server()
    elapsed = time.perf_counter() - t0
    release.set()
    pump(0.3)  # the worker finishes; its late result must be ignored

    assert elapsed < 0.1
    assert window.server_thread is None
    assert window.web_view.loads == []


# --------------------------------------------------------------------------- #
#  Exports (Plotly camera button -> QWebEngineDownloadRequest)
# --------------------------------------------------------------------------- #
def test_each_dashboard_accepts_only_its_own_exports(make_window, tmp_path, profile):
    area = make_window(folder=tmp_path / "area")  # connected to the shared profile first
    length = make_window(folder=tmp_path / "length")
    request = FakeDownload(length.web_view.page())

    profile.downloadRequested.emit(request)

    assert request.accepted
    assert os.path.normcase(request.downloadDirectory()) == os.path.normcase(
        str(tmp_path / "length")
    )
    assert area.save_directory != length.save_directory


def test_export_gets_a_free_name_next_to_the_csv(make_window, tmp_path):
    window = make_window()
    (tmp_path / "plot.png").write_bytes(b"old")
    (tmp_path / "plot_1.png").write_bytes(b"old")
    request = FakeDownload(window.web_view.page(), suggested="plot.png")

    window.handle_download(request)

    assert request.accepted
    assert request.downloadFileName() == "plot_2.png"


def test_export_without_a_suggested_name_uses_the_dashboard_prefix(make_window):
    window = make_window()
    request = FakeDownload(window.web_view.page(), suggested="")

    window.handle_download(request)

    name = request.downloadFileName()
    assert name.startswith("test_plot_") and name.endswith(".png")


def test_only_completed_exports_are_announced(make_window, tmp_path):
    window = make_window()
    saved = []
    window.export_saved.connect(saved.append)
    done = FakeDownload(window.web_view.page(), suggested="plot.svg")
    broken = FakeDownload(window.web_view.page(), suggested="other.svg")

    window.handle_download(done)
    window.handle_download(broken)
    done.finish()
    broken.finish(DownloadState.DownloadInterrupted)

    assert [os.path.normcase(p) for p in saved] == [os.path.normcase(str(tmp_path / "plot.svg"))]


# --------------------------------------------------------------------------- #
#  Prewarm (PKG-VIZ, V2): what the first open of a session used to wait for
# --------------------------------------------------------------------------- #
def test_prewarm_imports_loads_the_dashboard_stack():
    import sys

    import app.visualization.viz_window as vw

    vw.prewarm_imports()

    for module in (
        "dash",
        "plotly.graph_objects",
        "pandas",
        "app.visualization.dash_app",
        "app.visualization.dash_app_area",
        "app.data_processing.data_processor",
    ):
        assert module in sys.modules, module

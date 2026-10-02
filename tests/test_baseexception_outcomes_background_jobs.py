"""Background jobs report an outcome whatever ends them (KNOWN_ISSUES #7).

Same class as the warm-up fix (:mod:`app.gui.warmup`): a ``BaseException`` that is not an
``Exception`` (``SystemExit``, ``KeyboardInterrupt``, or any other) used to end a job's
thread without a word, so the GUI waited for good:

- the Measure CSV parse (``MetricsController.prewarm``) left ``metrics.loading`` True:
  no other parse, the warm-ups blocked and the bar never refreshed;
- the start-up device check left the chip at "Checking GPU…";
- a folder scan (``ImageLoaderThread``) caught ``OSError`` only: anything else reported
  neither outcome and the loading card stayed up.

Each now logs the traceback and reports a failure (never re-raised: the thread has no one
to hand it to, and PyQt aborts the process on an exception that escapes a pool job or
``QThread.run`` under the default ``sys.excepthook``). The tests record ``sys.excepthook``:
nothing may reach it.
"""

from __future__ import annotations

import os
import sys
import time
import types
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QThreadPool  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.gui import image_manager as im  # noqa: E402
from app.gui import shell_chrome  # noqa: E402
from app.gui.device_check import DeviceCheck  # noqa: E402
from app.gui.window import project_loader  # noqa: E402
from app.gui.window.metrics import MetricsController  # noqa: E402
from tests.qt_teardown import delete_widgets, flush_deleted  # noqa: E402

RUNTIME = "app.inference.runtime"


class Abort(BaseException):
    """Neither an ``Exception`` nor ``SystemExit`` / ``KeyboardInterrupt``."""


ENDINGS = [SystemExit(3), KeyboardInterrupt(), Abort("stopped")]
ENDING_IDS = ["exit", "interrupt", "other"]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def nothing_escapes(app, monkeypatch):
    """No exception may leave a job's thread (PyQt hands it to ``sys.excepthook``)."""
    hooked: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: hooked.append(exc))
    yield
    assert QThreadPool.globalInstance().waitForDone(10_000)
    flush_deleted()
    assert hooked == []


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def raiser(ending: BaseException):
    def raise_it(*_args, **_kwargs):
        raise ending

    return raise_it


# ------------------------------------------------------------------ the Measure CSV parse
class Index:
    """A measurement index whose first parse ends with ``ending``; later ones succeed."""

    def __init__(self, ending: BaseException) -> None:
        self.ending = ending
        self.prewarmed = 0

    def needs_load(self) -> bool:
        return True

    def prewarm(self) -> None:
        self.prewarmed += 1
        if self.prewarmed == 1:
            raise self.ending


def metrics_window() -> SimpleNamespace:
    return SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=None, images={}),
        display_controller=SimpleNamespace(current_name=None),
        stage_status=SimpleNamespace(check=lambda *a, **k: None),
        _current_stage=lambda: None,
    )


@pytest.mark.parametrize("ending", ENDINGS, ids=ENDING_IDS)
def test_a_parse_ended_by_a_base_exception_still_ends_the_loading(ending, caplog):
    controller = MetricsController(metrics_window())
    controller.signals.ready.connect(controller.on_ready)  # as the window connects it
    controller.index = index = Index(ending)

    controller.prewarm()
    assert controller.loading

    assert wait(lambda: not controller.loading), "the metrics stayed loading for good"
    assert "Could not read the measurement CSVs" in caplog.text
    controller.prewarm()  # the next parse is not blocked
    assert wait(lambda: index.prewarmed == 2 and not controller.loading)


# ------------------------------------------------------------------ the device check
class Line:
    """Stand-in status line: records the chip states."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_device_check(self, state, detail: str = "") -> None:
        self.calls.append((state, detail))

    def state(self) -> str:
        return "idle"


@pytest.fixture
def widgets():
    made: list = []
    yield made
    delete_widgets(made)


def start_check(widgets, resolve=None) -> Line:
    widget, line = QWidget(), Line()
    widgets.append(widget)
    DeviceCheck(widget, line, delay_ms=10, idle_ms=20, resolve=resolve, enabled=True)
    assert line.calls == [(shell_chrome.CHECKING, "")]
    widget.show()
    return line


@pytest.mark.parametrize("ending", ENDINGS, ids=ENDING_IDS)
def test_a_check_ended_by_a_base_exception_still_resolves_the_chip(widgets, ending, caplog):
    line = start_check(widgets, resolve=raiser(ending))

    assert wait(lambda: len(line.calls) == 2), "the chip stayed at 'Checking GPU…'"
    state, detail = line.calls[-1]
    assert state == shell_chrome.FAILED and detail  # "Device unknown", and why
    assert "Could not detect the inference device" in caplog.text


def test_the_real_check_ended_by_a_base_exception_still_resolves_the_chip(
    widgets, monkeypatch, caplog
):
    broken = types.ModuleType(RUNTIME)
    broken.describe_runtime = raiser(KeyboardInterrupt())
    monkeypatch.setitem(sys.modules, RUNTIME, broken)
    import app.inference as inference_package

    monkeypatch.setattr(inference_package, "runtime", broken, raising=False)
    line = start_check(widgets)  # resolve_device_logged, as in the app

    assert wait(lambda: len(line.calls) == 2), "the chip stayed at 'Checking GPU…'"
    assert line.calls[-1] == (shell_chrome.FAILED, "KeyboardInterrupt")
    assert "Could not detect the inference device" in caplog.text


# ------------------------------------------------------------------ the folder scan
def loader_window(calls: list) -> SimpleNamespace:
    """A window the project loader reports to, with a real :class:`ImageManager`."""
    window = SimpleNamespace(
        _loading_project=True,
        loading_overlay=SimpleNamespace(
            hide=lambda: calls.append("card hidden"), set_progress=lambda value: None
        ),
        empty_state=SimpleNamespace(set_loading=lambda name: None),
        task_progress=SimpleNamespace(
            set_progress=lambda value: None,
            fail=lambda message: calls.append(("failed", message)),
            finish=lambda message: calls.append(("finished", message)),
        ),
        notify=lambda message, kind="success", timeout=3200, key=None: calls.append(
            (kind, message)
        ),
        app_stack=SimpleNamespace(setCurrentIndex=lambda i: calls.append(("page", i))),
    )
    window.update_loading_progress = lambda v: project_loader.update_loading_progress(window, v)
    window.on_loading_failed = lambda f, m: project_loader.on_loading_failed(window, f, m)
    window.on_loading_empty = lambda f: project_loader.on_loading_empty(window, f)
    window.on_loading_finished = lambda *args: calls.append("loaded")
    window._end_failed_load = lambda m, k: project_loader.end_failed_load(window, m, k)
    window.image_manager = im.ImageManager(window)
    return window


@pytest.mark.parametrize(
    ("where", "ending"),
    [
        ("index_names", ValueError("a name the index cannot place")),
        ("index_names", MemoryError()),
        *(("scan_images", ending) for ending in ENDINGS),
    ],
    ids=["value-error", "memory-error", *ENDING_IDS],
)
def test_a_scan_ended_by_anything_but_an_oserror_reports_a_failure(
    tmp_path, monkeypatch, caplog, where, ending
):
    if where == "index_names":
        monkeypatch.setattr(im, "index_names", raiser(ending))
    else:
        monkeypatch.setattr(im.ProjectLayout, "scan_images", raiser(ending))
    folder = str(tmp_path / "trial")
    os.mkdir(folder)
    calls: list = []
    window = loader_window(calls)

    window.image_manager.load_images(folder)

    assert wait(lambda: "card hidden" in calls), "the loading card stayed up"
    assert window._loading_project is False and "loaded" not in calls
    (message,) = [entry[1] for entry in calls if entry[0] == "failed"]
    assert folder in message and type(ending).__name__ in message
    assert ("danger", message) in calls and ("page", 0) in calls  # nothing loaded before
    assert window.image_manager.images == {}
    assert "Failed to scan image folder" in caplog.text
    assert wait(lambda: window.image_manager.loader_thread is None)  # the thread is let go


def test_an_unreadable_folder_keeps_its_message(tmp_path, monkeypatch):
    """An ``OSError`` still reads "Cannot read <folder>: <why>"."""
    monkeypatch.setattr(
        im.ProjectLayout, "scan_images", raiser(PermissionError(13, "Access is denied"))
    )
    folder = str(tmp_path / "trial")
    os.mkdir(folder)
    calls: list = []
    window = loader_window(calls)

    window.image_manager.load_images(folder)

    assert wait(lambda: "card hidden" in calls)
    assert ("failed", f"Cannot read {folder}: Access is denied") in calls

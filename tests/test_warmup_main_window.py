"""The main window's warm-up policy (W3-WARM W2-W4, X-RUNTIME F1-F3, W5-D V9).

torch loads when the user heads for Generate (staying in it, resting on its run buttons),
dash/plotly/pandas when they head for Visualize (staying in Measure, resting on Measure's
run buttons or on the Visualize stage button); passing through a stage warms nothing (the
torch import stalled the work that followed, V9); never on a timer after start-up or a
project load, never while a folder loads or settles, a Measure CSV is parsed or a job runs,
and a warm-up that has not started when the window closes is dropped. Headless windows warm
nothing unless asked (``warm_up=True`` here). The warm-ups are stubs, the stage dwell is
:data:`DWELL_MS` instead of a second, and the heap is never frozen in the test process (the
suite frees Qt wrappers with gc.collect()).
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPointF, QSettings, QThreadPool  # noqa: E402
from PyQt6.QtGui import QEnterEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from app import gc_policy  # noqa: E402
from app.gui import (  # noqa: E402
    canvas_gl,
    main_window,
    shell_chrome,
    warmup,
)
from tests.qt_teardown import delete_widgets  # noqa: E402

STEMS = tuple(f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (31, 32, 33))
#: The stage dwell here (warmup.STAGE_DWELL_MS, read when the window is built).
DWELL_MS = 150


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test"])


class Warmups:
    """Stub warm-up jobs: which ran, on which thread, when."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, int, float]] = []
        self.results = {warmup.TORCH: "", warmup.MODELS: "", warmup.DASHBOARDS: ""}

    def job(self, key: str):
        def run() -> str:
            self.runs.append((key, threading.get_ident(), time.perf_counter()))
            return self.results[key]

        return run

    def keys(self) -> list[str]:
        return [key for key, _thread, _t in self.runs]


@pytest.fixture
def warmups(monkeypatch):
    stub = Warmups()
    monkeypatch.setattr(warmup, "warm_inference", stub.job(warmup.TORCH))
    monkeypatch.setattr(warmup, "warm_models", stub.job(warmup.MODELS))
    monkeypatch.setattr(warmup, "warm_dashboards", stub.job(warmup.DASHBOARDS))
    # Never freeze the test process's heap.
    monkeypatch.setattr(gc_policy, "freeze", lambda *a, **k: 0)
    monkeypatch.setattr(gc_policy, "hold_full_collections", lambda: None)
    monkeypatch.setattr(gc_policy, "release_full_collections", lambda: None)
    monkeypatch.setattr(main_window, "WARMUP_SETTLE_MS", 150)
    monkeypatch.setattr(warmup, "STAGE_DWELL_MS", DWELL_MS)
    return stub


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: pytest.fail("dialog"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    hooked = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: hooked.append(exc))
    yield
    assert hooked == []


@pytest.fixture
def make_window(app, tmp_path):
    made: list = []

    def make(**kwargs):
        settings = QSettings(
            str(tmp_path / f"settings_{len(made)}.ini"), QSettings.Format.IniFormat
        )
        window = main_window.MainWindow(settings=settings, **kwargs)
        made.append(window)
        window.resize(1280, 800)
        window.show()
        settle()
        return window

    yield make
    assert QThreadPool.globalInstance().waitForDone(10_000)
    for window in made:
        for editor in window._editors().values():
            editor.maybe_save_changes = lambda: True
    delete_widgets(made)
    QApplication.processEvents()


def settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def new_project(root) -> str:
    folder = root / "project"
    folder.mkdir()
    for i, stem in enumerate(STEMS):
        image = np.full((120, 160, 3), 60 + 30 * i, np.uint8)
        assert cv2.imwrite(str(folder / f"{stem}.jpg"), image)
    return str(folder)


def open_project(window, folder: str) -> float:
    """Open ``folder``; returns when the library is built (perf_counter)."""
    window.open_path(folder)
    assert wait(lambda: len(window.image_manager.images) == len(STEMS) and window._image_leaves())
    return time.perf_counter()


def rest_on(widget) -> None:
    point = QPointF(4, 4)
    QApplication.sendEvent(widget, QEnterEvent(point, point, widget.mapToGlobal(point)))


# ------------------------------------------------------------------ the policy
def test_staying_in_generate_warms_up_torch_once_off_the_gui_thread(make_window, warmups):
    window = make_window(warm_up=True)
    window.on_stage_changed("Generate")
    assert wait(lambda: warmups.keys() == [warmup.TORCH, warmup.MODELS])
    assert warmups.runs[0][1] != threading.main_thread().ident
    assert warmups.runs[1][2] >= warmups.runs[0][2]  # torch first, then the models
    window.on_stage_changed("Library")
    window.on_stage_changed("Generate")
    QTest.qWait(3 * DWELL_MS)
    assert warmups.keys() == [warmup.TORCH, warmup.MODELS]  # once per session


def test_measure_warms_up_the_dashboards_and_never_torch(make_window, warmups):
    window = make_window(warm_up=True)
    for stage in ("Library", "Correct", "Measure"):
        window.on_stage_changed(stage)
    assert wait(lambda: warmups.keys() == [warmup.DASHBOARDS])
    window.on_stage_changed("Visualize")
    QTest.qWait(3 * DWELL_MS)
    assert warmups.keys() == [warmup.DASHBOARDS]


# ------------------------------------------------------------------ the stage dwell (V9)
def test_passing_through_generate_warms_nothing(make_window, warmups):
    """Generate on the way to Correct: the torch import used to start at once and stall
    the zooming that followed (52-69 ms)."""
    window = make_window(warm_up=True)
    window.on_stage_changed("Generate")
    QApplication.processEvents()
    window.on_stage_changed("Correct")
    QTest.qWait(3 * DWELL_MS)
    assert warmups.runs == [] and window.warmups.pending() == ()
    assert window.warmups.state(warmup.TORCH) == "idle"


def test_passing_through_measure_on_the_way_to_visualize_warms_nothing(make_window, warmups):
    """The dashboard Visualize opens imports its libraries itself."""
    window = make_window(warm_up=True)
    window.on_stage_changed("Measure")
    window.on_stage_changed("Visualize")
    QTest.qWait(3 * DWELL_MS)
    assert warmups.runs == [] and window.warmups.pending() == ()


def test_generate_warms_up_once_the_user_has_stayed_for_the_dwell(make_window, warmups):
    window = make_window(warm_up=True)
    entered = time.perf_counter()
    window.on_stage_changed("Generate")
    QApplication.processEvents()
    assert warmups.runs == [] and window.warmups.pending() == ()  # entering is not enough
    assert wait(lambda: warmups.keys() == [warmup.TORCH, warmup.MODELS])
    assert warmups.runs[0][2] - entered >= 0.9 * DWELL_MS / 1000


def test_resting_on_a_run_button_does_not_wait_for_the_stage_dwell(
    make_window, warmups, monkeypatch
):
    """The run buttons keep their 200 ms dwell, and leaving the stage afterwards keeps
    what they asked for (only the stage's own dwell is dropped)."""
    monkeypatch.setattr(warmup, "STAGE_DWELL_MS", 60_000)
    window = make_window(warm_up=True)
    window._loading_project = True  # holds what is asked in the queue
    window.on_stage_changed("Generate")
    rest_on(window.generate_all_button)
    assert wait(lambda: window.warmups.pending() == (warmup.TORCH, warmup.MODELS), 2.0)
    window.on_stage_changed("Correct")
    window._loading_project = False
    assert wait(lambda: warmups.keys() == [warmup.TORCH, warmup.MODELS])


def test_closing_the_window_during_the_dwell_starts_nothing(make_window, warmups):
    window = make_window(warm_up=True)
    window.on_stage_changed("Generate")
    window.close()
    QTest.qWait(3 * DWELL_MS)
    assert warmups.runs == [] and window.warmups.pending() == ()


@pytest.mark.parametrize(
    "button, keys",
    [
        ("generate_all_button", [warmup.TORCH, warmup.MODELS]),
        ("generate_button", [warmup.TORCH, warmup.MODELS]),
        ("calculate_length_button", [warmup.DASHBOARDS]),
        ("calculate_area_button", [warmup.DASHBOARDS]),
    ],
)
def test_resting_on_a_run_button_asks_for_its_warmup(make_window, warmups, button, keys):
    window = make_window(warm_up=True)
    rest_on(getattr(window, button))
    QTest.qWait(80)
    assert warmups.runs == []  # passing over it is not enough
    assert wait(lambda: warmups.keys() == keys)


def test_resting_on_the_visualize_stage_button_warms_up_the_dashboards(make_window, warmups):
    window = make_window(warm_up=True)
    rest_on(window.ribbon_buttons["Visualize"])
    assert wait(lambda: warmups.keys() == [warmup.DASHBOARDS])


def test_headless_windows_warm_nothing_unless_asked(make_window, warmups):
    window = make_window()
    assert window._warm_ups is False
    window.on_stage_changed("Generate")
    window.on_stage_changed("Measure")
    rest_on(window.ribbon_buttons["Visualize"])
    QTest.qWait(400)
    assert warmups.runs == []
    assert wait(lambda: window.warmups.state(warmup.SKIMAGE) == "done")  # start-up, as before


def test_nothing_is_warmed_on_a_timer_after_a_project_load(make_window, warmups, tmp_path):
    window = make_window(warm_up=True)
    open_project(window, new_project(tmp_path))
    QTest.qWait(2300)  # the old timer fired 2 s after the load
    assert warmups.runs == []
    assert not hasattr(main_window, "DASHBOARD_PREWARM_DELAY_MS")


# ------------------------------------------------------------------ the gates
def test_a_warmup_waits_for_the_folder_load_and_its_settling(make_window, warmups, tmp_path):
    window = make_window(warm_up=True)
    folder = new_project(tmp_path)
    window._loading_project = True  # as open_path sets it
    window.on_stage_changed("Generate")
    QTest.qWait(300)
    assert warmups.runs == []
    assert "loading" in window._warmup_blocked()
    loaded = open_project(window, folder)  # finishes the load: settles for WARMUP_SETTLE_MS
    assert wait(lambda: warmups.runs)
    assert warmups.runs[0][2] - loaded >= main_window.WARMUP_SETTLE_MS / 1000.0 - 0.05


def test_a_warmup_waits_for_the_csv_parse_and_for_a_job(make_window, warmups, monkeypatch):
    window = make_window(warm_up=True)
    window._measurements_loading = True
    window.on_stage_changed("Measure")
    QTest.qWait(300)
    assert warmups.runs == [] and "CSV" in window._warmup_blocked()
    window._measurements_loading = False
    busy = [True]
    monkeypatch.setattr(window.jobs, "is_busy", lambda: busy[0])
    monkeypatch.setattr(window.jobs, "running_name", lambda: "Measure: root length")
    QTest.qWait(300)
    assert warmups.runs == [] and "root length is running" in window._warmup_blocked()
    busy[0] = False
    assert wait(lambda: warmups.keys() == [warmup.DASHBOARDS])


def test_a_warmup_waits_for_other_pool_jobs(make_window, warmups):
    window = make_window(warm_up=True)
    release = threading.Event()
    QThreadPool.globalInstance().start(lambda: release.wait(10))
    try:
        assert wait(lambda: QThreadPool.globalInstance().activeThreadCount() > 0)
        window.on_stage_changed("Generate")
        QTest.qWait(300)
        assert warmups.runs == [] and "background job" in window._warmup_blocked()
    finally:
        release.set()
    assert wait(lambda: warmups.keys() == [warmup.TORCH, warmup.MODELS])


def test_closing_the_window_drops_a_warmup_that_has_not_started(make_window, warmups):
    window = make_window(warm_up=True)
    window._loading_project = True
    window.on_stage_changed("Generate")
    assert wait(lambda: window.warmups.pending() == (warmup.TORCH, warmup.MODELS))
    window.close()
    window._loading_project = False
    QTest.qWait(400)
    assert warmups.runs == [] and window.warmups.pending() == ()


# ------------------------------------------------------------------ the chip
def test_the_torch_warmup_puts_the_runtime_on_the_chip(make_window, warmups, monkeypatch):
    window = make_window(warm_up=True)
    refreshed = []
    monkeypatch.setattr(window.statusline, "refresh_runtime", lambda: refreshed.append(1))
    window.on_stage_changed("Generate")
    assert wait(lambda: refreshed)


def test_a_failed_torch_warmup_says_why_on_the_chip(make_window, warmups, monkeypatch):
    # The chip names a missing model before the device; checkpoints/ is not committed, so
    # pin both model files present (any existing file) and the chip depends on nothing local.
    monkeypatch.setattr("app.inference.runtime.mask_weights_path", lambda: __file__)
    monkeypatch.setattr("app.inference.runtime.skeleton_weights_path", lambda: __file__)
    warmups.results[warmup.TORCH] = "DLL load failed while importing _C"
    window = make_window(warm_up=True)
    window.on_stage_changed("Generate")
    tip = window.statusline_info.toolTip
    assert wait(lambda: "DLL load failed while importing _C" in tip())
    assert window.statusline_info.text() == shell_chrome.DEVICE_CHECK_FAILED


def test_a_finished_job_freezes_what_it_imported(make_window, warmups, monkeypatch):
    """A Generate run that started before its warm-up imports torch itself."""
    window = make_window(warm_up=True)
    calls = []
    monkeypatch.setattr(window.warmups, "freeze_new_imports", calls.append)
    window.jobs.busy_changed.emit(True)
    assert calls == []
    window.jobs.busy_changed.emit(False)
    assert calls == ["a run's imports"]

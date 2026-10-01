"""The main window's parts with a stand-in window (review CQ-07).

Each part in ``app/gui/window/`` works on the window it is given, so a SimpleNamespace can
stand in for it and each branch runs without a MainWindow: closing (an editor or a job
that keeps the window open, a job that outlives both waits, a failing dashboard clean-up),
the metrics controller, the library navigator, the project loader and the editor switch.
The last tests build a MainWindow for what a stand-in cannot show: the measurement parse
reports to the window on the GUI thread, and reaches nothing once the window is gone.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QEvent, QSettings, QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.data_processing.measurement_index import Measurement  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from app.gui.window import (  # noqa: E402
    editors,
    lifecycle,
    metrics,
    navigator,
    project_loader,
    stages,
)
from app.gui.window.metrics import MetricsController  # noqa: E402
from tests.qt_teardown import delete_widgets, flush_deleted  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Event:
    """A close event: records ``ignore()``."""

    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class Item:
    """A library row holding ``name`` (None: a group row)."""

    def __init__(self, name: str | None) -> None:
        self.name = name

    def data(self, _column, _role):
        return self.name


# ------------------------------------------------------------------ closing
def closing_window(calls: list, *, keeps_edits=False, busy=False, stops=(True,), viz_fails=False):
    """A window to close: one editor, a job registry, the clean-up steps (all recorded)."""
    results = list(stops)
    editor = SimpleNamespace(maybe_save_changes=lambda: calls.append("prompt") or not keeps_edits)

    def close_length():
        calls.append("close length")
        if viz_fails:
            raise RuntimeError("the page is gone")

    window = SimpleNamespace(
        jobs=SimpleNamespace(
            is_busy=lambda: busy,
            running_name=lambda: "Generating masks",
            shutdown=lambda: calls.append("shutdown"),
            stop_all=lambda timeout_ms: calls.append(("stop_all", timeout_ms)) or results.pop(0),
        ),
        feedback=SimpleNamespace(status=lambda message, _timeout=0: calls.append(message)),
        hide=lambda: calls.append("hide"),
        warmups=SimpleNamespace(shutdown=lambda: calls.append("warmups")),
        device_check=SimpleNamespace(cancel=lambda: calls.append("device check")),
        root_length_viz=object(),
        root_area_viz=None,
        close_root_length_visualization=close_length,
        close_root_area_visualization=lambda: calls.append("close area"),
        _save_window_state=lambda: calls.append("save state"),
    )
    window._editors = lambda: {Panel.CORRECT: editor}
    window._stop_jobs_for_close = lambda: lifecycle.stop_jobs_for_close(window)
    return window


def answer(monkeypatch, button) -> None:
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: button))


def test_an_editor_that_keeps_its_edits_cancels_the_close(app):
    calls, event = [], Event()
    window = closing_window(calls, keeps_edits=True)
    assert lifecycle.close_event(window, event) is False
    assert event.ignored and calls == ["prompt"]  # no job was touched


def test_a_job_the_user_keeps_running_cancels_the_close(app, monkeypatch):
    answer(monkeypatch, QMessageBox.StandardButton.No)
    calls, event = [], Event()
    window = closing_window(calls, busy=True)
    assert lifecycle.close_event(window, event) is False
    assert event.ignored and calls == ["prompt"]  # the registry still takes jobs


def test_a_job_that_outlives_both_waits_is_logged_and_the_close_goes_on(app, monkeypatch, caplog):
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    calls, event = [], Event()
    window = closing_window(calls, busy=True, stops=(False, False))
    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        assert lifecycle.close_event(window, event) is True
    assert not event.ignored
    assert calls == [
        "prompt",
        "shutdown",
        "Stopping: Generating masks…",
        ("stop_all", lifecycle._CLOSE_WAIT_MS),
        "hide",
        ("stop_all", lifecycle._CLOSE_EXTRA_WAIT_MS),
        "warmups",
        "device check",
        "close length",
        "save state",
    ]
    assert "did not stop in time" in caplog.text


def test_a_failing_dashboard_clean_up_is_logged_and_the_layout_still_saved(app, caplog):
    calls, event = [], Event()
    window = closing_window(calls, viz_fails=True)
    with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
        assert lifecycle.close_event(window, event) is True
    assert calls[-2:] == ["close length", "save state"]
    assert "Error cleaning up visualizations on close" in caplog.text


def test_the_saved_layout_is_the_users_not_the_collapsed_one(caplog):
    calls = []
    window = SimpleNamespace(
        _persist_window_state=True,
        _pre_viz_splitter_sizes=[300, 900],
        left_panel=SimpleNamespace(setVisible=lambda shown: calls.append(("library", shown))),
        body_splitter=SimpleNamespace(setSizes=lambda sizes: calls.append(("sizes", sizes))),
        window_state=SimpleNamespace(save=lambda w, splitter: calls.append("save")),
    )
    lifecycle.save_window_state(window)
    assert calls == [("library", True), ("sizes", [300, 900]), "save"]

    window._persist_window_state = False
    lifecycle.save_window_state(window)
    assert calls[3:] == []  # a window that does not persist its layout saves nothing

    def fail(*_a):
        raise OSError("read-only settings")

    window._persist_window_state, window.window_state = True, SimpleNamespace(save=fail)
    with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
        lifecycle.save_window_state(window)  # logged, never raised
    assert "Failed to save the window state" in caplog.text


# ------------------------------------------------------------------ the metrics controller
class Bar:
    def __init__(self) -> None:
        self.shown: list[dict] = []

    def set_metrics(self, **values) -> None:
        self.shown.append(values)


class Index:
    """A measurement index stand-in: one measured image, counters for what was asked."""

    def __init__(self, *, needs_load: bool = False) -> None:
        self._needs_load = needs_load
        self.asked = self.prewarmed = 0

    def lookup(self, _name, load=False):
        return Measurement(12.5, 3.25, None)

    def needs_load(self) -> bool:
        self.asked += 1
        return self._needs_load

    def outdated(self, _name) -> dict:
        return {"length": False, "area": True}

    def sources(self) -> dict:
        return {"length": "root_lengths.csv", "area": "root_areas.csv"}

    def prewarm(self) -> None:
        self.prewarmed += 1


def metrics_window(*, bar=None, stage=None, checks=None) -> SimpleNamespace:
    checks = [] if checks is None else checks
    window = SimpleNamespace(
        image_manager=SimpleNamespace(
            has_mask=lambda name: name == "a",
            has_skeleton=lambda _name: False,
            has_correction=lambda _name: False,
            original_folder=None,
            images={},
        ),
        display_controller=SimpleNamespace(current_name="a"),
        stage_status=SimpleNamespace(check=lambda *a, **k: checks.append((a, k))),
        _current_stage=lambda: stage,
    )
    if bar is not None:
        window.metrics_bar = bar
    return window


class Pool:
    """The thread pool, recording the jobs it is given (run by hand)."""

    def __init__(self) -> None:
        self.jobs: list = []

    def globalInstance(self):  # noqa: N802 - Qt API name
        return self

    def start(self, job) -> None:
        self.jobs.append(job)


def test_the_bar_needs_a_metrics_bar():
    controller = MetricsController(metrics_window())
    controller.index = Index()
    controller.refresh("a")  # no metrics bar yet (before init_ui): nothing to update
    assert controller.index.asked == 0


def test_the_bar_shows_the_status_and_the_measured_values():
    bar = Bar()
    controller = MetricsController(metrics_window(bar=bar))
    controller.index = Index()
    controller.refresh("a")
    assert bar.shown == [
        {
            "length": 12.5,
            "area": 3.25,
            "status": "Mask ready",
            "name": "a",
            "error": None,
            "stale": False,
            "source": "root_lengths.csv",
            "area_stale": True,
            "area_source": "root_areas.csv",
            "length_note": None,
            "status_tip": None,
        }
    ]


def test_the_bar_never_asks_the_index_while_a_parse_runs():
    controller = MetricsController(metrics_window(bar=Bar()))
    controller.index, controller.loading = Index(needs_load=True), True
    controller.refresh("a")
    assert controller.index.asked == 0  # needs_load() waits for the parse's CSV lock


def test_one_parse_at_a_time_in_the_pool(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(metrics, "QThreadPool", pool)
    controller = MetricsController(metrics_window(bar=Bar()))
    controller.index = index = Index(needs_load=True)
    controller.prewarm()
    controller.prewarm()
    assert controller.loading and len(pool.jobs) == 1
    reported = []
    controller.signals.ready.connect(reported.append)
    pool.jobs[0]()  # the pool runs the job: it parses, then reports the index
    assert index.prewarmed == 1 and reported == [index]


def test_a_parse_of_a_replaced_project_refreshes_nothing():
    bar, checks = Bar(), []
    controller = MetricsController(metrics_window(bar=bar, stage="Measure", checks=checks))
    controller.index, controller.loading = Index(), True
    controller.on_ready(Index())  # the parse of the previous project's index
    assert controller.loading is False and bar.shown == [] and checks == []
    controller.on_ready(controller.index)
    assert len(bar.shown) == 1 and len(checks) == 1  # Measure's counts follow a new CSV


# ------------------------------------------------------------------ the library navigator
def test_the_debounce_starts_for_image_rows_only():
    started = []
    window = SimpleNamespace(_tree_load_timer=SimpleNamespace(start=lambda: started.append(1)))
    navigator.on_tree_current_changed(window, None)
    navigator.on_tree_current_changed(window, Item(None))  # a group row
    assert started == []
    navigator.on_tree_current_changed(window, Item("a"), Item("b"))
    assert started == [1]


def test_the_debounce_opens_a_new_image_and_leaves_the_keyboard_on_the_tree():
    opened, focused = [], []
    item = Item("b")
    window = SimpleNamespace(
        file_list=SimpleNamespace(
            currentItem=lambda: item, hasFocus=lambda: True, setFocus=lambda: focused.append(1)
        ),
        _shown_leaf_name="a",
        on_image_selected=opened.append,
    )
    navigator.load_current_tree_item(window)
    assert opened == [item] and focused == [1]
    window._shown_leaf_name = "b"  # already shown: nothing to load
    navigator.load_current_tree_item(window)
    assert opened == [item]


def paging_window(opened: list, step_to, *, editor=None) -> SimpleNamespace:
    selected = []
    window = SimpleNamespace(
        right_panel=SimpleNamespace(currentIndex=lambda: Panel.CORRECT),
        file_list=SimpleNamespace(
            currentItem=lambda: Item("tree"),
            setCurrentItem=selected.append,
            scrollToItem=lambda _item: None,
        ),
        library_index=SimpleNamespace(step=lambda current, delta, visible_only: step_to(current)),
        on_image_selected=opened.append,
        selected=selected,
    )
    window._editors = lambda: {} if editor is None else {Panel.CORRECT: editor}
    window._leaf_for_image = lambda path: Item(f"leaf of {path}")
    return window


def test_paging_steps_from_the_image_open_in_the_editor():
    opened, froms = [], []
    target = Item("next")
    editor = SimpleNamespace(current_image_path="open.jpg")
    window = paging_window(
        opened, lambda current: froms.append(current.name) or target, editor=editor
    )
    navigator.navigate_image(window, 1)
    assert froms == ["leaf of open.jpg"] and window.selected == [target] and opened == [target]


def test_paging_stops_after_the_last_image():
    opened = []
    window = paging_window(opened, lambda _current: None)
    navigator.navigate_image(window, 1)
    assert window.selected == [] and opened == []


def test_an_editor_that_kept_its_image_points_the_library_back(tmp_path):
    reselected = []
    window = SimpleNamespace(_select_tree_image=reselected.append)
    kept = str(tmp_path / "kept.jpg")
    editor = SimpleNamespace(current_image_path=kept)
    assert navigator.kept_other_image(window, editor, str(tmp_path / "asked.jpg")) is True
    assert reselected == [kept]
    assert navigator.kept_other_image(window, editor, kept) is False


# ------------------------------------------------------------------ the project loader
def loader_window(calls: list, *, keeps_edits=False) -> SimpleNamespace:
    editor = SimpleNamespace(maybe_save_changes=lambda: not keeps_edits)
    window = SimpleNamespace(
        notify=lambda message, kind="success", timeout=3200, key=None: calls.append(
            (message, kind, key)
        ),
        _refresh_recents=lambda: calls.append("recents"),
        app_stack=SimpleNamespace(setCurrentIndex=lambda i: calls.append(("page", i))),
        image_manager=SimpleNamespace(load_images=lambda d: calls.append("scan"), images={}),
    )
    window._editors = lambda: {Panel.CORRECT: editor}
    return window


def test_opening_a_missing_folder_says_so_and_changes_nothing(tmp_path):
    calls = []
    missing = str(tmp_path / "gone")
    project_loader.open_path(loader_window(calls), missing)
    assert calls == [(f"Folder not found: {missing}", "danger", "project-load"), "recents"]


def test_an_editor_that_keeps_its_edits_cancels_the_open(tmp_path):
    calls = []
    window = loader_window(calls, keeps_edits=True)
    project_loader.open_path(window, str(tmp_path))
    assert calls == [] and not hasattr(window, "_last_loaded_dir")


def test_a_folder_without_images_keeps_what_was_loaded(tmp_path):
    calls, done = [], []
    window = loader_window(calls)
    window._loading_project = True
    window.empty_state = SimpleNamespace(set_loading=lambda name: calls.append(("empty", name)))
    window.task_progress = SimpleNamespace(finish=done.append, fail=lambda m: calls.append(m))
    window._end_failed_load = lambda m, k: project_loader.end_failed_load(window, m, k)
    project_loader.on_loading_empty(window, str(tmp_path / "trial"))
    message = "No .jpg/.png images in trial — nothing loaded"
    assert window._loading_project is False and done == [message]
    # Nothing was loaded before: back to the Welcome screen.
    assert calls == [("empty", None), (message, "warn", "project-load"), ("page", 0)]


# ------------------------------------------------------------------ the editor pages
def switch_window(calls: list, *, keeps_edits: bool) -> SimpleNamespace:
    editor = SimpleNamespace(maybe_save_changes=lambda: not keeps_edits)
    window = SimpleNamespace(
        right_panel=SimpleNamespace(
            currentIndex=lambda: Panel.CORRECT, setCurrentIndex=calls.append
        )
    )
    window._editors = lambda: {Panel.CORRECT: editor}
    return window


def test_leaving_an_editor_that_keeps_its_edits_stays_in_it():
    calls = []
    assert editors.switch_right_panel(switch_window(calls, keeps_edits=True), "display") is False
    assert calls == []
    assert editors.switch_right_panel(switch_window(calls, keeps_edits=False), "display") is True
    assert calls == [Panel.DISPLAY]


def test_an_unknown_page_is_logged_and_changes_nothing(caplog):
    calls = []
    with caplog.at_level(logging.WARNING, logger=editors.logger.name):
        assert editors.switch_right_panel(switch_window(calls, keeps_edits=False), "x") is True
    assert calls == [] and "Unknown right panel" in caplog.text


# ------------------------------------------------------------------ the stages
def test_visualize_before_a_folder_names_the_first_step():
    window = SimpleNamespace(
        root_length_viz=None, image_manager=SimpleNamespace(original_folder=None, images={})
    )
    assert stages.missing_dashboard_data(window, "length") == (
        "No images loaded",
        "Open a folder of root images first (Ctrl+O).",
        "",
        None,
    )
    window.root_length_viz = object()  # open already: the dashboard controller decides
    assert stages.missing_dashboard_data(window, "length") is None


# ------------------------------------------------------------------ the pool's report
@pytest.fixture
def make_window(app, tmp_path):
    made: list = []

    def make():
        settings = QSettings(str(tmp_path / f"s{len(made)}.ini"), QSettings.Format.IniFormat)
        made.append(MainWindow(settings=settings))
        return made[-1]

    yield make
    QThreadPool.globalInstance().waitForDone(10_000)
    delete_widgets(made)


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_the_parse_reports_to_the_window_on_the_gui_thread(make_window, monkeypatch):
    window = make_window()
    ran_on = []
    monkeypatch.setattr(
        window.metrics, "on_ready", lambda index: ran_on.append(threading.get_ident())
    )
    signals = window.metrics.signals
    QThreadPool.globalInstance().start(lambda: signals.ready.emit(object()))
    assert wait(lambda: ran_on)
    assert ran_on == [threading.main_thread().ident]


def test_a_parse_that_ends_after_the_window_is_gone_reaches_nothing(app, tmp_path, monkeypatch):
    """The window, not the controller, receives the pool's report: once the window's C++
    side is deleted (its Python wrapper and the controller may live on until the next
    garbage collection), a parse that ends later reaches nothing."""
    reached, hooked = [], []
    monkeypatch.setattr(MetricsController, "on_ready", lambda self, index: reached.append(index))
    monkeypatch.setattr(sys, "excepthook", lambda *exc: hooked.append(exc))
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    signals = window.metrics.signals  # the worker's closure keeps it, as in prewarm()
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    try:
        assert sip.isdeleted(window)  # the wrapper (and its controller) are still here
        done = threading.Event()
        QThreadPool.globalInstance().start(lambda: (signals.ready.emit(object()), done.set()))
        assert done.wait(5)
        for _ in range(5):
            QApplication.processEvents()
        assert reached == [] and hooked == []
    finally:
        del window
        flush_deleted()

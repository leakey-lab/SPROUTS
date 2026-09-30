"""viz-embed: dashboard management for the main window (app/gui/visualization_manager.py).

A light stand-in for MainWindow (a real right_panel QStackedWidget and library
QTreeWidget, recording fakes for the rest) and stub dashboards that keep the real
widgets' contract: ``cleanup_server()`` emits ``server_closed`` once while a server
runs, plus ``export_saved`` and ``failed``. No browser and no Dash server start.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtOpenGLWidgets import QOpenGLWidget  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

import app.project_layout as project_layout  # noqa: E402
from app.gui import visualization_manager as vm  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

#: The fixed right-panel pages, in page order: ``switch_right_panel`` key -> index.
_PANELS = {panel.key: int(panel) for panel in Panel}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
#  Stand-ins
# --------------------------------------------------------------------------- #
class StubDashboard(QMainWindow):
    """Keeps the real dashboards' contract without a browser or a server."""

    server_closed = pyqtSignal()
    export_saved = pyqtSignal(str)
    made: list = []

    def __init__(self, csv_path):
        super().__init__()
        self.csv_path = csv_path
        self.failed = False
        self.cleanups = 0
        self._serving = True
        type(self).made.append(self)

    def cleanup_server(self):
        self.cleanups += 1
        if self._serving:  # emitted once, while a server runs
            self._serving = False
            self.server_closed.emit()


class StubLength(StubDashboard):
    made: list = []

    def __init__(self, csv_path, image_manager):
        super().__init__(csv_path)
        self.image_manager = image_manager


class StubArea(StubDashboard):
    made: list = []


class FakeImageManager:
    def __init__(self, folder):
        self.original_folder = str(folder)

    def get_image_path(self, name):
        return os.path.join(self.original_folder, f"{name}.jpg")


class FakeSkeletonHandler:
    def __init__(self):
        self.busy_kinds: set[str] = set()
        self.asked: list[str] = []

    def is_busy(self, kind: str) -> bool:
        self.asked.append(kind)
        return kind in self.busy_kinds


class FakeMainWindow(QWidget):
    """The parts of MainWindow that visualization_manager uses."""

    def __init__(self, project, *, opengl: bool):
        super().__init__()
        self.right_panel = QStackedWidget(self)
        for name in _PANELS:
            page = QWidget()
            page.setObjectName(name)
            self.right_panel.addWidget(page)
        self.file_list = QTreeWidget(self)
        folder = QTreeWidgetItem(["T001"])
        leaf = QTreeWidgetItem(["img_001.jpg"])
        leaf.setData(0, Qt.ItemDataRole.UserRole, "img_001")
        folder.addChild(leaf)
        self.file_list.addTopLevelItem(folder)
        self.file_list.setCurrentItem(leaf)
        self.image_manager = FakeImageManager(project)
        self.skeleton_handler = FakeSkeletonHandler()
        self.visualize_root_length_button = QPushButton("Visualize Root Length", self)
        self.visualize_root_area_button = QPushButton("Visualize Root Area", self)
        self.root_length_viz = None
        self.root_area_viz = None
        self.leave_allowed = True  # False: an editor with unsaved edits refuses to close
        self.gl_calls: list[bool] = []
        self.refreshed: list = []
        self.notices: list[tuple[str, str]] = []
        # An OpenGL canvas viewport, as the display/editor views have by default.
        self._gl_viewport = QOpenGLWidget(self) if opengl else None

    def switch_right_panel(self, panel):
        if not self.leave_allowed:
            return False
        self.right_panel.setCurrentIndex(_PANELS[panel])
        return True

    def set_opengl_viewports_enabled(self, enabled):
        self.gl_calls.append(enabled)
        if enabled and self._gl_viewport is None:
            self._gl_viewport = QOpenGLWidget(self)
        elif not enabled and self._gl_viewport is not None:
            self._gl_viewport.setParent(None)
            self._gl_viewport.deleteLater()
            self._gl_viewport = None

    def on_image_selected(self, item):
        self.refreshed.append(item)

    def notify(self, message, kind="success", timeout=3200):
        self.notices.append((message, kind))

    # MainWindow's server_closed slots, verbatim.
    def on_visualization_server_closed(self):
        if self.root_length_viz:
            vm.close_root_length_visualization(self)

    def on_area_visualization_server_closed(self):
        if self.root_area_viz:
            vm.close_root_area_visualization(self)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "skeletons").mkdir()
    (tmp_path / "mask").mkdir()
    (tmp_path / "skeletons" / "root_lengths.csv").write_text(
        "Image,Length (mm)\nimg_001,1.5\n", encoding="utf-8"
    )
    (tmp_path / "mask" / "root_areas.csv").write_text(
        "Image,Area (mm²)\nimg_001,0.5\n", encoding="utf-8"
    )
    return tmp_path


def _environment(qapp, monkeypatch, project, *, opengl: bool):
    StubLength.made = []
    StubArea.made = []
    monkeypatch.setattr(vm, "RootLengthVisualization", StubLength)
    monkeypatch.setattr(vm, "RootAreaVisualization", StubArea)
    boxes: list[tuple[str, str, str]] = []

    class Box:
        @staticmethod
        def information(_parent, title, text, *_rest):
            boxes.append(("information", title, text))

        @staticmethod
        def warning(_parent, title, text, *_rest):
            boxes.append(("warning", title, text))

        @staticmethod
        def critical(_parent, title, text, *_rest):
            boxes.append(("critical", title, text))

    monkeypatch.setattr(vm, "QMessageBox", Box)
    return SimpleNamespace(mw=FakeMainWindow(project, opengl=opengl), boxes=boxes, project=project)


@pytest.fixture
def env(qapp, monkeypatch, project):
    ctx = _environment(qapp, monkeypatch, project, opengl=False)
    yield ctx
    ctx.mw.deleteLater()
    del ctx
    flush_deleted()
    QCoreApplication.processEvents()


@pytest.fixture
def env_gl(qapp, monkeypatch, project):
    ctx = _environment(qapp, monkeypatch, project, opengl=True)
    yield ctx
    ctx.mw.deleteLater()
    del ctx
    flush_deleted()
    QCoreApplication.processEvents()


def _touch_newer(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


# --------------------------------------------------------------------------- #
#  Show is idempotent (UI-05, PV-04, RB-14)
# --------------------------------------------------------------------------- #
def test_show_opens_a_dashboard_once_then_only_brings_it_forward(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    first = mw.root_length_viz
    mw.switch_right_panel("display")

    vm.show_root_length_visualization(mw)

    assert StubLength.made == [first]
    assert mw.right_panel.currentWidget() is first
    assert first.cleanups == 0


def test_length_dashboard_reads_the_skeletons_csv_with_the_image_manager(env):
    vm.show_root_length_visualization(env.mw)

    (dashboard,) = StubLength.made
    expected = env.project / "skeletons" / "root_lengths.csv"
    assert os.path.normcase(dashboard.csv_path) == os.path.normcase(str(expected))
    assert dashboard.image_manager is env.mw.image_manager


def test_area_dashboard_reads_the_mask_csv(env):
    vm.show_root_area_visualization(env.mw)

    (dashboard,) = StubArea.made
    expected = env.project / "mask" / "root_areas.csv"
    assert os.path.normcase(dashboard.csv_path) == os.path.normcase(str(expected))


def test_showing_one_dashboard_hides_the_other_without_closing_it(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    vm.show_root_area_visualization(mw)

    vm.show_root_length_visualization(mw)

    assert len(StubLength.made) == 1 and len(StubArea.made) == 1
    assert mw.right_panel.currentWidget() is mw.root_length_viz
    assert mw.root_area_viz is StubArea.made[0]
    assert mw.root_area_viz.cleanups == 0
    assert vm.visible_kind(mw) == "length"


def test_visible_kind_follows_the_panel(env):
    mw = env.mw
    assert vm.visible_kind(mw) is None

    vm.show_root_area_visualization(mw)
    assert vm.visible_kind(mw) == "area"

    mw.switch_right_panel("display")
    assert vm.visible_kind(mw) is None


def test_dashboard_is_rebuilt_when_its_csv_changed_on_disk(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    old = mw.root_length_viz
    _touch_newer(env.project / "skeletons" / "root_lengths.csv", "Image,Length (mm)\n")

    vm.show_root_length_visualization(mw)

    assert old.cleanups == 1
    assert mw.right_panel.indexOf(old) == -1
    assert len(StubLength.made) == 2
    assert mw.root_length_viz is StubLength.made[1]
    assert mw.right_panel.currentWidget() is StubLength.made[1]


def test_failed_dashboard_is_rebuilt_on_show(env):
    mw = env.mw
    vm.show_root_area_visualization(mw)
    broken = mw.root_area_viz
    broken.failed = True

    vm.show_root_area_visualization(mw)

    assert broken.cleanups == 1
    assert len(StubArea.made) == 2
    assert mw.right_panel.currentWidget() is mw.root_area_viz


def test_generic_api_takes_the_dashboard_kind(env):
    mw = env.mw
    vm.show_visualization(mw, "area")
    assert mw.root_area_viz is not None

    vm.close_visualization(mw, "area")
    assert mw.root_area_viz is None

    with pytest.raises(ValueError):
        vm.show_visualization(mw, "volume")


# --------------------------------------------------------------------------- #
#  Guards before anything is built
# --------------------------------------------------------------------------- #
def test_missing_data_leaves_the_panels_and_opengl_alone(env):
    mw = env.mw
    os.remove(env.project / "skeletons" / "root_lengths.csv")

    vm.show_root_length_visualization(mw)

    assert StubLength.made == []
    assert mw.gl_calls == []
    assert mw.right_panel.currentIndex() == 0
    assert [box[0] for box in env.boxes] == ["information"]


def test_primary_csv_skips_the_legacy_folder_search(env, monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        return None

    monkeypatch.setattr(project_layout, "find_test_latest_dir", spy)
    monkeypatch.setattr(vm, "find_test_latest_dir", spy)

    vm.show_root_length_visualization(env.mw)

    assert len(StubLength.made) == 1
    assert calls == []


def test_show_waits_while_the_data_is_being_measured(env):
    mw = env.mw
    mw.skeleton_handler.busy_kinds = {"area"}

    vm.show_root_area_visualization(mw)

    assert StubArea.made == []
    assert mw.skeleton_handler.asked == ["area"]
    assert [box[0] for box in env.boxes] == ["information"]


def test_busy_check_falls_back_to_the_calculator_threads(env):
    class Running:
        def isRunning(self):
            return True

    class OldHandler:  # a SkeletonHandler without is_busy()
        calculator_thread = Running()
        area_calculator_thread = None

    env.mw.skeleton_handler = OldHandler()

    vm.show_root_length_visualization(env.mw)
    vm.show_root_area_visualization(env.mw)

    assert StubLength.made == []
    assert len(StubArea.made) == 1


def test_an_editor_that_refuses_to_close_keeps_its_panel(env):
    mw = env.mw
    mw.right_panel.setCurrentIndex(_PANELS["gt_tracing"])
    mw.leave_allowed = False

    vm.show_root_length_visualization(mw)

    assert StubLength.made == []
    assert mw.gl_calls == []
    assert mw.right_panel.currentIndex() == _PANELS["gt_tracing"]


def test_no_images_loaded_warns_and_opens_nothing(env):
    env.mw.file_list.clear()

    vm.show_root_length_visualization(env.mw)

    assert StubLength.made == []
    assert [box[0] for box in env.boxes] == ["warning"]


# --------------------------------------------------------------------------- #
#  Toggle and close (RB-12)
# --------------------------------------------------------------------------- #
def test_toggle_keeps_its_open_then_close_meaning(env):
    mw = env.mw
    vm.toggle_root_area_visualization(mw)
    dashboard = mw.root_area_viz
    assert dashboard is not None
    assert "Close" in mw.visualize_root_area_button.text()

    vm.toggle_root_area_visualization(mw)

    assert mw.root_area_viz is None
    assert dashboard.cleanups == 1
    assert mw.right_panel.indexOf(dashboard) == -1
    assert mw.right_panel.currentIndex() == _PANELS["display"]
    assert "Close" not in mw.visualize_root_area_button.text()


def test_close_cleans_up_once_although_server_closed_re_enters(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    dashboard = mw.root_length_viz

    vm.close_root_length_visualization(mw)

    assert dashboard.cleanups == 1
    assert len(mw.refreshed) == 1  # the current image is re-displayed once
    assert mw.root_length_viz is None


def test_closing_a_hidden_dashboard_keeps_the_current_panel(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    vm.show_root_area_visualization(mw)

    vm.close_root_length_visualization(mw)

    assert mw.right_panel.currentWidget() is mw.root_area_viz
    assert mw.refreshed == []


def test_exports_are_announced_in_the_main_window(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)

    mw.root_length_viz.export_saved.emit(str(env.project / "skeletons" / "plot.svg"))

    assert mw.notices and "plot.svg" in mw.notices[-1][0]


# --------------------------------------------------------------------------- #
#  OpenGL viewports vs. WebEngine (PV-12, UI-16)
# --------------------------------------------------------------------------- #
def test_opengl_stays_off_until_the_last_dashboard_closes(env_gl):
    mw = env_gl.mw
    vm.show_root_length_visualization(mw)
    vm.show_root_area_visualization(mw)

    vm.close_root_length_visualization(mw)
    assert mw.gl_calls == [False]  # the area dashboard still embeds a browser

    vm.close_root_area_visualization(mw)
    assert mw.gl_calls == [False, True]


def test_opengl_is_not_forced_on_when_it_was_off_before(env):
    mw = env.mw  # canvases on raster viewports (opt-out or no GL support)
    vm.show_root_length_visualization(mw)

    vm.close_root_length_visualization(mw)

    assert True not in mw.gl_calls


# --------------------------------------------------------------------------- #
#  Another project was opened (PKG-VIZ, V3)
# --------------------------------------------------------------------------- #
def test_opening_another_project_releases_both_dashboards(env_gl):
    # Before, both dashboards kept charting the previous project's CSVs (and their
    # servers, pages and the OpenGL hold) until they were shown again; the one on
    # screen stayed on screen under the new project's library.
    mw = env_gl.mw
    vm.show_root_length_visualization(mw)
    vm.show_root_area_visualization(mw)  # on screen
    length, area = mw.root_length_viz, mw.root_area_viz

    shown = vm.release_all_visualizations(mw)

    assert shown is True
    assert mw.root_length_viz is None and mw.root_area_viz is None
    assert (length.cleanups, area.cleanups) == (1, 1)
    assert mw.right_panel.indexOf(length) == -1 and mw.right_panel.indexOf(area) == -1
    assert mw.right_panel.currentIndex() == _PANELS["display"]
    assert mw.gl_calls == [False, True]  # the canvases get OpenGL back
    assert mw.refreshed == []  # the old project's image is not shown again
    assert "Close" not in mw.visualize_root_length_button.text()


def test_releasing_hidden_dashboards_keeps_the_current_page(env):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    mw.switch_right_panel("display")

    assert vm.release_all_visualizations(mw) is False
    assert mw.root_length_viz is None
    assert mw.right_panel.currentIndex() == _PANELS["display"]


def test_releasing_without_any_dashboard_does_nothing(env):
    assert vm.release_all_visualizations(env.mw) is False
    assert env.mw.gl_calls == []


def test_a_dashboard_opened_after_the_release_reads_the_new_project(env, tmp_path_factory):
    mw = env.mw
    vm.show_root_length_visualization(mw)
    other = tmp_path_factory.mktemp("other_project")
    (other / "skeletons").mkdir()
    (other / "skeletons" / "root_lengths.csv").write_text(
        "Image,Length (mm)\nimg_002,2.5\n", encoding="utf-8"
    )
    mw.image_manager = FakeImageManager(other)

    vm.release_all_visualizations(mw)
    vm.show_root_length_visualization(mw)

    assert len(StubLength.made) == 2
    expected = other / "skeletons" / "root_lengths.csv"
    assert os.path.normcase(mw.root_length_viz.csv_path) == os.path.normcase(str(expected))


# --------------------------------------------------------------------------- #
#  Prewarm (PKG-VIZ, V2)
# --------------------------------------------------------------------------- #
def test_prewarm_dashboards_imports_off_the_gui_thread(qapp, monkeypatch):
    import threading

    import app.visualization.viz_window as vw

    threads, engine = [], []
    done = threading.Event()

    def imports():
        threads.append(threading.current_thread())
        done.set()

    monkeypatch.setattr(vw, "prewarm_imports", imports)
    monkeypatch.setattr(vw, "prewarm_web_engine", lambda: engine.append(1) or True)

    vm.prewarm_dashboards(web_engine=False)

    assert done.wait(5)
    assert threads[0] is not threading.main_thread()
    assert engine == []

    done.clear()
    vm.prewarm_dashboards()
    assert done.wait(5)
    assert engine == [1]  # the engine starts on the calling (GUI) thread


def test_a_failing_import_prewarm_is_only_logged(qapp, monkeypatch, caplog):
    import threading

    import app.visualization.viz_window as vw

    done = threading.Event()

    def broken():
        done.set()
        raise ImportError("dash is not installed")

    monkeypatch.setattr(vw, "prewarm_imports", broken)

    vm.prewarm_dashboards(web_engine=False)

    assert done.wait(5)
    from PyQt6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    assert "dash is not installed" in caplog.text


# --------------------------------------------------------------------------- #
#  Idle release: dashboards close a while after the Visualize stage is left
#  (R-VIZ X1). Revisits within it stay instant; after it the canvases get
#  OpenGL back and the browser processes are freed.
# --------------------------------------------------------------------------- #
def _visit_and_leave(mw, stage="Correct"):
    vm.stage_changed(mw, "Visualize")
    vm.show_root_length_visualization(mw)
    mw.switch_right_panel("display")
    vm.stage_changed(mw, stage)
    return vm.controller_for(mw)


def test_leaving_visualize_counts_down_to_closing_the_dashboards(env_gl):
    mw = env_gl.mw
    controller = _visit_and_leave(mw)
    dashboard = mw.root_length_viz

    assert controller.idle_release_pending()
    assert controller._idle_timer.interval() == vm.IDLE_RELEASE_MS == 5 * 60 * 1000
    assert mw.gl_calls == [False]  # raster canvases while the dashboard lives

    controller._idle_timer.stop()
    controller._on_idle()  # the countdown ended

    assert mw.root_length_viz is None and dashboard.cleanups == 1
    assert mw.gl_calls == [False, True]  # OpenGL canvases again
    assert mw.right_panel.currentIndex() == _PANELS["display"]  # the page shown stays
    assert mw.refreshed == []


def test_the_countdown_really_fires(env, monkeypatch):
    monkeypatch.setattr(vm, "IDLE_RELEASE_MS", 30)
    mw = env.mw
    _visit_and_leave(mw)

    assert _wait(lambda: mw.root_length_viz is None, 3.0)


def _wait(predicate, seconds):
    import time

    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_coming_back_within_the_countdown_keeps_the_live_dashboards(env):
    mw = env.mw
    controller = _visit_and_leave(mw)
    dashboard = mw.root_length_viz

    vm.stage_changed(mw, "Visualize")
    vm.show_root_length_visualization(mw)

    assert not controller.idle_release_pending()
    assert mw.root_length_viz is dashboard and dashboard.cleanups == 0


def test_moving_between_other_stages_keeps_the_original_deadline(env, monkeypatch):
    mw = env.mw
    controller = _visit_and_leave(mw, "Correct")
    started = []
    monkeypatch.setattr(controller._idle_timer, "start", lambda *a: started.append(a))

    vm.stage_changed(mw, "Library")
    vm.stage_changed(mw, "Trace GT")

    assert started == []  # still counting from when Visualize was left
    assert controller.idle_release_pending()


def test_a_dashboard_back_on_screen_is_never_closed_by_the_countdown(env):
    mw = env.mw
    controller = _visit_and_leave(mw)
    vm.show_root_length_visualization(mw)  # e.g. a legacy button, no stage report

    controller._idle_timer.stop()
    controller._on_idle()

    assert mw.root_length_viz is not None and mw.root_length_viz.cleanups == 0


@pytest.mark.parametrize("busy", ["mouse", "popup", "modal"])
def test_a_release_during_a_stroke_or_dialog_waits(env, monkeypatch, busy):
    # The release swaps the canvases' viewports (raster -> OpenGL): never under a
    # brush stroke, a drag or an open dialog.
    mw = env.mw
    controller = _visit_and_leave(mw)
    if busy == "mouse":
        monkeypatch.setattr(
            vm.QApplication, "mouseButtons", staticmethod(lambda: Qt.MouseButton.LeftButton)
        )
    elif busy == "popup":
        monkeypatch.setattr(vm.QApplication, "activePopupWidget", staticmethod(lambda: mw))
    else:
        monkeypatch.setattr(vm.QApplication, "activeModalWidget", staticmethod(lambda: mw))

    controller._idle_timer.stop()
    controller._on_idle()

    assert mw.root_length_viz is not None  # kept for now
    assert controller.idle_release_pending()
    assert controller._idle_timer.interval() == vm.IDLE_RELEASE_RETRY_MS
    controller._idle_timer.stop()


def test_stage_changes_before_any_dashboard_create_nothing(env):
    vm.stage_changed(env.mw, "Correct")
    vm.stage_changed(env.mw, "Visualize")

    assert env.mw not in vm._controllers


def test_leaving_visualize_without_a_live_dashboard_starts_no_countdown(env):
    mw = env.mw
    controller = _visit_and_leave(mw)
    vm.close_root_length_visualization(mw)
    controller._idle_timer.stop()

    vm.stage_changed(mw, "Visualize")
    vm.stage_changed(mw, "Library")

    assert not controller.idle_release_pending()


def test_opening_another_project_ends_the_countdown(env):
    mw = env.mw
    controller = _visit_and_leave(mw)

    vm.release_all_visualizations(mw)

    assert not controller.idle_release_pending()


def test_the_countdown_does_not_keep_its_controller_alive(env):
    import gc
    import weakref as _weakref

    controller = vm.DashboardController(env.mw)  # not registered for the window
    controller._idle_timer.start(60_000)
    ref = _weakref.ref(controller)
    del controller
    gc.collect()

    assert ref() is None  # the timer's slot holds the controller weakly

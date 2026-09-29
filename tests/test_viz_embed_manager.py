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

from PyQt6.QtCore import QCoreApplication, QEvent, Qt, pyqtSignal  # noqa: E402
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

_PANELS = {
    "display": 0,
    "mask_tracing": 1,
    "visualization": 2,
    "skeleton_correction": 3,
    "gt_tracing": 4,
}


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


def _flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QCoreApplication.processEvents()


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
    _flush_deletes()


@pytest.fixture
def env_gl(qapp, monkeypatch, project):
    ctx = _environment(qapp, monkeypatch, project, opengl=True)
    yield ctx
    ctx.mw.deleteLater()
    _flush_deletes()


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

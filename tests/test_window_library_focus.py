"""Entering Visualize keeps the keyboard focus off the stage buttons (V2).

A dashboard collapses the library panel. With the library tree focused (the normal state
after picking an image), Qt passed the focus on to the first stage button when the panel
hid: the top bar showed two highlighted stages (a focus ring on Library, Visualize filled),
Space left the dashboard, and back in the Library the arrow keys were dead until the tree
was clicked. The focus now goes to the dashboard page when the library hides with it, and
the library gets it back when it returns. Only the two dashboards hide the library (the
Visualize stage's page without a CSV keeps it), so both are tested, entered by a click and
by Alt+6, and left for every other stage.

Offscreen; the dashboards are stand-ins (building one needs Dash and a browser).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.config import ROOT_AREAS_CSV, ROOT_LENGTHS_CSV  # noqa: E402
from app.gui import canvas_gl, image_manager  # noqa: E402
from app.gui import visualization_manager as vm  # noqa: E402
from app.gui.correct.layer import SKELETON  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402
from tests.test_correct_editor import qapp  # noqa: E402
from tests.test_correct_main_window import (  # noqa: E402
    STAGE_NAMES,
    activate,
    leaf,
    new_project,
    open_project,
    select,
    settle,
)

ALT = Qt.KeyboardModifier.AltModifier
#: The dashboard classes the controller builds, resolved on ``visualization_manager``.
DASHBOARD_CLASSES = ("RootLengthVisualization", "RootAreaVisualization")
_UNSET = object()


class StubDashboard(QMainWindow):
    """A dashboard page without Dash or a browser: the real one's signals and clean-up, and
    nothing that takes the focus, as the real page while it loads (W4 saw the focus ring on
    Library with it and with this stand-in)."""

    server_closed = pyqtSignal()
    export_saved = pyqtSignal(str)
    VIEW_FOCUS = Qt.FocusPolicy.NoFocus

    def __init__(self, csv_path, *_args):
        super().__init__()
        self.csv_path = csv_path
        self.failed = False
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.web_view = QWidget(central)
        self.web_view.setFocusPolicy(self.VIEW_FOCUS)
        layout.addWidget(self.web_view)
        self.setCentralWidget(central)

    def cleanup_server(self) -> None:
        pass


class LoadedStubDashboard(StubDashboard):
    """A dashboard whose page has loaded: its view takes the focus (Tab, a click)."""

    VIEW_FOCUS = Qt.FocusPolicy.StrongFocus


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    """No prompt, message box or file dialog, no GL, no delayed temp sweep, and stand-in
    dashboards (set on the module itself: a monkeypatch would import the Dash stack to
    read the old value)."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name: pytest.fail(f"box {n}: {a}"))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: pytest.fail("dialog"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    saved = {name: vm.__dict__.get(name, _UNSET) for name in DASHBOARD_CLASSES}
    for name in DASHBOARD_CLASSES:
        setattr(vm, name, StubDashboard)
    yield
    for name, value in saved.items():
        if value is _UNSET:
            vm.__dict__.pop(name, None)
        else:
            setattr(vm, name, value)


def _quiet(window) -> None:
    for editor in window._editors().values():
        editor.maybe_save_changes = lambda: True
    window.correct_editor.layers[SKELETON].cancel_scheduled_render()


@pytest.fixture
def window(app, tmp_path):
    """A shown, active 1280x800 window on a project with both CSVs, image 1 selected by a
    click and the library tree focused."""
    from app.gui.main_window import MainWindow

    made = [MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))]
    shown = made[0]
    shown.resize(1280, 800)
    shown.show()
    settle()
    project = new_project(tmp_path)
    for folder, name in (("skeletons", ROOT_LENGTHS_CSV), ("mask", ROOT_AREAS_CSV)):
        with open(os.path.join(project.folder, folder, name), "w", encoding="utf-8") as fh:
            fh.write("Image\n")
    open_project(shown, project)
    activate(shown)
    select(shown, 1)
    shown.file_list.setFocus()
    settle()
    assert QApplication.focusWidget() is shown.file_list
    del shown
    yield made[0]
    delete_widgets(made, prepare=_quiet)
    QApplication.processEvents()


def press(key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """A key press on the widget that has the keyboard."""
    QTest.keyClick(QApplication.focusWidget(), key, modifiers)
    settle()


def go_to(window, stage: str, how: str) -> None:
    """Enter ``stage`` by a click on its button or by its Alt+<n> key."""
    if how == "click":
        QTest.mouseClick(window.ribbon_buttons[stage], Qt.MouseButton.LeftButton)
        settle()
    else:
        press(Qt.Key(Qt.Key.Key_1.value + STAGE_NAMES.index(stage)), ALT)


def enter_dashboard(window, kind: str, how: str = "click"):
    """Enter Visualize on the ``kind`` dashboard; returns its page."""
    if kind == "area":
        window.action_bar_viz_seg.setValue("area", emit=False)
    go_to(window, "Visualize", how)
    page = window.right_panel.currentWidget()
    assert page is getattr(window, f"root_{kind}_viz") and page is not None
    assert not window.left_panel.isVisible()  # the dashboard has the width
    return page


def highlighted_stages(window) -> list[str]:
    """The stage buttons that look highlighted: checked, or showing the focus ring."""
    return [key for key, b in window.ribbon_buttons.items() if b.isChecked() or b.hasFocus()]


# ================================================================ entering a dashboard
@pytest.mark.parametrize("how", ["click", "Alt+6"])
@pytest.mark.parametrize("kind", ["length", "area"])
def test_the_dashboard_page_takes_the_focus_the_library_had(window, kind, how):
    page = enter_dashboard(window, kind, how)

    focus = QApplication.focusWidget()
    assert focus is page or page.isAncestorOf(focus), focus
    assert highlighted_stages(window) == ["Visualize"]


def test_space_on_the_dashboard_stays_in_visualize(window):
    page = enter_dashboard(window, "length")

    press(Qt.Key.Key_Space)

    assert window.ribbon_buttons["Visualize"].isChecked()
    assert window.right_panel.currentWidget() is page


@pytest.mark.parametrize("kind", ["length", "area"])
def test_a_loaded_dashboard_keeps_the_focus_inside_its_page(window, kind):
    for name in DASHBOARD_CLASSES:
        setattr(vm, name, LoadedStubDashboard)

    page = enter_dashboard(window, kind, "Alt+6")

    focus = QApplication.focusWidget()
    assert focus is page or page.isAncestorOf(focus), focus
    assert highlighted_stages(window) == ["Visualize"]


def test_switching_dashboards_keeps_the_focus_on_the_page(window):
    enter_dashboard(window, "length")

    window.action_bar_viz_seg.button("area").click()
    settle()

    page = window.right_panel.currentWidget()
    assert page is window.root_area_viz
    focus = QApplication.focusWidget()
    assert focus is page or page.isAncestorOf(focus), focus
    assert highlighted_stages(window) == ["Visualize"]


# ================================================================ leaving it again
def keyboard_owner(window, stage: str):
    """Who has the keyboard after ``stage`` is entered from the Library with the tree
    focused: the editor's canvas in Correct and Trace GT (an editor takes the keys when it
    shows and when it opens an image), else the tree."""
    editor = {"Correct": window.correct_editor, "Trace GT": window.gt_tracing_interface}
    return editor[stage].graphics_view if stage in editor else window.file_list


@pytest.mark.parametrize("how", ["click", "Alt+n"])
@pytest.mark.parametrize("stage", [s for s in STAGE_NAMES if s != "Visualize"])
def test_leaving_the_dashboard_gives_the_keyboard_as_from_the_library(window, stage, how):
    go_to(window, stage, how)
    expected = keyboard_owner(window, stage)
    assert QApplication.focusWidget() is expected  # the reference: entered from the Library
    go_to(window, "Library", how)
    window.file_list.setFocus()
    settle()
    enter_dashboard(window, "length")

    go_to(window, stage, how)

    assert window.left_panel.isVisible()
    assert window.ribbon_buttons[stage].isChecked()
    assert QApplication.focusWidget() is expected
    assert highlighted_stages(window) == [stage]


def test_back_in_the_library_the_arrow_keys_browse_again(window):
    enter_dashboard(window, "area", "Alt+6")
    go_to(window, "Library", "Alt+n")

    press(Qt.Key.Key_Down)

    assert window.file_list.currentItem() is leaf(window, 2)


def test_the_library_gives_back_only_the_focus_it_had(window):
    canvas = window.display_area
    canvas.setFocus()
    settle()
    assert QApplication.focusWidget() is canvas
    enter_dashboard(window, "length", "Alt+6")

    go_to(window, "Library", "Alt+n")

    focus = QApplication.focusWidget()
    assert focus is not window.file_list
    assert focus is not None and window.right_panel.widget(Panel.DISPLAY).isAncestorOf(focus)
    assert highlighted_stages(window) == ["Library"]


def test_a_dashboard_opened_while_the_window_is_inactive_has_the_focus_on_return(window):
    """A run ends while the user is in another window: the waiting Visualize page opens its
    dashboard. Back in the window, the dashboard page has the keyboard, not a stage button."""
    other = QWidget()
    other.resize(200, 100)
    other.show()
    other.activateWindow()
    try:
        assert QTest.qWaitForWindowActive(other, 3000)
        assert window.focusWidget() is window.file_list  # still the window's focus widget
        window.ribbon.activate_stage("Visualize")
        window.show_dashboard("length")
        settle()
        page = window.right_panel.currentWidget()
        assert page is window.root_length_viz and not window.left_panel.isVisible()

        activate(window)

        focus = QApplication.focusWidget()
        assert focus is page or page.isAncestorOf(focus), focus
        assert highlighted_stages(window) == ["Visualize"]
    finally:
        other.close()
        other.deleteLater()
        del other

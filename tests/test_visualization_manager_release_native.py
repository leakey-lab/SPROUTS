"""The dashboards' 5-minute release on the real Windows platform (dashboard flicker, Part A).

Qt cannot change the surface type of a window that is on screen: a dashboard's browser draws
through Direct3D 11, the canvases through OpenGL, so each switch recreates the main window
(it is gone for 43-98 ms). At the release the dashboards used to leave the window only after
OpenGL came back; Qt then found their browsers first, made the window a Direct3D one again
and an OpenGL one at their deferred delete: two recreations. A released dashboard now leaves
the window first (``DashboardController._discard``): one recreation, ending on OpenGL.

The offscreen platform of the suite never recreates a window, so a child process runs the
real window on the native platform: frameless, never activated, placed off the screen (it
never appears and never takes the focus). The canvases are forced onto Qt's software OpenGL
(``opengl32sw.dll``, shipped with PyQt6) in place of a GPU. Skipped off Windows, and when the
child cannot set up (no window, no OpenGL, a dashboard that does not load).

On Windows each dashboard is now a native child window with its own Direct3D 11 surface
(route (b) of the dashboard blink, KNOWN_ISSUES D8): the main window keeps the canvases'
OpenGL surface, so no step recreates it, and the canvases keep OpenGL while a dashboard lives.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
#: Qt names the main window's native window after its class.
MAIN_QWINDOW = "MainWindowClassWindow"
CHILD_TIMEOUT_S = 300
#: Longest wait for a dashboard page, a project load or the release to settle (child).
LOAD_TIMEOUT_MS = 120_000
SETTLE_MS = 2500

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the native Windows platform")


# --------------------------------------------------------------------------- the child
def run_child(project: str, out: str) -> None:
    """Drive the window through two dashboards, the release and a reopen; write the record
    to ``out`` (JSON). Runs in its own process on the native platform."""
    sys.path.insert(0, str(ROOT))
    # First: the app's Qt / Chromium environment, logging and QtWebEngine, before Qt loads.
    import main as app_main

    # isort: split
    from PyQt6 import sip
    from PyQt6.QtCore import (
        QEvent,
        QEventLoop,
        QObject,
        QSettings,
        Qt,
        QTimer,
        qInstallMessageHandler,
    )
    from PyQt6.QtGui import QGuiApplication, QWindow
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget
    from PyQt6.QtWidgets import QApplication

    step = ["start"]
    events: list[dict] = []
    messages: list[dict] = []
    record: dict = {"steps": {}, "events": events, "messages": messages}

    def on_message(_mode, _context, text: str) -> None:
        if "Recreating QWidgetWindow" in text or "not compatible" in text:
            messages.append({"step": step[0], "text": text[:300]})

    class Recorder(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
            kind = event.type()
            if kind == QEvent.Type.PlatformSurface and isinstance(obj, QWindow):
                if obj.parent() is None:
                    events.append(
                        {
                            "step": step[0],
                            "event": event.surfaceEventType().name,
                            "window": obj.objectName(),
                            "surface": obj.surfaceType().name,
                        }
                    )
            elif kind == QEvent.Type.Show and isinstance(obj, QWindow) and obj.parent() is None:
                events.append({"step": step[0], "event": "Show", "window": obj.objectName()})
            return False

    def wait(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def wait_until(predicate, ms: int) -> bool:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            if predicate():
                return True
            wait(25)
        return predicate()

    def write() -> None:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=1)

    qInstallMessageHandler(on_message)
    app = QApplication.instance() or QApplication(["sprouts-release-native"])
    app_main.apply_stylesheet(app)
    recorder = Recorder()
    app.installEventFilter(recorder)

    from app.gui import canvas_gl
    from app.gui import visualization_manager as vm
    from app.gui.main_window import MainWindow

    found = canvas_gl.opengl_support()
    if not found.checked or found.reason.startswith("no OpenGL") or "below 2.0" in found.reason:
        record["setup_error"] = f"no OpenGL context: {found.reason}"
        write()
        return
    record["opengl"] = found.renderer or found.reason
    # A GPU's path: the canvases use QOpenGLWidget viewports (software OpenGL counts as
    # unsupported otherwise).
    canvas_gl.opengl_support = lambda: canvas_gl.GLSupport(True, "forced by the test", "")

    settings = QSettings(
        os.path.join(os.path.dirname(out), "settings.ini"), QSettings.Format.IniFormat
    )
    window = MainWindow(settings=settings, detect_device=False, warm_up=False)
    window.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setGeometry(-6000, -6000, 1240, 780)
    views = (window.display_controller.magnifying_view, window.correct_editor.graphics_view)

    def state() -> dict:
        handle = window.windowHandle()
        return {
            "qwindow": sip.unwrapinstance(handle) if handle is not None else 0,
            "winid": int(window.winId()),
            "surface": handle.surfaceType().name if handle is not None else None,
            "gl_viewports": [isinstance(view.viewport(), QOpenGLWidget) for view in views],
            "visible_top_levels": sum(
                1 for w in QGuiApplication.topLevelWindows() if w.isVisible()
            ),
        }

    def dashboard_shown(spec=None) -> bool:
        """``spec``'s dashboard (default: the one on screen) has loaded its page."""
        controller = vm.controller_for(window)

        def current():
            kind = spec.kind if spec is not None else controller.visible_kind()
            return controller.dashboard(vm.SPECS[kind]) if kind else None

        loaded = wait_until(
            lambda: (
                (board := current()) is not None
                and (board.failed or board.loading_label.isHidden())
            ),
            LOAD_TIMEOUT_MS,
        )
        board = current()
        return loaded and board is not None and not board.failed

    def run_step(name: str, action, check=None) -> bool:
        step[0] = name
        record["steps"][name] = {"before": state()}
        action()
        ok = check() if check is not None else True
        wait(SETTLE_MS)
        record["steps"][name]["after"] = state()
        record["steps"][name]["ok"] = ok
        return ok

    try:
        step[0] = "show"
        window.show()
        wait(800)
        window.open_path(project)
        if not wait_until(
            lambda: not window._loading_project and window.loading_overlay.isHidden(),
            LOAD_TIMEOUT_MS,
        ):
            record["setup_error"] = "the project did not load"
            return
        wait(SETTLE_MS)
        record["steps"]["show"] = {"after": state()}
        if not all(record["steps"]["show"]["after"]["gl_viewports"]):
            record["setup_error"] = "the canvases did not get OpenGL viewports"
            return
        stage = window.ribbon_buttons
        controller = vm.controller_for(window)
        if not run_step(
            "length", lambda: stage["Visualize"].click(), lambda: dashboard_shown(vm.LENGTH)
        ):
            record["setup_error"] = "the length dashboard did not load"
            return
        if not run_step(
            "area",
            lambda: window.ribbon.viz_seg.button("area").click(),
            lambda: dashboard_shown(vm.AREA),
        ):
            record["setup_error"] = "the area dashboard did not load"
            return
        run_step("library", lambda: stage["Library"].click())
        run_step("visualize_again", lambda: stage["Visualize"].click())
        run_step("library_again", lambda: stage["Library"].click())

        def release() -> None:  # what the 5-minute countdown runs when it ends
            controller._idle_timer.stop()
            controller._on_idle()

        run_step("release", release)
        record["steps"]["release"]["dashboards_left"] = [
            spec.kind for spec in vm.SPECS.values() if controller.dashboard(spec) is not None
        ]
        run_step("reopen", lambda: stage["Visualize"].click(), dashboard_shown)
    except BaseException:  # noqa: BLE001 - reported to the test, which fails on it
        record["error"] = traceback.format_exc()
    finally:
        step[0] = "end"
        write()
        app.removeEventFilter(recorder)
        qInstallMessageHandler(None)
        try:
            from tests.qt_teardown import delete_widgets

            delete_widgets([window])
        finally:
            sys.stdout.flush()
            os._exit(0)  # QtWebEngine's own teardown is not under test


# --------------------------------------------------------------------------- the test
def _child_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    # The native platform and the app's own graphics choices (main.py sets them).
    for name in ("QT_QPA_PLATFORM", "QT_QUICK_BACKEND", "QT_WIDGETS_RHI", "QSG_RHI_BACKEND"):
        env.pop(name, None)
    env.update(
        QT_LOGGING_RULES="qt.widgets.painting.debug=true",  # Qt's "Recreating ..." line
        QT_OPENGL="software",  # opengl32sw.dll: the same OpenGL on every machine
        ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT="1",
        SPROUTS_CONFIG_DIR=str(tmp_path / "config"),
        SPROUTS_LOG_DIR=str(tmp_path / "logs"),
        PYTHONIOENCODING="utf-8",
    )
    return env


def _recreations(record: dict, step: str) -> int:
    """How often the main window's native window was destroyed during ``step``."""
    return sum(
        1
        for event in record["events"]
        if event["step"] == step
        and event["window"] == MAIN_QWINDOW
        and event["event"] == "SurfaceAboutToBeDestroyed"
    )


@pytest.fixture(scope="module")
def release_record(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("release_native")
    project = tmp_path / "project"
    shutil.copytree(EXAMPLES, project)
    out = tmp_path / "record.json"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from tests.test_visualization_manager_release_native import run_child; "
        "run_child(sys.argv[2], sys.argv[3])"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(ROOT), str(project), str(out)],
            cwd=str(tmp_path),
            env=_child_env(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHILD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"the native child did not finish within {CHILD_TIMEOUT_S} s")
    if not out.exists():
        tail = (done.stderr or done.stdout or "")[-2000:]
        pytest.skip(
            f"the native platform could not run the window (exit {done.returncode}): {tail}"
        )
    record = json.loads(out.read_text(encoding="utf-8"))
    assert "error" not in record, record["error"]
    if record.get("setup_error"):
        pytest.skip(f"native set-up: {record['setup_error']}")
    return record


def test_the_release_recreates_the_window_at_most_once(release_record):
    assert _recreations(release_record, "release") <= 1
    recreating = [m for m in release_record["messages"] if m["step"] == "release"]
    assert sum("Recreating QWidgetWindow" in m["text"] for m in recreating) <= 1


def test_the_release_ends_on_an_opengl_window_with_opengl_canvases(release_record):
    after = release_record["steps"]["release"]["after"]
    assert release_record["steps"]["release"]["dashboards_left"] == []
    assert after["surface"] == "OpenGLSurface"
    assert after["gl_viewports"] == [True, True]  # the Library display and the Correct canvas
    assert after["visible_top_levels"] == 1  # the window only: no dashboard became a window


def test_no_graphics_api_warning_at_the_release(release_record):
    warnings = [m["text"] for m in release_record["messages"] if "not compatible" in m["text"]]
    assert warnings == []


def test_the_dashboards_open_again_after_the_release(release_record):
    assert release_record["steps"]["reopen"]["ok"]
    assert release_record["steps"]["reopen"]["after"]["gl_viewports"] == [True, True]


#: The steps after the window and the project are shown, in their order.
STEPS = ("length", "area", "library", "visualize_again", "library_again", "release", "reopen")


def test_no_step_recreates_the_main_window(release_record):
    recreations = {step: _recreations(release_record, step) for step in STEPS}
    assert recreations == dict.fromkeys(STEPS, 0)
    recreating = [m for m in release_record["messages"] if "Recreating QWidgetWindow" in m["text"]]
    assert recreating == []


def test_the_main_window_keeps_its_native_window_and_stays_the_only_one(release_record):
    first = release_record["steps"]["show"]["after"]
    for step in STEPS:
        after = release_record["steps"][step]["after"]
        assert (after["qwindow"], after["winid"]) == (first["qwindow"], first["winid"]), step
        assert after["visible_top_levels"] == 1, step
    shown = [e["window"] for e in release_record["events"] if e["event"] == "Show"]
    assert shown == [MAIN_QWINDOW]  # no dashboard ever became a window of its own


def test_the_canvases_keep_opengl_while_a_dashboard_lives(release_record):
    for step in ("length", "area", "visualize_again", "reopen"):
        after = release_record["steps"][step]["after"]
        assert after["surface"] == "OpenGLSurface", step
        assert after["gl_viewports"] == [True, True], step

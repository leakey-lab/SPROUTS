"""Window width budget: the window's minimum stays at or under 900 px on every stage, also
with long names and with a measured image selected (UXS-04 / UI-06; guard of the text-fit
patches, Qt toolkit item 4).

A label that does not elide sets the window's minimum to its whole text, and the right panel
spreads its largest page's minimum to every stage. ``test_shell_chrome_topbar`` checks 900 px
with no project open; this file opens a project, with and without long names, and selects a
measured image (whose STATUS cell is the known offender P1-03).

Widths need real fonts: offscreen Qt on Windows has an empty font database (every glyph a
box) unless ``QT_QPA_FONTDIR`` names a font folder, which must be set before the application
exists. So a child process measures, on the offscreen platform with the system fonts. The
dashboards are stand-ins there (no browser offscreen); their pages hold no chrome text.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
BUDGET_PX = 900
STAGES = ("Library", "Generate", "Correct", "Measure", "Visualize")
#: The example measured in both CSVs (17.94 mm, 17.04 mm2).
MEASURED = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
LONG_TAIL = "_rhizotron_north_window_replicate_A_second_pass_with_a_long_camera_note"
LONG_FOLDER = "a project folder whose name is much longer than the window could ever show"
CHILD_TIMEOUT_S = 240


# --------------------------------------------------------------------------- the child
def run_child(projects_json: str, out: str) -> None:
    """Open each project, measure the window minimum per stage with nothing selected and with
    its measured image selected; write the widths to ``out`` (JSON)."""
    sys.path.insert(0, str(ROOT))
    # First: the app's environment and logging, before Qt loads.
    import main as app_main

    # isort: split
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication.instance() or QApplication(["sprouts-width-budget"])
    app_main.apply_stylesheet(app)

    from app.gui import visualization_manager as vm
    from tests.qt_teardown import delete_widgets
    from tests.qt_wait import settle, wait_until
    from tests.window_support import build_window, quiet_window

    class Dashboard(QMainWindow):
        """The dashboards' contract without a browser or a server."""

        server_closed = pyqtSignal()
        export_saved = pyqtSignal(str)
        failed = False

        def __init__(self, csv_path, image_manager=None):
            super().__init__()

        def cleanup_server(self) -> None:
            pass

    vm.RootLengthVisualization = vm.RootAreaVisualization = Dashboard
    record: dict = {"fonts": len(QFontDatabase.families()), "projects": {}}
    window = build_window(
        os.path.join(os.path.dirname(out), "settings.ini"), detect_device=False, warm_up=False
    )

    def minimum_per_stage() -> dict[str, int]:
        widths = {}
        for stage in STAGES:
            window.ribbon_buttons[stage].click()
            settle()
            widths[stage] = window.minimumSizeHint().width()
        window.ribbon_buttons["Library"].click()
        settle()
        return widths

    try:
        for name, (folder, measured) in json.loads(projects_json).items():
            window.open_path(folder)
            loaded = wait_until(
                lambda: not window._loading_project and bool(window._image_leaves()), 60
            )
            parsed = wait_until(lambda: not window.metrics.loading, 60)
            if not (loaded and parsed):
                record["error"] = f"{name}: the project did not load"
                return
            settle()
            widths = {"nothing_selected": minimum_per_stage()}
            leaf = window.find_tree_item_by_image_name(measured)
            window.file_list.setCurrentItem(leaf)
            window.on_image_selected(leaf)
            settle()
            widths["measured"] = minimum_per_stage()
            widths["status"] = window.metrics_bar._status_value.text()
            record["projects"][name] = widths
    except BaseException:  # noqa: BLE001 - reported to the test, which fails on it
        record["error"] = traceback.format_exc()
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=1)
        delete_widgets([window], prepare=quiet_window)
        sys.stdout.flush()
        os._exit(0)


# --------------------------------------------------------------------------- the projects
def _long_names_project(target: Path) -> Path:
    """The examples with every stem made long, in a folder with a long name; both CSVs name
    the long stems (so the measured image stays measured)."""
    shutil.copytree(EXAMPLES, target)
    stems = [path.stem for path in EXAMPLES.glob("*.jpg")]
    for stem in stems:
        for folder, suffix in (("", ".jpg"), ("mask", ".png"), ("skeletons", "_fake.png")):
            source = target / folder / f"{stem}{suffix}"
            source.rename(source.with_name(f"{stem}{LONG_TAIL}{suffix}"))
    for csv in (target / "skeletons" / "root_lengths.csv", target / "mask" / "root_areas.csv"):
        text = csv.read_text(encoding="utf-8")
        for stem in stems:
            text = text.replace(f"{stem},", f"{stem}{LONG_TAIL},")
        csv.write_text(text, encoding="utf-8")
    return target


def _child_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT="0",
        SPROUTS_CONFIG_DIR=str(tmp_path / "config"),
        SPROUTS_LOG_DIR=str(tmp_path / "logs"),
        PYTHONIOENCODING="utf-8",
    )
    if sys.platform == "win32":  # offscreen Qt finds no font of its own there
        env.setdefault(
            "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        )
    return env


@pytest.fixture(scope="module")
def widths(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("width_budget")
    projects = {
        "examples": (str(shutil.copytree(EXAMPLES, tmp_path / "examples")), MEASURED),
        "long names": (str(_long_names_project(tmp_path / LONG_FOLDER)), MEASURED + LONG_TAIL),
    }
    out = tmp_path / "widths.json"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from tests.test_window_width_budget import run_child; run_child(sys.argv[2], sys.argv[3])"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(ROOT), json.dumps(projects), str(out)],
        cwd=str(tmp_path),
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CHILD_TIMEOUT_S,
    )
    assert out.exists(), f"the child wrote nothing (exit {done.returncode}): {done.stderr[-2000:]}"
    record = json.loads(out.read_text(encoding="utf-8"))
    assert "error" not in record, record["error"]
    if not record["fonts"]:
        pytest.skip("no fonts: offscreen glyph widths are boxes")
    return record["projects"]


def _over_budget(widths: dict, case: str) -> dict:
    """``{(project, stage): width}`` of the stages whose window minimum exceeds the budget."""
    return {
        (project, stage): width
        for project, cases in widths.items()
        for stage, width in cases[case].items()
        if width > BUDGET_PX
    }


def test_every_stage_fits_the_budget_with_long_names(widths):
    assert _over_budget(widths, "nothing_selected") == {}


@pytest.mark.xfail(
    strict=True,
    reason="P1-03 (owner question #13): the plain STATUS label of the metrics bar sets the "
    "window minimum to about 1,112-1,126 px once a measured image is shown",
)
def test_every_stage_fits_the_budget_with_a_measured_image_selected(widths):
    assert _over_budget(widths, "measured") == {}

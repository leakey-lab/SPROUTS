"""viz-embed: importing the dashboard modules stays cheap and keeps every public name.

Each check runs in a fresh interpreter so sys.modules reflects only what that
import pulled in.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_python(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_dashboard_modules_do_not_import_the_dash_stack():
    # PV-13: dash, plotly and pandas cost ~1 s at start-up; they now load on the
    # init worker when a dashboard opens, werkzeug on the server thread.
    result = _run_python("""
        import sys
        import app.gui.visualization_manager
        import app.visualization
        import app.visualization.root_area_visualization
        import app.visualization.root_length_visulization
        heavy = ("dash", "plotly", "pandas", "requests", "flask", "werkzeug")
        print(sorted(name for name in heavy if name in sys.modules))
    """)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "[]"


def test_thread_helpers_can_be_imported_after_the_qapplication_exists():
    # Handlers import release_qthread whenever they like; that must not pull in
    # QtWebEngineWidgets, which refuses to load once a QCoreApplication exists.
    result = _run_python("""
        import sys
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication(sys.argv)
        from app.visualization._viz_server import join_qthread, release_qthread
        print("ok")
    """)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"


def test_public_names_still_resolve():
    result = _run_python("""
        import app.gui.visualization_manager as vm
        from app.visualization import (
            DashApp,
            DashAppArea,
            DashServerThread,
            DataCache,
            DashVisualizations,
            RootAreaVisualization,
            RootLengthVisualization,
            build_available_images_map,
            get_encoded_image,
        )
        from app.visualization.root_area_visualization import DashServerThreadArea
        from app.visualization.root_area_visualization import RootAreaVisualization as Area
        from app.visualization.root_length_visulization import RootLengthVisualization as Length
        assert RootLengthVisualization is Length and RootAreaVisualization is Area
        assert vm.RootLengthVisualization is Length and vm.RootAreaVisualization is Area
        assert callable(vm.find_test_latest_dir)
        print("ok")
    """)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"

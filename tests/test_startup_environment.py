"""Qt / Chromium start-up environment per OS (owner decision O21).

Windows pins the D3D11 RHI and the D3D11 ANGLE backend; Linux and macOS take Qt's
own defaults (OpenGL and Metal), because a D3D11 backend there leaves the
dashboards black.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app import qt_environment as qe

REPO_ROOT = Path(os.path.abspath(__file__)).parent.parent


def test_windows_sets_d3d11_and_angle_once():
    env = {"QTWEBENGINE_CHROMIUM_FLAGS": "--foo"}
    qe.configure_qt_environment(env, "win32")
    qe.configure_qt_environment(env, "win32")
    assert env["QSG_RHI_BACKEND"] == "d3d11"
    assert env["QTWEBENGINE_CHROMIUM_FLAGS"] == "--foo --use-angle=d3d11"


def test_windows_returns_what_it_set():
    env = {}
    assert qe.configure_qt_environment(env, "win32") == {
        "QSG_RHI_BACKEND": "d3d11",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--use-angle=d3d11",
    }
    assert qe.configure_qt_environment(env, "win32") == {}


def test_windows_keeps_a_users_backend():
    env = {"QSG_RHI_BACKEND": "opengl"}
    qe.configure_qt_environment(env, "win32")
    assert env["QSG_RHI_BACKEND"] == "opengl"


def test_linux_takes_qt_defaults():
    env = {}
    assert qe.configure_qt_environment(env, "linux", euid=1000) == {}
    assert env == {}


def test_linux_root_disables_the_chromium_sandbox():
    env = {}
    qe.configure_qt_environment(env, "linux", euid=0)
    assert env == {"QTWEBENGINE_DISABLE_SANDBOX": "1"}


def test_darwin_takes_metal_and_96_dpi_fonts():
    env = {}
    qe.configure_qt_environment(env, "darwin", euid=0)
    assert env == {"QT_FONT_DPI": "96"}


def test_style_is_native_on_windows_and_fusion_elsewhere():
    assert qe.qt_style_for("win32") is None
    assert qe.qt_style_for("linux") == qe.qt_style_for("darwin") == "Fusion"


def test_desktop_file_name_matches_the_script_name():
    assert qe.DESKTOP_FILE_NAME == "sprouts"


def test_the_module_is_qt_free():
    src = Path(qe.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src and "torch" not in src


def test_importing_main_sets_d3d11_only_on_windows(tmp_path):
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        PYTHONDONTWRITEBYTECODE="1",
        SPROUTS_LOG_DIR=str(tmp_path),
    )
    env.pop("QSG_RHI_BACKEND", None)
    env.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import main, os; print(os.environ.get('QSG_RHI_BACKEND'))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip().splitlines()[-1]
    assert out == ("d3d11" if sys.platform == "win32" else "None")

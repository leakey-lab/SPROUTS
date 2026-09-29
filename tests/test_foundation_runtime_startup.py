"""
Tests for the startup foundation: resource paths (``resources/resource_utils.py``),
logging (``app/logging_config.py``) and the ``main()`` entry point (``main.py``).

Logging and ``main()`` touch process-global state (root logger handlers,
excepthooks, the QApplication), so they run in fresh interpreters with
``SPROUTS_LOG_DIR`` pointed at ``tmp_path``: nothing is written outside it.
``main()`` runs with a stub window so no model or dataset is needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from resources.resource_utils import get_resource_path, project_root, resource_path

REPO_ROOT = Path(os.path.abspath(__file__)).parent.parent


def _run(code: str, *, cwd: Path, env_extra: dict[str, str] | None = None, timeout: int = 180):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    env.pop("SPROUTS_LOG_DIR", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --- resource paths ------------------------------------------------------------------


def test_resource_path_is_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    qss = resource_path("resources", "themes", "dark_theme.qss")
    assert os.path.isabs(qss)
    assert os.path.isfile(qss)
    assert os.path.isfile(resource_path("resources/app_icon.ico"))


def test_resource_path_accepts_joined_or_split_parts():
    assert resource_path("resources/themes/dark_theme.qss") == resource_path(
        "resources", "themes", "dark_theme.qss"
    )
    assert resource_path(Path("resources") / "themes") == resource_path("resources", "themes")


def test_resource_path_uses_the_pyinstaller_bundle_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert project_root() == tmp_path
    assert resource_path("resources", "icons") == os.path.join(str(tmp_path), "resources", "icons")


def test_project_root_is_the_repository_in_development():
    assert project_root() == REPO_ROOT


def test_get_resource_path_remains_an_alias():
    assert get_resource_path is resource_path


# --- logging ---------------------------------------------------------------------


def test_setup_logging_writes_a_version_banner_and_is_idempotent(tmp_path):
    proc = _run(
        f"""
        import logging
        from pathlib import Path
        from app.logging_config import current_log_file, setup_logging
        first = setup_logging(Path({str(tmp_path)!r}))
        count = len(logging.getLogger().handlers)
        second = setup_logging(Path({str(tmp_path)!r}) / "elsewhere")
        print(first == second == current_log_file(), count == len(logging.getLogger().handlers))
        logging.getLogger("probe").info("hello log")
        """,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["True", "True"]
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "SPROUTS" in text and "Python" in text
    assert "hello log" in text


def test_log_dir_env_override_wins_over_user_paths(tmp_path):
    env_dir = tmp_path / "from-env"
    proc = _run(
        """
        from app.logging_config import setup_logging
        print(setup_logging())
        """,
        cwd=REPO_ROOT,
        env_extra={"SPROUTS_LOG_DIR": str(env_dir)},
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == env_dir / "app.log"


def test_default_log_dir_comes_from_user_paths_when_available(tmp_path):
    user_dir = tmp_path / "user-logs"
    proc = _run(
        f"""
        import sys, types
        from pathlib import Path
        fake = types.ModuleType("app.user_paths")
        fake.user_log_dir = lambda: Path({str(user_dir)!r})
        sys.modules["app.user_paths"] = fake
        from app.logging_config import setup_logging
        print(setup_logging())
        """,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == user_dir / "app.log"


def test_setup_logging_survives_a_missing_stderr(tmp_path):
    proc = _run(
        f"""
        import logging, sys
        from pathlib import Path
        from app.logging_config import setup_logging
        real_stdout = sys.stdout
        sys.stderr = None  # pythonw.exe / windowed PyInstaller builds
        setup_logging(Path({str(tmp_path)!r}))
        logging.getLogger("probe").warning("no console here")
        real_stdout.write("ok")
        """,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ok"
    assert "no console here" in (tmp_path / "app.log").read_text(encoding="utf-8")


# --- main() ------------------------------------------------------------------------

_MAIN_DRIVER = """
import sys
from pathlib import Path
sys.path.insert(0, {repo!r})
import main
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

class StubWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        app = QApplication.instance()  # inspected while main() is running
        print("QSS", len(app.styleSheet()) > 1000)
        print("ICON", not app.windowIcon().isNull())
        print("NAMES", app.organizationName(), app.applicationName())
        print("HOOK", sys.excepthook.__qualname__)
        if {fail!r}:
            raise RuntimeError("checkpoint missing")
        QTimer.singleShot(100, app.quit)

main.MainWindow = StubWindow
QMessageBox.critical = staticmethod(lambda *a, **k: print("CRITICAL-BOX", a[2]))
print("RC", main.main(["sprouts"]))
"""


def test_main_starts_from_any_working_directory(tmp_path):
    log_dir = tmp_path / "logs"
    proc = _run(
        _MAIN_DRIVER.format(repo=str(REPO_ROOT), fail=False),
        cwd=tmp_path,  # not the repository: the QSS and icon must still resolve
        env_extra={"SPROUTS_LOG_DIR": str(log_dir)},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert "RC 0" in out
    assert "QSS True" in out
    assert "ICON True" in out
    assert "NAMES LeakeyLab SPROUTS" in out
    assert "HOOK ExceptionHooks._sys_hook" in out
    assert (log_dir / "app.log").is_file()


def test_main_reports_a_startup_failure_instead_of_vanishing(tmp_path):
    log_dir = tmp_path / "logs"
    proc = _run(
        _MAIN_DRIVER.format(repo=str(REPO_ROOT), fail=True),
        cwd=tmp_path,
        env_extra={"SPROUTS_LOG_DIR": str(log_dir)},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert "RC 1" in out
    assert any(line.startswith("CRITICAL-BOX") and "checkpoint missing" in line for line in out)
    log_text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "RuntimeError: checkpoint missing" in log_text


def test_an_import_failure_is_written_to_the_log(tmp_path):
    log_dir = tmp_path / "logs"
    proc = _run(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        sys.modules["app.gui.main_window"] = None  # simulate a broken app module
        import main
        """,
        cwd=tmp_path,
        env_extra={"SPROUTS_LOG_DIR": str(log_dir)},
    )
    assert proc.returncode != 0  # still fatal: there is nothing to run
    log_text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "Could not import the application" in log_text
    assert "import of app.gui.main_window halted" in log_text  # the traceback's last line


@pytest.mark.parametrize("name", ["resource_path", "apply_stylesheet", "MainWindow", "main"])
def test_main_keeps_the_names_the_ui_harness_uses(name, tmp_path):
    proc = _run(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        import main
        print(callable(getattr(main, {name!r})))
        """,
        cwd=tmp_path,
        env_extra={"SPROUTS_LOG_DIR": str(tmp_path / "logs")},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "True"

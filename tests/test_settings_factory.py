"""One settings factory (``app.settings.app_settings``): the only ``QSettings`` constructor.

``QSettings(organization, application)`` ignores ``setDefaultFormat`` and ``setPath``, so a
store built anywhere else would read and write the user's real settings (the registry on
Windows) from tests, scripts and agents (C5-04, X8). The factory gives the user's store
on a screen and a fresh memory store on the ``offscreen`` platform; every default store
goes through it. These tests never open the user's settings: the factory's ``QSettings``
is replaced wherever a store could reach it.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import app.settings as factory
from app import APP_NAME, ORGANIZATION_NAME
from app.settings import MemorySettings, app_settings, headless

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "app" / "settings.py"


def _builds_qsettings(path: Path) -> list[int]:
    """Lines of ``path`` that call ``QSettings(...)`` (also as ``QtCore.QSettings(...)``)."""
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "QSettings":
            lines.append(node.lineno)
    return lines


def test_no_module_but_the_factory_builds_a_qsettings():
    paths = [ROOT / "main.py", *sorted((ROOT / "app").rglob("*.py"))]
    paths += sorted((ROOT / "scripts").rglob("*.py"))
    offenders = {
        str(path.relative_to(ROOT)): lines
        for path in paths
        if path != HOME and (lines := _builds_qsettings(path))
    }
    assert offenders == {}
    assert _builds_qsettings(HOME)  # the factory itself (the check finds a call)


@pytest.fixture
def no_user_settings(monkeypatch):
    """Building the user's store fails the test (it would open the user's settings)."""

    def refuse(*args, **kwargs):
        pytest.fail(f"QSettings{args} built: the user's settings were opened")

    monkeypatch.setattr(factory, "QSettings", refuse)


def test_headless_gives_a_fresh_memory_store_each_time(app, no_user_settings):
    assert headless()  # the suite runs on the offscreen platform
    first, second = app_settings(), app_settings()
    assert isinstance(first, MemorySettings) and isinstance(second, MemorySettings)
    first.setValue("recent_projects", "[1]")
    assert second.value("recent_projects", "[]") == "[]"  # nothing is shared


def test_on_a_screen_it_is_the_users_qsettings(app, monkeypatch):
    made = []
    monkeypatch.setattr(factory, "headless", lambda: False)
    monkeypatch.setattr(factory, "QSettings", lambda *args: made.append(args) or "the user's")
    assert app_settings() == "the user's"
    assert made == [(ORGANIZATION_NAME, APP_NAME)]


def test_the_memory_store_answers_what_the_stores_call():
    store = MemorySettings()
    assert store.value("window/geometry") is None
    assert store.value("window/last_folder", "") == ""
    store.setValue("correct/mask/opacity", 0.5)
    store.sync()
    assert store.value("correct/mask/opacity") == 0.5
    assert store.status() == factory.QSettings.Status.NoError


def test_every_default_store_is_memory_when_headless(app, no_user_settings):
    from app.gui.correct.layer_prefs import LayerPrefs
    from app.gui.device_check import device_check_enabled
    from app.gui.recent_projects import RecentProjectsStore
    from app.gui.window_state import WindowStateStore

    assert isinstance(WindowStateStore()._settings, MemorySettings)
    assert isinstance(RecentProjectsStore().settings, MemorySettings)
    assert isinstance(LayerPrefs.for_app(None).settings, MemorySettings)
    assert device_check_enabled(None, {}) is True  # nothing stored: on by default


def test_a_headless_window_uses_no_user_settings(app, no_user_settings, window_guards):
    from app.gui.main_window import MainWindow
    from tests.qt_teardown import delete_widgets

    made = [MainWindow()]
    try:
        window = made[0]
        assert isinstance(window.window_state._settings, MemorySettings)
        assert isinstance(window.recent_projects.settings, MemorySettings)
        assert window._persist_window_state is False
        assert window.device_check is None and window._warm_ups is False
    finally:
        delete_widgets(made)


def _headless_in_fresh_process(platform: str | None) -> str:
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    if platform is not None:
        env["QT_QPA_PLATFORM"] = platform
    code = textwrap.dedent(
        """
        from app.settings import MemorySettings, headless, app_settings
        if headless():
            print("headless", type(app_settings()).__name__)
        else:
            print("screen")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip()


def test_before_any_application_the_platform_variable_decides():
    # Without a QGuiApplication, Qt reports its built-in default ("windows" here), so the
    # factory reads QT_QPA_PLATFORM: a script or agent started offscreen stays off the
    # user's settings before it makes its application.
    assert _headless_in_fresh_process("offscreen") == "headless MemorySettings"
    assert _headless_in_fresh_process("offscreen:fontengine=freetype") == (
        "headless MemorySettings"
    )
    assert _headless_in_fresh_process(None) == "screen"

"""The status line names the inference device soon after start-up (KNOWN_ISSUES #5, H2).

Before: the chip read "Device not detected yet" until the first inference resolved the
runtime. Now MainWindow resolves it in the thread pool a moment after the window first
shows (torch loads there, never on the GUI thread or before the first paint), and the chip
reads "Checking GPU…" meanwhile, "Device unknown" (warning colour, reason in the tooltip)
if it fails. Headless windows (tests) skip the check unless asked.

The runtime is faked (``sys.modules["app.inference.runtime"]``), so torch is never loaded.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.gui import device_check, shell_chrome
from app.gui.shell_chrome import DEVICE_CHECK_FAILED, DEVICE_CHECKING, runtime_status
from app.gui.widgets import tokens
from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.qt_wait import wait_until as wait
from tests.window_support import build_window, quiet_window

RUNTIME = "app.inference.runtime"

pytestmark = pytest.mark.usefixtures("no_prompts", "slot_errors")


class FakeRuntime:
    """A stand-in runtime module: unresolved until ``resolve()`` runs."""

    def __init__(self, *, mask_weights: bool = True) -> None:
        self.resolved = False
        self.mask_weights = mask_weights
        self.module = types.ModuleType(RUNTIME)
        self.module.describe_runtime = self.describe

    def describe(self, resolve: bool = False):
        if resolve:
            self.resolved = True
        device = "GPU" if self.resolved else "unknown"
        text = "GPU · Fake RTX · CUDA 12.8 · TF32" if self.resolved else "Device not detected yet"
        if not self.mask_weights:
            text += " · mask model missing"
        return types.SimpleNamespace(
            device=device,
            text=text,
            models_ok=self.mask_weights,
            mask_weights=self.mask_weights,
            skeleton_weights=True,
        )


@pytest.fixture
def runtime(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setitem(sys.modules, RUNTIME, fake.module)
    return fake


@pytest.fixture
def make_window(app, tmp_path):
    """``make(**kwargs)``: a MainWindow on its own INI settings, neither resized nor shown."""
    made = []

    def make(**kwargs):
        path = tmp_path / f"settings_{len(made)}.ini"
        made.append(build_window(path, size=None, show=False, **kwargs))
        return made[-1]

    yield make
    assert QThreadPool.globalInstance().waitForDone(5000)
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


# ------------------------------------------------------------------ the chip text
def test_an_unresolved_device_reads_checking_while_the_check_runs(runtime):
    assert runtime_status("checking") == (DEVICE_CHECKING, True)
    assert runtime_status() == ("Device not detected yet", True)  # no check: as before


def test_checking_keeps_the_missing_model_warning(monkeypatch):
    fake = FakeRuntime(mask_weights=False)
    monkeypatch.setitem(sys.modules, RUNTIME, fake.module)
    assert runtime_status("checking") == (f"{DEVICE_CHECKING} · mask model missing", False)


def test_a_failed_check_reads_device_unknown(runtime):
    assert runtime_status("failed")[0] == DEVICE_CHECK_FAILED


def test_a_resolved_device_wins_over_the_check_state(runtime):
    runtime.describe(resolve=True)
    assert runtime_status("checking")[0] == "GPU · Fake RTX · CUDA 12.8 · TF32"


def test_checking_before_the_runtime_module_is_loaded(monkeypatch):
    monkeypatch.delitem(sys.modules, RUNTIME, raising=False)
    assert runtime_status("checking") == (DEVICE_CHECKING, True)
    assert RUNTIME not in sys.modules  # the chip never imports it


# ---------------------------------------------------------------- the window check
def test_the_window_resolves_the_device_in_the_pool_after_it_shows(
    app, make_window, runtime, monkeypatch
):
    threads = []

    def resolve() -> str:
        threads.append(threading.get_ident())
        runtime.describe(resolve=True)
        return ""

    monkeypatch.setattr(device_check, "resolve_device_logged", resolve)
    window = make_window(detect_device=True)
    assert window.statusline_info.text() == DEVICE_CHECKING  # neutral text from the start
    QTest.qWait(device_check.DEVICE_CHECK_DELAY_MS + 200)
    settle()
    assert threads == []  # not before the window shows

    window.show()
    assert wait(lambda: "GPU · Fake RTX" in window.statusline_info.toolTip())
    assert window.statusline_info.text() != DEVICE_CHECKING
    assert len(threads) == 1 and threads[0] != threading.main_thread().ident

    window.hide()
    window.show()  # a second show does not check again
    QTest.qWait(device_check.DEVICE_CHECK_DELAY_MS + 200)
    settle()
    assert len(threads) == 1


def test_a_failed_check_warns_on_the_chip_and_never_raises(app, make_window, runtime, monkeypatch):
    monkeypatch.setattr(device_check, "resolve_device_logged", lambda: "DLL load failed")
    window = make_window(detect_device=True)
    window.show()

    assert wait(lambda: window.statusline_info.text() == DEVICE_CHECK_FAILED)
    assert "DLL load failed" in window.statusline_info.toolTip()
    assert tokens.WARN in window.statusline_info.styleSheet()


def test_resolve_device_logged_reports_an_import_failure(monkeypatch, caplog):
    broken = types.ModuleType(RUNTIME)

    def describe_runtime(resolve: bool = False):
        raise ImportError("DLL load failed while importing _C")

    broken.describe_runtime = describe_runtime
    monkeypatch.setitem(sys.modules, RUNTIME, broken)
    import app.inference as inference_package

    monkeypatch.setattr(inference_package, "runtime", broken, raising=False)
    assert device_check.resolve_device_logged() == "DLL load failed while importing _C"
    assert "Could not detect the inference device" in caplog.text


def test_headless_windows_skip_the_check_by_default(app, make_window, runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(device_check, "resolve_device_logged", lambda: calls.append(1))
    window = make_window()
    window.show()
    QTest.qWait(device_check.DEVICE_CHECK_DELAY_MS + 200)
    settle()
    assert calls == []
    assert window.statusline_info.text() == "Device not detected yet"


def test_the_statusline_state_api(app, runtime, monkeypatch):
    from app.gui import canvas_gl

    monkeypatch.setattr(canvas_gl, "canvas_line", lambda: ("OpenGL · Fake GPU", False))
    line = shell_chrome.StatusLine()
    line.set_device_check("checking")
    assert line.info.text() == DEVICE_CHECKING
    line.set_device_check("failed", "no CUDA driver")
    assert line.info.text() == DEVICE_CHECK_FAILED
    assert "no CUDA driver" in line.info.toolTip()
    runtime.describe(resolve=True)
    line.set_device_check(None)
    assert line.info.toolTip() == (
        "Inference runtime: GPU · Fake RTX · CUDA 12.8 · TF32\nCanvas: OpenGL · Fake GPU"
    )
    assert tokens.WARN not in line.info.styleSheet()
    line.deleteLater()

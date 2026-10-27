"""The device chip names the device from the NVIDIA driver, without torch (W3-WARM W1).

The start-up check (:class:`app.gui.device_check.DeviceCheck`) no longer loads torch: its
job asks the driver and reads the torch build (:mod:`app.inference.device_probe`). The chip
shows that guess ("GPU · <name> · CUDA 12.8" / "CPU", why in the tooltip) until torch has
resolved the runtime, whose full line then wins; a failed check or a missing torch still
reads "Device unknown". The runtime module and the probe are fakes: torch is never loaded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

from app.gui import canvas_gl, device_check, shell_chrome
from app.gui.device_check import DeviceCheck
from app.gui.shell_chrome import DEVICE_CHECK_FAILED, DEVICE_CHECKING
from app.gui.widgets import tokens
from app.inference import device_probe
from app.inference.device_probe import DeviceGuess
from tests.qt_teardown import delete_widgets, flush_deleted
from tests.qt_wait import wait_until as wait
from tests.window_support import build_window

RUNTIME = "app.inference.runtime"
GPU = DeviceGuess(
    "GPU", name="Fake RTX 9000", cuda_version="12.8", note="Named by the NVIDIA driver."
)
CPU = DeviceGuess("CPU", note="PyTorch 2.8.0+cpu is a CPU-only build: inference runs on the CPU.")


@pytest.fixture(autouse=True)
def guards(no_prompts, slot_errors):
    """No prompt and no exception in a Qt slot; the widgets a test deleteLater()s go first,
    so the slot check sees what their deletion raises."""
    yield
    flush_deleted()


class FakeRuntime:
    """``app.inference.runtime`` before and after torch resolved it."""

    def __init__(self, *, resolved: bool = False, mask_weights: bool = True) -> None:
        self.resolved = resolved
        self.mask_weights = mask_weights
        self.module = types.ModuleType(RUNTIME)
        self.module.describe_runtime = self.describe

    def describe(self, resolve: bool = False):
        if resolve:
            self.resolved = True
        text = (
            "GPU · Fake RTX 9000 · CUDA 12.8 · TF32" if self.resolved else "Device not detected yet"
        )
        if not self.mask_weights:
            text += " · mask model missing"
        return types.SimpleNamespace(
            device="GPU" if self.resolved else "unknown",
            text=text,
            models_ok=self.mask_weights,
            mask_weights=self.mask_weights,
            skeleton_weights=True,
        )


@pytest.fixture
def canvas(monkeypatch):
    """The canvas path is fixed: the chip's tooltip ends with its line."""
    monkeypatch.setattr(canvas_gl, "canvas_line", lambda: ("OpenGL · Fake GPU", False))


@pytest.fixture
def runtime(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setitem(sys.modules, RUNTIME, fake.module)
    import app.inference as inference_package

    monkeypatch.setattr(inference_package, "runtime", fake.module, raising=False)
    return fake


@pytest.fixture
def line(app):
    made = [shell_chrome.StatusLine()]
    yield made[0]
    delete_widgets(made, close=False)


def shown(line) -> str:
    """The chip's full text (the label itself is elided to INFO_MAX_WIDTH)."""
    return line.info.toolTip().splitlines()[0].removeprefix("Inference runtime: ")


# ------------------------------------------------------------------ the chip
def test_the_chip_names_the_guessed_gpu_with_why_in_the_tooltip(runtime, canvas, line):
    line.set_device_check(shell_chrome.CHECKING)
    assert line.info.text() == DEVICE_CHECKING
    line.set_device_guess(GPU)
    assert shown(line) == "GPU · Fake RTX 9000 · CUDA 12.8"
    assert line.info.text().startswith("GPU")
    assert line.info.toolTip() == (
        "Inference runtime: GPU · Fake RTX 9000 · CUDA 12.8\nNamed by the NVIDIA driver."
        "\nCanvas: OpenGL · Fake GPU"
    )
    assert tokens.WARN not in line.info.styleSheet()


def test_a_cpu_guess_reads_cpu(runtime, line):
    line.set_device_guess(CPU)
    assert line.info.text() == "CPU"
    assert "CPU-only build" in line.info.toolTip()


def test_the_guess_keeps_the_missing_model_warning(monkeypatch, line):
    fake = FakeRuntime(mask_weights=False)
    monkeypatch.setitem(sys.modules, RUNTIME, fake.module)
    line.set_device_guess(GPU)
    assert shown(line) == "GPU · Fake RTX 9000 · CUDA 12.8 · mask model missing"
    assert tokens.WARN in line.info.styleSheet()


def test_the_resolved_runtime_replaces_the_guess(runtime, canvas, line):
    line.set_device_guess(GPU)
    runtime.describe(resolve=True)  # torch loaded (the Generate warm-up, or a job)
    line.refresh_runtime()
    assert shown(line) == "GPU · Fake RTX 9000 · CUDA 12.8 · TF32"
    assert line.info.toolTip() == (
        "Inference runtime: GPU · Fake RTX 9000 · CUDA 12.8 · TF32\nCanvas: OpenGL · Fake GPU"
    )


def test_a_failure_after_the_guess_wins_and_drops_its_note(runtime, line):
    line.set_device_guess(GPU)
    line.set_device_check(shell_chrome.FAILED, "DLL load failed while importing _C")
    assert line.info.text() == DEVICE_CHECK_FAILED
    assert "Named by the NVIDIA driver" not in line.info.toolTip()
    assert "DLL load failed" in line.info.toolTip()


def test_an_unknown_guess_keeps_the_default_text_and_says_why(runtime, line):
    unknown = DeviceGuess("unknown", note="The PyTorch build could not be read.")
    line.set_device_guess(unknown)
    assert line.info.text() == "Device not detected yet"
    assert "could not be read" in line.info.toolTip()


def test_the_chip_text_before_the_runtime_module_is_loaded(monkeypatch):
    monkeypatch.delitem(sys.modules, RUNTIME, raising=False)
    assert shell_chrome.runtime_status(None, GPU) == ("GPU · Fake RTX 9000 · CUDA 12.8", True)
    assert RUNTIME not in sys.modules


# ------------------------------------------------------------------ the check's job
def test_the_job_asks_the_driver_while_the_runtime_is_unresolved(runtime, monkeypatch):
    monkeypatch.setattr(device_probe, "probe_device", lambda: GPU)
    assert device_check.resolve_device_logged() is GPU


def test_the_job_asks_nothing_once_the_runtime_is_resolved(runtime, monkeypatch):
    runtime.describe(resolve=True)

    def probe():
        raise AssertionError("the driver was asked although torch knows the device")

    monkeypatch.setattr(device_probe, "probe_device", probe)
    assert device_check.resolve_device_logged() == ""


def test_the_real_job_never_imports_torch():
    code = (
        "import sys\n"
        "from app.gui import device_check\n"
        "result = device_check.resolve_device_logged()\n"
        "print('torch' in sys.modules, type(result).__name__)\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env=env,
    ).stdout.split()
    assert out == ["False", "DeviceGuess"]


class Line:
    """Stand-in status line that also takes a guess."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_device_check(self, state, detail: str = "") -> None:
        self.calls.append(("check", state, detail))

    def set_device_guess(self, guess) -> None:
        self.calls.append(("guess", guess))

    def state(self) -> str:
        return "idle"


@pytest.fixture
def widget(app):
    made = [QWidget()]
    yield made[0]
    delete_widgets(made)
    assert QThreadPool.globalInstance().waitForDone(5000)


def test_the_check_hands_its_guess_to_the_chip(app, widget):
    line = Line()
    DeviceCheck(widget, line, delay_ms=10, idle_ms=20, resolve=lambda: GPU, enabled=True)
    widget.show()
    assert wait(lambda: line.calls[-1] == ("guess", GPU))
    assert line.calls[0] == ("check", "checking", "")


def test_no_torch_is_a_failed_check(app, widget):
    line = Line()
    missing = DeviceGuess("unknown", problem="PyTorch is not installed: Generate cannot run.")
    DeviceCheck(widget, line, delay_ms=10, idle_ms=20, resolve=lambda: missing, enabled=True)
    widget.show()
    assert wait(lambda: line.calls[-1][:2] == ("check", "failed"))
    assert "not installed" in line.calls[-1][2]


def test_a_cancelled_check_never_starts(app, widget):
    line = Line()
    runs = []
    check = DeviceCheck(
        widget, line, delay_ms=10, idle_ms=20, resolve=lambda: runs.append(1), enabled=True
    )
    widget.show()
    check.cancel()  # the window closes before the check started
    QTest.qWait(150)
    assert runs == [] and not check.watching_input()


def test_the_window_chip_shows_the_driver_guess(app, runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(device_probe, "probe_device", lambda: GPU)
    made = [build_window(tmp_path / "settings.ini", size=None, show=False, detect_device=True)]
    window = made[0]
    try:
        assert window.statusline_info.text() == DEVICE_CHECKING
        window.show()
        tip = window.statusline_info.toolTip
        assert wait(lambda: tip().startswith("Inference runtime: GPU · Fake RTX 9000 · CUDA 12.8"))
        assert "Named by the NVIDIA driver." in tip()
    finally:
        assert QThreadPool.globalInstance().waitForDone(5000)
        delete_widgets(made)

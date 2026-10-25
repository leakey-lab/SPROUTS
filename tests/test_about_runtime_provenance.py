"""The About dialog's Runtime line names the device exactly, also before PyTorch has loaded.

Since the warm-ups follow intent (W3-WARM), PyTorch loads only when the user heads for
Generate, so the About dialog, a provenance block people copy into methods sections, read
"Device not detected yet" for most of a session although the status line named the GPU.
It now asks the NVIDIA driver itself, off the GUI thread (``app.inference.device_probe``,
about 20 ms), and says where the name came from: "<device> (from the NVIDIA driver;
PyTorch not loaded yet)". Once PyTorch has resolved the runtime, the line is that runtime
(device, CUDA, precision), and the driver is not asked. "Copy details" copies the same line.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui import about_dialog  # noqa: E402
from app.inference import runtime  # noqa: E402
from app.inference.device_probe import DeviceGuess  # noqa: E402
from app.inference.runtime import RuntimeSummary  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

GPU_NAME = "NVIDIA GeForce RTX 5070 Ti"
FROM_DRIVER = "(from the NVIDIA driver; PyTorch not loaded yet)"
GPU_GUESS = DeviceGuess(
    "GPU", name=GPU_NAME, cuda_version="12.8", note="Named by the NVIDIA driver; ..."
)
CPU_NOTE = "PyTorch 2.8.0+cpu is a CPU-only build: inference runs on the CPU."
CPU_GUESS = DeviceGuess("CPU", note=CPU_NOTE)


def _summary(device="unknown", *, name=None, cuda=None, precision=None, mask=True, skel=True):
    return RuntimeSummary(
        device=device,
        device_name=name,
        cuda_version=cuda,
        precision=precision,
        mask_weights=mask,
        skeleton_weights=skel,
        mask_loaded=False,
        skeleton_loaded=False,
    )


UNRESOLVED = _summary()
RESOLVED = _summary("GPU", name=GPU_NAME, cuda="12.8", precision="tf32")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(app, monkeypatch):
    """Made dialogs, deleted afterwards; no model hashing."""
    monkeypatch.setattr(about_dialog, "_verify_models", lambda: {})
    made: list = []
    yield made
    delete_widgets(made)


class Probe:
    """A stand-in for the driver probe: its answer and the threads it ran on."""

    def __init__(self, guess):
        self.guess = guess
        self.threads: list[threading.Thread] = []

    def __call__(self):
        self.threads.append(threading.current_thread())
        return self.guess


def _runtime(monkeypatch, summary) -> None:
    monkeypatch.setattr(runtime, "describe_runtime", lambda resolve=False: summary)


def _probe(monkeypatch, guess) -> Probe:
    probe = Probe(guess)
    monkeypatch.setattr(about_dialog, "_probe_device", probe)
    return probe


def _runtime_line(dialog) -> str:
    return dialog._runtime_label.text()


def _wait(app, predicate, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ---------------------------------------------------------------- the line (Qt-free)
def test_the_resolved_runtime_is_shown_as_it_is(monkeypatch):
    _runtime(monkeypatch, RESOLVED)

    # A guess made before PyTorch loaded never replaces what PyTorch resolved.
    for guess in (None, GPU_GUESS, CPU_GUESS):
        assert about_dialog.runtime_text(guess) == f"GPU · {GPU_NAME} · CUDA 12.8 · TF32"


def test_a_driver_named_device_says_where_its_name_came_from(monkeypatch):
    _runtime(monkeypatch, UNRESOLVED)

    assert about_dialog.runtime_text(GPU_GUESS) == f"{GPU_NAME} {FROM_DRIVER}"


def test_a_cpu_guess_says_why_and_that_pytorch_has_not_loaded(monkeypatch):
    _runtime(monkeypatch, UNRESOLVED)

    text = about_dialog.runtime_text(CPU_GUESS)

    assert text == f"CPU (PyTorch not loaded yet) · {CPU_NOTE.rstrip('.')}"


@pytest.mark.parametrize(
    "guess, expected",
    [
        (
            DeviceGuess("unknown", note="PyTorch 2.8 is a ROCm build: named later."),
            "Device not detected yet (PyTorch not loaded yet) · PyTorch 2.8 is a ROCm build: "
            "named later",
        ),
        (
            DeviceGuess("unknown", problem="PyTorch is not installed: Generate cannot run."),
            "Device not detected yet · PyTorch is not installed: Generate cannot run",
        ),
        (None, "Device not detected yet"),
    ],
)
def test_an_unknown_device_says_so(monkeypatch, guess, expected):
    _runtime(monkeypatch, UNRESOLVED)

    assert about_dialog.runtime_text(guess) == expected


def test_missing_weights_are_named_after_the_device(monkeypatch):
    _runtime(monkeypatch, _summary(mask=False))

    assert about_dialog.runtime_text(GPU_GUESS) == f"{GPU_NAME} {FROM_DRIVER} · mask model missing"


def test_details_take_the_runtime_line_they_are_given(monkeypatch):
    _runtime(monkeypatch, UNRESOLVED)
    line = f"{GPU_NAME} {FROM_DRIVER}"

    assert f"Runtime: {line}\n" in about_dialog.details_text({}, runtime=line)
    assert "Runtime: Device not detected yet\n" in about_dialog.details_text({})


def test_details_without_an_application_neither_probe_nor_crash(monkeypatch):
    from PyQt6.QtGui import QGuiApplication

    from app.gui import canvas_gl

    def probe():
        raise AssertionError("details_text must not probe")

    monkeypatch.setattr(canvas_gl, "probe_opengl_support", probe)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: None))
    _runtime(monkeypatch, UNRESOLVED)

    assert "Canvas: not checked\n" in about_dialog.details_text({})
    assert "Canvas: OpenGL · GPU\n" in about_dialog.details_text({}, canvas="OpenGL · GPU")


# ---------------------------------------------------------------- the dialog
def test_the_dialog_names_the_driver_device_off_the_gui_thread(app, dialogs, monkeypatch):
    _runtime(monkeypatch, UNRESOLVED)
    probe = _probe(monkeypatch, GPU_GUESS)

    dialog = about_dialog.AboutDialog()
    dialogs.append(dialog)

    assert _wait(app, lambda: FROM_DRIVER in _runtime_line(dialog)), _runtime_line(dialog)
    assert _runtime_line(dialog) == f"{GPU_NAME} {FROM_DRIVER}"
    assert probe.threads and probe.threads[0] is not threading.main_thread()
    dialog.copy_details()
    assert f"Runtime: {GPU_NAME} {FROM_DRIVER}" in QApplication.clipboard().text()


def test_the_dialog_shows_the_resolved_runtime_without_asking_the_driver(app, dialogs, monkeypatch):
    _runtime(monkeypatch, RESOLVED)
    probe = _probe(monkeypatch, CPU_GUESS)

    dialog = about_dialog.AboutDialog()
    dialogs.append(dialog)
    _wait(app, lambda: False, timeout=0.1)

    assert _runtime_line(dialog) == f"GPU · {GPU_NAME} · CUDA 12.8 · TF32"
    assert probe.threads == []


def test_copy_details_reads_a_runtime_resolved_since_the_dialog_opened(app, dialogs, monkeypatch):
    _runtime(monkeypatch, UNRESOLVED)
    _probe(monkeypatch, GPU_GUESS)
    dialog = about_dialog.AboutDialog()
    dialogs.append(dialog)
    assert _wait(app, lambda: FROM_DRIVER in _runtime_line(dialog))

    _runtime(monkeypatch, RESOLVED)  # a Generate warm-up loaded PyTorch meanwhile
    dialog.copy_details()

    assert f"Runtime: GPU · {GPU_NAME} · CUDA 12.8 · TF32\n" in QApplication.clipboard().text()
    assert _runtime_line(dialog) == f"GPU · {GPU_NAME} · CUDA 12.8 · TF32"

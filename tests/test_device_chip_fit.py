"""The device chip keeps its warnings whole: the device name gives way first (V10).

The chip was middle-elided to 320 px as one string, so with missing weights (a fresh
install without checkpoints/) it read "GPU · NVIDIA GeForce RTX 5…g · skeleton model
missing": the "mask model missing" warning was cut to "…g". Now the parts give way in
order: the GPU name loses its vendor words, then the precision and the CUDA version go,
then the name is shortened and dropped; "GPU" / "CPU" and every warning are always shown
whole, and a status such as "Device not detected yet" gives way to the warnings. The
tooltip keeps the full line.
"""

from __future__ import annotations

import sys
import types

import pytest
from PyQt6.QtGui import QFontMetrics

from app.gui import shell_chrome
from app.gui.shell_chrome import INFO_MAX_WIDTH, StatusLine, runtime_chip_text
from app.inference.device_probe import DeviceGuess
from tests.qt_teardown import delete_widgets

RUNTIME = "app.inference.runtime"
BOTH = "mask model missing · skeleton model missing"
GPU_LINE = "GPU · NVIDIA GeForce RTX 5070 Ti · CUDA 12.8 · TF32"


class CharMetrics:
    """6 px a character; eliding keeps a prefix and "…" (as Qt's ElideRight)."""

    def horizontalAdvance(self, text: str) -> int:  # noqa: N802 - QFontMetrics API
        return 6 * len(text)

    def elidedText(self, text: str, _mode, width: int) -> str:  # noqa: N802 - QFontMetrics API
        if 6 * len(text) <= width:
            return text
        keep = width // 6 - 1
        return text[:keep] + "…" if keep >= 0 else ""


def chip(text: str) -> str:
    return runtime_chip_text(text, CharMetrics(), 320)  # 53 characters


# ------------------------------------------------------------------ the parts' order
@pytest.mark.parametrize(
    "text",
    ["CPU", "GPU · RTX 4080 · CUDA 12.8 · TF32", "Device not detected yet · mask model missing"],
)
def test_a_line_that_fits_is_shown_as_it_is(text):
    assert chip(text) == text


def test_the_walkthrough_line_keeps_both_warnings_whole():
    """'GPU · NVIDIA GeForce RTX 5…g · skeleton model missing' before."""
    assert chip(f"GPU · NVIDIA GeForce RTX 5070 Ti · CUDA 12.8 · {BOTH}") == f"GPU · {BOTH}"


def test_the_vendor_words_go_first_then_the_details_from_the_end():
    assert chip("GPU · NVIDIA GeForce RTX 5070 Ti Laptop GPU · CUDA 12.8 · TF32") == (
        "GPU · RTX 5070 Ti Laptop GPU · CUDA 12.8 · TF32"
    )
    assert chip(f"{GPU_LINE} · mask model missing") == (
        "GPU · RTX 5070 Ti · CUDA 12.8 · mask model missing"
    )
    assert chip(f"{GPU_LINE} · skeleton model missing") == (
        "GPU · RTX 5070 Ti · skeleton model missing"
    )


def test_a_long_name_is_shortened_before_it_is_dropped():
    shown = chip(
        "GPU · NVIDIA RTX 6000 Ada Generation Workstation · CUDA 12.8 · mask model missing"
    )
    assert shown == "GPU · RTX 6000 Ada Generation W… · mask model missing"
    assert chip("GPU · " + "X" * 400) == "GPU · " + "X" * 46 + "…"


def test_cpu_and_gpu_stay_and_a_status_gives_way_to_the_warnings():
    assert chip(f"CPU · FP32 · {BOTH}") == f"CPU · {BOTH}"
    assert chip(f"Device not detected yet · {BOTH}") == BOTH
    assert chip(f"Checking GPU… · {BOTH}") == BOTH
    narrow = runtime_chip_text(f"GPU · RTX 4080 · {BOTH}", CharMetrics(), 100)
    assert narrow == f"GPU · {BOTH}"  # wider than asked: the kind and the warnings stay whole


# ---------------------------------------------------------------- the status line
class FakeRuntime:
    """``app.inference.runtime`` with both weights missing (a fresh install)."""

    def __init__(self, text: str, device: str = "GPU") -> None:
        self.module = types.ModuleType(RUNTIME)
        summary = types.SimpleNamespace(
            device=device,
            text=text,
            models_ok=False,
            mask_weights=False,
            skeleton_weights=False,
        )
        self.module.describe_runtime = lambda resolve=False: summary


@pytest.fixture
def make_line(app, monkeypatch):
    made = []

    def make(text: str, device: str = "GPU", guess=None) -> StatusLine:
        monkeypatch.setitem(sys.modules, RUNTIME, FakeRuntime(text, device).module)
        line = StatusLine()  # its first refresh already shows the line
        if guess is not None:
            line.set_device_guess(guess)
        made.append(line)
        return line

    yield make
    delete_widgets(made, close=False)


def check_chip(line: StatusLine, full: str, warnings: str, kind: str | None) -> str:
    """The chip shows the warnings whole (and ``kind`` first), no wider than the warnings
    with the kind need when they need more than INFO_MAX_WIDTH; the tooltip has it all."""
    shown = line.info.text()
    line.info.ensurePolished()  # its stylesheet font (the chip's monospace)
    width = QFontMetrics(line.info.font()).horizontalAdvance
    assert shown.endswith(warnings), shown
    if kind is not None:
        assert shown.startswith(kind + " · "), shown
    least = f"{kind} · {warnings}" if kind else warnings
    assert width(shown) <= max(INFO_MAX_WIDTH, width(least)), shown
    assert line.info.toolTip().splitlines()[0] == f"Inference runtime: {full}"
    return shown


def test_the_resolved_runtime_keeps_both_warnings(make_line):
    full = f"{GPU_LINE} · {BOTH}"
    line = make_line(full)
    check_chip(line, full, BOTH, "GPU")
    assert line.info.property("models_ok") is False


def test_the_driver_guess_keeps_both_warnings(make_line):
    guess = DeviceGuess("GPU", name="NVIDIA GeForce RTX 5070 Ti", cuda_version="12.8")
    line = make_line(f"Device not detected yet · {BOTH}", device="unknown", guess=guess)
    full = f"GPU · NVIDIA GeForce RTX 5070 Ti · CUDA 12.8 · {BOTH}"
    check_chip(line, full, BOTH, "GPU")


def test_an_unresolved_device_gives_way_to_the_warnings(make_line):
    full = f"Device not detected yet · {BOTH}"
    line = make_line(full, device="unknown")
    shown = check_chip(line, full, BOTH, None)
    assert "…" not in shown  # dropped, not cut


def test_one_warning_keeps_as_much_of_the_device_as_fits(make_line):
    full = f"{GPU_LINE} · skeleton model missing"
    line = make_line(full)
    shown = check_chip(line, full, "skeleton model missing", "GPU")
    assert "NVIDIA GeForce" not in shown  # the vendor words went first


def test_the_chip_measures_with_its_own_font_from_the_first_refresh(make_line):
    full = f"{GPU_LINE} · {BOTH}"
    line = make_line(full)
    line.info.ensurePolished()
    metrics = QFontMetrics(line.info.font())
    assert line.info.text() == runtime_chip_text(full, metrics)
    assert shell_chrome.INFO_MAX_WIDTH == INFO_MAX_WIDTH

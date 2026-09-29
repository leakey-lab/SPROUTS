"""OpenGL viewport policy for the image canvases (app/gui/canvas_gl.py).

GL viewports are used only when the env opt-out allows them AND a one-time probe
found a usable hardware OpenGL context (UI-16, PE-05).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui import canvas_gl  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_env_flag_unset_requests_opengl(monkeypatch):
    monkeypatch.delenv(canvas_gl.ENV_VAR, raising=False)
    assert canvas_gl.opengl_viewport_requested() is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        (" Yes ", True),
        ("ON", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_env_flag_is_read_at_call_time(monkeypatch, value, expected):
    monkeypatch.setenv(canvas_gl.ENV_VAR, value)
    assert canvas_gl.opengl_viewport_requested() is expected


@pytest.mark.parametrize(
    ("renderer", "software"),
    [
        ("Gallium 0.4 on llvmpipe (LLVM 3.6, 128 bits)", True),
        ("llvmpipe (LLVM 15.0.7, 256 bits)", True),
        ("GDI Generic", True),
        ("Microsoft Basic Render Driver", True),
        ("Google SwiftShader", True),
        ("NVIDIA GeForce RTX 5070 Ti/PCIe/SSE2", False),
        ("AMD Radeon(TM) Graphics", False),
        ("Intel(R) UHD Graphics 620", False),
        ("", False),
    ],
)
def test_software_rasterizers_are_recognised(renderer, software):
    assert canvas_gl.is_software_renderer(renderer) is software


def test_env_opt_out_never_runs_the_probe(monkeypatch):
    calls = []

    def probe():
        calls.append(1)
        return canvas_gl.GLSupport(True, "fake")

    monkeypatch.setattr(canvas_gl, "opengl_support", probe)
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    assert canvas_gl.opengl_viewports_allowed() is False
    assert calls == []


@pytest.mark.parametrize("supported", [True, False])
def test_allowed_follows_the_probe_when_requested(monkeypatch, supported):
    monkeypatch.setattr(canvas_gl, "opengl_support", lambda: canvas_gl.GLSupport(supported, "fake"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "1")
    assert canvas_gl.opengl_viewports_allowed() is supported


def test_probe_finds_no_opengl_on_the_offscreen_platform(app):
    if app.platformName() != "offscreen":
        pytest.skip("the offscreen platform is the one known to have no OpenGL")
    support = canvas_gl.probe_opengl_support()
    assert support.supported is False
    assert support.reason

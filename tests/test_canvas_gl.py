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


def _fake_support(monkeypatch, support):
    monkeypatch.setattr(canvas_gl, "opengl_support", lambda: support)


def test_canvas_line_names_the_renderer(monkeypatch):
    monkeypatch.delenv(canvas_gl.ENV_VAR, raising=False)
    _fake_support(
        monkeypatch,
        canvas_gl.GLSupport(True, "hardware OpenGL", "NVIDIA GeForce RTX 5070 Ti"),
    )
    assert canvas_gl.canvas_line() == ("OpenGL · NVIDIA GeForce RTX 5070 Ti", False)


def test_canvas_line_says_the_user_turned_opengl_off(monkeypatch):
    def probe():
        raise AssertionError("an opted-out session must not probe")

    monkeypatch.setattr(canvas_gl, "opengl_support", probe)
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    assert canvas_gl.canvas_line() == (
        "Software drawing · turned off by ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT",
        False,
    )


def test_canvas_line_flags_an_unchosen_slow_path(monkeypatch):
    monkeypatch.delenv(canvas_gl.ENV_VAR, raising=False)
    _fake_support(
        monkeypatch, canvas_gl.GLSupport(False, "software renderer 'llvmpipe'", "llvmpipe")
    )
    assert canvas_gl.canvas_line() == (
        "Software drawing (slower) · software renderer 'llvmpipe'",
        True,
    )


def test_slow_path_notice_only_off_offscreen(monkeypatch):
    monkeypatch.delenv(canvas_gl.ENV_VAR, raising=False)
    _fake_support(
        monkeypatch, canvas_gl.GLSupport(False, "software renderer 'llvmpipe'", "llvmpipe")
    )
    assert canvas_gl.slow_path_notice("windows") == (
        "Canvases draw without OpenGL (slower): software renderer 'llvmpipe'. See About."
    )
    assert canvas_gl.slow_path_notice("offscreen") is None
    assert canvas_gl.slow_path_notice("minimal") is None
    _fake_support(monkeypatch, canvas_gl.GLSupport(True, "hardware OpenGL", "GPU"))
    assert canvas_gl.slow_path_notice("windows") is None
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    assert canvas_gl.slow_path_notice("windows") is None


def test_the_canvas_notice_is_one_warn_toast(monkeypatch):
    from types import SimpleNamespace

    from app.gui.window import body

    calls = []
    window = SimpleNamespace(notify=lambda text, kind: calls.append((text, kind)))
    monkeypatch.setattr(canvas_gl, "slow_path_notice", lambda platform: "slow canvases")
    assert body.notify_canvas_path(window) is True
    assert calls == [("slow canvases", "warn")]
    calls.clear()
    monkeypatch.setattr(canvas_gl, "slow_path_notice", lambda platform: None)
    assert body.notify_canvas_path(window) is False
    assert calls == []

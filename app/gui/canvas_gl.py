"""OpenGL viewport policy for the image canvases.

Every image ``QGraphicsView`` in the app (see :mod:`app.gui.canvas_view`) renders
through a ``QOpenGLWidget`` viewport only when that is both requested and useful:

* **requested**: the ``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT`` environment variable,
  read at call time. Unset means on, except on macOS (``sys.platform == "darwin"``),
  where unset means off: Apple's deprecated GL 4.1 over Metal is untested here, so
  the raster viewport (tested on every OS) is the default and ``1`` opts in. When
  set, only ``1``/``true``/``yes``/``on`` keep it on. It is the documented escape
  hatch for machines where GL misbehaves.
* **supported**: a one-time probe creates a real OpenGL context. Platforms
  without GL (``offscreen``, some remote sessions) fail it. A software
  rasterizer (Mesa llvmpipe from Qt's ``opengl32sw.dll`` fallback, Windows'
  "GDI Generic") is slower than the plain raster viewport, so it counts as
  unsupported too.

Cost: creating the first context loads the GL driver (about a second on a cold
Windows start). Qt pays that cost anyway as soon as a window holding a
``QOpenGLWidget`` is shown, so probing first adds no time where GL is used, and
with the env opt-out set the probe never runs.

GL viewports ask for :func:`gl_samples` MSAA samples (4 by default) so vector items
(skeleton lines, brush rings, endpoint markers, previews) are antialiased on GL too;
``ROOT_VIEWER_GL_SAMPLES=0`` turns multisampling off on weak GPUs.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENV_VAR = "ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
DARWIN_REASON = (
    "macOS: raster canvases by default (set ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=1 to try OpenGL)"
)

SAMPLES_ENV_VAR = "ROOT_VIEWER_GL_SAMPLES"
DEFAULT_GL_SAMPLES = 4
MAX_GL_SAMPLES = 16

# Lower-case GL_RENDERER fragments of CPU rasterizers.
_SOFTWARE_RENDERERS = (
    "llvmpipe",
    "softpipe",
    "swrast",
    "swiftshader",
    "gdi generic",
    "microsoft basic render",
)

_GL_RENDERER = 0x1F01

# Qt platforms that have no OpenGL by design: no slow-path warning there.
_NO_GL_PLATFORMS = frozenset({"offscreen", "minimal"})


@dataclass(frozen=True)
class GLSupport:
    """Outcome of the OpenGL probe: usable or not, and why (for the log)."""

    supported: bool
    reason: str
    renderer: str = ""


def opengl_viewport_requested() -> bool:
    """Whether GL viewports are wanted (env and platform read now, not at import).

    Unset: on, except on macOS where the raster viewport is the default.
    """
    value = os.environ.get(ENV_VAR)
    if value is None:
        return sys.platform != "darwin"
    return value.strip().lower() in _TRUE_VALUES


def gl_samples() -> int:
    """MSAA samples for GL canvas viewports: ``ROOT_VIEWER_GL_SAMPLES``, read now.

    Unset or not an integer → ``DEFAULT_GL_SAMPLES`` (4); ``0`` disables multisampling;
    other values are clamped to ``0 .. MAX_GL_SAMPLES``.
    """
    value = os.environ.get(SAMPLES_ENV_VAR)
    if value is None:
        return DEFAULT_GL_SAMPLES
    try:
        samples = int(value.strip())
    except ValueError:
        return DEFAULT_GL_SAMPLES
    return max(0, min(MAX_GL_SAMPLES, samples))


def is_software_renderer(renderer: str) -> bool:
    """Whether a ``GL_RENDERER`` string names a CPU rasterizer."""
    name = renderer.lower()
    return any(fragment in name for fragment in _SOFTWARE_RENDERERS)


def probe_opengl_support() -> GLSupport:
    """Create a throwaway OpenGL context and judge whether GL viewports are worth it.

    Needs a ``QGuiApplication``. Uncached; use :func:`opengl_support`.
    """
    try:
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget  # noqa: F401  (the viewport class)
    except ImportError as exc:
        return GLSupport(False, f"Qt OpenGL modules unavailable ({exc})")

    context = QOpenGLContext()
    if not context.create():
        return GLSupport(False, "no OpenGL context on this platform")
    fmt = context.format()
    if fmt.majorVersion() < 2:
        return GLSupport(False, f"OpenGL {fmt.majorVersion()}.{fmt.minorVersion()} is below 2.0")
    renderer = _renderer_name(context)
    if is_software_renderer(renderer):
        return GLSupport(False, f"software renderer {renderer!r}", renderer)
    return GLSupport(True, "hardware OpenGL", renderer)


def _renderer_name(context) -> str:
    """``GL_RENDERER`` of ``context``, or ``""`` when it cannot be read (e.g. GLES)."""
    try:
        from PyQt6.QtGui import QOffscreenSurface
        from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile
    except ImportError:
        return ""
    surface = QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    try:
        if not context.makeCurrent(surface):
            return ""
        profile = QOpenGLVersionProfile()
        profile.setVersion(2, 0)
        functions = QOpenGLVersionFunctionsFactory.get(profile, context)
        if functions is None or not functions.initializeOpenGLFunctions():
            return ""
        return str(functions.glGetString(_GL_RENDERER) or "")
    finally:
        context.doneCurrent()
        surface.destroy()


@functools.cache
def opengl_support() -> GLSupport:
    """The probe result, computed once per process and logged."""
    support = probe_opengl_support()
    if support.supported:
        logger.info("OpenGL canvas viewports available (%s)", support.renderer or support.reason)
    elif opengl_viewport_requested():
        logger.warning("OpenGL canvas viewports off (slower drawing): %s", support.reason)
    else:
        logger.info("OpenGL canvas viewports off: %s", support.reason)
    return support


def opengl_viewports_allowed() -> bool:
    """Whether canvases may use a ``QOpenGLWidget`` viewport right now.

    The env opt-out is checked first, so an opted-out session never loads GL.
    """
    return opengl_viewport_requested() and opengl_support().supported


def canvas_line() -> tuple[str, bool]:
    """Which canvas path is active, as ``(text, slow)``; ``slow`` when the user did not choose it.

    The env opt-out answers first, so an opted-out session never probes.
    """
    if not opengl_viewport_requested():
        if ENV_VAR not in os.environ:
            return f"Software drawing · {DARWIN_REASON}", False
        return f"Software drawing · turned off by {ENV_VAR}", False
    support = opengl_support()
    if support.supported:
        return f"OpenGL · {support.renderer or support.reason}", False
    return f"Software drawing (slower) · {support.reason}", True


def slow_path_notice(platform: str) -> str | None:
    """The warning toast text when the canvases draw slowly without the user's say-so.

    ``None`` on the ``offscreen`` / ``minimal`` platforms (no GL by design) and when the
    path is OpenGL or was turned off by the user.
    """
    if platform in _NO_GL_PLATFORMS or not canvas_line()[1]:
        return None
    return f"Canvases draw without OpenGL (slower): {opengl_support().reason}. See About."

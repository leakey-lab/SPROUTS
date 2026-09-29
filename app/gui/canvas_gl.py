"""OpenGL viewport policy for the image canvases.

Every image ``QGraphicsView`` in the app (see :mod:`app.gui.canvas_view`) renders
through a ``QOpenGLWidget`` viewport only when that is both requested and useful:

* **requested**: the ``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT`` environment variable,
  read at call time. Unset means on; only ``1``/``true``/``yes``/``on`` keep it on
  when set. It is the documented escape hatch for machines where GL misbehaves.
* **supported**: a one-time probe creates a real OpenGL context. Platforms
  without GL (``offscreen``, some remote sessions) fail it. A software
  rasterizer (Mesa llvmpipe from Qt's ``opengl32sw.dll`` fallback, Windows'
  "GDI Generic") is slower than the plain raster viewport, so it counts as
  unsupported too.

Cost: creating the first context loads the GL driver (about a second on a cold
Windows start). Qt pays that cost anyway as soon as a window holding a
``QOpenGLWidget`` is shown, so probing first adds no time where GL is used, and
with the env opt-out set the probe never runs.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENV_VAR = "ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

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


@dataclass(frozen=True)
class GLSupport:
    """Outcome of the OpenGL probe: usable or not, and why (for the log)."""

    supported: bool
    reason: str
    renderer: str = ""


def opengl_viewport_requested() -> bool:
    """True unless ``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT`` opts out (read now, not at import)."""
    value = os.environ.get(ENV_VAR)
    if value is None:
        return True
    return value.strip().lower() in _TRUE_VALUES


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
    else:
        logger.info("OpenGL canvas viewports off: %s", support.reason)
    return support


def opengl_viewports_allowed() -> bool:
    """Whether canvases may use a ``QOpenGLWidget`` viewport right now.

    The env opt-out is checked first, so an opted-out session never loads GL.
    """
    return opengl_viewport_requested() and opengl_support().supported

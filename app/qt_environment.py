"""Qt / Chromium start-up environment per operating system (owner decision O21).

Qt reads these variables once, when it loads, so ``main.py`` calls
:func:`configure_qt_environment` before logging and before any Qt import. Stdlib
only: no Qt, no model code.

- **Windows (D3D11 / ANGLE).** ``QWebEngineView`` is backed by a ``QQuickWidget``,
  which needs a QRhi-backed composition path. Forcing OpenGL (for example
  ``QT_OPENGL=software``) or mixing a ``QOpenGLWidget`` into the same top-level
  window can leave the WebEngine surface black, with warnings such as "OpenGL is
  not compatible with this QQuickWidget" / "Failed to get a QRhi". The app pins
  the D3D11 RHI (``QSG_RHI_BACKEND=d3d11``) and lets Chromium translate GLES to
  Direct3D 11 through ANGLE (``--use-angle=d3d11``). An earlier ``--disable-gpu``
  disabled ANGLE and dropped WebEngine to software GLES2 ("Failed to create GLES3
  context, fallback to GLES2"); do not bring it back.
- **Linux (OpenGL RHI, Qt default).** Nothing is set: a D3D11 backend cannot exist
  there, Qt then fails "Failed to create QRhi" and the dashboards render black.
  Chromium refuses to start as root unless its sandbox is off, so
  ``QTWEBENGINE_DISABLE_SANDBOX=1`` is set only when the effective user id is 0.
- **macOS (Metal RHI, Qt default).** No backend is set. ``QT_FONT_DPI=96`` keeps
  text at its designed size: Qt maps 1 pt to 1 px on macOS and the theme sizes
  text in ``pt``. Unverified without a Mac; if Cocoa ignores it, convert ``pt`` to
  ``px`` when the QSS is loaded instead.

Every value is set with ``setdefault`` semantics: a value the user already
exported wins.
"""

from __future__ import annotations

import sys
from collections.abc import MutableMapping

#: Desktop file name given to the application (Wayland compositors match the
#: window to its icon through it); the same name as the ``sprouts`` script.
DESKTOP_FILE_NAME = "sprouts"

_ANGLE_D3D11_FLAG = "--use-angle=d3d11"


def configure_qt_environment(
    environ: MutableMapping[str, str],
    platform: str = sys.platform,
    euid: int | None = None,
) -> dict[str, str]:
    """Set the Qt / Chromium start-up variables for ``platform`` in ``environ``.

    Args:
        environ: The environment to update (``os.environ`` at start-up).
        platform: A ``sys.platform`` value.
        euid: The effective user id (``os.geteuid()``), or ``None`` where the
            platform has none.

    Returns:
        The variables this call set or changed, with their new values.
    """
    wanted: dict[str, str] = {}
    if platform == "win32":
        wanted["QSG_RHI_BACKEND"] = "d3d11"
    elif platform.startswith("linux"):
        if euid == 0:
            wanted["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    elif platform == "darwin":
        wanted["QT_FONT_DPI"] = "96"

    applied: dict[str, str] = {}
    for name, value in wanted.items():
        if name not in environ:
            environ[name] = value
            applied[name] = value

    if platform == "win32":
        flags = environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
        if _ANGLE_D3D11_FLAG not in flags.split():
            flags = f"{flags} {_ANGLE_D3D11_FLAG}".strip()
            environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
            applied["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
    return applied


def qt_style_for(platform: str = sys.platform) -> str | None:
    """The Qt widget style to set before the theme, or ``None`` to keep the native one.

    Windows keeps its native style, which the theme was tuned on. Elsewhere Fusion
    makes the QSS and the palette render the same as there.
    """
    return None if platform == "win32" else "Fusion"

"""The single resolver for files shipped with the app (themes, icons, config defaults).

Paths are resolved against the project root, never the working directory, so the
app starts the same way from a shortcut, another folder or a console entry point:

* development - the repository root (the parent of this ``resources/`` folder);
* PyInstaller - the bundle directory ``sys._MEIPASS``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# abspath, not resolve(): keep mapped/subst drive letters instead of UNC or target paths.
_REPO_ROOT = Path(os.path.abspath(__file__)).parent.parent


def project_root() -> Path:
    """Directory that shipped resources are relative to (bundle dir when frozen)."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else _REPO_ROOT


def resource_path(*parts: str | os.PathLike[str]) -> str:
    """Absolute path of a shipped resource, independent of the working directory.

    ``resource_path("resources/themes/dark_theme.qss")`` and
    ``resource_path("resources", "themes", "dark_theme.qss")`` are equivalent.
    """
    return os.path.normpath(os.path.join(project_root(), *parts))


# Backward-compatible name used by existing callers.
get_resource_path = resource_path

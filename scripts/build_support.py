"""
Build-time helpers for the Windows bundle (``scripts/build_executable.spec``).

Everything the spec needs to know about the repository lives here so it can be
unit tested without running PyInstaller (``tests/test_fair_build.py``):

* :func:`resolve_datas` -- the data files to bundle, anchored at the repository
  root, failing loudly (:class:`BuildConfigError`) when a required one is missing;
* :func:`app_modules` -- every module of the ``app`` package, so modules that are
  only imported lazily (PEP 562, ``importlib``) are still bundled;
* :func:`windows_version_info` -- the Windows version resource, from ``app.__version__``;
* :func:`collect_build_info` / :func:`write_build_info` -- ``build_info.json``,
  bundled at the root of the app so a frozen build (no git) can report what it
  was built from.

Import-light: no Qt or torch import; PyInstaller is imported only by
:func:`windows_version_info`. The repository root must be on ``sys.path``
(the spec arranges that) because the app's own metadata is read from ``app``.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import os
import platform
import posixpath
import subprocess
from pathlib import Path

import app
from app.models import MODELS
from app.provenance import BUILD_INFO_FILENAME  # the frozen app reads its commit from here

__all__ = [
    "BUILD_INFO_FILENAME",
    "MODEL_DATAS",
    "REQUIRED_DATAS",
    "ROOT",
    "BuildConfigError",
    "app_modules",
    "collect_build_info",
    "resolve_datas",
    "version_tuple",
    "windows_version_info",
    "write_build_info",
]

ROOT = Path(__file__).resolve().parents[1]

# (source relative to the repository root, destination folder inside the bundle).
# The app resolves these at run time with resources.resource_utils (sys._MEIPASS).
REQUIRED_DATAS: tuple[tuple[str, str], ...] = (
    ("resources/themes", "resources/themes"),
    ("resources/icons", "resources/icons"),
    ("resources/app_icon.ico", "resources"),
    ("resources/app_icon.png", "resources"),
    ("app/assets", "app/assets"),
    ("config/normalization_defaults.json", "config"),
)


# Model weights are not in git (docs/MODELS.md): bundled at their registered path
# (app.models) when present, else a warning.
MODEL_DATAS: tuple[tuple[str, str], ...] = tuple(
    (spec.relpath, posixpath.dirname(spec.relpath)) for spec in MODELS.values()
)


class BuildConfigError(RuntimeError):
    """A file the bundle needs is missing; raised before PyInstaller does any work."""


def resolve_datas(root: Path = ROOT) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ``(datas, warnings)`` for PyInstaller's ``Analysis(datas=...)``.

    ``datas`` holds absolute ``(source, destination)`` pairs: every entry of
    ``REQUIRED_DATAS`` plus the model weights that exist. ``warnings`` names each
    missing weights file. Raises :class:`BuildConfigError` listing every missing
    required source.
    """
    missing = [source for source, _dest in REQUIRED_DATAS if not (root / source).exists()]
    if missing:
        raise BuildConfigError(f"missing under {root}: {', '.join(missing)}")
    datas = [(str(root / source), dest) for source, dest in REQUIRED_DATAS]
    warnings = []
    for source, dest in MODEL_DATAS:
        if (root / source).is_file():
            datas.append((str(root / source), dest))
        else:
            warnings.append(
                f"model weights not found: {source} -- the bundle will not be able to "
                "generate masks/skeletons (see docs/MODELS.md)"
            )
    return datas, warnings


def app_modules(root: Path = ROOT) -> list[str]:
    """Dotted names of every ``.py`` module under ``<root>/app`` (packages included)."""
    names = []
    for path in sorted((root / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(root).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """The four integers a Windows version resource needs: the release numbers of
    a PEP 440 version, padded with zeros (``"3.1.0rc1"`` -> ``(3, 1, 0, 0)``)."""
    from packaging.version import Version

    release = Version(version).release[:4]
    return tuple(release + (0,) * (4 - len(release)))


def windows_version_info(version: str = app.__version__):
    """PyInstaller ``VSVersionInfo`` for ``SPROUTS.exe`` (Explorer > Properties > Details)."""
    from PyInstaller.utils.win32 import versioninfo as vi

    numbers = version_tuple(version)
    strings = [
        vi.StringStruct("CompanyName", app.ORGANIZATION_NAME),
        vi.StringStruct("FileDescription", app.APP_FULL_NAME),
        vi.StringStruct("FileVersion", version),
        vi.StringStruct("InternalName", app.APP_NAME),
        vi.StringStruct("OriginalFilename", f"{app.APP_NAME}.exe"),
        vi.StringStruct("ProductName", app.APP_NAME),
        vi.StringStruct("ProductVersion", version),
    ]
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            vi.StringFileInfo([vi.StringTable("040904B0", strings)]),  # en-US, UTF-16
            vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def _git_state(root: Path) -> tuple[str | None, bool | None]:
    """``(commit, dirty)`` of the checkout at ``root``; ``(None, None)`` without git."""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    return head.stdout.strip() or None, bool(status.stdout.strip())


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def collect_build_info(root: Path = ROOT) -> dict:
    """What is being built: app name/version, ``git_commit`` and ``git_dirty``
    (``None`` without git; ``app.provenance.git_commit`` reads the commit from this
    file in a frozen app), local build time as ISO 8601 with UTC offset, the Python
    and platform, and the versions of the packages that change results (torch
    encodes its CUDA build, e.g. ``2.8.0+cu128``)."""
    commit, dirty = _git_state(root)
    return {
        "name": app.APP_NAME,
        "version": app.__version__,
        "git_commit": commit,
        "git_dirty": dirty,
        "built_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: _dist_version(name) for name in ("torch", "torchvision", "PyQt6", "pyinstaller")
        },
    }


def write_build_info(path: str | os.PathLike, info: dict) -> Path:
    """Write ``info`` as JSON at ``path`` (parent folders created) and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return path

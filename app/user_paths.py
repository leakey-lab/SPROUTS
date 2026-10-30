"""
Per-user folders for settings, data and logs.

User preferences must never be written into the application folder: in a
development checkout that dirties the git tree (``config/normalization_defaults.json``),
and in a frozen build the folder is read-only or temporary (audit FR-11).

Locations, named after :data:`app.APP_NAME`:

* Windows: config ``%APPDATA%/SPROUTS``; data ``%LOCALAPPDATA%/SPROUTS``;
  logs ``%LOCALAPPDATA%/SPROUTS/Logs``.
* macOS: config and data ``~/Library/Application Support/SPROUTS``;
  logs ``~/Library/Logs/SPROUTS``.
* Linux: config ``$XDG_CONFIG_HOME/sprouts`` (``~/.config/sprouts``); data
  ``$XDG_DATA_HOME/sprouts`` (``~/.local/share/sprouts``); logs
  ``$XDG_STATE_HOME/sprouts/log`` (``~/.local/state/sprouts/log``).

``SPROUTS_CONFIG_DIR`` relocates everything (config there, data and logs in
its ``data``/``logs`` subfolders unless ``SPROUTS_DATA_DIR`` / ``SPROUTS_LOG_DIR``
are set); tests and portable installs use it.

Qt-free; the platform is read at call time.
"""

from __future__ import annotations

import os
import sys

from app import APP_NAME

__all__ = ["user_config_dir", "user_data_dir", "user_log_dir"]


def _home() -> str:
    return os.path.expanduser("~")


def _env_dir(name: str, *fallback: str) -> str:
    value = os.environ.get(name)
    return value if value else os.path.join(_home(), *fallback)


def _ensure(path: str, ensure: bool) -> str:
    if ensure:
        os.makedirs(path, exist_ok=True)
    return path


_OVERRIDE_SUBDIRS = {"data": "data", "log": "logs"}


def _override(kind: str) -> str | None:
    """Folder forced by ``SPROUTS_<KIND>_DIR`` or ``SPROUTS_CONFIG_DIR``, if any."""
    explicit = os.environ.get(f"SPROUTS_{kind.upper()}_DIR")
    if explicit:
        return explicit
    base = os.environ.get("SPROUTS_CONFIG_DIR")
    if not base:
        return None
    return os.path.join(base, _OVERRIDE_SUBDIRS[kind]) if kind in _OVERRIDE_SUBDIRS else base


def user_config_dir(ensure: bool = True) -> str:
    """Folder for user settings (created unless ``ensure`` is False)."""
    path = _override("config")
    if path is None:
        if sys.platform == "win32":
            path = os.path.join(_env_dir("APPDATA", "AppData", "Roaming"), APP_NAME)
        elif sys.platform == "darwin":
            path = os.path.join(_home(), "Library", "Application Support", APP_NAME)
        else:
            path = os.path.join(_env_dir("XDG_CONFIG_HOME", ".config"), APP_NAME.lower())
    return _ensure(path, ensure)


def user_data_dir(ensure: bool = True) -> str:
    """Folder for machine-local app data such as caches (created unless ``ensure`` is False)."""
    path = _override("data")
    if path is None:
        if sys.platform == "win32":
            path = os.path.join(_env_dir("LOCALAPPDATA", "AppData", "Local"), APP_NAME)
        elif sys.platform == "darwin":
            path = os.path.join(_home(), "Library", "Application Support", APP_NAME)
        else:
            path = os.path.join(_env_dir("XDG_DATA_HOME", ".local", "share"), APP_NAME.lower())
    return _ensure(path, ensure)


def user_log_dir(ensure: bool = True) -> str:
    """Folder for log files (created unless ``ensure`` is False)."""
    path = _override("log")
    if path is None:
        if sys.platform == "win32":
            path = os.path.join(user_data_dir(ensure=False), "Logs")
        elif sys.platform == "darwin":
            path = os.path.join(_home(), "Library", "Logs", APP_NAME)
        else:
            state = _env_dir("XDG_STATE_HOME", ".local", "state")
            path = os.path.join(state, APP_NAME.lower(), "log")
    return _ensure(path, ensure)

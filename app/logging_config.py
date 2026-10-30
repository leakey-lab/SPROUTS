"""Centralized logging setup for SPROUTS (Root-Mask-and-Skeletons).

Call ``setup_logging()`` once at application startup, before other app imports,
so module-level loggers are captured. Records go to stderr (when the process has
one; ``pythonw.exe`` and windowed builds do not) and to a rotating ``app.log``
(5 MB, 3 backups) in the first of:

1. the ``log_dir`` argument;
2. the ``SPROUTS_LOG_DIR`` environment variable;
3. ``app.user_paths.user_log_dir()`` (the per-user data folder), when available;
4. ``~/.root_mask_logs`` (the legacy location).

The first line written is a banner with the app version, Python and OS.
Set the ``DEBUG`` environment variable for debug-level output.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

#: Environment variable that overrides the log folder (tests, HPC, portable installs).
LOG_DIR_ENV = "SPROUTS_LOG_DIR"
LOG_FILE_NAME = "app.log"

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d — %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False
_log_file: Path | None = None


def default_log_dir() -> Path:
    """Log folder used when ``setup_logging`` gets no ``log_dir`` (see module docstring)."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    try:  # optional module; any failure falls back to the legacy folder
        from app.user_paths import user_log_dir

        return Path(user_log_dir())
    except Exception:
        return Path.home() / ".root_mask_logs"


def current_log_file() -> Path | None:
    """The active log file, or None when file logging is not configured."""
    return _log_file


def setup_logging(log_dir: Path | None = None) -> Path | None:
    """Configure the root logger (stderr + rotating file) once.

    A second call changes nothing and returns the active log file.

    Returns:
        The log file path, or None if the folder could not be created (then
        only stderr logging is active and a warning says why).
    """
    global _configured, _log_file
    if _configured:
        return _log_file
    _configured = True

    formatter = logging.Formatter(_FORMAT, _DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if os.getenv("DEBUG") else logging.INFO)
    if sys.stderr is not None:
        _add_handler(root, logging.StreamHandler(), formatter)

    directory = Path(log_dir) if log_dir is not None else default_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / LOG_FILE_NAME,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("File logging disabled (%s): %s", directory, exc)
        return None
    _add_handler(root, file_handler, formatter)
    _log_file = directory / LOG_FILE_NAME
    _log_banner()
    return _log_file


def _add_handler(root: logging.Logger, handler: logging.Handler, fmt: logging.Formatter) -> None:
    handler.setFormatter(fmt)
    root.addHandler(handler)


def _os_description() -> str:
    """Short OS name/version without ``platform.platform()`` (it spawns ``cmd /c ver`` on Windows)."""
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        return f"Windows {version.major}.{version.minor}.{version.build}"
    uname = os.uname()
    return f"{uname.sysname} {uname.release}"


def _log_banner() -> None:
    import app  # the package itself: already imported, import-light by contract

    logging.getLogger("app").info(
        "%s %s starting (Python %s, %s); log file %s",
        getattr(app, "APP_NAME", "SPROUTS"),
        getattr(app, "__version__", "unknown"),
        ".".join(map(str, sys.version_info[:3])),
        _os_description(),
        _log_file,
    )

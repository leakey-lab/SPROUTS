"""Persist the main window's geometry, body splitter and last folder (QSettings).

``WindowStateStore`` restores what the user left: window geometry, the library /
canvas splitter sizes and the last folder opened (the ``QFileDialog`` start
directory). On the first run, or when the stored state is unreadable, the window
gets :func:`initial_geometry`: 90% of the screen's available area, never smaller
than the window's minimum size, centred and kept on screen.

Settings live under ``QSettings("LeakeyLab", "SPROUTS")`` next to
``recent_projects``; tests inject an ini-file ``QSettings``.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRect, QSettings, QSize
from PyQt6.QtWidgets import QSplitter, QWidget

from app import ORGANIZATION_NAME

_APP = "SPROUTS"
_GEOMETRY = "window/geometry"
_SPLITTER = "window/body_splitter"
_LAST_FOLDER = "window/last_folder"
DEFAULT_SCREEN_FRACTION = 0.9


def initial_geometry(
    available: QRect, minimum: QSize, fraction: float = DEFAULT_SCREEN_FRACTION
) -> QRect:
    """``fraction`` of ``available``, at least ``minimum`` (but never beyond the
    available area when it fits), centred in ``available``."""
    width = max(int(available.width() * fraction), minimum.width())
    height = max(int(available.height() * fraction), minimum.height())
    width, height = min(width, available.width()), min(height, available.height())
    rect = QRect(0, 0, width, height)
    rect.moveCenter(available.center())
    return rect


def _as_bytes(value) -> QByteArray | None:
    if isinstance(value, QByteArray):
        return value
    if isinstance(value, (bytes, bytearray)):
        return QByteArray(bytes(value))
    return None


class WindowStateStore:
    """Save / restore a window's geometry, its splitter state and the last folder."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings if settings is not None else QSettings(ORGANIZATION_NAME, _APP)

    # ------------------------------------------------------------ geometry
    def restore(self, window: QWidget, splitter: QSplitter | None = None) -> bool:
        """Restore ``window`` (and ``splitter``). Returns False and applies the
        first-run geometry when nothing valid was stored."""
        geometry = _as_bytes(self._settings.value(_GEOMETRY))
        restored = bool(geometry is not None and window.restoreGeometry(geometry))
        if not restored:
            self.apply_default_geometry(window)
        state = _as_bytes(self._settings.value(_SPLITTER))
        if splitter is not None and state is not None:
            splitter.restoreState(state)
        return restored

    def save(self, window: QWidget, splitter: QSplitter | None = None) -> None:
        """Store ``window`` geometry and ``splitter`` state (call from closeEvent)."""
        self._settings.setValue(_GEOMETRY, window.saveGeometry())
        if splitter is not None:
            self._settings.setValue(_SPLITTER, splitter.saveState())
        self._settings.sync()

    @staticmethod
    def apply_default_geometry(window: QWidget) -> None:
        """First-run geometry on ``window``'s screen (see :func:`initial_geometry`)."""
        screen = window.screen()
        if screen is None:
            return
        rect = initial_geometry(screen.availableGeometry(), window.minimumSizeHint())
        window.setGeometry(rect)

    # ---------------------------------------------------------- last folder
    def last_folder(self) -> str:
        """The folder the user opened last ("" if none): the file dialog's start dir."""
        value = self._settings.value(_LAST_FOLDER, "")
        return value if isinstance(value, str) else ""

    def set_last_folder(self, path: str) -> None:
        self._settings.setValue(_LAST_FOLDER, str(path or ""))
        self._settings.sync()

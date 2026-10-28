"""The app's settings store: one factory, the only place that builds a ``QSettings``.

:func:`app_settings` gives the user's ``QSettings(ORGANIZATION_NAME, APP_NAME)``: the
registry on Windows, a plist on macOS, ``~/.config/LeakeyLab/SPROUTS.conf`` on Linux. On a
headless platform (``offscreen``: tests, scripts, audit agents) it gives a fresh
:class:`MemorySettings` instead, so a headless window never reads or writes the user's
settings (C5-04, X8). The window state, the recent projects, the Correct layers'
opacity and visibility and the device-check switch default to it; a caller may hand its
own store (the tests' INI files).

``QSettings(organization, application)`` ignores ``setDefaultFormat`` and ``setPath``, so
nothing at the Qt level can redirect it: every store must come from here
(``tests/test_settings_factory.py`` checks that no other module builds one).

Layer-neutral, like :mod:`app.workers` (PyQt6 only): ``app.handlers`` and ``app.gui`` may
both import it.
"""

from __future__ import annotations

import os
from typing import Any

from PyQt6.QtCore import QCoreApplication, QSettings
from PyQt6.QtGui import QGuiApplication

from app import APP_NAME, ORGANIZATION_NAME

__all__ = ["HEADLESS_PLATFORMS", "MemorySettings", "app_settings", "headless"]

#: Qt platforms without a screen: they never use the user's settings.
HEADLESS_PLATFORMS = frozenset({"offscreen"})

_NO_ERROR = QSettings.Status.NoError


class MemorySettings:
    """The ``QSettings`` calls the stores make, over a dict that lives as long as this
    object: nothing is persisted."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def value(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802 - Qt API name
        self._values[key] = value

    def sync(self) -> None:
        """Nothing to write."""

    def status(self) -> QSettings.Status:
        return _NO_ERROR


def headless() -> bool:
    """True on a platform without a screen (``offscreen``: tests, scripts, agents).

    Before a ``QGuiApplication`` exists, the platform it would start on
    (``QT_QPA_PLATFORM``): Qt then reports its built-in default instead.
    """
    if isinstance(QCoreApplication.instance(), QGuiApplication):
        name = QGuiApplication.platformName()
    else:
        name = os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0].strip()
    return name in HEADLESS_PLATFORMS


def app_settings() -> QSettings | MemorySettings:
    """The user's ``QSettings(ORGANIZATION_NAME, APP_NAME)``; a fresh :class:`MemorySettings`
    on a headless platform (see the module docstring)."""
    if headless():
        return MemorySettings()
    return QSettings(ORGANIZATION_NAME, APP_NAME)

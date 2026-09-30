"""The Correct layers' opacity and visibility, kept across app restarts (QSettings).

Each layer's user opacity (0..1) and visibility are stored as ``correct/<layer id>/opacity``
and ``correct/<layer id>/visible`` in the app's QSettings (``QSettings("LeakeyLab",
"SPROUTS")``, next to the window state and the recent projects). The page restores them
when it is built and stores them when it is hidden, which is also what quitting the app
does to it. A value that cannot be read leaves the layer's default. On the ``offscreen``
platform (tests, scripts) nothing is kept unless a QSettings is given, as for the window
state.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QGuiApplication

from app import APP_NAME, ORGANIZATION_NAME
from app.gui.correct.layer import EditLayer

logger = logging.getLogger(__name__)

__all__ = ["LayerPrefs"]

_GROUP = "correct"
_TRUE, _FALSE = ("true", "1", "yes"), ("false", "0", "no")


def _opacity(value: object) -> float | None:
    try:
        number = float(value)  # an INI / registry value may come back as text
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return True if text in _TRUE else False if text in _FALSE else None


class LayerPrefs:
    """Restore and store the layers' opacity and visibility in ``settings`` (None: keep
    nothing)."""

    def __init__(self, settings: QSettings | None) -> None:
        self.settings = settings

    @classmethod
    def for_app(cls, settings: QSettings | None) -> LayerPrefs:
        """``settings``, else the app's QSettings; none on the ``offscreen`` platform."""
        if settings is None and QGuiApplication.platformName() != "offscreen":
            settings = QSettings(ORGANIZATION_NAME, APP_NAME)
        return cls(settings)

    def restore(self, layers: Iterable[EditLayer]) -> None:
        """Give each layer its stored opacity and visibility (unreadable: its default)."""
        if self.settings is None:
            return
        for layer in layers:
            key = f"{_GROUP}/{layer.layer_id}"
            opacity = _opacity(self.settings.value(f"{key}/opacity"))
            if opacity is not None:
                layer.set_opacity(opacity)
            visible = _flag(self.settings.value(f"{key}/visible"))
            if visible is not None:
                layer.set_visible(visible)

    def store(self, layers: Iterable[EditLayer]) -> None:
        """Write each layer's opacity and visibility (flushed at once)."""
        if self.settings is None:
            return
        for layer in layers:
            key = f"{_GROUP}/{layer.layer_id}"
            self.settings.setValue(f"{key}/opacity", float(layer.opacity()))
            self.settings.setValue(f"{key}/visible", bool(layer.is_visible()))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            logger.warning("Could not store the Correct layers' opacity and visibility")

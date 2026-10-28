"""The Correct layers' opacity and visibility, kept across app restarts (QSettings).

Each layer's user opacity (0..1) and visibility are stored as ``correct/<layer id>/opacity``
and ``correct/<layer id>/visible`` in the app's QSettings (``QSettings("LeakeyLab",
"SPROUTS")``, next to the window state and the recent projects). The page restores them
when it is built and stores them when it is hidden, which is also what quitting the app
does to it: only the values that differ from what the settings hold (as read at restore,
or last written) are written, and the settings are flushed only after a write, so a stage
switch that changed nothing writes nothing (a flush of an INI file took 22-58 ms). A value
that cannot be read leaves the layer's default. On the ``offscreen`` platform (tests,
scripts) nothing is kept unless a QSettings is given, as for the window state.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable

from PyQt6.QtCore import QSettings

from app.gui.correct.layer import EditLayer
from app.settings import app_settings

logger = logging.getLogger(__name__)

__all__ = ["LayerPrefs"]

_GROUP = "correct"
_TRUE, _FALSE = ("true", "1", "yes"), ("false", "0", "no")


def _opacity(value: object) -> float | None:
    try:
        number = float(value)  # an INI / registry value may come back as text
    except TypeError, ValueError:
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
        # What the settings hold, by key: as read at restore (None: nothing readable) or as
        # last written. A key missing here is written by the next store.
        self._held: dict[str, float | bool | None] = {}

    @classmethod
    def for_app(cls, settings: QSettings | None) -> LayerPrefs:
        """``settings``, else the app's store (:func:`app.settings.app_settings`: memory on
        the ``offscreen`` platform, so nothing is kept)."""
        return cls(settings if settings is not None else app_settings())

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
            self._held.update({f"{key}/opacity": opacity, f"{key}/visible": visible})

    def store(self, layers: Iterable[EditLayer]) -> None:
        """Write each layer's opacity and visibility that differs from what the settings
        hold, then flush them once; nothing changed: nothing is written or flushed."""
        if self.settings is None:
            return
        values: dict[str, float | bool] = {}
        for layer in layers:
            key = f"{_GROUP}/{layer.layer_id}"
            values[f"{key}/opacity"] = float(layer.opacity())
            values[f"{key}/visible"] = bool(layer.is_visible())
        changed = {key: value for key, value in values.items() if self._held.get(key) != value}
        if not changed:
            return
        for key, value in changed.items():
            self.settings.setValue(key, value)
        self._held.update(changed)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            logger.warning("Could not store the Correct layers' opacity and visibility")

"""Named indices for the main window's right-panel ``QStackedWidget``.

The page order is a hard contract shared by ``MainWindow``, the shell chrome and
the tests: 0 display, 1 mask tracing, 2 visualization placeholder, 3 skeleton
correction, 4 GT tracing. ``Panel`` is an ``IntEnum``, so it can be passed to
``setCurrentIndex`` / ``widget`` directly and compares equal to the plain ints.
"""

from __future__ import annotations

from enum import IntEnum


class Panel(IntEnum):
    """Right-panel page index (``main_window.right_panel``)."""

    DISPLAY = 0
    MASK_TRACING = 1
    VISUALIZATION = 2
    SKELETON_CORRECTION = 3
    GT_TRACING = 4

    @property
    def key(self) -> str:
        """Legacy ``switch_right_panel`` key, e.g. ``"gt_tracing"``."""
        return self.name.lower()

    @classmethod
    def coerce(cls, value: Panel | int | str) -> Panel:
        """Return the panel named by a ``Panel``, a page index or a legacy string key.

        String keys are matched case-insensitively (``"display"``, ``"mask_tracing"``,
        ``"visualization"``, ``"skeleton_correction"``, ``"gt_tracing"``).

        Raises:
            ValueError: ``value`` names no panel.
        """
        if isinstance(value, str):
            try:
                return cls[value.upper()]
            except KeyError:
                raise ValueError(f"unknown panel key: {value!r}") from None
        if isinstance(value, int) and not isinstance(value, bool):
            return cls(value)  # ValueError for an index outside the contract
        raise ValueError(f"not a panel: {value!r}")

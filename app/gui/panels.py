"""Named indices for the main window's right-panel ``QStackedWidget``.

The page order is a hard contract shared by ``MainWindow``, the shell chrome and the
tests: 0 display (the Library), 1 Correct (the mask and the skeleton of an image on one
canvas), 2 visualization placeholder, 3 Trace GT. Dashboards are appended after these
fixed pages. ``Panel`` is an ``IntEnum``, so it can be passed to ``setCurrentIndex`` /
``widget`` directly and compares equal to the plain ints; name pages with its members,
never int literals (``tests/test_right_panel_contract.py``).

The Correct stage replaced the separate mask and skeleton editors, and the pages were
renumbered on purpose without aliases: the keys and indices of those two editors
(``mask_tracing``, ``skeleton_correction``, index 4) name no page any more, so
:meth:`Panel.coerce` rejects them.
"""

from __future__ import annotations

from enum import IntEnum


class Panel(IntEnum):
    """Right-panel page index (``main_window.right_panel``)."""

    DISPLAY = 0
    CORRECT = 1
    VISUALIZATION = 2
    GT_TRACING = 3

    @property
    def key(self) -> str:
        """The ``switch_right_panel`` string key, e.g. ``"gt_tracing"``."""
        return self.name.lower()

    @classmethod
    def coerce(cls, value: Panel | int | str) -> Panel:
        """Return the panel named by a ``Panel``, a page index or a string key.

        String keys are matched case-insensitively (``"display"``, ``"correct"``,
        ``"visualization"``, ``"gt_tracing"``).

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

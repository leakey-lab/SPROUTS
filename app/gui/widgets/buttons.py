"""Primary / ghost push-button factories: one button system for the whole app.

The factories only name a button (``primaryButton`` / ``ghostButton``) and give it an
icon, cursor and tooltip; ``resources/themes/dark_theme.qss`` owns the look (design
``styles.css``: primary = accent fill, hover ACCENT_PRESS; ghost = borderless, muted
text, BG_3 on hover; both show the keyboard-focus ring). Callers therefore never ship a
button stylesheet of their own that could drift from the design.

``compact=True`` sets the ``density="compact"`` property: 32 px tall instead of 42, for
editor docks and the top bar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from . import tokens
from .icons import load_icon

PRIMARY_BUTTON = "primaryButton"
GHOST_BUTTON = "ghostButton"
_ICON_PX = 16


def primary_button(
    text: str,
    icon_name: str | None = None,
    *,
    tooltip: str | None = None,
    compact: bool = False,
    parent: QWidget | None = None,
) -> QPushButton:
    """Accent-filled button for the one main action of a view or dialog."""
    return _button(PRIMARY_BUTTON, text, icon_name, tokens.ACCENT_INK, tooltip, compact, parent)


def ghost_button(
    text: str,
    icon_name: str | None = None,
    *,
    tooltip: str | None = None,
    compact: bool = False,
    parent: QWidget | None = None,
) -> QPushButton:
    """Borderless secondary button (muted text, BG_3 on hover)."""
    return _button(GHOST_BUTTON, text, icon_name, tokens.TEXT_MUTED, tooltip, compact, parent)


def _button(
    object_name: str,
    text: str,
    icon_name: str | None,
    icon_color: str,
    tooltip: str | None,
    compact: bool,
    parent: QWidget | None,
) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName(object_name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if compact:
        button.setProperty("density", "compact")  # read by the QSS at polish time
    if icon_name:
        button.setIcon(load_icon(icon_name, icon_color, _ICON_PX))
    if tooltip:
        button.setToolTip(tooltip)
        if not text:
            # Qt reads the text as the accessible name; a tooltip is only the description.
            button.setAccessibleName(tooltip)
    return button

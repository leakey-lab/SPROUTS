"""Keyboard-focus policy for controls that float over a canvas.

The canvas editors read hold-keys (Space = pan, B = brush) from the focused canvas. A control
with Qt's default ``StrongFocus`` takes focus when clicked, so after clicking Save a Space
press clicked Save again and a B tap no longer selected the brush (audit UXE-03).
:func:`keep_canvas_focus` strips click and wheel focus from overlay controls: a click leaves
focus where it was, while Tab can still reach them.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


def keep_canvas_focus(*widgets: QWidget, tab_reachable: bool = True) -> None:
    """Stop clicks on ``widgets`` and all their descendants from taking keyboard focus.

    Every focusable widget in those trees keeps only Tab focus (``tab_reachable=True``) or
    loses focus entirely (``tab_reachable=False``, for panels whose controls must never hold
    focus, such as the GT class panel). Widgets that never take focus (labels) are untouched.
    """
    allowed = Qt.FocusPolicy.TabFocus if tab_reachable else Qt.FocusPolicy.NoFocus
    for root in widgets:
        for widget in (root, *root.findChildren(QWidget)):
            # findChildren can hand back a stale wrapper typed as a widget for another object
            # now at its address (a C++-made child of a widget whose dead Python wrapper is
            # still held): touch widgets only.
            if not widget.isWidgetType():
                continue
            policy = widget.focusPolicy()
            restricted = Qt.FocusPolicy(policy & allowed)
            if restricted != policy:
                widget.setFocusPolicy(restricted)

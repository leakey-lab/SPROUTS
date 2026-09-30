"""The Correct page's keys: one ``QShortcut`` per key of the registry's Correct rows, and
PgDn / PgUp, all parented to the page (``install_page_shortcuts``).

The registry (:mod:`app.gui.shortcuts`) documents every key; this module only binds them
to the page's actions. The page binds PgDn / PgUp itself (the Global next / previous rows
document them) because it saves the unsaved layers before it moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut

from app.gui import shortcuts
from app.gui.canvas_view import PIXEL_GRID_OFF, PIXEL_GRID_ON
from app.gui.correct.layer import MASK, SKELETON

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.editor import CorrectEditor

__all__ = ["install_page_shortcuts"]

_PAGING = (("PgDown", 1), ("PgUp", -1))  # documented by the Global next / previous rows
_NO_REPEAT = ("swap_layer", "toggle_layers", "toggle_reference", "pixel_grid")  # no flicker


def install_page_shortcuts(page: CorrectEditor) -> list[QShortcut]:
    """Bind the registry's Correct rows and PgDn / PgUp to ``page``; the shortcuts made."""
    view = page.graphics_view
    installed = shortcuts.install_shortcuts(
        page,
        page.SHORTCUT_SCOPE,
        {
            "undo": page.undo,
            "redo": page.redo,
            "save": page.save,
            "mask_layer": lambda: page.set_active_layer(MASK),
            "skeleton_layer": lambda: page.set_active_layer(SKELETON),
            "swap_layer": page.swap_layer,
            "fill": lambda: page.request_tool(MASK, "fill"),
            "select_tool": lambda: page.request_tool(SKELETON, "select"),
            "polyline": lambda: page.request_tool(SKELETON, "polyline"),
            "connect": lambda: page.request_tool(SKELETON, "connect"),
            "eraser": lambda: page.request_tool(page.active_layer_id, "eraser"),
            "size_smaller": lambda: page.layers[page.active_layer_id].step_size(-1),
            "size_larger": lambda: page.layers[page.active_layer_id].step_size(1),
            "toggle_layers": page.toggle_layers,
            "toggle_reference": page.toggle_reference,
            "fit": lambda: view.fit_to_view(),
            "zoom_100": lambda: view.set_zoom(1.0),
            "zoom_in": lambda: view.zoom_step(1),
            "zoom_out": lambda: view.zoom_step(-1),
            "pixel_grid": lambda: _toggle_pixel_grid(page),
        },
    )
    for action in _NO_REPEAT:
        for shortcut in installed[action]:
            shortcut.setAutoRepeat(False)
    made = [shortcut for group in installed.values() for shortcut in group]
    for key, delta in _PAGING:  # the page binds them itself so it can save first
        shortcut = QShortcut(QKeySequence(key), page)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda d=delta: page.navigate(d))
        made.append(shortcut)
    return made


def _toggle_pixel_grid(page: CorrectEditor) -> None:
    """Flip the canvas pixel grid and say so (it only shows at 800 % and above)."""
    on = page.graphics_view.toggle_pixel_grid()
    page.notice.emit(PIXEL_GRID_ON if on else PIXEL_GRID_OFF, "info")

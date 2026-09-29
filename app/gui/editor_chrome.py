"""The floating overlays every canvas editor shares: :class:`EditorChrome` (contract C10).

A canvas editor (Correct, Trace GT) is a widget whose canvas, an
:class:`~app.gui.editor_canvas.EditorCanvasView`, fills it; the controls float over the
canvas. The chrome builds the parts every editor has and wires them once:

* a tool rail (left) and a dock (bottom), both empty: the editor adds its own tools and
  actions, and places :attr:`EditorChrome.pan_button` and :attr:`EditorChrome.enhance_button`
  on the rail where it wants them;
* the Enhance popover (top right) holding the editor's ``NormalizationControls``, opened by
  the Enhance button, which follows it; closing it gives the keys back to the canvas;
* the status chip (top left, file name, save state and a hint line) and the zoom cluster
  (bottom right), bound to the canvas;
* one :class:`~app.gui.widgets.overlay_layout.OverlayLayout` over the editor widget that
  places them all without collisions, and whose free area is what "fit" keeps the image in.

Clicks on the rail, the dock and the popover's buttons never take the keyboard focus from the
canvas (its hold-keys, Space and B, need it). Create the chrome after the canvas was added to
the editor's layout, so the overlays stack above it.
"""

from __future__ import annotations

from functools import partial

from PyQt6.QtWidgets import QAbstractButton, QWidget

from .editor_canvas import EditorCanvasView
from .image_normalization_interface import NormalizationControls
from .widgets import (
    EnhancePopover,
    FloatingDock,
    IconButton,
    OverlayLayout,
    StatusChip,
    ToolRail,
    ZoomCluster,
    keep_canvas_focus,
)

__all__ = ["EditorChrome"]


def _focus_canvas_when_closed(view: EditorCanvasView, shown: bool) -> None:
    """Closing the Enhance popover gives the keys back to the canvas."""
    if not shown:
        view.setFocus()


class EditorChrome:
    """The floating overlays every canvas editor shares, placed by one OverlayLayout
    over the editor widget: tool rail (empty), dock (empty), Enhance popover, status
    chip, zoom cluster, plus the Pan and Enhance rail buttons."""

    PAN_TOOLTIP = "Pan (hold Space, or middle-drag)"
    ENHANCE_TOOLTIP = "Image enhancement"

    def __init__(self, host: QWidget, view: EditorCanvasView, norm: NormalizationControls) -> None:
        if view.parentWidget() is host:
            view.lower()  # the canvas stays under every overlay

        self.rail = ToolRail(host)
        self.dock = FloatingDock(host)
        self.popover = EnhancePopover(host)
        self.popover.set_content(norm)
        self.chip = StatusChip(host)
        self.zoom = ZoomCluster(host)

        self.pan_button = IconButton("hand", self.PAN_TOOLTIP, checkable=True)
        self.enhance_button = IconButton("contrast", self.ENHANCE_TOOLTIP, checkable=True)
        keep_canvas_focus(
            self.rail,
            self.dock,
            self.pan_button,
            self.enhance_button,
            *self.popover.findChildren(QAbstractButton),
        )

        self.layout = OverlayLayout(host)
        self.layout.register(
            rail=self.rail,
            dock=self.dock,
            popover=self.popover,
            zoom_cluster=self.zoom,
            top_chip=self.chip,
        )
        # Every fit (button, Ctrl+0, load, resize re-fit) keeps the image clear of them.
        view.set_fit_insets_provider(self.layout.insets)
        self.zoom.bind(view)

        # Qt-object slots and a partial only: the wiring must not die with this object (PyQt
        # holds a bound method of a plain object weakly), editors may keep just the parts.
        self.pan_button.toggled.connect(view.set_mode)
        self.enhance_button.clicked.connect(self.popover.toggle)
        self.popover.toggled.connect(self.enhance_button.setChecked)
        self.popover.toggled.connect(partial(_focus_canvas_when_closed, view))

    def register(self, **roles: QWidget | None) -> None:
        """Hand more overlays to the layout by role (``side_panel``, ``prompt``); None releases."""
        self.layout.register(**roles)

    def relayout(self) -> None:
        """Place the overlays now (the layout also follows resizes and show / hide by itself)."""
        self.layout.relayout()

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

Tab reaches the overlays' controls. Qt puts a widget in the focus chain when it is parented,
so their order is the build order until :meth:`EditorChrome.order_tab_focus` makes it the
screen's (WCAG 2.4.3).
"""

from __future__ import annotations

from functools import partial

from PyQt6.QtCore import Qt
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

# Tab after the canvas: the prompt (top centre, about what is being drawn), the rail top to
# bottom and the Enhance popover its last button opens, the dock left to right, the zoom
# cluster (bottom right), then the side panel (right edge).
_TAB_ORDER = ("prompt", "rail", "popover", "dock", "zoom_cluster", "side_panel")


def _focus_canvas_when_closed(view: EditorCanvasView, shown: bool) -> None:
    """Closing the Enhance popover gives the keys back to the canvas."""
    if not shown:
        view.setFocus()


def _grouped(widget: QWidget) -> bool:
    """A button of a button group (the rail's tools): Tab stops at one of them, and the
    group decides which by giving the others no focus policy."""
    return isinstance(widget, QAbstractButton) and widget.group() is not None


def _tab_stops(root: QWidget) -> list[QWidget]:
    """``root`` and its descendants that Tab can reach, and every grouped button, in the
    order they were added: the order on screen in the rail, the dock and the panels (their
    layouts add in order)."""
    return [
        widget
        for widget in (root, *root.findChildren(QWidget))
        # A stale wrapper can be typed as a widget for another object (see
        # widgets.focus.keep_canvas_focus): touch widgets only.
        if widget.isWidgetType()
        and (widget.focusPolicy() & Qt.FocusPolicy.TabFocus or _grouped(widget))
    ]


class EditorChrome:
    """The floating overlays every canvas editor shares, placed by one OverlayLayout
    over the editor widget: tool rail (empty), dock (empty), Enhance popover, status
    chip, zoom cluster, plus the Pan and Enhance rail buttons."""

    PAN_TOOLTIP = "Pan (hold Space, or middle-drag)"
    ENHANCE_TOOLTIP = "Image enhancement"

    def __init__(self, host: QWidget, view: EditorCanvasView, norm: NormalizationControls) -> None:
        if view.parentWidget() is host:
            view.lower()  # the canvas stays under every overlay
        self._view = view
        self._overlays: dict[str, QWidget] = {}  # by OverlayLayout role, for the Tab order

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
        self.register(
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
        for role, widget in roles.items():
            if widget is None:
                self._overlays.pop(role, None)
            else:
                self._overlays[role] = widget

    def order_tab_focus(self) -> None:
        """Make Tab from the canvas follow the screen: the prompt, the rail and the popover
        it opens, the dock, the zoom cluster, then the side panel (``_TAB_ORDER``). Shift+Tab
        walks back. Call it once the editor has added its controls, and again after adding
        one (a control added later joins the end of the chain). Focus policies stay as they
        are: a click still leaves the keys on the canvas."""
        chain = [self._view]
        for role in _TAB_ORDER:
            overlay = self._overlays.get(role)
            if overlay is not None:
                chain += _tab_stops(overlay)
        # setTabOrder never moves a widget without a focus policy: the grouped buttons Tab
        # does not stop at get one meanwhile, so each keeps its place for when it is the stop.
        idle = [widget for widget in chain if widget.focusPolicy() == Qt.FocusPolicy.NoFocus]
        for widget in idle:
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        try:
            for first, second in zip(chain, chain[1:], strict=False):
                QWidget.setTabOrder(first, second)
        finally:
            for widget in idle:
                widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def relayout(self) -> None:
        """Place the overlays now (the layout also follows resizes and show / hide by itself)."""
        self.layout.relayout()

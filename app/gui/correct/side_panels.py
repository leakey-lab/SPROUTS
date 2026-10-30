"""The Correct page's side card: :class:`SidePanels` (WG-T5, Design 3.2 and 3.5).

One floating card at the right edge (the ``EditorChrome``'s ``side_panel`` role): the Layers
panel (``LayerPanel``, a row per layer) above the class panel (``app.gui.class_panel.
ClassPanel``: the classes, their "checked" boxes and the gear of the class editor), which
shows only while the class labels are the edited layer (:meth:`SidePanels.set_active_layer`).
Both keep their parts and focus rules; inside the card they lose their own frame and shadow
(Qt draws no nested graphics effect, and a child's shadow would be clipped at the card's
edge). The card sizes itself by summing its shown parts (``preferred_height`` /
``fit_height``, the OverlayLayout hooks) and hands its layout to them, so either can ask
for a new place (``reposition``) when its rows change.

It also keeps the class panel in step with the class labels (:meth:`SidePanels.bind_classes`):
a row click, a tick and the gear go to the layer and the page; the layer's
``on_classes_changed`` (an open, a save, Discard, a new registry, a tick, the active class,
every committed plane change, never a mouse move) puts its registry, active class and ticks
on the panel, each only when it changed (a stroke costs two small set compares). A click on
the panel hands the keys back to the canvas; a box ticked from the keyboard keeps them for
the next box; a box that locks under the focus (its class got pixels) hands them to the
canvas first, so a Space never ticks the box after it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QFrame, QWidget

from app.gui.correct.layer import CLASSES
from app.gui.widgets import tokens
from app.gui.widgets.overlays import _FloatingPanel

if TYPE_CHECKING:  # pragma: no cover
    from app.gt import Registry
    from app.gui.class_panel import ClassPanel
    from app.gui.correct.classes_layer import ClassesLayer
    from app.gui.correct.layer_panel import LayerPanel

__all__ = ["SidePanels"]

_RULE_PX = 1  # the line between the layers and the classes


def _inline(panel: QWidget) -> None:
    """``panel`` drawn as a part of the card: no frame, fill or shadow of its own."""
    panel.setGraphicsEffect(None)
    panel.setProperty("sproutsPanel", False)  # its floating-panel style no longer matches
    panel.style().unpolish(panel)
    panel.style().polish(panel)


class SidePanels(_FloatingPanel):
    """The side card of the Correct page: ``layer_panel`` above ``class_panel`` (shown while
    the class labels are edited), a line between them."""

    def __init__(
        self, layer_panel: LayerPanel, class_panel: ClassPanel, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent, horizontal=False)
        self.setObjectName("sidePanels")
        self._box.setContentsMargins(0, 0, 0, 0)  # the panels keep their own margins
        self._box.setSpacing(0)
        self.layer_panel, self.class_panel = layer_panel, class_panel
        self._rule = QFrame(self)
        self._rule.setFrameShape(QFrame.Shape.HLine)
        self._rule.setFixedHeight(_RULE_PX)
        self._rule.setStyleSheet(f"background-color: {tokens.BORDER}; border: none;")
        for part in (layer_panel, self._rule, class_panel):
            self._box.addWidget(part)
        for panel in (layer_panel, class_panel):
            _inline(panel)
        self._classes_shown = False
        self._rule.hide()
        class_panel.hide()
        self._layer: ClassesLayer | None = None
        self._canvas: QWidget | None = None
        self._registry: Registry | None = None  # what the class panel shows
        self._reviewed: tuple | None = None  # (hand ticks, classes with pixels) it shows

    # ------------------------------------------------------------------ the card
    def set_active_layer(self, layer_id: str) -> None:
        """``layer_id``'s row is the edited one's; the class panel shows while it is the
        class labels (a box or the gear holding the keys hands them to the canvas first)."""
        self.layer_panel.set_active(layer_id)
        shown = layer_id == CLASSES
        if shown == self._classes_shown:
            return
        if not shown and self._canvas is not None:
            if self.class_panel.isAncestorOf(QApplication.focusWidget()):
                self._canvas.setFocus()
        self._classes_shown = shown
        self._rule.setVisible(shown)
        self.class_panel.setVisible(shown)
        self.reposition()

    def set_overlay_layout(self, layout) -> None:
        """Placed by ``layout``: a panel of the card asks it for a new place as well."""
        super().set_overlay_layout(layout)
        self.layer_panel.set_overlay_layout(layout)
        self.class_panel.set_overlay_layout(layout)

    def preferred_height(self, host_height: int) -> int:
        """The rows of the layers, then those of the classes when shown (capped at 60 % of
        ``host_height``: they scroll then); OverlayLayout hook."""
        height = self.layer_panel.preferred_height(host_height)
        if self._classes_shown:
            height += _RULE_PX + self.class_panel.preferred_height(host_height)
        return height

    def fit_height(self, height: int) -> None:
        """Take ``height`` px (OverlayLayout hook): the layers first, the classes the rest
        (their rows scroll; in a canvas too low for the class panel's header, its bottom is
        cut), never more than the shown parts need."""
        layers = self.layer_panel
        layers.fit_height(height)
        total = layers.height()
        if self._classes_shown:
            self.class_panel.fit_height(max(0, height - total - _RULE_PX))
            total += _RULE_PX + self.class_panel.height()
        self.setFixedHeight(min(total, height))
        self._box.invalidate()  # the panels' own fits resized them: the card's width again
        self._box.activate()

    # ------------------------------------------------------------------ the classes
    def bind_classes(
        self, layer: ClassesLayer, canvas: QWidget, on_edit: Callable[[], object]
    ) -> None:
        """Keep the class panel showing ``layer`` (Design 3.5): a row click picks the class,
        a tick goes to the layer, the gear calls ``on_edit`` (the page's Classes…);
        ``canvas`` takes the keys back after a click."""
        self._layer, self._canvas = layer, canvas
        panel = self.class_panel
        panel.active_changed.connect(self._pick)
        panel.reviewed_changed.connect(self._tick)
        panel.edit_requested.connect(on_edit)
        layer.on_classes_changed = self.sync_classes
        self.sync_classes()

    def pick_class(self, number: int) -> None:
        """The class key ``number`` (1-9: the n-th class, 0: Ignore) picks its class."""
        key = self.class_panel.key_for_shortcut(number)
        if key is not None and self._layer is not None:
            self._layer.set_active_class(key)

    def sync_classes(self) -> None:
        """Show the layer's registry, active class and ticks on the class panel (only what
        changed: it runs once per committed stroke)."""
        layer, panel = self._layer, self.class_panel
        registry = layer.registry()
        if registry != self._registry:
            self._registry, self._reviewed = registry, None
            panel.set_registry(registry)
            self.reposition()
            QTimer.singleShot(0, self.reposition)  # the new rows count once they are shown
        panel.set_active(layer.active_class())  # it does nothing when the class is shown
        document = layer.document
        reviewed = (document.ticked(), frozenset(document.present_keys()))
        if reviewed == self._reviewed:
            return
        self._reviewed = reviewed
        focused = panel.focused_key()
        if focused is not None and focused in reviewed[1]:
            self._canvas.setFocus()  # the box locks under the focus: Space must not move on
        panel.set_reviewed(set(reviewed[0]), set(reviewed[1]))

    def _pick(self, key: str) -> None:
        """A row was clicked: paint with its class; the keys go back to the canvas."""
        self._layer.set_active_class(key)
        self.sync_classes()  # a class the layer refused: the panel shows the active one
        self._canvas.setFocus()

    def _tick(self, key: str, on: bool) -> None:
        """A "checked" box changed: the layer ticks it, or refuses and the box goes back. A
        click hands the keys back to the canvas; Space on a focused box keeps them there."""
        self._reviewed = None  # the box shows the click: show the layer's answer either way
        self._layer.set_ticked(key, on)
        if self.class_panel.focused_key() != key:
            self._canvas.setFocus()

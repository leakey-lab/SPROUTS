"""What the Correct page's status chip shows (:func:`chip_content`) and how a change reaches
it (:func:`show_on_chip`, :class:`ChipView`): the file name, the worst state of the layers
(error > unsaved > saved > none; a layer's own lock, ``LayerStatus.local``, counts only while
that layer is edited), the image's place in the library and the hint "<Layer> · <tool
hint>" with a dot in the edited layer's colour. The chip is updated where something changed
only.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from app.gui.correct.layer import title  # re-exported: the page imports it from here

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.layer import EditLayer, LayerStatus
    from app.gui.widgets.overlays import StatusChip

__all__ = ["ChipView", "chip_content", "show_on_chip", "title"]

_RANK = {"none": 0, "clean": 1, "dirty": 2, "error": 3}  # the chip shows the worst layer state


def chip_content(
    path: str,
    statuses: Mapping[str, LayerStatus],
    layer: EditLayer,
    position: tuple[int, int] | None,
) -> tuple:
    """``(name, (state, label, tooltip), (hint, hint tooltip, dot), position)`` for the
    image at ``path`` ("" when none is open) with its layers' ``statuses``; ``layer`` is
    the edited one. A failed save shows that layer's label ("Not saved") and reason. A
    ``local`` status (the class labels' "Read-only") counts only while its layer is edited,
    so it never hides another layer's unsaved edits."""
    state, label, tip = "none", None, ""
    ranked = [s for i, s in statuses.items() if not s.local or i == layer.layer_id]
    if path and ranked:
        state = max((status.state for status in ranked), key=_RANK.__getitem__)
    if state == "error":
        failed = next(status for status in ranked if status.state == "error")
        label, tip = failed.label, failed.tooltip
    if path:
        hint, hint_tip = layer.tool_hint()
        hint = f"{title(layer.noun)} · {hint}"
    else:
        hint, hint_tip = "Select an image in the Library", ""
    name = os.path.basename(path) or "No image"
    return (name, (state, label, tip), (hint, hint_tip, layer.color), position)


def show_on_chip(chip: StatusChip, shown: tuple, old: tuple) -> None:
    """Put ``shown`` (a :func:`chip_content`) on ``chip`` where it differs from ``old``."""
    old = old or (None, None, None, None)
    if shown[0] != old[0]:
        chip.set_text(shown[0])
    if shown[1] != old[1]:
        state, label, tip = shown[1]
        chip.set_state(state, label, tooltip=tip)
    if shown[2] != old[2]:
        hint, hint_tip, dot = shown[2]
        chip.set_hint(hint, hint_tip, dot=dot)
    if shown[3] != old[3]:
        chip.set_position(*(shown[3] or (None,)))


class ChipView:
    """The page's status chip: :func:`chip_content` put on it where it changed; the open
    image's library position (``position(path)``) is asked once per image."""

    def __init__(self, chip: StatusChip, position: Callable[[str], tuple[int, int] | None]) -> None:
        self._chip, self._position = chip, position
        self._shown: tuple = ()  # what the chip shows
        self._placed: tuple = (None, None)  # (path, its library position)

    def forget_position(self) -> None:
        """The library changed: the position is asked again."""
        self._placed = (None, None)

    def show(self, path: str, statuses: Mapping[str, LayerStatus], layer: EditLayer) -> None:
        """Show the image at ``path`` ("" when none) with its layers' ``statuses``; ``layer``
        is the edited one."""
        if self._placed[0] != path:
            self._placed = (path, self._position(path))
        shown = chip_content(path, statuses, layer, self._placed[1])
        show_on_chip(self._chip, shown, self._shown)
        self._shown = shown

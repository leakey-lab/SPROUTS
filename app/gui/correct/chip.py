"""What the Correct page's status chip shows (:func:`chip_content`) and how a change reaches
it (:func:`show_on_chip`): the file name, the worst state of the two layers (error > unsaved >
saved > none), the image's place in the library and the hint "<Layer> · <tool hint>" with a
dot in the edited layer's colour. The chip is updated where something changed only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.layer import EditLayer, LayerStatus
    from app.gui.widgets.overlays import StatusChip

__all__ = ["chip_content", "show_on_chip", "title"]

_RANK = {"none": 0, "clean": 1, "dirty": 2, "error": 3}  # the chip shows the worst layer state


def title(noun: str) -> str:
    """``"mask"`` -> ``"Mask"``."""
    return f"{noun[:1].upper()}{noun[1:]}"


def chip_content(
    path: str,
    statuses: Mapping[str, LayerStatus],
    layer: EditLayer,
    position: tuple[int, int] | None,
) -> tuple:
    """``(name, (state, label, tooltip), (hint, hint tooltip, dot), position)`` for the
    image at ``path`` ("" when none is open) with its layers' ``statuses``; ``layer`` is
    the edited one. A failed save shows that layer's label ("Not saved") and reason."""
    state, label, tip = "none", None, ""
    if path:
        state = max((status.state for status in statuses.values()), key=_RANK.__getitem__)
    if state == "error":
        failed = next(status for status in statuses.values() if status.state == "error")
        label, tip = failed.label, failed.tooltip
    hint, hint_tip = layer.tool_hint()
    hint = f"{title(layer.noun)} · {hint}"
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

"""The file stamps of the image open in the Correct workspace (Qt-free).

:class:`ImageStamps` remembers what the open image's documents were read from: the image
file's stamp at open, and each layer's ``file_stamps()`` as taken at open, reload, save and
Discard (or again after a layer wrote a file of its own outside a save, ``retake``). The
session asks it whether a file changed on disk since (``files_changed``, ``image_changed``,
``layer_changed``) and whether the documents still hold exactly what is on disk (``held``,
the hand-back to the read-ahead). The save guard keeps its own write stamps
(``app.gui.correct.save_gate``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.layer import EditLayer

__all__ = ["ImageStamps", "file_stamp"]


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a readable file, ``(path, None)`` otherwise."""
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None)
    return (path, stat.st_mtime_ns, stat.st_size)


class ImageStamps:
    """The open image's stamp and its layers' file stamps as last taken (none: empty)."""

    def __init__(self) -> None:
        self._layers: dict[str, tuple] = {}
        self._image: tuple = ()

    def image_opened(self, image_path: str) -> None:
        """``image_path`` was opened: take its stamp (the layers' follow as they open)."""
        self._image = file_stamp(image_path)

    def clear(self) -> None:
        """Nothing is open: forget every stamp."""
        self._layers = {}
        self._image = ()

    def take(self, layer_id: str, layer: EditLayer) -> None:
        """``layer``'s files hold its saved state: take its file stamps."""
        self._layers[layer_id] = layer.file_stamps()

    def retake(self, layer_id: str, layer: EditLayer) -> None:
        """``layer`` wrote a file of its own outside a save: take its stamps again, if it
        has any taken (so that write is no change on disk)."""
        if layer_id in self._layers:
            self._layers[layer_id] = layer.file_stamps()

    def has(self, layer_id: str) -> bool:
        """True when stamps were taken for ``layer_id``."""
        return layer_id in self._layers

    def layer_changed(self, layer_id: str, layer: EditLayer) -> bool:
        """True when ``layer``'s file stamps differ from those taken."""
        return layer.file_stamps() != self._layers.get(layer_id)

    def files_changed(self, layers: Mapping[str, EditLayer]) -> bool:
        """True when any layer's file stamps differ from those taken."""
        return any(self.layer_changed(layer_id, layer) for layer_id, layer in layers.items())

    def image_changed(self, image_path: str) -> bool:
        """True when the image file's stamp differs from the one at open."""
        return file_stamp(image_path) != self._image

    def held(self, layers: Mapping[str, EditLayer]) -> dict[str, tuple] | None:
        """Each layer's file stamps now, when they are all as taken; None otherwise."""
        stamps = {layer_id: layer.file_stamps() for layer_id, layer in layers.items()}
        return stamps if stamps == self._layers else None

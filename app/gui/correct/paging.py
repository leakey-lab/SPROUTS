"""Where the open image sits in the library, and its neighbours read ahead (Correct page).

MainWindow knows the library order; the page asks it through two providers it is given
(``CorrectEditor.set_position_provider`` / ``set_neighbour_provider``):

- ``position(path) -> (index, count)``: the image's 1-based place in the library, for the
  status chip ("Image 12 / 40"); None when it is not in the library;
- ``neighbours(path) -> [path, ...]``: the images PgDn / PgUp would open, nearest first.

:class:`Neighbours` reads the first :attr:`Neighbours.COUNT` neighbours on a worker thread
(:mod:`app.gui.correct.read_ahead`) once the open image has been shown: a short timer that
every open restarts, so paging held down reads nothing until it stops. The next open takes
a finished read instead of decoding (never waiting for one still running). Each layer that
has the optional ``read_ahead`` hook (see :mod:`app.gui.correct.layer`) is asked for a
reader here, on the GUI thread; the worker runs it. A provider or a hook that raises is
logged and read-ahead goes on without it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from functools import partial

from PyQt6.QtCore import QObject, QTimer

from app.gui.correct.read_ahead import AheadImage, ReadAhead, Reader, read_image

logger = logging.getLogger(__name__)

__all__ = ["NeighbourProvider", "Neighbours", "PositionProvider"]

NeighbourProvider = Callable[[str], Sequence[str]]
PositionProvider = Callable[[str], "tuple[int, int] | None"]


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class Neighbours(QObject):
    """The library position of the open image and its neighbours read ahead."""

    COUNT = 2  # the next image and the previous one (as the provider orders them)
    DELAY_MS = 60  # after an open: the new image paints first, then its neighbours are read

    def __init__(self, parent: QObject, layers: Mapping[str, object]) -> None:
        super().__init__(parent)
        self._layers = layers
        self._neighbours: NeighbourProvider | None = None
        self._position: PositionProvider | None = None
        self._ahead: ReadAhead | None = None  # made with the first neighbour provider
        self._path = ""
        self._base: str | None = None
        self._timer = QTimer(self, singleShot=True, interval=self.DELAY_MS)
        self._timer.timeout.connect(self._read_neighbours)

    # ------------------------------------------------------------------ providers
    def set_neighbour_provider(self, provider: NeighbourProvider | None) -> None:
        """Read ahead the images ``provider(path)`` names (None: read nothing ahead)."""
        self._neighbours = provider
        if provider is None and self._ahead is not None:
            self._ahead.close()
            self._ahead = None
        elif provider is not None and self._ahead is None:
            self._ahead = ReadAhead()
            self.destroyed.connect(self._ahead.close)  # the page goes: its worker ends too

    def set_position_provider(self, provider: PositionProvider | None) -> None:
        """Ask ``provider(path)`` for the image's ``(index, count)`` (None: no position).

        MainWindow hands it again when a library search changes the order, so the open
        image's neighbours are read again too (a read that is still current is kept); while
        the page is hidden that waits for the image to be shown again (``opened``)."""
        self._position = provider
        shown = getattr(self.parent(), "isVisible", lambda: True)()  # the page: while shown
        if self._path and self._ahead is not None and shown:
            self._timer.start()

    def position(self, path: str) -> tuple[int, int] | None:
        """``(index, count)`` of ``path`` in the library (1-based), or None."""
        provider = self._position
        if provider is None or not path:
            return None
        try:
            index, count = provider(path) or (0, 0)
        except Exception:  # noqa: BLE001 - the chip then shows no position
            logger.exception("The library position of %s is unknown", path)
            return None
        if isinstance(index, int) and isinstance(count, int) and 1 <= index <= count:
            return index, count
        return None

    # ------------------------------------------------------------------ read-ahead
    def take(self, path: str) -> AheadImage | None:
        """A finished, current read of ``path`` (handed over once), else None."""
        return None if self._ahead is None else self._ahead.take(path)

    def put_back(self, image: AheadImage | None) -> None:
        """A read taken for an open that did not happen (the user stayed): keep it."""
        if image is not None and self._ahead is not None:
            self._ahead.put(image)

    def opened(self, path: str, images_base_folder: str | None) -> None:
        """``path`` was opened: read its neighbours once it has been shown."""
        self._path, self._base = path, images_base_folder
        if self._ahead is not None:
            self._timer.start()

    def forget(self, path: str | None = None) -> None:
        """Drop what was read of ``path`` (its files changed), or of every image (None)."""
        if self._ahead is None:
            return
        if path is None:
            self._ahead.clear()
        else:
            self._ahead.invalidate(path)

    def closed(self) -> None:
        """Nothing is open any more: forget the reads, read nothing more."""
        self._timer.stop()
        self._path, self._base = "", None
        self.forget()

    def pending(self) -> list[str]:
        """What is kept or being read (normalised paths, oldest first); a test hook."""
        return [] if self._ahead is None else self._ahead.pending()

    # ------------------------------------------------------------------ internals
    def _read_neighbours(self) -> None:
        ahead, provider, current = self._ahead, self._neighbours, self._path
        if ahead is None or provider is None or not current:
            return
        try:
            paths = [str(path) for path in provider(current) or () if path]
        except Exception:  # noqa: BLE001 - paging still works, it just reads by itself
            logger.exception("The neighbours of %s are unknown", current)
            return
        paths = [path for path in paths if not _same_file(path, current)][: self.COUNT]
        ahead.keep_only(paths)
        for path in paths:
            ahead.request(path, partial(read_image, path, self._readers(path)))

    def _readers(self, path: str) -> dict[str, Reader]:
        """Each layer's reader for ``path`` (its optional ``read_ahead`` hook)."""
        readers: dict[str, Reader] = {}
        for layer_id, layer in self._layers.items():
            hook = getattr(layer, "read_ahead", None)
            if hook is None:
                continue
            try:
                reader = hook(path, self._base)
            except Exception:  # noqa: BLE001 - that layer opens by itself
                logger.exception("The %s of %s cannot be read ahead", layer_id, path)
                continue
            if reader is not None:
                readers[layer_id] = reader
        return readers

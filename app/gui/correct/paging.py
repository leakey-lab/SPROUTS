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

One decode per photo:

- the image the page leaves is kept as a finished read (:mod:`app.gui.correct.read_back`),
  so PgUp after PgDn reads nothing again;
- with a photo source (``set_photo_source``: the Library display's decodes, handed over),
  opening an image and reading its neighbours start from the photos the Library decoded,
  and the Library forgets them: its cache empties while this page is shown;
- :meth:`Neighbours.cached_photo` shares this page's photos (the open one, the finished
  reads) with the Library display when the user goes back to it.

:class:`PagingApi` is the page's side of it for MainWindow (a mixin of ``CorrectEditor``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer

from app.gui.correct.read_ahead import AheadImage, Photo, ReadAhead, Reader, file_stamp, read_image
from app.gui.correct.read_back import closed_image

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from app.gui.correct.session import CorrectSession

logger = logging.getLogger(__name__)

__all__ = ["NeighbourProvider", "Neighbours", "PagingApi", "PhotoSource", "PositionProvider"]

NeighbourProvider = Callable[[str], Sequence[str]]
PositionProvider = Callable[[str], "tuple[int, int] | None"]
#: ``source(path)``: a decoded photo of ``path`` with its stamp, or None (never decodes).
PhotoSource = Callable[[str], "Photo | None"]


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
        self._photos: PhotoSource | None = None  # the Library's decodes, handed over
        self._ahead: ReadAhead | None = None  # made with the first neighbour provider
        self._path = ""
        self._base: str | None = None
        self._open: AheadImage | None = None  # the open image's photo (no layer payloads)
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
        if self._path and self._ahead is not None and self._shown():
            self._timer.start()

    def set_photo_source(self, source: PhotoSource | None) -> None:
        """Open and read ahead from the photos ``source(path)`` hands over (None: decode)."""
        self._photos = source

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

    def photo_for(self, path: str, decode: Callable[[str], np.ndarray | None]) -> AheadImage | None:
        """What opening ``path`` starts from: its finished read (photo and layers), else the
        photo the Library decoded (the layers then open by themselves), else the photo
        ``decode(path)`` gives here; None when it cannot be read. The Library forgets its
        decode of ``path`` either way: this page holds the photo now."""
        read, shared = self.take(path), self._shared(path)
        if read is not None:
            return read
        if shared is not None:
            return AheadImage(path, *shared)
        stamp = file_stamp(path)  # before the decode: a change meanwhile makes it stale
        photo = decode(path)
        return None if photo is None else AheadImage(path, stamp, photo)

    def put_back(self, image: AheadImage | None) -> None:
        """A read taken for an open that did not happen (the user stayed): keep it, unless
        it lacks a layer's payload (that layer would then be read on the GUI thread)."""
        if image is not None and self._ahead is not None and self._complete(image):
            self._ahead.put(image)

    def leaving(self, session: CorrectSession) -> None:
        """The open image is about to be replaced: keep it as a finished read (the image
        PgUp / PgDn goes back to) while its photo and every layer are as on disk."""
        opened, self._open = self._open, None
        if self._ahead is None or opened is None:
            return
        if session.image_path and _same_file(session.image_path, opened.image_path):
            image = closed_image(opened, self._layers, session.saved_stamps())
            if image is not None:
                self._ahead.put(image)

    def opened(
        self, path: str, images_base_folder: str | None, image: AheadImage | None = None
    ) -> None:
        """``path`` was opened from ``image`` (its photo is kept for the hand-back and the
        Library; None: the open image was shown again): read its neighbours once shown."""
        self._path, self._base = path, images_base_folder
        if image is not None:  # its layer payloads are the documents' now: not kept
            self._open = AheadImage(path, image.stamp, image.photo)
        if self._ahead is not None:
            self._timer.start()

    def cached_photo(self, path: str) -> Photo | None:
        """This page's decoded photo of ``path`` with its stamp (the open image's, or a
        finished read's, kept), for the Library display to share (read-only); None
        otherwise. Never waits, never decodes."""
        image = self._open
        if image is None or not _same_file(path, image.image_path):
            image = None if self._ahead is None else self._ahead.peek(path)
        return None if image is None else (image.stamp, image.photo)

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
        self._path, self._base, self._open = "", None, None
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
        shown = self._shown()  # while the Library is on screen its photos stay there
        if shown:
            self._shared(current)  # the Library's copy of the open photo goes
        ahead.keep_only(paths)
        # Every job is planned before the first is queued: a job that starts while this
        # thread still plans the next one competes with it for the GIL (1 in 20 first plans
        # took 9-17 ms instead of 1.5).
        photos = [self._shared(path) if shown else None for path in paths]
        jobs = [
            (path, partial(read_image, path, self._readers(path), photo))
            for path, photo in zip(paths, photos, strict=True)
        ]
        for path, job in jobs:
            ahead.request(path, job)

    def _shared(self, path: str) -> Photo | None:
        """The Library's decode of ``path``, handed over (it forgets it) while the file is
        still the one decoded; None when there is none, or no photo source."""
        source = self._photos
        if source is None:
            return None
        try:
            found = source(path)
        except Exception:  # noqa: BLE001 - the page decodes the photo itself
            logger.exception("The Library's photo of %s is unavailable", path)
            return None
        stamp = file_stamp(path)
        if found is None or stamp[1:] == (None,) or tuple(found[0][1:]) != stamp[1:]:
            return None
        return stamp, found[1]

    def _complete(self, image: AheadImage) -> bool:
        """True when ``image`` holds the payload of every layer that reads ahead."""
        wanted = [i for i, layer in self._layers.items() if getattr(layer, "read_ahead", None)]
        return all(layer_id in image.layers for layer_id in wanted)

    def _shown(self) -> bool:
        return getattr(self.parent(), "isVisible", lambda: True)()  # the page: while shown

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


class PagingApi:
    """The page's read-ahead API for MainWindow: a mixin of ``CorrectEditor``, whose
    ``paging`` is its :class:`Neighbours`."""

    paging: Neighbours

    def set_neighbour_provider(self, provider: NeighbourProvider | None) -> None:
        """``provider(path)``: the images PgDn / PgUp would open next, nearest first; the
        page reads them ahead on a worker thread (None: nothing is read ahead)."""
        self.paging.set_neighbour_provider(provider)

    def set_photo_source(self, source: PhotoSource | None) -> None:
        """``source(path)``: the Library display's decoded photo of ``path`` with its stamp,
        handed over (the Library forgets it); the page opens and reads ahead from it instead
        of decoding the photo again (None: it decodes)."""
        self.paging.set_photo_source(source)

    def cached_photo(self, path: str) -> Photo | None:
        """The page's decoded photo of ``path`` with its stamp (the open image's, or one read
        ahead), for the Library display to share (read-only); None otherwise."""
        return self.paging.cached_photo(path)

    def discard_read_ahead(self) -> None:
        """Files changed on disk (a Generate run): forget every image read ahead."""
        self.paging.forget()

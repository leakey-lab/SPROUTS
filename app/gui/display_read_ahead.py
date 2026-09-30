"""Decode the Library's next photos ahead, off the GUI thread (Qt-free).

The Library display decodes the selected photo on the GUI thread: about 1 ms at
640 x 480 and 6 ms of a 10 ms step at 2048 x 1536 (JPEG). Once a photo is shown,
:class:`DisplayReadAhead` decodes its neighbours (the images PgDn / PgUp / the arrow keys
open next) on one worker thread, and keeps the photo just shown, so stepping on or back
takes a finished decode instead of decoding again.

Rules (the same as the Correct page's read-ahead):

- a decode is used only while the file on disk is the one that was read: its
  ``(path, mtime, size)`` stamp is taken before the read and compared at :meth:`get`;
- a few photos are kept, bounded by count and by bytes, oldest dropped first;
- a read still queued when its photo is asked for is dropped (the caller decodes it
  itself); one already running is waited for, since it is partly done;
- decoded arrays are made read-only: they are shared with the photo item, and with the
  Correct page, which takes the photos it opens or reads ahead (:meth:`DisplayReadAhead.take`
  hands one over and forgets it, never waiting), so this cache empties while it is shown.

Only the GUI thread calls the object; the worker runs the decode jobs and touches
nothing else.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["DisplayReadAhead", "file_stamp"]

MAX_IMAGES = 3  # the photo shown, the next and the previous one
MAX_BYTES = 192 * 2**20  # about 20 photos of 2048 x 1536, two of 6000 x 4000

Decode = Callable[[str], "np.ndarray | None"]


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a readable file, ``(path, None)`` otherwise."""
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None)
    return (path, stat.st_mtime_ns, stat.st_size)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _readable(stamp: tuple) -> bool:
    return stamp[1:] != (None,)


def _read(decode: Decode, path: str) -> tuple[tuple, np.ndarray | None]:
    """The worker's job: ``(stamp, photo)``, stamped before the read (never raises)."""
    stamp = file_stamp(path)
    if not _readable(stamp):
        return stamp, None
    try:
        image = decode(path)
    except Exception:  # noqa: BLE001 - the display then decodes (and reports) it itself
        logger.exception("Could not read %s ahead", path)
        return stamp, None
    if image is not None:
        image.flags.writeable = False
    return stamp, image


@dataclass
class _Entry:
    stamp: tuple | None = None
    image: np.ndarray | None = None
    future: Future | None = None

    @property
    def nbytes(self) -> int:
        return 0 if self.image is None else int(self.image.nbytes)


class DisplayReadAhead:
    """Photos decoded ahead on one worker thread and the photo shown, keyed by path.

    ``decode(path)`` returns the photo (or None); ``executor`` is for tests (by default
    the object owns a one-thread pool, stopped by :meth:`close`).
    """

    def __init__(
        self,
        decode: Decode,
        *,
        max_images: int = MAX_IMAGES,
        max_bytes: int = MAX_BYTES,
        executor: Executor | None = None,
    ) -> None:
        if max_images < 1:
            raise ValueError(f"max_images must be at least 1, got {max_images}")
        self._decode = decode
        self._max_images = int(max_images)
        self._max_bytes = int(max_bytes)
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sprouts-library-read-ahead"
        )
        self._entries: OrderedDict[str, _Entry] = OrderedDict()  # oldest first
        self._closed = False

    # ------------------------------------------------------------------ API
    def get(self, path: str) -> np.ndarray | None:
        """The photo of ``path`` if it was decoded ahead (or kept) and its file is
        unchanged; None otherwise (the caller decodes it). It stays kept."""
        key = _key(path)
        entry = self._entries.get(key)
        if entry is None or not self._settle(key, entry):
            return None
        if entry.image is None or file_stamp(path) != entry.stamp:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        self._trim()  # after the move: the photo handed over is the newest, which stays
        return entry.image

    def take(self, path: str) -> tuple[tuple, np.ndarray] | None:
        """Hand over the photo of ``path`` with its stamp and forget it (another page holds
        it now); None when there is none, its file changed or its decode has not finished
        (never waits: that decode is forgotten too)."""
        entry = self._entries.pop(_key(path), None)
        if entry is None:
            return None
        future = entry.future
        if future is not None:
            if not future.done():
                future.cancel()  # a decode already running ends on its own; it is dropped
                return None
            try:
                entry.stamp, entry.image = future.result()
            except CancelledError:
                return None
        stamp = entry.stamp
        if entry.image is None or stamp is None or not _readable(stamp):
            return None
        return (stamp, entry.image) if file_stamp(path)[1:] == stamp[1:] else None

    def holds(self, path: str) -> bool:
        """True when ``path``'s photo is kept or being decoded (never waits)."""
        return _key(path) in self._entries

    def is_ready(self, path: str) -> bool:
        """True when a decode of ``path`` has finished (a test hook; never waits)."""
        entry = self._entries.get(_key(path))
        if entry is None:
            return False
        return entry.future is None or entry.future.done()

    def keep(self, path: str, stamp: tuple, image: np.ndarray | None) -> None:
        """Keep ``image``, decoded by the caller from the file ``stamp`` describes
        (``file_stamp`` taken before the read), e.g. the photo just shown."""
        if self._closed or image is None or not _readable(stamp):
            return
        image.flags.writeable = False
        key = _key(path)
        self._drop(key)
        self._entries[key] = _Entry(stamp=stamp, image=image)
        self._trim()

    def request(self, paths: Iterable[str]) -> None:
        """Decode ``paths`` ahead, in order, unless kept or already being decoded."""
        if self._closed:
            return
        for path in paths:
            key = _key(path)
            if key in self._entries:
                self._entries.move_to_end(key)
                continue
            future = self._executor.submit(_read, self._decode, path)
            self._entries[key] = _Entry(future=future)
            self._trim()

    def retain(self, paths: Iterable[str]) -> None:
        """Forget every photo but those of ``paths`` (a queued decode is cancelled)."""
        keep = {_key(path) for path in paths}
        for key in [key for key in self._entries if key not in keep]:
            self._drop(key)

    def pending(self) -> list[str]:
        """Keys (normalised paths) of the photos kept or being decoded, oldest first."""
        return list(self._entries)

    def clear(self) -> None:
        """Forget every photo (another folder); queued decodes are cancelled."""
        for key in list(self._entries):
            self._drop(key)

    def close(self) -> None:
        """Forget everything and stop the worker (a running decode ends on its own)."""
        self.clear()
        self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- internals
    def _settle(self, key: str, entry: _Entry) -> bool:
        """Turn a decode into its result: waits for a running one, drops a queued one."""
        future = entry.future
        if future is None:
            return True
        if future.cancel():  # still queued: the caller is quicker decoding it now
            self._entries.pop(key, None)
            return False
        try:
            entry.stamp, entry.image = future.result()
        except CancelledError:
            self._entries.pop(key, None)
            return False
        entry.future = None  # the caller trims once this entry is the newest
        return True

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None and entry.future is not None:
            entry.future.cancel()

    def _trim(self) -> None:
        """Drop the oldest entries beyond the count and byte bounds (the newest stays)."""
        while len(self._entries) > self._max_images:
            self._drop(next(iter(self._entries)))
        while len(self._entries) > 1 and sum(e.nbytes for e in self._entries.values()) > (
            self._max_bytes
        ):
            self._drop(next(iter(self._entries)))

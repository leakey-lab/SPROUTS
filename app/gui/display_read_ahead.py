"""Decode the Library's next photos ahead, off the GUI thread (Qt-free).

The Library display decodes the selected photo on the GUI thread: about 1 ms at
640 x 480 and 6 ms of a 10 ms step at 2048 x 1536 (JPEG). Once a photo is shown,
:class:`DisplayReadAhead` decodes its neighbours (the images PgDn / PgUp / the arrow keys
open next) on one worker thread, and keeps the photo just shown, so stepping on or back
takes a finished decode instead of decoding again.

Rules (the same as the Correct page's read-ahead):

- a decode is used only while the file on disk is the one that was read: its
  ``(path, mtime, size)`` stamp is taken before the read and compared at :meth:`get`;
- a few photos are kept, bounded by count and by bytes. A decode counts towards the bytes
  as soon as it finishes, shown or not, and the bounds are enforced then too. The oldest
  go first (the photo shown before its neighbours were asked for, then the reads in the
  order asked); a decode queued or running holds nothing yet, so it is never dropped for
  bytes; the photo just decoded, kept or asked for always stays;
- a read still queued when its photo is asked for is dropped (the caller decodes it
  itself); one already running is waited for, since it is partly done;
- decoded arrays are made read-only: they are shared with the photo item, and with the
  Correct page, which takes the photos it opens or reads ahead (:meth:`DisplayReadAhead.take`
  hands one over and forgets it, never waiting), so this cache empties while it is shown.

Only the GUI thread calls the object; the worker runs the decode jobs, and the end of one
only re-checks the bounds, under a lock.
"""

from __future__ import annotations

import functools
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from app.io_utils import path_key

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
    return path_key(path)


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


def _decoded(future: Future) -> tuple[tuple | None, np.ndarray | None]:
    """A finished decode's ``(stamp, photo)``; ``(None, None)`` while it is queued or
    running, or when it was cancelled or failed (never waits)."""
    if not future.done() or future.cancelled() or future.exception() is not None:
        return None, None
    return future.result()


@dataclass
class _Entry:
    stamp: tuple | None = None
    image: np.ndarray | None = None
    future: Future | None = None

    @property
    def nbytes(self) -> int:
        """Bytes of the photo held: a kept one, or a finished decode (shown or not); a
        decode queued or running holds nothing yet."""
        image = self.image
        if image is None and self.future is not None:
            image = _decoded(self.future)[1]
        return 0 if image is None else int(image.nbytes)


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
        self._lock = threading.Lock()  # the end of a decode trims on the worker
        self._closed = False

    # ------------------------------------------------------------------ API
    def get(self, path: str) -> np.ndarray | None:
        """The photo of ``path`` if it was decoded ahead (or kept) and its file is
        unchanged; None otherwise (the caller decodes it). It stays kept, as the newest."""
        key = _key(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            future = entry.future
            if future is not None and future.cancel():  # still queued: the caller is quicker
                del self._entries[key]
                return None
        if future is None:
            stamp, image = entry.stamp, entry.image
        else:
            try:
                stamp, image = future.result()  # a running decode is waited for, unlocked
            except CancelledError:
                stamp, image = None, None
        current = image is not None and file_stamp(path) == stamp
        with self._lock:
            if self._entries.get(key) is entry:
                del self._entries[key]
            if not current or self._closed:
                return None
            # Kept as the newest even when a bound check dropped it while it was waited for.
            self._entries[key] = _Entry(stamp=stamp, image=image)
            self._trim(stay=key)
        return image

    def take(self, path: str) -> tuple[tuple, np.ndarray] | None:
        """Hand over the photo of ``path`` with its stamp and forget it (another page holds
        it now); None when there is none, its file changed or its decode has not finished
        (never waits: that decode is forgotten too)."""
        with self._lock:
            entry = self._entries.pop(_key(path), None)
        if entry is None:
            return None
        future = entry.future
        if future is None:
            stamp, image = entry.stamp, entry.image
        elif not future.done():
            future.cancel()  # a decode already running ends on its own; it is dropped
            return None
        else:
            stamp, image = _decoded(future)
        if image is None or stamp is None or not _readable(stamp):
            return None
        return (stamp, image) if file_stamp(path)[1:] == stamp[1:] else None

    def holds(self, path: str) -> bool:
        """True when ``path``'s photo is kept or being decoded (never waits)."""
        with self._lock:
            return _key(path) in self._entries

    def is_ready(self, path: str) -> bool:
        """True when a decode of ``path`` has finished (a test hook; never waits)."""
        with self._lock:
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
        with self._lock:
            self._drop(key)
            self._entries[key] = _Entry(stamp=stamp, image=image)
            self._trim(stay=key)

    def request(self, paths: Iterable[str]) -> None:
        """Decode ``paths`` ahead, in order, unless kept or already being decoded."""
        queued: list[tuple[str, Future]] = []
        with self._lock:
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
                queued.append((key, future))
        for key, future in queued:  # unlocked: a decode done already reports at once
            future.add_done_callback(functools.partial(self._completed, key))

    def retain(self, paths: Iterable[str]) -> None:
        """Forget every photo but those of ``paths`` (a queued decode is cancelled)."""
        keep = {_key(path) for path in paths}
        with self._lock:
            for key in [key for key in self._entries if key not in keep]:
                self._drop(key)

    def pending(self) -> list[str]:
        """Keys (normalised paths) of the photos kept or being decoded, oldest first."""
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """Forget every photo (another folder); queued decodes are cancelled."""
        with self._lock:
            for key in list(self._entries):
                self._drop(key)

    def close(self) -> None:
        """Forget everything and stop the worker (a running decode ends on its own)."""
        self.clear()
        with self._lock:
            self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- internals
    def _completed(self, key: str, future: Future) -> None:
        """A decode ended (on the worker, or at once): its photo now counts towards the
        bytes, and it stays. A cancelled one holds nothing, and ``cancel`` calls this with
        the lock held: it must return before taking the lock."""
        if future.cancelled():
            return
        with self._lock:
            entry = self._entries.get(key)
            if not self._closed and entry is not None and entry.future is future:
                self._trim(stay=key)

    def _drop(self, key: str) -> None:
        """Forget ``key`` (lock held); a queued decode is cancelled."""
        entry = self._entries.pop(key, None)
        if entry is not None and entry.future is not None:
            entry.future.cancel()

    def _trim(self, stay: str | None = None) -> None:
        """Drop entries beyond the count and byte bounds (lock held), oldest first. ``stay``
        (by default the newest) always stays, and a decode not finished holds no bytes, so
        it is never dropped for them."""
        if stay is None:
            stay = next(reversed(self._entries), None)
        order = [key for key in self._entries if key != stay]  # oldest first
        while len(self._entries) > self._max_images:
            self._drop(order.pop(0))
        held = {key: self._entries[key].nbytes for key in self._entries}
        total = sum(held.values())
        for key in order:
            if total <= self._max_bytes:
                break
            if held[key]:
                total -= held[key]
                self._drop(key)

"""Decode the Library's next photos ahead, off the GUI thread (Qt-free).

The Library display decodes the selected photo on the GUI thread: about 1 ms at
640 x 480 and 6 ms of a 10 ms step at 2048 x 1536 (JPEG). Once a photo is shown,
:class:`DisplayReadAhead` decodes its neighbours (the images PgDn / PgUp / the arrow keys
open next) on one worker thread, and keeps the photo just shown, so stepping on or back
takes a finished decode instead of decoding again.

Rules (the same as the Correct page's read-ahead; both use :mod:`app.read_ahead_cache`):

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

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Executor, Future

import numpy as np

from app.io_utils import file_stamp, path_key
from app.read_ahead_cache import ReadAheadCache, finished

logger = logging.getLogger(__name__)

__all__ = ["DisplayReadAhead"]

MAX_IMAGES = 3  # the photo shown, the next and the previous one
MAX_BYTES = 192 * 2**20  # about 20 photos of 2048 x 1536, two of 6000 x 4000

Decode = Callable[[str], "np.ndarray | None"]


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


class DisplayReadAhead(ReadAheadCache):
    """Photos decoded ahead on one worker thread and the photo shown, keyed by path.

    ``decode(path)`` returns the photo (or None); ``executor`` is for tests (by default
    the object owns a one-thread pool, stopped by :meth:`close`). Every entry ends with
    ``(stamp, photo)``: a decode's future, or a photo kept (a finished entry).
    """

    def __init__(
        self,
        decode: Decode,
        *,
        max_images: int = MAX_IMAGES,
        max_bytes: int = MAX_BYTES,
        executor: Executor | None = None,
    ) -> None:
        super().__init__(
            max_images=max_images,
            max_bytes=max_bytes,
            thread_name="sprouts-library-read-ahead",
            executor=executor,
        )
        self._decode = decode

    # ------------------------------------------------------------------ API
    def get(self, path: str) -> np.ndarray | None:
        """The photo of ``path`` if it was decoded ahead (or kept) and its file is
        unchanged; None otherwise (the caller decodes it). It stays kept, as the newest."""
        key = path_key(path)
        with self._lock:
            future = self._jobs.get(key)
            if future is None:
                return None
            if future.cancel():  # still queued: the caller is quicker
                del self._jobs[key]
                return None
        try:
            stamp, image = future.result()  # a running decode is waited for, unlocked
        except CancelledError:
            stamp, image = None, None
        current = image is not None and file_stamp(path) == stamp
        with self._lock:
            if self._jobs.get(key) is future:
                del self._jobs[key]
            if not current or self._closed:
                return None
            # Kept as the newest even when a bound check dropped it while it was waited for.
            # A new entry, not the decode's: that decode's end may still call back, late.
            self._put(key, (stamp, image), [key])
        return image

    def take(self, path: str) -> tuple[tuple, np.ndarray] | None:
        """Hand over the photo of ``path`` with its stamp and forget it (another page holds
        it now); None when there is none, its file changed or its decode has not finished
        (never waits: that decode is forgotten too)."""
        with self._lock:
            future = self._jobs.pop(path_key(path), None)
        if future is None:
            return None
        if not future.done():
            future.cancel()  # a decode already running ends on its own; it is dropped
            return None
        result = finished(future)
        stamp, image = (None, None) if result is None else result
        if image is None or stamp is None or not _readable(stamp):
            return None
        return (stamp, image) if file_stamp(path)[1:] == stamp[1:] else None

    def holds(self, path: str) -> bool:
        """True when ``path``'s photo is kept or being decoded (never waits)."""
        with self._lock:
            return path_key(path) in self._jobs

    def is_ready(self, path: str) -> bool:
        """True when a decode of ``path`` has finished (a test hook; never waits)."""
        with self._lock:
            future = self._jobs.get(path_key(path))
        return future is not None and future.done()

    def keep(self, path: str, stamp: tuple, image: np.ndarray | None) -> None:
        """Keep ``image``, decoded by the caller from the file ``stamp`` describes
        (``file_stamp`` taken before the read), e.g. the photo just shown."""
        if self._closed or image is None or not _readable(stamp):
            return
        image.flags.writeable = False
        key = path_key(path)
        with self._lock:
            self._drop(key)
            self._put(key, (stamp, image), [key])

    def request(self, paths: Iterable[str]) -> None:
        """Decode ``paths`` ahead, in order, unless kept or already being decoded."""
        queued: list[tuple[str, Future]] = []
        with self._lock:
            if self._closed:
                return
            for path in paths:
                key = path_key(path)
                if key in self._jobs:
                    self._jobs.move_to_end(key)
                    continue
                queued.append((key, self._submit(key, _read, self._decode, path)))
        for key, future in queued:  # unlocked: a decode done already reports at once
            self._watch(key, future)

    def retain(self, paths: Iterable[str]) -> None:
        """Forget every photo but those of ``paths`` (a queued decode is cancelled)."""
        keep = {path_key(path) for path in paths}
        with self._lock:
            for key in [key for key in self._jobs if key not in keep]:
                self._drop(key)

    # ------------------------------------------------------------- internals
    def _ended(self, key: str, future: Future) -> None:
        """A decode ended: its photo now counts towards the bytes, and it stays, while it is
        still ``key``'s entry (not taken, forgotten or replaced by a kept photo since)."""
        if self._jobs.get(key) is future:
            self._trim([key])

    def _nbytes(self, result: object) -> int:
        """Bytes of the photo of a finished ``(stamp, photo)`` (0 without one)."""
        image = None if result is None else result[1]
        return 0 if image is None else int(image.nbytes)

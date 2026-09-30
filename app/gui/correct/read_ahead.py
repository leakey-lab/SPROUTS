"""Read the Correct page's next images ahead, off the GUI thread (Qt-free).

Paging (PgDn / PgUp) opens an image the user is likely to open next. :class:`ReadAhead`
reads it on one worker thread while the user looks at the open image, so the open only
adopts what was read. It keeps a few results, bounded by count and by bytes, keyed by the
image's path; a result is handed over once, and only while the photo on disk is still the
one that was read (path, mtime, size). The GUI thread never waits for the worker:
:meth:`ReadAhead.take` returns a finished result or None, and the caller then reads the
image itself, as it would without a read-ahead.

The job, :func:`read_image`, decodes the photo as :class:`app.enhancement.SourceImageCache`
does (Qt's geometry, EXIF orientation ignored) and hands its size to each layer's reader
(the optional ``read_ahead`` hook of an edit layer, see :mod:`app.gui.correct.layer`); a
reader's payload goes back to that layer's ``open``. A reader that raises is logged and
left out: that layer then opens by itself. Nothing here touches Qt, and a job must not.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from app import enhancement

logger = logging.getLogger(__name__)

__all__ = ["AheadImage", "ReadAhead", "Reader", "file_stamp", "read_image"]

#: A layer's reader: ``reader((w, h))`` -> its payload for that image (runs on the worker).
Reader = Callable[[tuple[int, int]], object]

MAX_IMAGES = 3  # the next image, the previous one and one more
MAX_BYTES = 256 * 2**20  # about 16 results of 2048 x 1536, one of 6000 x 4000


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a readable file, ``(path, None)`` otherwise."""
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None)
    return (path, stat.st_mtime_ns, stat.st_size)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _nbytes(payload: object) -> int:
    """Bytes a payload holds: its ``nbytes`` (an array's, or a payload's own), else 0."""
    size = getattr(payload, "nbytes", 0)
    return int(size) if isinstance(size, (int, np.integer)) else 0


@dataclass(frozen=True)
class AheadImage:
    """What :func:`read_image` read for one image.

    ``photo`` is the decoded BGR photo (read-only, as the editor's source cache keeps
    it), ``stamp`` the photo file's :func:`file_stamp` taken *before* it was read, and
    ``layers`` each layer's payload by layer id (a layer without one opens by itself).
    """

    image_path: str
    stamp: tuple
    photo: np.ndarray
    layers: Mapping[str, object] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        """``(w, h)`` of the photo."""
        return int(self.photo.shape[1]), int(self.photo.shape[0])

    @property
    def nbytes(self) -> int:
        """Bytes held: the photo and every payload that reports its ``nbytes``."""
        return int(self.photo.nbytes) + sum(_nbytes(p) for p in self.layers.values())

    def is_current(self) -> bool:
        """True while the photo on disk is the one that was read."""
        return self.stamp[1:] != (None,) and file_stamp(self.image_path) == self.stamp


def read_image(image_path: str, readers: Mapping[str, Reader]) -> AheadImage | None:
    """Read ``image_path`` and its layers' payloads (the worker's job); None: unreadable.

    The photo is stamped before it is read, so a file replaced meanwhile never passes
    :meth:`AheadImage.is_current`. A reader that raises is logged and left out.
    """
    stamp = file_stamp(image_path)
    if stamp[1:] == (None,):
        return None
    photo = enhancement.imread_unicode(image_path, enhancement.DECODE_FLAGS)
    if photo is None:
        return None
    photo.flags.writeable = False  # shared with the photo item, the pyramid and Enhance
    size = (int(photo.shape[1]), int(photo.shape[0]))
    layers: dict[str, object] = {}
    for layer_id, reader in readers.items():
        try:
            layers[layer_id] = reader(size)
        except Exception:  # noqa: BLE001 - that layer opens by itself instead
            logger.exception("Could not read the %s of %s ahead", layer_id, image_path)
    return AheadImage(image_path, stamp, photo, layers)


class ReadAhead:
    """A few images read ahead on one worker thread: bounded, keyed by path, never waited on.

    Only the thread that owns it (the GUI thread) calls it; the worker runs the jobs and
    touches nothing else. ``executor`` is for tests (an executor that runs jobs inline);
    by default the object owns a one-thread pool, stopped by :meth:`close`.
    """

    def __init__(
        self,
        *,
        max_images: int = MAX_IMAGES,
        max_bytes: int = MAX_BYTES,
        executor: Executor | None = None,
    ) -> None:
        if max_images < 1:
            raise ValueError(f"max_images must be at least 1, got {max_images}")
        self._max_images = int(max_images)
        self._max_bytes = int(max_bytes)
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sprouts-read-ahead"
        )
        self._jobs: OrderedDict[str, Future] = OrderedDict()  # oldest first
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ API
    def request(self, image_path: str, job: Callable[[], AheadImage | None]) -> bool:
        """Run ``job`` for ``image_path`` on the worker, unless a current result or a job
        for it is already there; True when it was queued (the newest entry either way)."""
        key = _key(image_path)
        with self._lock:
            if self._closed:
                return False
            future = self._jobs.get(key)
            if future is not None and _usable(future):
                self._jobs.move_to_end(key)
                return False
            self._jobs.pop(key, None)
            self._jobs[key] = self._executor.submit(job)
            self._trim()
            return True

    def take(self, image_path: str) -> AheadImage | None:
        """The finished, current read of ``image_path``, handed over once; None when there
        is none, it is stale or its job still runs (never waits: the caller reads itself)."""
        key = _key(image_path)
        with self._lock:
            future = self._jobs.get(key)
            if future is None or not future.done():
                return None
            del self._jobs[key]
        result = _result(future)
        return result if result is not None and result.is_current() else None

    def put(self, image: AheadImage) -> None:
        """Keep ``image`` again (it was taken, but the open did not happen), as the newest."""
        future: Future = Future()
        future.set_result(image)
        key = _key(image.image_path)
        with self._lock:
            if self._closed:
                return
            self._jobs.pop(key, None)
            self._jobs[key] = future
            self._trim()

    def pending(self) -> list[str]:
        """Keys (normalised paths) of the entries kept, finished or not, oldest first."""
        with self._lock:
            return list(self._jobs)

    def keep_only(self, image_paths: Iterable[str]) -> None:
        """Forget every entry but those of ``image_paths`` (a queued job is cancelled)."""
        keep = {_key(path) for path in image_paths}
        with self._lock:
            for key in [key for key in self._jobs if key not in keep]:
                self._jobs.pop(key).cancel()

    def invalidate(self, image_path: str) -> None:
        """Forget ``image_path``'s entry (its files changed: a save, a new run)."""
        with self._lock:
            future = self._jobs.pop(_key(image_path), None)
        if future is not None:
            future.cancel()

    def clear(self) -> None:
        """Forget every entry (a new folder); a queued job is cancelled."""
        with self._lock:
            futures, self._jobs = list(self._jobs.values()), OrderedDict()
        for future in futures:
            future.cancel()

    def close(self) -> None:
        """Forget everything and stop the worker (a running job ends on its own)."""
        self.clear()
        with self._lock:
            self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ internals
    def _trim(self) -> None:
        """Drop the oldest entries past ``max_images`` or past ``max_bytes`` (finished
        ones count their bytes; the newest entry always stays)."""
        while len(self._jobs) > self._max_images:
            self._jobs.popitem(last=False)[1].cancel()
        while len(self._jobs) > 1 and self._bytes() > self._max_bytes:
            self._jobs.popitem(last=False)[1].cancel()

    def _bytes(self) -> int:
        total = 0
        for future in self._jobs.values():
            result = _result(future) if future.done() else None
            total += 0 if result is None else result.nbytes
        return total


def _result(future: Future) -> AheadImage | None:
    """A finished future's result; None when it was cancelled or raised (logged)."""
    if future.cancelled():
        return None
    error = future.exception()
    if error is not None:
        logger.warning("A read-ahead job failed: %s", error)
        return None
    return future.result()


def _usable(future: Future) -> bool:
    """A job still running or queued, or a finished one whose result is current."""
    if not future.done():
        return True
    result = _result(future)
    return result is not None and result.is_current()

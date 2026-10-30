"""Read the Correct page's next images ahead, off the GUI thread (Qt-free).

Paging (PgDn / PgUp) opens an image the user is likely to open next. :class:`ReadAhead`
reads it on one worker thread while the user looks at the open image, so the open only
adopts what was read. It keeps a few results, bounded by count and by bytes, keyed by the
image's path; a result is handed over once, and only while the photo on disk is still the
one that was read (path, mtime, size). The GUI thread never waits for the worker:
:meth:`ReadAhead.take` returns a finished result or None, and the caller then reads the
image itself, as it would without a read-ahead.

The byte budget counts finished reads (a queued or running job holds nothing yet), and it
is enforced when a read is queued, put back or completes. What goes first is what is least
likely to be opened: the entries outside the last plan (:meth:`ReadAhead.keep_only`, the
neighbours nearest first), oldest first, then the plan's from its end; the plan's first
entry (without a plan, the newest) always stays. The bounded cache is
:mod:`app.read_ahead_cache`'s, which the Library's read-ahead uses too.

The job, :func:`read_image`, decodes the photo as :class:`app.enhancement.SourceImageCache`
does (Qt's geometry, EXIF orientation ignored), or takes a photo decoded already (the
Library display's) while its file is unchanged, and hands its size to each layer's reader
(the optional ``read_ahead`` hook of an edit layer, see :mod:`app.gui.correct.layer`); a
reader's payload goes back to that layer's ``open``. A reader that raises is logged and
left out: that layer then opens by itself. Nothing here touches Qt, and a job must not.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field

import numpy as np

from app import enhancement
from app.io_utils import file_stamp, path_key
from app.read_ahead_cache import ReadAheadCache, finished

logger = logging.getLogger(__name__)

__all__ = ["AheadImage", "Photo", "ReadAhead", "Reader", "read_image"]

#: A layer's reader: ``reader((w, h))`` -> its payload for that image (runs on the worker).
Reader = Callable[[tuple[int, int]], object]
#: A decoded photo: ``(stamp, array)``, the stamp (:func:`~app.io_utils.file_stamp`) taken
#: before the decode and the array read-only.
Photo = tuple[tuple, np.ndarray]

MAX_IMAGES = 3  # the next image, the previous one and one more
MAX_BYTES = 256 * 2**20  # about 16 results of 2048 x 1536, one of 8000 x 6000


def _nbytes(payload: object) -> int:
    """Bytes a payload holds: its ``nbytes`` (an array's, or a payload's own), else 0."""
    size = getattr(payload, "nbytes", 0)
    return int(size) if isinstance(size, (int, np.integer)) else 0


@dataclass(frozen=True)
class AheadImage:
    """What :func:`read_image` read for one image.

    ``photo`` is the decoded BGR photo (read-only, as the editor's source cache keeps
    it), ``stamp`` the photo file's :func:`~app.io_utils.file_stamp` taken *before* it was read, and
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


def read_image(
    image_path: str, readers: Mapping[str, Reader], photo: Photo | None = None
) -> AheadImage | None:
    """Read ``image_path`` and its layers' payloads (the worker's job); None: unreadable.

    The photo is stamped before it is read, so a file replaced meanwhile never passes
    :meth:`AheadImage.is_current`. ``photo`` (a decode made already, e.g. the Library
    display's) is used instead of decoding while the file is still the one it was decoded
    from. A reader that raises is logged and left out.
    """
    stamp = file_stamp(image_path)
    if stamp[1:] == (None,):
        return None
    if photo is not None and tuple(photo[0][1:]) == stamp[1:]:
        decoded = photo[1]
    else:
        decoded = enhancement.imread_unicode(image_path, enhancement.DECODE_FLAGS)
        if decoded is None:
            return None
    decoded.flags.writeable = False  # shared with the photo item, the pyramid and Enhance
    size = (int(decoded.shape[1]), int(decoded.shape[0]))
    layers: dict[str, object] = {}
    for layer_id, reader in readers.items():
        try:
            layers[layer_id] = reader(size)
        except Exception:  # noqa: BLE001 - that layer opens by itself instead
            logger.exception("Could not read the %s of %s ahead", layer_id, image_path)
    return AheadImage(image_path, stamp, decoded, layers)


class ReadAhead(ReadAheadCache):
    """A few images read ahead on one worker thread: bounded, keyed by path, never waited on.

    Only the thread that owns it (the GUI thread) calls it; the worker runs the jobs and
    touches nothing else (the end of a job only re-checks the byte budget, under the lock).
    ``executor`` is for tests (an executor that runs jobs inline); by default the object
    owns a one-thread pool, stopped by :meth:`close`. The plan is the last
    :meth:`keep_only`'s keys, the most wanted first.
    """

    def __init__(
        self,
        *,
        max_images: int = MAX_IMAGES,
        max_bytes: int = MAX_BYTES,
        executor: Executor | None = None,
    ) -> None:
        super().__init__(
            max_images=max_images,
            max_bytes=max_bytes,
            thread_name="sprouts-read-ahead",
            executor=executor,
        )

    # ------------------------------------------------------------------ API
    def request(self, image_path: str, job: Callable[[], AheadImage | None]) -> bool:
        """Run ``job`` for ``image_path`` on the worker, unless a current result or a job
        for it is already there; True when it was queued (the newest entry either way)."""
        key = path_key(image_path)
        with self._lock:
            if self._closed:
                return False
            future = self._jobs.get(key)
            if future is not None and _usable(future):
                self._jobs.move_to_end(key)
                return False
            future = self._submit(key, job)  # the stale or failed read it replaces is done
        self._watch(key, future)  # its result counts once it is done
        return True

    def take(self, image_path: str) -> AheadImage | None:
        """The finished, current read of ``image_path``, handed over once; None when there
        is none, it is stale or its job still runs (never waits: the caller reads itself)."""
        key = path_key(image_path)
        with self._lock:
            future = self._jobs.get(key)
            if future is None or not future.done():
                return None
            del self._jobs[key]
        return _current(future)

    def peek(self, image_path: str) -> AheadImage | None:
        """The finished, current read of ``image_path``, kept (not handed over): its photo
        is read-only and may be shared; None otherwise. Never waits."""
        with self._lock:
            future = self._jobs.get(path_key(image_path))
        return None if future is None else _current(future)

    def put(self, image: AheadImage) -> None:
        """Keep ``image`` as the newest read: one taken for an open that did not happen, or
        the image just left (handed back). The last plan is over (LRU until the next)."""
        key = path_key(image.image_path)
        with self._lock:
            if self._closed:
                return
            self._plan = []
            self._put(key, image)

    def keep_only(self, image_paths: Iterable[str]) -> None:
        """Forget every entry but those of ``image_paths`` (a queued job is cancelled). Their
        order is the plan, most wanted first: under the byte budget the last goes first."""
        plan = list(dict.fromkeys(path_key(path) for path in image_paths))
        keep = set(plan)
        with self._lock:
            self._plan = plan
            for key in [key for key in self._jobs if key not in keep]:
                self._drop(key)

    def invalidate(self, image_path: str) -> None:
        """Forget ``image_path``'s entry (its files changed: a save, a new run)."""
        with self._lock:
            self._drop(path_key(image_path))


def _current(entry: Future) -> AheadImage | None:
    """An entry's finished read (a job's, or one put back) whose photo is still the one on
    disk; None otherwise (not done yet, cancelled, failed or stale)."""
    result = finished(entry)
    return result if result is not None and result.is_current() else None


def _usable(entry: Future) -> bool:
    """A job still running or queued, or a finished entry whose result is current."""
    return not entry.done() or _current(entry) is not None

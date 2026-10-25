"""The mask document of the Correct workspace (Qt-free at import).

:class:`MaskDocument` is the mask of one image: the committed ``uint8`` 0/1 plane with
its patch history (:class:`app.gui.patch_history.PatchHistory`), a copy of the plane as
last saved (the *baseline*: "dirty" is a pixel comparison, and Discard reverts in
memory) and the file I/O through :class:`app.masks.MaskStore`. Load and save are
``MaskTracingInterface._open_document`` / ``save_mask`` moved verbatim, without Qt:
notices come back as :class:`app.gui.correct.layer.Notice` values and a save returns a
:class:`MaskSaveResult`; the mask layer turns both into toasts.

On disk the mask is ``mask/<stem>.png``, white on transparent RGBA (PNG colour type 6),
written atomically; legacy ``.jpg`` / ``.jpeg`` masks (any case) are still read and
removed. An empty plane removes every saved variant instead of writing a file. A mask
file the document could not load (another size than the image, undecodable) is not shown
and is kept: the open says so, the status reads "Mask not shown", and a save of the empty
plane leaves the file (the session asks before a save of painted pixels replaces it).

The owner changes the plane only through ``history`` and calls
:meth:`MaskDocument.touch` after each change (commit, undo, redo), so
:meth:`MaskDocument.is_dirty` compares the pixels at most once per revision.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.gui.correct.layer import LayerStatus, Notice
from app.gui.patch_history import PatchHistory
from app.masks import MaskFileError, MaskStore, write_mask_file

logger = logging.getLogger(__name__)

__all__ = ["MaskDocument", "MaskRead", "MaskSaveResult"]

# Why a mask file was not loaded (``MaskRead.not_loaded``) -> the open notice / status tooltip.
_NOT_SHOWN = {
    "size": "Saved mask size does not match the image: it is not shown and is kept unless you"
    " save over it",
    "unreadable": "Saved mask could not be read: it is not shown and is kept unless you save"
    " over it",
}


@dataclass(frozen=True)
class MaskSaveResult:
    """What :meth:`MaskDocument.save` did.

    ``ok``: the file now holds the plane (written, or removed for an empty plane), or a
    file that was not loaded was kept. ``path``: the mask file written, or the one a failed
    write was aimed at; None when the plane was empty (nothing to write). ``removed``: an
    empty plane removed an existing saved file. ``error``: why the save failed (the
    exception text), else "". ``kept``: the plane was empty and a mask file the document
    did not load was left as it is (nothing written or removed).
    """

    ok: bool
    path: str | None = None
    removed: bool = False  # an empty mask removed an existing file
    error: str = ""
    kept: bool = False  # an empty plane left a file that was not loaded


@dataclass(frozen=True)
class MaskRead:
    """The saved mask of one image, read ahead on a worker thread (:meth:`MaskDocument.read`).

    ``stamps`` are the mask files' stamps taken before the read (``file_stamps`` order);
    ``plane`` is the 0/1 plane at ``size`` (None: no usable file); ``not_loaded``: why a
    mask file that exists was not loaded, ``"size"`` (another size) or ``"unreadable"``
    (undecodable), else ``""`` (the open warns about it).
    """

    image_path: str
    size: tuple[int, int]
    stamps: tuple
    plane: np.ndarray | None
    has_saved_file: bool
    not_loaded: str

    @property
    def nbytes(self) -> int:
        """Bytes held (the read-ahead cache counts them)."""
        return 0 if self.plane is None else int(self.plane.nbytes)


class MaskDocument:
    """The mask of one image: committed plane + patch history + saved baseline + MaskStore I/O.

    ``history.plane`` is the ``uint8`` 0/1 ``(h, w)`` plane (None while closed);
    ``image_path`` is ``""`` while closed; ``has_saved_file`` says whether a mask file
    of the image holds the saved state (the status shows "No mask" otherwise);
    :meth:`unloaded_file` says whether a mask file was found but not loaded.
    ``on_history_change`` becomes the history's ``on_change`` (undo/redo availability).
    """

    def __init__(self, *, on_history_change: Callable[[], None] | None = None) -> None:
        self.history = PatchHistory(on_change=on_history_change)
        self.image_path = ""
        self.has_saved_file = False
        self._store: MaskStore | None = None
        self._baseline: np.ndarray | None = None  # the plane as last opened or saved
        self._revision = 0
        self._dirty = False
        self._dirty_revision = 0  # the revision _dirty was computed for
        self._save_error = ""
        self._error_revision = -1  # the revision a failed save happened at
        self._not_loaded = ""  # MaskRead.not_loaded of the open file, until a write

    # ------------------------------------------------------------------ state
    @property
    def revision(self) -> int:
        """Plane revision: +1 per :meth:`touch` (and on open, revert and close)."""
        return self._revision

    def is_open(self) -> bool:
        """True while a document is open."""
        return self.history.plane is not None

    def touch(self) -> None:
        """The plane changed: bump the revision, so the next :meth:`is_dirty` compares again."""
        self._revision += 1

    def is_dirty(self) -> bool:
        """True when the plane differs from the saved state (compared once per revision)."""
        plane, baseline = self.history.plane, self._baseline
        if plane is None or baseline is None:
            return False
        if self._dirty_revision != self._revision:
            self._dirty = not _planes_equal(plane, baseline)
            self._dirty_revision = self._revision
        return self._dirty

    def unloaded_file(self) -> bool:
        """True while a mask file found at open was not loaded and nothing was written over it."""
        return bool(self._not_loaded)

    def status(self) -> LayerStatus:
        """The save state: a failed save (until the next edit) > unsaved > a file not shown >
        saved > no mask."""
        if not self.is_open():
            return LayerStatus("none")
        if self._save_error and self._error_revision == self._revision:
            return LayerStatus("error", "Not saved", self._save_error)
        if self.is_dirty():
            return LayerStatus("dirty")
        if self._not_loaded:
            return LayerStatus("error", "Mask not shown", _NOT_SHOWN[self._not_loaded])
        if self.has_saved_file:
            return LayerStatus("clean")
        return LayerStatus("none", "No mask")

    def file_stamps(self) -> tuple:
        """Every mask file the image may have, in lookup order, stamped or absent.

        One entry per ``MaskStore.for_image(image).layout.mask_candidates(stem)`` path:
        ``(path, mtime_ns, size)`` for an existing file, ``(path, None)`` otherwise. Read
        afresh on each call (compare two results with ``==``); ``()`` while closed.
        """
        if self._store is None or not self.image_path:
            return ()
        return tuple(_stamp(path) for path in self._store.layout.mask_candidates(self.image_path))

    # ------------------------------------------------------------------ lifecycle
    @staticmethod
    def read(image_path: str, size: tuple[int, int]) -> MaskRead:
        """Read the saved mask of ``image_path`` for an image of ``size`` ``(w, h)``, as
        :meth:`open` would (any thread: touches no document)."""
        store = MaskStore.for_image(image_path)
        stamps = tuple(_stamp(path) for path in store.layout.mask_candidates(image_path))
        width, height = size
        try:
            on = store.read(image_path)
        except MaskFileError as exc:
            logger.warning("Failed to load existing mask: %s", exc)
            exists = any(stamp[1:] != (None,) for stamp in stamps)
            return MaskRead(image_path, size, stamps, None, False, "unreadable" if exists else "")
        if on is None:
            return MaskRead(image_path, size, stamps, None, False, "")
        if on.shape != (height, width):
            logger.warning(
                "Mask of %s is %sx%s, image is %sx%s; ignoring it",
                image_path,
                on.shape[1],
                on.shape[0],
                width,
                height,
            )
            return MaskRead(image_path, size, stamps, None, False, "size")
        return MaskRead(image_path, size, stamps, _zero_one_plane(on), True, "")

    def open(
        self, image_path: str, size: tuple[int, int], read: MaskRead | None = None
    ) -> list[Notice]:
        """Open the saved mask of ``image_path`` for an image of ``size`` ``(w, h)``.

        The mask as saved (grey above 200) becomes the plane and the baseline: clean,
        with an empty history. No saved mask, an undecodable one or one of another size
        opens empty; the last two are not shown and are kept (:meth:`unloaded_file`), with
        a warning notice. ``read`` (a :meth:`read` done ahead) is used while the mask
        files are still the ones it read.
        """
        store = MaskStore.for_image(image_path)
        current = tuple(_stamp(path) for path in store.layout.mask_candidates(image_path))
        if read is None or (read.image_path, tuple(read.size), read.stamps) != (
            image_path,
            tuple(size),
            current,
        ):
            read = self.read(image_path, size)
        width, height = size
        plane = read.plane if read.plane is not None else np.zeros((height, width), np.uint8)
        self._store = store
        self.image_path = image_path
        self.has_saved_file = read.has_saved_file
        self._not_loaded = read.not_loaded
        self._baseline = plane.copy()
        self.history.reset(plane)
        self._restart()
        return [Notice(_NOT_SHOWN[read.not_loaded], "warn")] if read.not_loaded else []

    def close(self) -> None:
        """Forget the document: no plane, no history, no file."""
        self._store = None
        self.image_path = ""
        self.has_saved_file = False
        self._not_loaded = ""
        self._baseline = None
        self.history.reset(None)
        self._restart()

    def revert(self) -> None:
        """Discard: the plane goes back to the saved state and the history is cleared."""
        plane, baseline = self.history.plane, self._baseline
        if plane is None or baseline is None:
            return
        if plane.shape == baseline.shape:
            np.copyto(plane, baseline)  # in place: holders of the plane stay valid
        else:
            plane = baseline.copy()
        self.history.reset(plane)
        self._restart()

    def save(self) -> MaskSaveResult:
        """Write the plane as ``mask/<stem>.png`` (atomically; the old file survives errors).

        An empty plane removes every saved variant instead, unless a mask file was not
        loaded (:meth:`unloaded_file`): that file is kept, nothing is touched
        (``kept``). On success the plane becomes the saved state. On failure the previous
        mask file is kept (the write is atomic; a failed removal keeps the variants it had
        not reached), the document stays dirty and :meth:`status` reports the error until
        the next :meth:`touch`.
        """
        plane, store = self.history.plane, self._store
        if plane is None or store is None or not self.image_path:
            return MaskSaveResult(ok=False, error="No mask is open")
        on = plane > 0

        if not on.any():
            if self._not_loaded:
                self._saved(plane)
                return MaskSaveResult(ok=True, kept=True)
            try:
                removed = store.remove(self.image_path)
            except OSError as exc:
                return self._failed(None, exc)
            self.has_saved_file = False
            self._saved(plane)
            return MaskSaveResult(ok=True, removed=removed)

        path = store.path_for(self.image_path)
        try:
            write_mask_file(path, on)
        except (OSError, ValueError) as exc:
            return self._failed(path, exc)
        self.has_saved_file = True
        self._not_loaded = ""
        self._saved(plane)
        return MaskSaveResult(ok=True, path=path)

    # ------------------------------------------------------------------ internals
    def _restart(self) -> None:
        """A new plane was adopted: new revision, clean, no save error."""
        self.touch()
        self._save_error = ""
        self._dirty = False
        self._dirty_revision = self._revision

    def _saved(self, plane: np.ndarray) -> None:
        """The file now holds ``plane``: it becomes the baseline (clean without a compare)."""
        if self._baseline is not None and self._baseline.shape == plane.shape:
            np.copyto(self._baseline, plane)
        else:
            self._baseline = plane.copy()
        self._save_error = ""
        self._dirty = False
        self._dirty_revision = self._revision

    def _failed(self, path: str | None, exc: Exception) -> MaskSaveResult:
        """Record a failed save (shown until the next edit) and describe it."""
        if path is None:
            logger.warning("Could not remove the mask of %s: %s", self.image_path, exc)
        else:
            logger.warning("Could not save mask to %s: %s", path, exc)
        self._save_error = str(exc)
        self._error_revision = self._revision
        return MaskSaveResult(ok=False, path=path, error=str(exc))


def _zero_one_plane(on: np.ndarray) -> np.ndarray:
    """The ``uint8`` 0/1 plane of the boolean mask ``on`` (``MaskStore.read``'s fresh array).

    A contiguous boolean array already holds bytes 0 and 1: its ``uint8`` view is the plane,
    with no second array to fill (1.7 ms at 2048 x 1536). Anything else is converted.
    """
    if on.dtype == np.bool_ and on.ndim == 2 and on.flags.c_contiguous and on.flags.writeable:
        return on.view(np.uint8)
    return np.asarray(on, dtype=bool).astype(np.uint8)


def _planes_equal(a: np.ndarray, b: np.ndarray) -> bool:
    """``np.array_equal`` for two planes, compared as 64-bit words (8x fewer elements).

    0.07 ms instead of 0.54 ms at 2048x1536; equal cost at 640x480.
    """
    if a.shape != b.shape or a.dtype != b.dtype:
        return False
    flat_a, flat_b = a.reshape(-1), b.reshape(-1)
    n = flat_a.size - flat_a.size % 8
    return bool(
        np.array_equal(flat_a[:n].view(np.uint64), flat_b[:n].view(np.uint64))
        and np.array_equal(flat_a[n:], flat_b[n:])
    )


def _stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a regular file, else ``(path, None)``."""
    try:
        st = os.stat(path)
    except OSError:
        return (path, None)
    if not stat.S_ISREG(st.st_mode):
        return (path, None)
    return (path, st.st_mtime_ns, st.st_size)

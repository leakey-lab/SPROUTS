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
removed. An empty plane removes every saved variant instead of writing a file.

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

__all__ = ["MaskDocument", "MaskSaveResult"]

_SIZE_MISMATCH = "Saved mask size does not match the image; starting empty"


@dataclass(frozen=True)
class MaskSaveResult:
    """What :meth:`MaskDocument.save` did.

    ``ok``: the file now holds the plane (written, or removed for an empty plane).
    ``path``: the mask file written, or the one a failed write was aimed at; None when
    the plane was empty (nothing to write). ``removed``: an empty plane removed an
    existing saved file. ``error``: why the save failed (the exception text), else "".
    """

    ok: bool
    path: str | None = None
    removed: bool = False  # an empty mask removed an existing file
    error: str = ""


class MaskDocument:
    """The mask of one image: committed plane + patch history + saved baseline + MaskStore I/O.

    ``history.plane`` is the ``uint8`` 0/1 ``(h, w)`` plane (None while closed);
    ``image_path`` is ``""`` while closed; ``has_saved_file`` says whether a mask file
    of the image holds the saved state (the status shows "No mask" otherwise).
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

    def status(self) -> LayerStatus:
        """The save state: a failed save (until the next edit) > unsaved > saved > no mask."""
        if not self.is_open():
            return LayerStatus("none")
        if self._save_error and self._error_revision == self._revision:
            return LayerStatus("error", "Not saved", self._save_error)
        if self.is_dirty():
            return LayerStatus("dirty")
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
    def open(self, image_path: str, size: tuple[int, int]) -> list[Notice]:
        """Open the saved mask of ``image_path`` for an image of ``size`` ``(w, h)``.

        The mask as saved (grey above 200) becomes the plane and the baseline: clean,
        with an empty history. No saved mask, an undecodable one or one of another size
        opens empty; a size mismatch also returns a warning notice.
        """
        store = MaskStore.for_image(image_path)
        width, height = size
        plane = np.zeros((height, width), np.uint8)
        notices: list[Notice] = []
        has_saved_file = False
        try:
            on = store.read(image_path)
        except MaskFileError as exc:
            logger.warning("Failed to load existing mask: %s", exc)
            on = None
        if on is not None:
            if on.shape != (height, width):
                logger.warning(
                    "Mask of %s is %sx%s, image is %sx%s; ignoring it",
                    image_path,
                    on.shape[1],
                    on.shape[0],
                    width,
                    height,
                )
                notices.append(Notice(_SIZE_MISMATCH, "warn"))
            else:
                plane[on] = 1
                has_saved_file = True
        self._store = store
        self.image_path = image_path
        self.has_saved_file = has_saved_file
        self._baseline = plane.copy()
        self.history.reset(plane)
        self._restart()
        return notices

    def close(self) -> None:
        """Forget the document: no plane, no history, no file."""
        self._store = None
        self.image_path = ""
        self.has_saved_file = False
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

        An empty plane removes every saved variant instead. On success the plane
        becomes the saved state. On failure the previous mask file is kept (the write is
        atomic; a failed removal keeps the variants it had not reached), the document
        stays dirty and :meth:`status` reports the error until the next :meth:`touch`.
        """
        plane, store = self.history.plane, self._store
        if plane is None or store is None or not self.image_path:
            return MaskSaveResult(ok=False, error="No mask is open")
        on = plane > 0

        if not on.any():
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

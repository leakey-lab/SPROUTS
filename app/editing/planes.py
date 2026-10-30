"""The edited planes of the Correct raster documents (Qt-free: numpy and cv2).

The mask and the class labels (``app.gui.correct.mask_document``, ``classes_document``) each
edit one ``uint8`` plane through a patch history and keep the plane as last opened or saved:
"dirty" is a pixel comparison with it, and Discard copies it back in memory.

- :func:`planes_equal`: the one exact comparison of two planes.
- :class:`SavedPlane`: a plane as last opened or saved; the edited plane is compared with it
  at most once per revision, and Discard copies it back in place (the skeleton model,
  ``app.gui.skeleton_graph_model``, keeps its saved skeleton in one too).
- :class:`PlaneDocument`: what both raster documents share: the history, the revision that
  :meth:`~PlaneDocument.touch` bumps after each change, the saved plane, a failed save's
  error (shown until the next edit), the GT file a save writes and whether the file chosen at
  open was found but not loaded.

``app.editing`` does not import this module, so importing that package stays standard library
only.
"""

from __future__ import annotations

from typing import Protocol

import cv2
import numpy as np

from app.io_utils import file_stamp

__all__ = ["PlaneDocument", "SavedPlane", "planes_equal"]


def planes_equal(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    """True when ``a`` and ``b`` hold the same pixels: same shape, dtype and values (None:
    False). Two 2-D C-contiguous ``uint8`` planes (every plane of the documents) take one
    ``cv2.norm`` pass with no temporary array: 0.06 ms at 2048 x 1536, ``np.array_equal``
    0.6 ms."""
    if a is None or b is None or a.shape != b.shape or a.dtype != b.dtype:
        return False
    if a.size == 0:
        return True
    plain = a.ndim == 2 and a.dtype == np.uint8 and a.flags.c_contiguous and b.flags.c_contiguous
    return cv2.norm(a, b, cv2.NORM_INF) == 0 if plain else bool(np.array_equal(a, b))


class SavedPlane:
    """A plane as last opened or saved (``plane``; None while nothing is open).

    The owner counts the revisions of the edited plane (+1 per change): :meth:`differs`
    compares only when the revision changed since its last answer, and :meth:`take` makes a
    revision clean without a compare.
    """

    def __init__(self) -> None:
        self.plane: np.ndarray | None = None
        self._same, self._revision = True, 0  # the last answer and the revision it is for

    def take(self, plane: np.ndarray | None, revision: int) -> None:
        """``plane`` (None: closed) is the saved state, clean at ``revision``. It is copied into
        the held buffer when that fits, so no new full-size array is made."""
        saved = self.plane
        if plane is None:
            self.plane = None
        elif saved is not None and saved.shape == plane.shape and saved.dtype == plane.dtype:
            np.copyto(saved, plane)
        else:
            self.plane = plane.copy()
        self._same, self._revision = True, revision

    def differs(self, plane: np.ndarray, revision: int) -> bool:
        """True when ``plane`` at ``revision`` does not hold the saved pixels (also when there
        is no saved plane)."""
        if self._revision != revision:
            self._same, self._revision = planes_equal(plane, self.plane), revision
        return not self._same

    def restore(self, plane: np.ndarray) -> np.ndarray:
        """Discard: the saved pixels (a saved plane is required), written into ``plane`` when
        the shapes fit (holders of the plane stay valid), else a copy; the plane to keep."""
        saved = self.plane
        if plane.shape == saved.shape:
            np.copyto(plane, saved)
            return plane
        return saved.copy()


class _History(Protocol):
    """What :class:`PlaneDocument` needs of its history (a ``PatchHistory``)."""

    plane: np.ndarray | None

    def reset(self, plane: np.ndarray | None) -> None: ...


class PlaneDocument:
    """The shared half of a raster document (see the module docstring).

    ``history.plane`` is the plane (None while closed); the owner changes it only through
    ``history`` and calls :meth:`touch` after each change. ``image_path`` is ``""`` while
    closed; ``gt_path`` is the file a save writes; ``has_saved_file`` says whether a file
    holds the saved state.
    """

    def __init__(self, history: _History) -> None:
        self.history = history
        self.image_path = self.gt_path = ""
        self.has_saved_file = False
        self._baseline = SavedPlane()  # the plane as last opened or saved
        self._revision = 0
        self._save_error, self._error_revision = "", -1  # a failed save and its revision
        self._not_loaded = ""  # why the file chosen at open was not loaded, until a write
        self._root = ""  # the project folder the file labels are relative to

    @property
    def revision(self) -> int:
        """+1 per :meth:`touch` (and on open, revert and close)."""
        return self._revision

    def is_open(self) -> bool:
        """True while a document is open."""
        return self.history.plane is not None

    def touch(self) -> None:
        """The plane changed: a new revision (the next dirty check compares again)."""
        self._revision += 1

    def unloaded_file(self) -> bool:
        """True while the file chosen at open was not loaded and nothing was written over it."""
        return bool(self._not_loaded)

    def write_stamps(self) -> tuple:
        """``DiskGuardedLayer.write_stamps``: the GT file, the one file a save writes or
        removes (``()`` while closed)."""
        return (file_stamp(self.gt_path),) if self.image_path else ()

    def revert(self) -> None:
        """Discard: the plane goes back to the saved state and the history is cleared."""
        plane = self.history.plane
        if plane is None or self._baseline.plane is None:
            return
        self.history.reset(self._baseline.restore(plane))
        self._reverted()
        self._restart()

    # ------------------------------------------------------------------ internals
    def _reverted(self) -> None:
        """:meth:`revert` put the saved plane back: restore what else the save holds."""

    def _plane_saved(self) -> bool:
        """True when the open plane holds the saved pixels (compared once per revision)."""
        return not self._baseline.differs(self.history.plane, self._revision)

    def _restart(self) -> None:
        """A new plane was adopted (open, revert, close): a new revision, the saved state."""
        self.touch()
        self._save_error = ""
        self._remember()

    def _remember(self) -> None:
        """The plane now is the saved state (clean without a compare)."""
        self._baseline.take(self.history.plane, self._revision)

    def _save_failed(self, exc: Exception) -> str:
        """Record a failed save (the status says so until the next edit); its text."""
        self._save_error, self._error_revision = str(exc), self._revision
        return self._save_error

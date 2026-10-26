"""The mask document of the Correct workspace (Qt-free at import).

:class:`MaskDocument` is the mask of one image: the committed ``uint8`` 0/1 plane with
its patch history (:class:`app.gui.patch_history.PatchHistory`), a copy of the plane as
last saved (the *baseline*: "dirty" is a pixel comparison, and Discard reverts in
memory) and the file I/O. Notices come back as :class:`app.gui.correct.layer.Notice`
values and a save returns a :class:`MaskSaveResult` (:meth:`MaskDocument.outcome` words
it for the workspace); the mask layer turns both into toasts.

The document opens the GT mask ``gt/mask/<stem>.png`` when it exists (``source`` "gt"),
else the model's prediction ``mask/<stem>.png`` (legacy ``.jpg`` / ``.jpeg`` names too;
``source`` "prediction"), else an empty plane (``source`` None); :mod:`.mask_files` reads
them (:class:`~app.gui.correct.mask_files.MaskRead`). It keeps the prediction
as opened (:meth:`MaskDocument.prediction`) for "Load prediction" and for the save rule.
A save writes the GT file only, white on transparent RGBA (PNG colour type 6),
atomically, and never writes or removes a prediction: a plane without edits writes
nothing (O2); a plane equal to the prediction removes the GT file; any other plane is
written, an empty one too (O9: Measure then gives 0 for it). A file the document chose
but could not load (a GT mask of another size than the image, undecodable) is not shown
and is kept:
the open says so, the status reads "Mask not shown", and a save of the empty plane
leaves it (the session asks before a save of painted pixels replaces a GT file). A
prediction of another size opens stretched to the image with nearest neighbour (O8): the
open says so, and the stretched plane is the prediction (the baseline, "Load prediction",
the save rule); a save writes the image's size.

The owner changes the plane only through ``history`` and calls
:meth:`MaskDocument.touch` after each change (commit, undo, redo), so
:meth:`MaskDocument.is_dirty` compares the pixels at most once per revision.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.gui.correct.layer import MASK, LayerStatus, Notice, SaveOutcome, project_label
from app.gui.correct.mask_files import (
    SOURCE_GT,
    SOURCE_PREDICTION,
    MaskRead,
    gt_mask_of,
    mask_stamps,
    read_mask,
)
from app.gui.patch_history import PatchHistory
from app.io_utils import file_stamp
from app.masks import write_mask_file
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

# MaskRead and the sources live in mask_files; the document's callers import them here.
__all__ = [
    "RESIZED_TEXT",
    "SOURCE_GT",
    "SOURCE_PREDICTION",
    "MaskDocument",
    "MaskRead",
    "MaskSaveResult",
]

#: The open notice of a prediction stretched to the photo (``MaskRead.resized_from``).
RESIZED_TEXT = (
    "Mask is {fw} x {fh}; shown at the photo's {w} x {h}. Saving stores it at the photo's size."
)

# Why the chosen mask file was not loaded (``MaskRead.not_loaded``) -> the open notice /
# status tooltip, for a GT file (a save may replace it) and for a prediction (never written;
# only an unreadable one: another size is stretched).
_NOT_SHOWN = {
    (SOURCE_GT, "size"): "Saved mask size does not match the image: it is not shown and is"
    " kept unless you save over it",
    (SOURCE_GT, "unreadable"): "Saved mask could not be read: it is not shown and is kept"
    " unless you save over it",
    (SOURCE_PREDICTION, "unreadable"): "The prediction could not be read: it is not shown",
}


@dataclass(frozen=True)
class MaskSaveResult:
    """What :meth:`MaskDocument.save` did.

    ``ok``: the GT file now holds the plane (written, or removed for a plane equal to the
    prediction), the plane had no edits (nothing written), or a file that was not loaded
    was kept. ``path``: the GT file written, or the one a failed write was aimed at; None
    when nothing was written. ``removed``: the plane equals the prediction and the GT file
    is gone. ``error``: why the save failed (the exception text), else "". ``kept``: the
    plane was empty and a mask file the document did not load was left as it is (nothing
    written or removed). ``message``: what the save tells the user beyond "Saved", else "".
    """

    ok: bool
    path: str | None = None
    removed: bool = False  # the plane equals the prediction: the GT file was removed
    kept: bool = False  # an empty plane left a file that was not loaded
    error: str = ""
    message: str = ""


class MaskDocument:
    """The mask of one image: committed plane + patch history + saved baseline + GT I/O.

    ``history.plane`` is the ``uint8`` 0/1 ``(h, w)`` plane (None while closed);
    ``image_path`` is ``""`` while closed; ``has_saved_file`` says whether a mask file
    of the image holds the saved state (the status shows "No mask" otherwise);
    ``source`` says which (``"gt"``, ``"prediction"``, None); ``gt_path`` is the GT file
    a save writes, ``prediction_path`` the prediction found at open (None: none);
    :meth:`unloaded_file` says whether the chosen file was found but not loaded.
    ``on_history_change`` becomes the history's ``on_change`` (undo/redo availability).
    """

    def __init__(self, *, on_history_change: Callable[[], None] | None = None) -> None:
        self.history = PatchHistory(on_change=on_history_change)
        self.image_path = ""
        self.has_saved_file = False
        self.source: str | None = None
        self.gt_path = ""
        self.prediction_path: str | None = None
        self._layout: ProjectLayout | None = None
        self._prediction: np.ndarray | None = None  # the prediction as opened
        self._baseline: np.ndarray | None = None  # the plane as last opened or saved
        self._revision = 0
        self._dirty = False
        self._dirty_revision = 0  # the revision _dirty was computed for
        self._save_error = ""
        self._error_revision = -1  # the revision a failed save happened at
        self._not_loaded = ""  # MaskRead.not_loaded of the open file, until a write
        self._prediction_resized_from: tuple[int, int] | None = None  # as read at open
        self._prediction_stamps: tuple = ()  # the prediction's names as read at open
        self._root = ""  # the project folder the file labels are relative to

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

    @property
    def prediction_stamps(self) -> tuple:
        """The stamps of the prediction's names when :meth:`prediction` was read (``()``
        while closed): the plane belongs to these files, whatever is on disk now."""
        return self._prediction_stamps

    @property
    def prediction_resized_from(self) -> tuple[int, int] | None:
        """The ``(w, h)`` of the prediction file when :meth:`prediction` is stretched from it."""
        return self._prediction_resized_from

    @property
    def resized_from(self) -> tuple[int, int] | None:
        """The ``(w, h)`` of the prediction file when the saved mask is that prediction,
        stretched to the image (a fresh open of it gives the stretch notice), else None."""
        return self._prediction_resized_from if self.source == SOURCE_PREDICTION else None

    def prediction(self) -> np.ndarray | None:
        """The prediction's 0/1 plane as opened, at the image's size (stretched when the file
        has another; None: no usable prediction). Read only: "Load prediction" commits a
        copy of it."""
        return self._prediction

    def unloaded_file(self) -> bool:
        """True while the file chosen at open was not loaded and nothing was written over it."""
        return bool(self._not_loaded)

    def matches_disk(self) -> bool:
        """True when :meth:`open` would now open exactly the saved state: the mask files
        read (:meth:`read`; no usable file reads as empty) give the baseline from the same
        source, and the same file, if any, is not shown. Reads the files: call it only
        after a stamp changed."""
        baseline = self._baseline
        if baseline is None or not self.image_path:
            return False
        height, width = baseline.shape
        read = self.read(self.image_path, (width, height))
        plane = read.plane if read.plane is not None else np.zeros_like(baseline)
        same_file = (read.source, read.not_loaded) == (self.source, self._not_loaded)
        return same_file and _planes_equal(plane, baseline)

    def status(self) -> LayerStatus:
        """The save state: a failed save (until the next edit) > unsaved > a file not shown >
        GT > the prediction > no mask."""
        if not self.is_open():
            return LayerStatus("none")
        if self._save_error and self._error_revision == self._revision:
            return LayerStatus("error", "Not saved", self._save_error)
        if self.is_dirty():
            return LayerStatus("dirty")
        if self._not_loaded:
            return LayerStatus("error", "Mask not shown", _NOT_SHOWN[self.source, self._not_loaded])
        if self.has_saved_file and self.source == SOURCE_GT:
            return LayerStatus("clean", "GT", self._label(self.gt_path))
        if self.has_saved_file:
            prediction, gt = self._label(self.prediction_path or ""), self._label(self.gt_path)
            tip = f"{prediction} — the model's output. Save after an edit writes {gt}"
            return LayerStatus("none", "Prediction", tip)
        return LayerStatus("none", "No mask")

    def file_stamps(self) -> tuple:
        """The GT mask, then every name the prediction may have, stamped or absent.

        ``(path, mtime_ns, size)`` for an existing file, ``(path, None)`` otherwise. Read
        afresh on each call (compare two results with ``==``); ``()`` while closed.
        """
        if self._layout is None or not self.image_path:
            return ()
        return mask_stamps(self._layout, self.gt_path, self.image_path)

    def write_stamps(self) -> tuple:
        """``DiskGuardedLayer.write_stamps``: the GT mask, the one file a save writes or
        removes (``()`` while closed)."""
        return (file_stamp(self.gt_path),) if self.image_path else ()

    # ------------------------------------------------------------------ lifecycle
    @staticmethod
    def read(image_path: str, size: tuple[int, int], *, gt_path: str | None = None) -> MaskRead:
        """Read the mask of ``image_path`` for an image of ``size`` ``(w, h)``, as
        :meth:`open` would (any thread: touches no document; :func:`read_mask`)."""
        return read_mask(image_path, size, gt_path=gt_path)

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        read: MaskRead | None = None,
        *,
        root: str | None = None,
    ) -> list[Notice]:
        """Open the mask of ``image_path`` for an image of ``size`` ``(w, h)``.

        The GT mask, else the prediction, as saved (grey above `MASK_THRESHOLD`, 127) becomes
        the plane and the baseline: clean, with an empty history. No file opens empty; a chosen file that
        cannot be decoded, or a GT mask of another size, opens empty too, is not shown and
        is kept (:meth:`unloaded_file`), with a warning notice. A prediction of another size
        is stretched to the image (nearest neighbour), with an info notice. ``read`` (a
        :meth:`read` done ahead) is used while the mask files are still the ones it read.
        ``root``: the project folder the file labels are relative to (None: the image's
        folder).
        """
        layout = ProjectLayout.for_image(image_path)
        gt_path = gt_mask_of(layout, image_path)  # looked up once per open
        current = mask_stamps(layout, gt_path, image_path)
        if read is None or (read.image_path, tuple(read.size), read.stamps) != (
            image_path,
            tuple(size),
            current,
        ):
            read = self.read(image_path, size, gt_path=gt_path)
        width, height = size
        plane = read.plane if read.plane is not None else np.zeros((height, width), np.uint8)
        prediction = read.prediction_plane
        if prediction is not None and prediction is read.plane:
            prediction = prediction.copy()  # the plane is edited in place
        self._layout = layout
        self._root = root or layout.root
        self.image_path = image_path
        self._prediction_stamps = tuple(read.stamps[1:])
        self.has_saved_file = read.has_saved_file
        self.source, self.gt_path, self.prediction_path = (
            read.source,
            read.gt_path,
            read.prediction_path,
        )
        self._prediction = prediction
        self._not_loaded = read.not_loaded
        self._prediction_resized_from = read.prediction_resized_from
        self._baseline = plane.copy()
        self.history.reset(plane)
        self._restart()
        if read.not_loaded:
            return [Notice(_NOT_SHOWN[read.source, read.not_loaded], "warn")]
        if read.resized_from is not None:
            fw, fh = read.resized_from
            return [Notice(RESIZED_TEXT.format(fw=fw, fh=fh, w=width, h=height), "info")]
        return []

    def close(self) -> None:
        """Forget the document: no plane, no history, no file."""
        self._layout = None
        self.image_path = ""
        self.has_saved_file = False
        self.source, self.gt_path, self.prediction_path = None, "", None
        self._prediction = None
        self._prediction_stamps, self._root = (), ""
        self._not_loaded, self._prediction_resized_from = "", None
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
        """Write the plane as the GT mask (atomically; the old file survives errors).

        An empty plane over a file that was not loaded (:meth:`unloaded_file`) keeps that
        file, nothing is touched (``kept``). A plane without edits writes nothing. A plane
        equal to the prediction as opened removes the GT file (``removed``), unless the
        prediction file changed since (a Generate run): then, as any other plane, it is
        written, an empty one too. The prediction is never written or removed. On success
        the plane becomes the saved state. On failure the previous GT file is kept, the
        document stays dirty and :meth:`status` reports the error until the next
        :meth:`touch`.
        """
        plane = self.history.plane
        if plane is None or self._layout is None or not self.image_path:
            return MaskSaveResult(ok=False, error="No mask is open")
        on = plane > 0
        if self._not_loaded and not on.any():
            self._saved(plane)
            return MaskSaveResult(ok=True, kept=True)
        if not self.is_dirty():
            return MaskSaveResult(ok=True)
        if self._prediction is not None and _planes_equal(plane, self._prediction):
            if self.file_stamps()[1:] == self._prediction_stamps:
                return self._remove_gt(plane)
        try:
            write_mask_file(self.gt_path, on)
        except (OSError, ValueError) as exc:
            return self._failed(self.gt_path, exc)
        self._now_from(SOURCE_GT, plane)
        if on.any():
            return MaskSaveResult(ok=True, path=self.gt_path)
        message = f"Empty mask saved as GT for {os.path.basename(self.image_path)}: root area 0"
        return MaskSaveResult(ok=True, path=self.gt_path, message=message)

    def outcome(self, result: MaskSaveResult) -> SaveOutcome:
        """A :meth:`save` result as the workspace reports it (the texts of C15)."""
        if result.ok and result.kept:
            message = "Nothing to save: the saved mask was kept"
            return SaveOutcome(MASK, ok=True, message=message, kind="info")
        if result.ok and result.removed:
            return SaveOutcome(MASK, ok=True, cleared=True, message=result.message, kind="info")
        if result.ok:
            kind = "info" if result.message else "success"
            return SaveOutcome(MASK, ok=True, path=result.path, message=result.message, kind=kind)
        if result.path is not None:
            message = f"Could not save mask to {result.path}: {result.error}"
        else:
            message = f"Could not remove {self._label(self.gt_path)}: {result.error}"
        return SaveOutcome(MASK, ok=False, message=message, kind="danger")

    # ------------------------------------------------------------------ internals
    def _remove_gt(self, plane: np.ndarray) -> MaskSaveResult:
        """The plane equals the prediction: the GT file goes (already gone is fine)."""
        label, done = self._label(self.gt_path), "removed {}"
        try:
            os.remove(self.gt_path)
        except FileNotFoundError:
            done = "{} was already gone"
        except OSError as exc:
            return self._failed(None, exc)
        self._now_from(SOURCE_PREDICTION, plane)
        message = f"Mask equals the prediction: {done.format(label)}"
        return MaskSaveResult(ok=True, removed=True, message=message)

    def _now_from(self, source: str, plane: np.ndarray) -> None:
        """A save succeeded: ``source``'s file holds ``plane``, the saved state."""
        self.source = source
        self.has_saved_file = True
        self._not_loaded = ""
        self._saved(plane)

    def _label(self, path: str) -> str:
        """``path`` as the prompts name it: relative to the project folder, ``/`` separators
        (``gt/mask/a.png``; ``tube1/gt/mask/a.png`` for an image in a sub-folder)."""
        return project_label(path, self._root)

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
            logger.warning("Could not remove the GT mask of %s: %s", self.image_path, exc)
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

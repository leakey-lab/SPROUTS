"""The skeleton document of the Correct workspace (Qt-free).

:class:`SkeletonDocument` holds the skeleton of one image: the editable
:class:`~app.gui.skeleton_graph_model.SkeletonCorrectionModel`, which file it came from
(``source``) and where it saves. Opening, Discard (:meth:`SkeletonDocument.reopen`),
Load file and Save are today's skeleton editor code
(``skeleton_correction_interface.py``) moved without Qt: messages come back as
:class:`~app.gui.correct.layer.Notice` lists and save results, never as dialogs.

The file rules are :class:`app.gui.skeleton_document.SkeletonFiles`: the saved
correction ``skeleton_corrections/<stem>_skeleton.png`` wins over the generated
``skeletons/<stem>_fake.png`` (an injected resolver is asked first). Saving writes only
the correction, atomically, at image resolution: the generated file is smaller
(``SKELETON_OUTPUT_SIZE``) and is upscaled when opened, so a save never shrinks
the edited skeleton back and never breaks its lines (known issue Q1).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from app.editing.thinning import is_generated_as_opened, thin_raster
from app.gui.correct.layer import SKELETON, LayerStatus, Notice, SaveOutcome
from app.gui.skeleton_document import CORRECTED, GENERATED, GeneratedResolver, SkeletonFiles
from app.gui.skeleton_graph_model import SkeletonCorrectionModel, skeleton_tips
from app.io_utils import imread_unicode, imwrite_unicode

__all__ = [
    "CORRECTED",
    "GENERATED",
    "LOADED",
    "GeneratedResolver",
    "SkeletonDocument",
    "SkeletonRead",
    "SkeletonSaveResult",
]

logger = logging.getLogger(__name__)

LOADED = "loaded"  # source of a file opened with Load that is neither of the two above


@dataclass(frozen=True)
class SkeletonSaveResult:
    """What :meth:`SkeletonDocument.save` did.

    ``path`` is the correction file written (None when nothing was written) and
    ``error`` the reason of a failure. ``unchanged``: nothing to write (the skeleton is
    as saved, or is the generated one with no correction on disk); ``removed``: the
    skeleton is the generated one again, so its correction file was removed.
    """

    ok: bool
    path: str | None = None
    error: str = ""
    unchanged: bool = False
    removed: bool = False


@dataclass(frozen=True)
class SkeletonRead:
    """The start file of one image's skeleton, read and thinned ahead on a worker thread.

    ``stamps`` are the ``file_stamps()`` when the read was planned; ``path`` / ``kind`` the
    start file (None: nothing to read); ``mask`` the thinned skeleton at ``size`` and ``tips``
    its endpoints (``skeleton_tips``); ``mask`` None with a ``path``: unreadable, the open
    reads it again (and says so).
    """

    image_path: str
    size: tuple[int, int]
    stamps: tuple
    path: str | None
    kind: str | None
    save_size: tuple[int, int] | None = None
    mask: np.ndarray | None = None
    tips: np.ndarray | None = None

    @property
    def nbytes(self) -> int:
        """Bytes held (the read-ahead cache counts them)."""
        held = (self.mask, self.tips)
        return sum(int(array.nbytes) for array in held if array is not None)


class SkeletonDocument:
    """The skeleton of one image: model + which file it came from + where it saves.

    ``model`` is one object for the document's whole life: opening, Discard, Load
    and close reset it in place, so hooks set on it (``on_action``) stay connected.
    ``source`` is ``CORRECTED``, ``GENERATED``, ``LOADED`` or None (nothing was read).
    ``save_size`` is the ``(w, h)`` of the raster read, or of the correction last written
    (informational; saves are at image resolution). ``save_error`` and ``error_revision``
    describe the last failed save, which :meth:`status` reports until the next edit.
    """

    def __init__(self, resolver: GeneratedResolver | None = None) -> None:
        self.model = SkeletonCorrectionModel()
        self.image_path = ""
        self.images_base_folder: str | None = None
        self.source: str | None = None
        self.save_size: tuple[int, int] | None = None
        self.save_error = ""
        self.error_revision = -1
        self.last_load_ok = False  # the last load_file read its file
        self._resolver = resolver
        self._size = (0, 0)  # (w, h) of the image: the model's size

    def set_resolver(self, resolver: GeneratedResolver | None) -> None:
        """Inject ``image_path -> generated skeleton path | None`` (asked before
        ``skeletons/<stem>_fake.png``); it applies from the next file lookup."""
        self._resolver = resolver

    def files(self) -> SkeletonFiles:
        """The skeleton files of the open image (asks nothing yet: lookups are lazy)."""
        root = self.images_base_folder or os.path.dirname(self.image_path)
        return SkeletonFiles(self.image_path, root, self._resolver)

    # ------------------------------------------------------------------ lifecycle
    def plan_read(
        self, image_path: str, images_base_folder: str | None
    ) -> Callable[[tuple[int, int]], SkeletonRead]:
        """A reader of ``image_path``'s start file for a worker thread (``reader((w, h))``).

        Runs on the GUI thread: the files are looked up here (the resolver is asked now);
        the reader only reads, thins and finds the tips, touching no document.
        """
        root = images_base_folder or os.path.dirname(image_path)
        files = SkeletonFiles(image_path, root, self._resolver)
        path, kind = files.start_file()
        stamps = _stamps_of(files)

        def reader(size: tuple[int, int]) -> SkeletonRead:
            size = (max(1, int(size[0])), max(1, int(size[1])))
            gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE) if path else None
            if gray is None:
                return SkeletonRead(image_path, size, stamps, path, kind)
            mask = thin_raster(gray, size)
            save_size = (int(gray.shape[1]), int(gray.shape[0]))
            return SkeletonRead(
                image_path, size, stamps, path, kind, save_size, mask, skeleton_tips(mask)
            )

        return reader

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        images_base_folder: str | None,
        read: SkeletonRead | None = None,
    ) -> list[Notice]:
        """Open the skeleton of ``image_path`` (an image of ``size`` ``(w, h)``).

        The project root is ``images_base_folder``, else the image's folder. Reads the
        start file (the correction, else the generated skeleton); nothing to read opens
        an empty skeleton. Returns the notices to show. ``read`` (a :meth:`plan_read`
        reader's result) is adopted while the files are still the ones it read.
        """
        self.image_path = image_path
        self.images_base_folder = images_base_folder or os.path.dirname(image_path)
        w, h = size
        self._size = (max(1, int(w)), max(1, int(h)))
        if read is None or not self._current(read):
            return self.reopen()
        self.save_error = ""
        if read.mask is None:  # nothing to read: an empty skeleton
            self.model.set_empty(self._size)
            self.save_size, self.source = None, None
        else:
            self.model.load_thinned(read.mask, read.tips)
            self.save_size, self.source = read.save_size, read.kind
        return []

    def read_back(self, stamps: tuple) -> SkeletonRead | None:
        """The open skeleton as a :meth:`plan_read` reader would read it again: its payload
        for the read-ahead when the page leaves the image (``app.gui.correct.read_back``).

        ``stamps`` are the files' stamps at open or at the last save. None unless the
        skeleton is as saved and shows today's start file (a loaded file, or a start file it
        could not read, is read again). The mask is handed over, not copied: the next open
        replaces it here.
        """
        mask = self.model.mask
        if not self.image_path or mask is None or self.is_dirty():
            return None
        path, kind = self.files().start_file()
        if kind != self.source:
            return None
        if path is None:  # no skeleton file: an empty skeleton
            return SkeletonRead(self.image_path, self._size, tuple(stamps), None, None)
        tips = self.model.endpoint_array()  # (x, y), row-major: flat indices ascending
        flat = tips[:, 1] * mask.shape[1] + tips[:, 0]
        return SkeletonRead(
            self.image_path, self._size, tuple(stamps), path, kind, self.save_size, mask, flat
        )

    def _current(self, read: SkeletonRead) -> bool:
        """True when ``read`` was made for the open image, its size and today's files."""
        if (read.image_path, tuple(read.size)) != (self.image_path, self._size):
            return False
        files = self.files()
        if read.stamps != _stamps_of(files) or (read.path, read.kind) != files.start_file():
            return False
        return read.path is None or read.mask is not None  # unreadable: read it again

    def reopen(self) -> list[Notice]:
        """Re-read the start file (Discard): the document is clean, its history empty."""
        if not self.image_path:
            return []
        self.model.set_empty(self._size)
        self.save_size = None
        self.save_error = ""
        self.source = None
        notices: list[Notice] = []
        path, kind = self.files().start_file()
        if path and self._read(path, notices):
            self.source = kind
        return notices

    def load_file(self, path: str) -> list[Notice]:
        """Open another skeleton file for this image (the Load button); history cleared.

        The loaded skeleton is an unsaved edit while it differs from the skeleton opened or
        last saved: Save writes it as the correction, Discard (:meth:`reopen`) reads the
        start file again. ``source`` becomes ``CORRECTED`` or ``GENERATED`` when ``path`` is
        one of those files, else ``LOADED``. An unreadable file changes nothing;
        ``last_load_ok`` says which of the two happened.
        """
        self.last_load_ok = False
        if not self.image_path:
            return [Notice("Open an image first", "warn")]
        notices: list[Notice] = []
        self.last_load_ok = self._read(path, notices, saved=False)
        if self.last_load_ok:
            files = self.files()
            generated = files.generated_path()
            if _same_file(path, files.correction_path):
                self.source = CORRECTED
            elif generated and _same_file(path, generated):
                self.source = GENERATED
            else:
                self.source = LOADED
        return notices

    def _read(self, path: str, notices: list[Notice], *, saved: bool = True) -> bool:
        """Load ``path`` into the model at image size (resized, then thinned); ``saved``
        False keeps the saved baseline (Load: an unsaved edit)."""
        gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            notices.append(Notice(f"Could not read skeleton {os.path.basename(path)}", "danger"))
            return False
        self.save_size = (int(gray.shape[1]), int(gray.shape[0]))
        self.model.load_from_raster(gray, target_size=self._size, saved=saved)
        return True

    def close(self) -> None:
        """Forget the image: no skeleton, no history, nothing unsaved."""
        self.image_path = ""
        self.images_base_folder = None
        self.source = None
        self.save_size = None
        self.save_error = ""
        self.error_revision = -1
        self._size = (0, 0)
        self.model.mask = None
        self.model.clear_history()
        self.model.mark_saved()

    def is_open(self) -> bool:
        """True while an image's skeleton is open."""
        return bool(self.image_path)

    def is_dirty(self) -> bool:
        """True when the skeleton differs from the file it was opened from or saved to."""
        return bool(self.image_path) and self.model.has_unsaved_changes()

    # ------------------------------------------------------------------ save
    def save(self) -> SkeletonSaveResult:
        """Write ``skeleton_corrections/<stem>_skeleton.png`` atomically, only when the
        skeleton differs from the generated one as Correct opens it (O2: saving is not
        acceptance).

        A skeleton as saved writes nothing (``unchanged``), unless it shows a file other
        than the correction and is the generated one while a correction is on disk (an old
        no-op save). One equal to the generated skeleton writes nothing and removes the
        correction (``removed``; ``unchanged`` when there was none); an emptied skeleton
        over a non-empty generated one is written.
        The raster is written at image resolution, exactly as edited (1 px, 0/255), so
        reopening it gives back the same skeleton. Shrinking it to the generated file's
        size (``SKELETON_OUTPUT_SIZE``) broke thin lines into fragments (known issue Q1).
        A failure keeps the previous file, is recorded for :meth:`status` and is
        returned, never raised.
        """
        if not self.image_path or self.model.mask is None:
            return SkeletonSaveResult(False, error="No skeleton is open")
        files = self.files()
        out_path = files.correction_path
        if not self.is_dirty():  # a correction is never removed unread (source None)
            legacy = self.source not in (CORRECTED, None) and os.path.isfile(out_path)
            if not (legacy and self._is_generated(files)):
                return SkeletonSaveResult(True, unchanged=True)
            return self._remove_correction(out_path)
        if self._is_generated(files):
            return self._remove_correction(out_path)
        h, w = self.model.mask.shape
        rendered = self.model.render_to_size((w, h))
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            imwrite_unicode(out_path, rendered)
        except (OSError, ValueError) as exc:
            return self._failed(out_path, exc)
        self.model.mark_saved()
        self.source = CORRECTED
        self.save_size = (w, h)  # the start file is now the correction just written
        self.save_error = ""
        return SkeletonSaveResult(True, out_path)

    def _remove_correction(self, path: str) -> SkeletonSaveResult:
        """The skeleton is the generated one: no correction stays on disk."""
        existed = os.path.isfile(path)
        if existed:
            try:
                os.remove(path)
            except OSError as exc:
                return self._failed(path, exc)
        self.model.mark_saved()
        generated = self.files().generated_path()  # GENERATED only if it reads, as reopen()
        readable = generated and imread_unicode(generated, cv2.IMREAD_GRAYSCALE) is not None
        self.source = GENERATED if readable else None
        self.save_size = None
        self.save_error = ""
        return SkeletonSaveResult(True, removed=existed, unchanged=not existed)

    def _is_generated(self, files: SkeletonFiles) -> bool:
        """The skeleton is the generated one as Correct opens it."""
        return is_generated_as_opened(self.model.mask, files.generated_path(), self._size)

    def _failed(self, path: str, exc: Exception) -> SkeletonSaveResult:
        """A write or a removal of ``path`` failed: recorded for :meth:`status`."""
        logger.warning("Could not save skeleton to %s: %s", path, exc)
        self.save_error = str(exc)
        self.error_revision = self.model.revision
        return SkeletonSaveResult(False, error=self.save_error)

    def outcome(self, result: SkeletonSaveResult) -> SaveOutcome:
        """A :meth:`save` result as the workspace reports it."""
        if result.ok and result.removed:
            message = "Skeleton is the generated one again: correction removed"
            return SaveOutcome(SKELETON, ok=True, cleared=True, message=message, kind="info")
        if result.ok and result.unchanged:
            message = "Skeleton unchanged: nothing to save"
            return SaveOutcome(SKELETON, ok=True, message=message, kind="info")
        if result.ok:
            return SaveOutcome(SKELETON, True, path=result.path)
        name = os.path.basename(self.files().correction_path) if self.is_open() else "it"
        message = f"Could not save {name}: {result.error}"
        return SaveOutcome(SKELETON, False, message=message, kind="danger")

    # ------------------------------------------------------------------ state
    def status(self, *, pending_edit: bool = False) -> LayerStatus:
        """The save state today's skeleton editor showed on its chip.

        ``pending_edit``: an edit that is not in the model yet (the layer's polyline draft)
        reads as unsaved, like an edit in the model.
        """
        if not self.image_path:
            return LayerStatus("none")
        if self.save_error and self.model.revision == self.error_revision:
            return LayerStatus("error", "Not saved", self.save_error)
        if pending_edit or self.is_dirty():
            return LayerStatus("dirty")
        if self.source == CORRECTED:
            return LayerStatus("clean", "Saved", self.files().correction_path)
        if self.source == GENERATED:
            mask = self.model.mask
            empty = mask is None or not mask.any()
            label = "Generated · empty" if empty else "Generated"
            return LayerStatus("none", label, "Not corrected yet")
        if self.source == LOADED:
            return LayerStatus("none", "Loaded file")
        return LayerStatus("none", "No skeleton")

    def file_stamps(self) -> tuple:
        """The pinned file set: ``(correction, generated)``, read afresh on each call.

        Each entry is ``(path, mtime_ns, size)``, or ``(path, None)`` for a missing
        file. The generated path is the resolver's file when it finds one, else
        ``skeletons/<stem>_fake.png``. ``()`` when nothing is open. Our own save
        (the correction appears or changes) and a Generate run both change it.
        """
        if not self.image_path:
            return ()
        return _stamps_of(self.files())

    def write_stamps(self) -> tuple:
        """The file :meth:`save` writes or removes, the correction, stamped afresh (``()``
        when closed): a Generate run, which rewrites only the generated file, is no change."""
        return (_stamp(self.files().correction_path),) if self.image_path else ()

    def matches_disk(self) -> bool:
        """True when :meth:`reopen` would now open exactly the saved skeleton: the start file
        thinned at the image size (none, or an unreadable one: empty). Reads the file."""
        saved = self.model.saved_mask
        if not self.image_path or saved is None:
            return False
        path, _kind = self.files().start_file()
        return is_generated_as_opened(saved, path, self._size)  # any file, as opened


def _stamps_of(files: SkeletonFiles) -> tuple:
    """``(correction, generated)`` stamps; the generated path is the resolver's file when it
    finds one, else ``skeletons/<stem>_fake.png``."""
    generated = files.generated_path() or files.layout.skeleton_path(files.stem)
    return (_stamp(files.correction_path), _stamp(generated))


def _stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a file, ``(path, None)`` when it cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return (path, None)
    return (path, st.st_mtime_ns, st.st_size)


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

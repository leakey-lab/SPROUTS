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
the correction, atomically, at the size of the raster that was opened (usually
341x256), else ``SKELETON_OUTPUT_SIZE``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import cv2

from app.config import SKELETON_OUTPUT_SIZE
from app.gui.correct.layer import LayerStatus, Notice
from app.gui.skeleton_document import CORRECTED, GENERATED, GeneratedResolver, SkeletonFiles
from app.gui.skeleton_graph_model import SkeletonCorrectionModel
from app.io_utils import imread_unicode, imwrite_unicode

__all__ = [
    "CORRECTED",
    "GENERATED",
    "LOADED",
    "GeneratedResolver",
    "SkeletonDocument",
    "SkeletonSaveResult",
]

logger = logging.getLogger(__name__)

LOADED = "loaded"  # source of a file opened with Load that is neither of the two above


@dataclass(frozen=True)
class SkeletonSaveResult:
    """What :meth:`SkeletonDocument.save` did.

    ``path`` is the correction file written (None when nothing was written) and
    ``error`` the reason of a failure.
    """

    ok: bool
    path: str | None = None
    error: str = ""


class SkeletonDocument:
    """The skeleton of one image: model + which file it came from + where it saves.

    ``model`` is one object for the document's whole life: opening, Discard, Load
    and close reset it in place, so hooks set on it (``on_action``) stay connected.
    ``source`` is ``CORRECTED``, ``GENERATED``, ``LOADED`` or None (nothing was read).
    ``save_size`` is the ``(w, h)`` of the raster read, the size the correction is
    saved at. ``save_error`` and ``error_revision`` describe the last failed save,
    which :meth:`status` reports until the next edit.
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
    def open(
        self, image_path: str, size: tuple[int, int], images_base_folder: str | None
    ) -> list[Notice]:
        """Open the skeleton of ``image_path`` (an image of ``size`` ``(w, h)``).

        The project root is ``images_base_folder``, else the image's folder. Reads the
        start file (the correction, else the generated skeleton); nothing to read opens
        an empty skeleton. Returns the notices to show.
        """
        self.image_path = image_path
        self.images_base_folder = images_base_folder or os.path.dirname(image_path)
        w, h = size
        self._size = (max(1, int(w)), max(1, int(h)))
        return self.reopen()

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

        ``source`` becomes ``CORRECTED`` or ``GENERATED`` when ``path`` is one of those
        files, else ``LOADED``. An unreadable file changes nothing; ``last_load_ok`` says
        which of the two happened.
        """
        self.last_load_ok = False
        if not self.image_path:
            return [Notice("Open an image first", "warn")]
        notices: list[Notice] = []
        self.last_load_ok = self._read(path, notices)
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

    def _read(self, path: str, notices: list[Notice]) -> bool:
        """Load ``path`` into the model at image size (resized nearest, then thinned)."""
        gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            notices.append(Notice(f"Could not read skeleton {os.path.basename(path)}", "danger"))
            return False
        self.save_size = (int(gray.shape[1]), int(gray.shape[0]))
        self.model.load_from_raster(gray, target_size=self._size)
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
        """Write ``skeleton_corrections/<stem>_skeleton.png`` atomically.

        The raster is the model rendered at ``save_size`` (else ``SKELETON_OUTPUT_SIZE``)
        with nearest neighbour and thinned again. A failure keeps the previous file,
        is recorded for :meth:`status` and is returned, never raised.
        """
        if not self.image_path or self.model.mask is None:
            return SkeletonSaveResult(False, error="No skeleton is open")
        out_path = self.files().correction_path
        rendered = self.model.render_to_size(self.save_size or SKELETON_OUTPUT_SIZE)
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            imwrite_unicode(out_path, rendered)
        except (OSError, ValueError) as exc:
            logger.warning("Could not save skeleton to %s: %s", out_path, exc)
            self.save_error = str(exc)
            self.error_revision = self.model.revision
            return SkeletonSaveResult(False, error=self.save_error)
        self.model.mark_saved()
        self.source = CORRECTED
        self.save_error = ""
        return SkeletonSaveResult(True, out_path)

    # ------------------------------------------------------------------ state
    def status(self) -> LayerStatus:
        """The save state today's skeleton editor showed on its chip."""
        if not self.image_path:
            return LayerStatus("none")
        if self.save_error and self.model.revision == self.error_revision:
            return LayerStatus("error", "Not saved", self.save_error)
        if self.is_dirty():
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
        files = self.files()
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

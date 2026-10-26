"""The class-label document of the Correct workspace (Qt-free at import).

:class:`ClassesDocument` is the class labels of one image (``gt/<stem>.png``) and the
registry ``gt/classes.json``, through :mod:`app.gt` only (never ``mask/`` or ``skeletons/``):
the id plane with its patch history, the "checked" ticks, the pixels per id and all three as
last saved; the former Trace GT editor's storage half without Qt (colours: ``classes_paint``; the
files read: ``classes_files``).

The rules (the GT goldens pin them): reviewed (what Save writes) = the hand ticks + the
classes with pixels, whose boxes are locked; a save makes the reviewed set the ticks. Dirty
= the ticks or a class count differ from the saved ones, else a pixel does, or the open
remapped ids (until saved or discarded; a lossy remap asks until saved). Save always writes
the file, ``gt/classes.json`` first when missing (its failure keeps the file). An invalid
registry makes it read-only (its status says so; the open sends no notice: the layer's first
refused edit does, once per image); a file that cannot be read or has another size opens empty, is
not shown and is kept unless a save replaces it. The owner changes the plane only through
``history``, then calls ``count_patch`` and ``touch``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from app import gt
from app.config import GT_BACKGROUND_ID, GT_CLASSES_FILENAME, GT_DIRNAME
from app.gt import Registry, Tracing
from app.gui.correct.classes_files import ClassesRead, labels_files, read_classes
from app.gui.correct.classes_paint import N_IDS, canvas_ids, class_lut, histogram
from app.gui.correct.layer import LayerStatus, Notice, project_label
from app.gui.patch_history import Patch, PatchHistory, Rect
from app.io_utils import file_stamp

logger = logging.getLogger(__name__)

# ClassesRead lives in classes_files; the document's callers import it here.
__all__ = ["DEFAULT_BRUSH_SIZE", "ClassesDocument", "ClassesRead", "ClassesSaveResult"]

DEFAULT_BRUSH_SIZE = 8  # a class's brush until it is sized (the raster layers' default)
REGISTRY_LABEL = f"{GT_DIRNAME}/{GT_CLASSES_FILENAME}"
_KEPT = "they are not shown and are kept unless you save over them"
_NOT_SHOWN = {  # ClassesRead.error -> the open notice and the status tooltip
    "size": f"Saved class labels do not match the image size: {_KEPT}",
    "unreadable": f"Saved class labels could not be read: {_KEPT}",
}


def _same_plane(plane: np.ndarray, saved: np.ndarray | None) -> bool:
    """True when ``saved`` holds ``plane``'s pixels: one ``cv2.norm`` pass, no temporary
    array (0.05 ms at 2048 x 1536)."""
    if saved is None or saved.shape != plane.shape:
        return False
    return plane.size == 0 or cv2.norm(plane, saved, cv2.NORM_INF) == 0


def _default_key(registry: Registry) -> str:
    return (registry.labelled() or registry.active())[0].key


@dataclass(frozen=True)
class ClassesSaveResult:
    """What a save did: ``path`` written (or aimed at; None: nothing tried), ``error``."""

    ok: bool
    path: str | None = None
    error: str = ""


class ClassesDocument:
    """The labels of one image: ``history.plane`` (None while closed), ``registry`` and
    ``registry_error`` ("" when valid), the ``active_key`` the brush paints, the ``gt_path``
    a save writes and the lossy remap ``warnings`` of the open, until saved."""

    noun = "class labels"
    LOCKED_NOTICE = (
        "Editing is off: gt/classes.json is invalid ({err}). Fix the file, then reopen the image."
    )

    def __init__(self, *, on_history_change: Callable[[], None] | None = None) -> None:
        self.history = PatchHistory(on_change=on_history_change)
        self.registry: Registry = gt.default_registry()
        self.registry_error = ""
        self.active_key = _default_key(self.registry)
        self.image_path = self.gt_path = ""
        self.has_saved_file = False  # a labels file holds the saved state
        self.warnings: tuple[str, ...] = ()
        self._lut = class_lut(self.registry)
        self._size = (0, 0)
        self._root = self._project_dir = self._registry_path = ""
        self._brush_sizes: dict[str, int] = {}
        self._ticked: set[str] = set()
        self._counts = np.zeros(N_IDS, np.int64)
        self._saved_ticked: frozenset[str] = frozenset()
        self._saved_counts = self._counts.copy()
        self._baseline: np.ndarray | None = None
        self._remapped = False  # the open remapped the file's ids: they differ on disk
        self._opened_unsaved = False  # that remap is unsaved (until saved or discarded)
        self._not_loaded = ""  # ClassesRead.error of the open file, until a save
        self._revision = 0
        self._same, self._same_revision = True, 0  # the last plane compare and its revision
        self._save_error, self._error_revision = "", -1

    # ------------------------------------------------------------------ state
    @property
    def revision(self) -> int:
        """+1 per :meth:`touch` (and on open, revert, close and a changed tick)."""
        return self._revision

    def is_open(self) -> bool:
        return self.history.plane is not None

    def touch(self) -> None:
        """The plane changed: a new revision (the next :meth:`is_dirty` compares again)."""
        self._revision += 1

    def is_dirty(self) -> bool:
        """True while a save would write something new (see the module docstring)."""
        plane = self.history.plane
        if plane is None:
            return False
        if self._opened_unsaved or self._ticked != self._saved_ticked:
            return True
        if not np.array_equal(self._counts, self._saved_counts):
            return True  # other ids: no pixel compare needed
        if self._same_revision != self._revision:
            self._same, self._same_revision = _same_plane(plane, self._baseline), self._revision
        return not self._same

    def is_read_only(self) -> bool:
        """True while ``gt/classes.json`` is invalid: nothing can be edited or saved."""
        return bool(self.registry_error)

    def lossy_remap(self) -> bool:
        """True from an open that sent unknown classes to Ignore until a save writes it."""
        return bool(self.warnings)

    def unloaded_file(self) -> bool:
        """True while the labels file was found but not loaded and nothing was written."""
        return bool(self._not_loaded)

    def status(self) -> LayerStatus:
        """Read-only > not saved (to the next edit) > unsaved > not shown > GT > none."""
        if not self.is_open():
            return LayerStatus("none")
        if self.registry_error:
            tip = f"{REGISTRY_LABEL} is invalid: {self.registry_error}"
            return LayerStatus("error", "Read-only", tip, local=True)  # the chip: while edited
        if self._save_error and self._error_revision == self._revision:
            return LayerStatus("error", "Not saved", self._save_error)
        if self.is_dirty():
            if not self.warnings:
                return LayerStatus("dirty")
            tip = "Unknown classes were mapped to Ignore:\n" + "\n".join(self.warnings)
            return LayerStatus("dirty", "Unsaved · remapped", tip)
        if self._not_loaded:
            return LayerStatus("error", "Class labels not shown", _NOT_SHOWN[self._not_loaded])
        if not self.has_saved_file:
            return LayerStatus("none", "No class labels")
        labelled = {c.key for c in self.registry.labelled()}
        label = f"GT · {len(labelled & self.reviewed())}/{len(labelled)} checked"
        return LayerStatus("clean", label, project_label(self.gt_path, self._root))

    # ------------------------------------------------------------------ classes
    def lut(self) -> np.ndarray:
        """``(256, 4)`` B, G, R, A of each id in the registry's colours (0 transparent)."""
        return self._lut

    def ids_from_argb(self, argb: np.ndarray, rect: Rect) -> np.ndarray:
        """Canvas box ``rect``'s ids (``canvas_ids`` with the committed plane's box)."""
        plane = self.history.plane
        if plane is None:
            return canvas_ids(argb, self.registry)
        x, y, w, h = rect
        return canvas_ids(argb, self.registry, plane[y : y + h, x : x + w])

    def fill_source(self, plane: np.ndarray) -> tuple[np.ndarray, int]:
        """Outlines of the active class only (another class's outline never counts)."""
        class_id = self.registry.by_key(self.active_key).id
        return (plane == class_id).astype(np.uint8) * 255, class_id

    def count_patch(self, patch: Patch, forward: bool) -> None:
        """Recount ``patch``'s box only (applied, or undone when not ``forward``)."""
        if patch.is_empty or self.history.plane is None:
            return
        old = patch.before() if forward else patch.after()
        self._counts += histogram(self.history.plane[patch.slices]) - histogram(old)

    def set_active_class(self, key: str) -> bool:
        """Paint with class ``key``; False (nothing changes) for an unknown or retired key."""
        paintable = any(c.key == key for c in self.registry.active())
        if paintable:
            self.active_key = key
        return paintable

    def brush_size(self, key: str) -> int:
        """Class ``key``'s brush size (each class keeps its own)."""
        return self._brush_sizes.get(key, DEFAULT_BRUSH_SIZE)

    def set_brush_size(self, key: str, px: int) -> None:
        self._brush_sizes[key] = int(px)

    def counts(self) -> dict[str, int]:
        """Pixels per id with any, by key ("background": 0; an unowned id: its number)."""
        names = {c.id: c.key for c in self.registry.classes}
        names[GT_BACKGROUND_ID] = "background"
        return {names.get(i, str(i)): int(n) for i, n in enumerate(self._counts) if n}

    def present_keys(self) -> set[str]:
        """The classes with pixels (retired included), Ignore excluded."""
        counts = self._counts
        return {c.key for c in self.registry.classes if not c.is_ignore and counts[c.id] > 0}

    def ticked(self) -> frozenset[str]:
        """The classes ticked "checked" by hand (or by a save)."""
        return frozenset(self._ticked)

    def reviewed(self) -> frozenset[str]:
        """What Save writes as checked: the ticks and the classes with pixels."""
        return frozenset(self._ticked | self.present_keys())

    def set_ticked(self, key: str, on: bool) -> bool:
        """Tick ``key`` by hand; False (no change): it has pixels, Ignore, unknown, locked."""
        known = {c.key for c in self.registry.classes if not c.is_ignore}
        if not self.is_open() or self.registry_error or key not in known:
            return False
        if key in self.present_keys():
            return False
        if on != (key in self._ticked):
            (self._ticked.add if on else self._ticked.discard)(key)
            self.touch()
        return True

    def apply_registry(self, registry: Registry) -> None:
        """The class editor saved ``registry``: new colours, same ids (not read-only)."""
        if not self.registry_error:
            self._use_registry(registry)

    # ------------------------------------------------------------------ files
    def file_stamps(self) -> tuple:
        """``gt/<stem>.png`` and ``gt/classes.json``, stamped or absent (``()`` closed)."""
        if not self.image_path:
            return ()
        return (file_stamp(self.gt_path), file_stamp(self._registry_path))

    def write_stamps(self) -> tuple:
        """``DiskGuardedLayer``: ``gt/<stem>.png``, the one file a save replaces."""
        return (file_stamp(self.gt_path),) if self.image_path else ()

    def matches_disk(self) -> bool:
        """True when :meth:`open` would now open the saved state (it reads the files)."""
        if self._baseline is None:
            return False
        read = self.read(self.image_path, self._size)
        remapped = read.changed and not read.registry_error
        found = (read.error, read.registry_error, read.registry, remapped)
        if found != (self._not_loaded, self.registry_error, self.registry, self._remapped):
            return False
        tracing = read.tracing
        plane = np.zeros_like(self._baseline) if tracing is None else tracing.ids
        ticks = frozenset() if tracing is None else tracing.reviewed
        return ticks == self._saved_ticked and _same_plane(plane, self._baseline)

    def read_back(self, stamps: tuple) -> ClassesRead | None:
        """The labels as :meth:`read` would read them (the plane not copied), with the
        session's ``stamps``, for the read-ahead; None unless they are as on disk."""
        if not self.is_open() or self.is_dirty() or self.registry_error or self._not_loaded:
            return None
        if self._remapped or (not self.has_saved_file and stamps and stamps[0][1:] != (None,)):
            return None  # the file holds other ids, or one appeared: read it again
        tracing = Tracing(self.history.plane, self._saved_ticked) if self.has_saved_file else None
        common = (self.image_path, self._size, tuple(stamps), self.registry, "")
        return ClassesRead(*common, tracing, (), False, "")

    # ------------------------------------------------------------------ lifecycle
    @staticmethod
    def read(image_path: str, size: tuple[int, int]) -> ClassesRead:
        """Read and align what :meth:`open` reads for ``image_path`` at ``(w, h)`` (any
        thread: :func:`~app.gui.correct.classes_files.read_classes`)."""
        return read_classes(image_path, size)

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        read: ClassesRead | None = None,
        *,
        root: str | None = None,
    ) -> list[Notice]:
        """Open the labels at ``size`` ``(w, h)``, clean unless ids were remapped; the notices.
        ``read`` (done ahead) is used while its stamps hold; ``root`` roots the file labels."""
        layout, labels, registry_file = labels_files(image_path)
        current = (image_path, tuple(size), (file_stamp(labels), file_stamp(registry_file)))
        if read is None or (read.image_path, tuple(read.size), read.stamps) != current:
            read = self.read(image_path, size)
        tracing, (width, height) = read.tracing, size
        self.image_path, self.gt_path, self._registry_path = image_path, labels, registry_file
        self._size, self._project_dir = (width, height), layout.root
        self._root = root or layout.root
        self.registry_error = read.registry_error
        self._use_registry(read.registry)
        self.has_saved_file, self._not_loaded = tracing is not None, read.error
        self._remapped = read.changed and not read.registry_error  # read-only: nothing to save
        self.warnings = read.warnings if self._remapped else ()
        self._ticked = set() if tracing is None else set(tracing.reviewed)
        plane = np.zeros((height, width), np.uint8) if tracing is None else tracing.ids
        self.history.reset(np.ascontiguousarray(plane, dtype=np.uint8))
        self._counts = histogram(self.history.plane)
        self._restart()
        self._opened_unsaved = self._remapped
        notices = [Notice(_NOT_SHOWN[read.error], "warn")] if read.error else []
        name = os.path.basename(labels)
        return notices + [Notice(f"Class labels {name}: {w}", "warn") for w in read.warnings]

    def close(self) -> None:
        """Forget the document: no plane, no history, no file (the registry stays)."""
        self.image_path = self.gt_path = self._registry_path = self._root = ""
        self.has_saved_file, self.registry_error, self.warnings = False, "", ()
        self._not_loaded, self._remapped, self._ticked = "", False, set()
        self.history.reset(None)
        self._counts = np.zeros(N_IDS, np.int64)
        self._restart()

    def revert(self) -> None:
        """Discard: the ids and the ticks go back to the saved state; the history is cleared."""
        plane, baseline = self.history.plane, self._baseline
        if plane is None or baseline is None:
            return
        if plane.shape == baseline.shape:
            np.copyto(plane, baseline)  # in place: holders of the plane stay valid
        else:
            plane = baseline.copy()
        self.history.reset(plane)
        self._ticked, self._counts = set(self._saved_ticked), self._saved_counts.copy()
        self._restart()

    def save(self) -> ClassesSaveResult:
        """Write the file (also empty), ``gt/classes.json`` first when missing; a failure keeps
        the old files, stays dirty and is the status until the next edit."""
        plane = self.history.plane
        if plane is None:
            return ClassesSaveResult(False, None, "No class labels are open")
        if self.registry_error:
            return ClassesSaveResult(False, None, self.registry_error)
        self._counts = histogram(plane)  # a fresh count: the classes with pixels
        reviewed = self.reviewed()
        try:
            if not os.path.exists(self._registry_path):  # first: its failure keeps the file
                gt.save_registry(self._project_dir, self.registry)
            gt.write_tracing(self.gt_path, Tracing(plane, reviewed), self.registry)
        except (OSError, gt.GtFileError) as exc:
            logger.warning("Could not save the class labels to %s: %s", self.gt_path, exc)
            self._save_error, self._error_revision = str(exc), self._revision
            return ClassesSaveResult(False, self.gt_path, str(exc))
        self._ticked, self.has_saved_file, self.warnings = set(reviewed), True, ()
        self._not_loaded, self._remapped, self._opened_unsaved = "", False, False
        self._save_error = ""
        self._remember()
        return ClassesSaveResult(True, self.gt_path)

    # ------------------------------------------------------------------ internals
    def _use_registry(self, registry: Registry) -> None:
        """Show ``registry``'s colours; a gone (or retired) active class falls back."""
        self.registry, self._lut = registry, class_lut(registry)
        if not self.set_active_class(self.active_key):
            self.active_key = _default_key(registry)

    def _restart(self) -> None:
        """A new plane was adopted (open, revert, close): a new revision, the saved state."""
        self.touch()
        self._save_error, self._opened_unsaved = "", False
        self._remember()

    def _remember(self) -> None:
        """The plane, the ticks and the counts now are the saved state (clean, no compare)."""
        plane = self.history.plane
        if plane is None:
            self._baseline = None
        elif self._baseline is not None and self._baseline.shape == plane.shape:
            np.copyto(self._baseline, plane)
        else:
            self._baseline = plane.copy()
        self._saved_ticked, self._saved_counts = frozenset(self._ticked), self._counts.copy()
        self._same, self._same_revision = True, self._revision

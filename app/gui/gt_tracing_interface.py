"""
Ground-truth (GT) tracing editor: the "Trace GT" stage.

Paints hand-traced, multi-class ground truth for training segmentation models
(see ``docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`` §3).
It shares :class:`RasterEditorBase` with the mask editor (canvas, tool rail,
dock, zoom cluster, brush preview, hotkeys, patch undo and dirty tracking) as
a sibling, not a subclass, so it has no mask storage at all:

- the document is a class-id plane; the canvas shows each pixel opaque in its
  class colour (or transparent = background). Strokes are non-antialiased with
  ``CompositionMode_Source``, so colours stay exact and map back to class ids;
- load/save go through ``app.gt`` (``gt/<stem>.png``, class ids in a palette
  PNG, plus ``gt/classes.json``). The GT editor is independent of every ML
  model in the app: it never reads or writes ``mask/`` or ``skeletons/``.

Keys, on top of the base editor's: 1-9 select the n-th class, 0 selects
Ignore, PgDn / PgUp save (if there are unsaved edits, without a prompt) and
ask for the next / previous image (``navigate_requested``). A lossy class
remap on load (unknown classes became Ignore) is never saved silently: PgDn
then asks like any other unsaved change.

Unsaved edits are never dropped behind the user's back: when a save fails,
``maybe_save_changes`` asks again (Discard / Keep editing) and returns False
on "Keep editing", so the caller (next image, stage switch, window close,
open folder) stays on this image. While ``gt/classes.json`` is invalid,
saving is impossible, so every edit is refused up front.
"""

from __future__ import annotations

import logging
import os

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut

from app.config import GT_BACKGROUND_ID, GT_CLASSES_FILENAME, GT_DIRNAME, GT_UNKNOWN_COLOR
from app.gt import (
    GtFileError,
    Registry,
    RegistryError,
    Tracing,
    align_to_registry,
    default_registry,
    empty_tracing,
    gt_path,
    load_registry,
    present_keys,
    read_tracing,
    registry_path,
    save_registry,
    write_tracing,
)

from . import shortcuts
from .class_panel import ClassPanel
from .mask_drawing_tools import pixmap_to_argb_array
from .mask_tracing_interface import RasterEditorBase
from .patch_history import Patch

logger = logging.getLogger(__name__)

_REGISTRY_NAME = f"{GT_DIRNAME}/{GT_CLASSES_FILENAME}"
_N_IDS = 256  # class ids are uint8


def _bgra(color: str) -> tuple[int, int, int, int]:
    """``#rrggbb`` -> opaque (B, G, R, A), the byte order of ``pixmap_to_argb_array``."""
    c = QColor(color)
    return c.blue(), c.green(), c.red(), 255


def _packed(bgra) -> int:
    """Pack a BGRA colour as ``B | G << 8 | R << 16 | A << 24`` (one canvas pixel word)."""
    return int(bgra[0]) | (int(bgra[1]) << 8) | (int(bgra[2]) << 16) | (int(bgra[3]) << 24)


class GtTracingInterface(RasterEditorBase):
    """Multi-class GT editor: one class per pixel, saved to ``gt/<stem>.png``.

    Its plane is the class-id image itself (0 = background), so undo patches,
    saving and class presence all work on ids; the canvas only shows them.
    """

    gt_saved = pyqtSignal(str)  # image path
    navigate_requested = pyqtSignal(int)  # +1 next image, -1 previous

    EDITOR_NOUN = "GT"
    SHORTCUT_SCOPE = shortcuts.TRACE_GT
    CLEARED_NOTICE = "Tracing cleared. Ctrl+Z to undo."
    CLEAR_TIP = "Clear the tracing. Undo with Ctrl+Z; Save writes an empty tracing."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry: Registry = default_registry()
        self._registry_error: str | None = None
        self._project_dir: str | None = None
        self._has_saved_mask = False  # a GT file was loaded or saved for this image
        self._reviewed: set[str] = set()  # classes ticked "checked" by hand
        self._present: set[str] = set()  # classes with pixels on the canvas
        # Pixels per class id, kept up to date from each edit's patch (PE-13).
        self._counts = np.zeros(_N_IDS, np.int64)
        self._brush_sizes: dict[str, int] = {}
        self._active_key: str | None = None
        # Warnings of a lossy remap on load (unknown classes -> Ignore); cleared on save.
        self._remap_warnings: list[str] = []
        # A GT file exists but could not be used (corrupt / wrong size).
        self._gt_unreadable = False

        self._retitle_controls()

        self.class_panel = ClassPanel(self)
        self.class_panel.set_registry(self.registry)
        self.class_panel.active_changed.connect(self.set_active_class)
        self.class_panel.reviewed_changed.connect(self._on_reviewed_changed)
        self.class_panel.edit_requested.connect(self.edit_classes)
        # Panel clicks never keep keyboard focus (Space must pan, not tick a box).
        self.class_panel.active_changed.connect(self._focus_canvas)
        self.class_panel.reviewed_changed.connect(self._focus_canvas)
        self.overlay_layout.register(side_panel=self.class_panel)

        self.set_active_class(self._default_key())
        self._refresh_panel()
        self._update_status_chip()

    # ================================================================== UI
    def _retitle_controls(self):
        """Tooltips that name the tracing rather than the mask."""
        self.clear_icon_button.setToolTip("Clear tracing (undoable)")
        self.fill_button.setToolTip(self._tip("Fill a closed outline of the active class", "fill"))
        self.mask_visible_button.setToolTip(self._tip("Show tracing", "toggle_overlay"))
        self.opacity_container.setToolTip("Tracing opacity")

    def _install_shortcuts(self):
        """Base keys, 1-9 / 0 for the classes, and PgDn / PgUp (save, then next image)."""
        super()._install_shortcuts()
        numbers = (*range(1, 10), 0)
        installed = shortcuts.install_shortcuts(
            self,
            self.SHORTCUT_SCOPE,
            {"select_class": [lambda n=n: self._select_by_number(n) for n in numbers]},
        )
        self._shortcuts += installed["select_class"]
        # The app-wide paging keys; the GT editor binds them itself so it can save first.
        for key, delta in (("PgDown", 1), ("PgUp", -1)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=delta: self._navigate(d))
            self._shortcuts.append(sc)

    def _style_brush_preview(self):
        """Base ring styling, with the ring in the active class colour."""
        super()._style_brush_preview()
        erasing = hasattr(self, "eraser_button") and self.eraser_button.isChecked()
        if not erasing and getattr(self, "_active_key", None):
            self.brush_preview.set_style(self.brush_color)

    def _status(self) -> tuple[str, str | None, str]:
        """``● Saved · n/m checked`` / ``● Unsaved`` / ``● GT unreadable`` / ``● No GT``.

        ``● Unsaved · remapped`` after a lossy class remap on load, until saved.
        """
        registry = getattr(self, "registry", None)
        if registry is None:
            return super()._status()
        if self.dirty:
            warnings = self._remap_warnings
            if warnings:
                tooltip = "Unknown classes were mapped to Ignore:\n" + "\n".join(warnings)
                return "dirty", "Unsaved · remapped", tooltip
            return "dirty", None, ""
        if self._has_saved_mask:
            labelled = {c.key for c in registry.labelled()}
            checked = len(labelled & (self._reviewed | self._present))
            return "clean", f"Saved · {checked}/{len(labelled)} checked", ""
        if self._gt_unreadable:
            tooltip = "The GT file could not be used; it is left as is unless you save."
            return "error", "GT unreadable", tooltip
        return "none", "No GT", ""

    # ============================================================= classes
    def _default_key(self) -> str:
        return (self.registry.labelled() or self.registry.active())[0].key

    def set_active_class(self, key: str) -> None:
        """Paint with class ``key``; each class keeps its own brush size."""
        try:
            cls = self.registry.by_key(key)
        except KeyError:
            return
        if cls.retired:
            return
        if self._active_key is not None:
            self._brush_sizes[self._active_key] = self.size_slider.value()
        self._active_key = key
        self.brush_color = QColor(cls.color)
        self.size_slider.setValue(self._brush_sizes.get(key, self.DEFAULT_BRUSH))
        self.class_panel.set_active(key)
        self._style_brush_preview()

    def _select_by_number(self, n: int):
        key = self.class_panel.key_for_shortcut(n)
        if key:
            self.set_active_class(key)

    def _set_registry(self, registry: Registry):
        self.registry = registry
        self.class_panel.set_registry(registry)
        active = {c.key for c in registry.active()}
        self.set_active_class(
            self._active_key if self._active_key in active else self._default_key()
        )
        # The row count changed: resize and re-centre the panel now, and again
        # once the new rows are shown (a visible parent shows them on the next
        # event-loop pass, and until then the layout does not count them).
        self.class_panel.reposition()
        QTimer.singleShot(0, self.class_panel.reposition)

    def _refresh_panel(self):
        """Checked boxes = ticked by hand or has pixels; boxes of painted classes are locked."""
        self.class_panel.set_reviewed(set(self._reviewed), set(self._present))

    def _focus_canvas(self, *_args):
        """Give keyboard focus back to the canvas after a class-panel click."""
        self.graphics_view.setFocus()

    def _on_reviewed_changed(self, key: str, checked: bool):
        if not self.current_image_path or key in self._present:
            # Nothing open, or the class has pixels (its box is locked checked).
            self._refresh_panel()
            return
        if self._edit_blocked():
            self._refresh_panel()  # undo the tick: it could never be saved
            return
        was = key in self._reviewed
        if checked:
            self._reviewed.add(key)
        else:
            self._reviewed.discard(key)
        if was != checked:
            self.mark_dirty()
        self._refresh_panel()

    def edit_classes(self) -> None:
        """Open the class editor; save the new registry and recolour the canvas."""
        if self._registry_error:
            self.notice.emit(
                f"Fix {_REGISTRY_NAME} and reopen the image before editing classes: "
                f"{self._registry_error}",
                "danger",
            )
            return
        if not self._project_dir:
            self.notice.emit("Open an image first; classes are saved with its folder.", "info")
            return
        from .class_editor_dialog import ClassEditorDialog

        dialog = ClassEditorDialog(self.registry, self)
        try:
            dialog.exec()
            new_registry = dialog.result_registry()
        finally:
            dialog.deleteLater()  # parented to this long-lived editor: free it
        self._focus_canvas()
        if new_registry is None or self._registry_error:
            return
        try:
            save_registry(self._project_dir, new_registry)
        except OSError as exc:
            self.notice.emit(f"Could not save {_REGISTRY_NAME}: {exc}", "danger")
            return
        self._set_registry(new_registry)
        self._recolour()
        self._present_from_counts()
        self._refresh_panel()
        self._update_status_chip()
        self.notice.emit("Classes updated", "success")

    def _recolour(self):
        """Re-render the canvas in the registry's colours.

        The plane and the undo history hold class ids, so neither changes.
        """
        if self.edits.plane is None:
            return
        self.mask_pixmap = self._plane_to_pixmap()
        self.refresh_mask_display()

    # ======================================================= ids <-> canvas
    @staticmethod
    def _lut(registry: Registry) -> np.ndarray:
        """Class id -> BGRA. Background is transparent; ids no class owns are grey."""
        lut = np.zeros((_N_IDS, 4), np.uint8)
        lut[:] = _bgra(GT_UNKNOWN_COLOR)
        for c in registry.classes:  # retired classes keep their own colour
            lut[c.id] = _bgra(c.color)
        lut[GT_BACKGROUND_ID] = 0
        return lut

    def _plane_lut(self) -> np.ndarray:
        return self._lut(self.registry)

    @staticmethod
    def _ids_from_argb(
        arr: np.ndarray, registry: Registry, fallback: np.ndarray | None = None
    ) -> np.ndarray:
        """Canvas pixels -> ``(h, w)`` uint8 class ids by exact colour match.

        Colours are unique across the registry (retired included), so each
        class colour maps back to one id. Transparent is background; a colour
        no class owns keeps its ``fallback`` id (the committed plane).
        """
        arr = np.ascontiguousarray(arr)
        # Zero-copy view: one little-endian word per pixel, B | G<<8 | R<<16 | A<<24.
        packed = arr.view("<u4")[:, :, 0]
        ids = np.zeros(packed.shape, np.uint8)
        known = arr[:, :, 3] == 0  # transparent = background
        for c in registry.classes:
            hit = packed == _packed(_bgra(c.color))
            ids[hit] = c.id
            known |= hit
        other = ~known
        if fallback is not None and other.any():
            ids[other] = fallback[other]
        return ids

    def _argb_to_plane(self, argb: np.ndarray, rect) -> np.ndarray:
        x, y, w, h = rect
        return self._ids_from_argb(argb, self.registry, self.edits.plane[y : y + h, x : x + w])

    def _pixmap_to_ids(self, pixmap: QPixmap | None = None, registry: Registry | None = None):
        """The canvas read back as class ids (the committed plane fills unknown colours)."""
        pixmap = self.mask_pixmap if pixmap is None else pixmap
        arr = pixmap_to_argb_array(pixmap)
        plane = self.edits.plane
        fallback = plane if plane is not None and plane.shape == arr.shape[:2] else None
        return self._ids_from_argb(arr, registry or self.registry, fallback)

    def _fill_source(self, plane: np.ndarray) -> tuple[np.ndarray, int]:
        """Outlines of the *active class* only: another class's outline never counts."""
        class_id = self.registry.by_key(self._active_key).id
        return (plane == class_id).astype(np.uint8) * 255, class_id

    # ---------------------------------------------------- class presence
    def _refresh_present(self):
        """Recount every class from the plane (on load)."""
        plane = self.edits.plane
        if plane is None:
            self._counts = np.zeros(_N_IDS, np.int64)
        else:
            self._counts = np.bincount(plane.ravel(), minlength=_N_IDS).astype(np.int64)
        self._present_from_counts()

    def _present_from_counts(self):
        counts = self._counts
        self._present = {
            c.key for c in self.registry.classes if not c.is_ignore and counts[c.id] > 0
        }

    def _on_patch(self, patch: Patch, forward: bool) -> None:
        """Update the class counts from the edited box only (no full-canvas scan)."""
        if patch.is_empty:
            return
        old, new = (patch.before(), patch.after()) if forward else (patch.after(), patch.before())
        self._counts -= np.bincount(old.ravel(), minlength=_N_IDS)
        self._counts += np.bincount(new.ravel(), minlength=_N_IDS)
        self._present_from_counts()

    # ============================================================= editing
    def _registry_problem(self, prefix: str) -> str:
        return (
            f"{prefix}: {_REGISTRY_NAME} is invalid ({self._registry_error}). "
            "Fix the file, then reopen the image."
        )

    def _edit_blocked(self) -> bool:
        """True, with a danger notice, while an invalid registry makes saving impossible.

        Edits made then could never be saved, so they are refused up front
        instead of being lost later.
        """
        if not self._registry_error:
            return False
        self.notice.emit(self._registry_problem("Editing is off"), "danger")
        return True

    def _after_stroke(self):
        """An action changed the canvas: show which classes are present now."""
        self._refresh_panel()
        self._update_status_chip()

    def _navigate(self, delta: int):
        """PgDn / PgUp: save pending edits, then ask for the next image.

        Saves without a prompt, except after a lossy class remap on load
        (unknown classes became Ignore): that is never written silently, so
        the normal unsaved-changes prompt runs instead.
        """
        if self.has_unsaved_changes():
            if self._remap_warnings:
                if not self.maybe_save_changes():
                    return  # the user kept editing
            elif not self.save_mask():
                return  # stay put so the failure notice is not lost
        self.navigate_requested.emit(delta)

    def navigate(self, delta: int) -> None:
        """Public entry for the app's paging keys: save pending edits, then move ``delta``."""
        self._navigate(delta)

    # ========================================================= load / save
    def reset(self) -> bool:
        """A new folder was opened: prompt, then drop the image and the registry.

        Keeps everything when the user chose to keep editing a tracing that
        could not be saved (``MainWindow.open_path`` cancels the open then).
        """
        if not super().reset():
            return False
        self._reviewed = set()
        self._counts = np.zeros(_N_IDS, np.int64)
        self._present = set()
        self._has_saved_mask = False
        self._remap_warnings = []
        self._gt_unreadable = False
        self._project_dir = None  # reload gt/classes.json with the next image
        self._refresh_panel()
        self._update_status_chip()
        return True

    def _load_registry(self, project_dir: str):
        self._project_dir = project_dir
        try:
            registry = load_registry(project_dir)
            self._registry_error = None
        except RegistryError as exc:
            registry = default_registry()
            self._registry_error = str(exc)
            self.notice.emit(
                f"{_REGISTRY_NAME} is invalid ({exc}). Saving GT is off: fix the file, "
                "then reopen the image.",
                "danger",
            )
        self._set_registry(registry)

    def _read_tracing(self, image_path: str, height: int, width: int):
        """Return ``(tracing, warnings, changed_by_alignment, status)``; never raises.

        ``status`` is ``"loaded"``, ``"missing"`` (no GT file yet) or
        ``"unreadable"`` (a GT file exists but is corrupt or the wrong size;
        the canvas opens empty and the file is left as is unless you save).
        """
        path = gt_path(image_path)
        name = os.path.basename(path)
        kept = "Opened empty; the file is left as is unless you save."
        try:
            tracing = read_tracing(path)
        except GtFileError as exc:
            self.notice.emit(f"Could not read GT {name}: {exc}. {kept}", "danger")
            return empty_tracing(height, width), [], False, "unreadable"
        if tracing is None:
            return empty_tracing(height, width), [], False, "missing"
        if tracing.ids.shape != (height, width):
            fh, fw = tracing.ids.shape
            self.notice.emit(
                f"GT {name} is {fw}x{fh} but the image is {width}x{height}. {kept}", "danger"
            )
            return empty_tracing(height, width), [], False, "unreadable"
        tracing, warnings, changed = align_to_registry(tracing, self.registry)
        for warning in warnings:
            self.notice.emit(f"GT {name}: {warning}", "warn")
        return tracing, warnings, changed, "loaded"

    def _open_document(self, image_path: str, pixmap: QPixmap) -> tuple[np.ndarray, bool]:
        """The GT tracing (``gt/<stem>.png``) of ``image_path`` as class ids, or empty."""
        project_dir = os.path.dirname(image_path)
        # New folder, or the registry was invalid: (re)read it, so a fixed
        # gt/classes.json takes effect on the next image load.
        if project_dir != self._project_dir or self._registry_error:
            self._load_registry(project_dir)

        tracing, warnings, changed, status = self._read_tracing(
            image_path, pixmap.height(), pixmap.width()
        )
        # Remapped ids are unsaved edits, unless nothing can be saved anyway
        # (invalid registry: the view is read-only and the file stays as is).
        dirty_on_load = changed and not self._registry_error
        self._remap_warnings = list(warnings) if dirty_on_load else []
        self._gt_unreadable = status == "unreadable"
        self._reviewed = set(tracing.reviewed)
        self._has_saved_mask = status == "loaded"
        return np.array(tracing.ids, np.uint8, copy=True), dirty_on_load

    def _document_opened(self) -> None:
        self._refresh_present()
        self._refresh_panel()

    def save_mask(self, save_path=None) -> bool:
        """Write ``gt/<stem>.png`` (even when empty); ``save_path`` is ignored.

        ``reviewed`` = classes ticked by hand plus every class with pixels.
        ``gt/classes.json`` is written too if it does not exist yet.
        """
        if self.edits.plane is None or not self.current_image_path:
            return False
        if self._registry_error:
            self.notice.emit(self._registry_problem("Not saved"), "danger")
            return False

        self._finish_stroke()
        ids = self.edits.plane.copy()
        reviewed = self._reviewed | present_keys(Tracing(ids), self.registry)
        project_dir = os.path.dirname(self.current_image_path)
        try:
            write_tracing(
                gt_path(self.current_image_path), Tracing(ids, frozenset(reviewed)), self.registry
            )
            if not os.path.exists(registry_path(project_dir)):
                save_registry(project_dir, self.registry)
        except (OSError, GtFileError) as exc:
            self.notice.emit(f"Could not save GT: {exc}", "danger")
            return False

        self._reviewed = reviewed
        self._has_saved_mask = True
        self._remap_warnings = []
        self._gt_unreadable = False
        self._set_clean()
        self._refresh_panel()
        self.gt_saved.emit(self.current_image_path)
        return True

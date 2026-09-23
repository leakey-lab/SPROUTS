"""
Ground-truth (GT) tracing editor: the "Trace GT" stage.

Paints hand-traced, multi-class ground truth for training segmentation models
(see ``docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`` §3).
It subclasses :class:`MaskTracingInterface` for the canvas, tool rail, dock,
zoom cluster, brush preview, hotkeys, undo and dirty tracking, and replaces
the storage:

- the canvas is still one ARGB pixmap, but each pixel is opaque in its class
  colour (or transparent = background). Strokes are non-antialiased with
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
from collections import deque

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import QMessageBox

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
from app.gui.widgets import tokens

from .class_panel import ClassPanel
from .mask_drawing_tools import argb_array_to_pixmap, pixmap_to_argb_array
from .mask_tracing_interface import MaskTracingInterface

logger = logging.getLogger(__name__)

_REGISTRY_NAME = f"{GT_DIRNAME}/{GT_CLASSES_FILENAME}"


def _bgra(color: str) -> tuple[int, int, int, int]:
    """``#rrggbb`` -> opaque (B, G, R, A), the byte order of ``pixmap_to_argb_array``."""
    c = QColor(color)
    return c.blue(), c.green(), c.red(), 255


def _packed(bgra) -> int:
    """Pack a BGRA colour as ``B | G << 8 | R << 16 | A << 24`` (one canvas pixel word)."""
    return int(bgra[0]) | (int(bgra[1]) << 8) | (int(bgra[2]) << 16) | (int(bgra[3]) << 24)


class GtTracingInterface(MaskTracingInterface):
    """Multi-class GT editor: one class per pixel, saved to ``gt/<stem>.png``."""

    gt_saved = pyqtSignal(str)  # image path
    navigate_requested = pyqtSignal(int)  # +1 next image, -1 previous

    EDITOR_NOUN = "GT"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry: Registry = default_registry()
        self._registry_error: str | None = None
        self._project_dir: str | None = None
        # Class ids as loaded; a canvas colour no class owns keeps this id on save.
        self._ids_on_load: np.ndarray | None = None
        self._reviewed: set[str] = set()  # classes ticked "checked" by hand
        self._present: set[str] = set()  # classes with pixels on the canvas
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
        self._install_gt_shortcuts()

        self.set_active_class(self._default_key())
        self._refresh_panel()
        self._update_status_chip()

    # ================================================================== UI
    def _retitle_controls(self):
        """Tooltips that name the tracing rather than the mask."""
        self.save_button.setToolTip("Save GT (Ctrl+S)")
        self.clear_button.setToolTip(
            "Clear the tracing. Undo with Ctrl+Z; Save writes an empty tracing."
        )
        self.clear_icon_button.setToolTip("Clear tracing (undoable)")
        self.fill_button.setToolTip("Fill a closed outline of the active class (F)")
        self.mask_visible_button.setToolTip("Show tracing (H)")
        self.opacity_container.setToolTip("Tracing opacity")

    def _overlays(self):
        return super()._overlays() + [getattr(self, "class_panel", None)]

    def _install_gt_shortcuts(self):
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        bindings = [(str(n), lambda n=n: self._select_by_number(n)) for n in range(10)]
        bindings += [("PgDown", lambda: self._navigate(1)), ("PgUp", lambda: self._navigate(-1))]
        for key, slot in bindings:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(context)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

    def _style_brush_preview(self):
        """Base ring styling, with the inner ring in the active class colour."""
        super()._style_brush_preview()
        erasing = hasattr(self, "eraser_button") and self.eraser_button.isChecked()
        if not erasing and getattr(self, "_active_key", None):
            pen = QPen(QColor(self.brush_color), 1)
            pen.setCosmetic(True)
            self._preview_inner.setPen(pen)

    def _update_status_chip(self):
        """``name ● Saved · n/m checked`` / ``● Unsaved`` / ``● GT unreadable`` / ``● No GT``.

        ``● Unsaved · remapped`` after a lossy class remap on load, until saved.
        """
        registry = getattr(self, "registry", None)
        if not hasattr(self, "state_label") or registry is None or not self.current_image_path:
            if hasattr(self, "state_label"):
                self.state_label.setToolTip("")
            super()._update_status_chip()
            return
        warnings = getattr(self, "_remap_warnings", [])
        tooltip = ""
        if self.dirty:
            text, color = "Unsaved", tokens.WARN
            if warnings:
                text += " · remapped"
                tooltip = "Unknown classes were mapped to Ignore:\n" + "\n".join(warnings)
        elif self._has_saved_mask:
            labelled = {c.key for c in registry.labelled()}
            checked = len(labelled & (self._reviewed | self._present))
            text, color = f"Saved · {checked}/{len(labelled)} checked", tokens.OK
        elif getattr(self, "_gt_unreadable", False):
            text, color = "GT unreadable", tokens.DANGER
            tooltip = "The GT file could not be used; it is left as is unless you save."
        else:
            text, color = "No GT", tokens.TEXT_FAINT
        self.file_label.setText(os.path.basename(self.current_image_path))
        self.state_label.setText(f'<span style="color:{color}; font-size:11.5px;">● {text}</span>')
        self.state_label.setToolTip(tooltip)
        self.state_label.setVisible(True)
        self.status_chip.reposition()

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
        self._recolour(self.registry, new_registry)
        self._set_registry(new_registry)
        self._refresh_present()
        self._refresh_panel()
        self._update_status_chip()
        self.notice.emit("Classes updated", "success")

    def _recolour(self, old: Registry, new: Registry):
        """Re-render the canvas and undo history in ``new``'s colours (ids unchanged)."""
        changed = any(
            old.by_id(c.id) is not None and old.by_id(c.id).color.lower() != c.color.lower()
            for c in new.classes
        )
        if not changed or self.mask_pixmap is None:
            return

        def convert(pixmap: QPixmap) -> QPixmap:
            return self._ids_to_pixmap(self._pixmap_to_ids(pixmap, old), new)

        self.mask_pixmap = convert(self.mask_pixmap)
        self.undo_stack = deque(map(convert, self.undo_stack), maxlen=self.max_stack_size)
        self.redo_stack = deque(map(convert, self.redo_stack), maxlen=self.max_stack_size)
        self.refresh_mask_display()

    # ======================================================= ids <-> canvas
    @staticmethod
    def _lut(registry: Registry) -> np.ndarray:
        """Class id -> BGRA. Background is transparent; ids no class owns are grey."""
        lut = np.zeros((256, 4), np.uint8)
        lut[:] = _bgra(GT_UNKNOWN_COLOR)
        for c in registry.classes:  # retired classes keep their own colour
            lut[c.id] = _bgra(c.color)
        lut[GT_BACKGROUND_ID] = 0
        return lut

    def _ids_to_pixmap(self, ids: np.ndarray, registry: Registry | None = None) -> QPixmap:
        return argb_array_to_pixmap(self._lut(registry or self.registry)[ids])

    def _pixmap_to_ids(self, pixmap: QPixmap | None = None, registry: Registry | None = None):
        """Canvas -> ``(h, w)`` uint8 class ids by exact colour match.

        Colours are unique across the registry (retired included), so each
        class colour maps back to one id. Transparent is background; a colour
        no class owns keeps the id it had when the image was loaded.
        """
        pixmap = self.mask_pixmap if pixmap is None else pixmap
        registry = registry or self.registry
        arr = pixmap_to_argb_array(pixmap)  # contiguous (h, w, 4) B, G, R, A
        # Zero-copy view: one little-endian word per pixel, B | G<<8 | R<<16 | A<<24.
        packed = arr.view("<u4")[:, :, 0]
        ids = np.zeros(packed.shape, np.uint8)
        known = arr[:, :, 3] == 0  # transparent = background
        for c in registry.classes:
            hit = packed == _packed(_bgra(c.color))
            ids[hit] = c.id
            known |= hit
        other = ~known
        if other.any() and self._ids_on_load is not None and self._ids_on_load.shape == ids.shape:
            ids[other] = self._ids_on_load[other]
        return ids

    def _refresh_present(self):
        if self.mask_pixmap is None:
            self._present = set()
        else:
            self._present = present_keys(Tracing(self._pixmap_to_ids()), self.registry)

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

    def begin_stroke(self, pos):
        """Brush, eraser and fill all start here; refused while editing is blocked."""
        if self._edit_blocked():
            self.drawing = False  # continue_stroke then draws nothing
            return
        super().begin_stroke(pos)

    def _after_stroke(self):
        """A stroke/fill changed the canvas: re-derive which classes are present."""
        self._refresh_present()
        self._refresh_panel()
        self._update_status_chip()

    def flood_fill(self, pos) -> bool:
        """Fill the closed outline of the *active class* that contains ``pos``.

        Same algorithm as the base fill, but the contours come from pixels of
        the active class colour only, so another class's outline never counts.
        """
        if not self.mask_pixmap:
            return False
        arr = pixmap_to_argb_array(self.mask_pixmap)
        colour = np.array(_bgra(self.brush_color.name()), np.uint8)
        binary = np.all(arr == colour, axis=2).astype(np.uint8) * 255
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        seed = (float(pos.x()), float(pos.y()))
        target = next((c for c in contours if cv2.pointPolygonTest(c, seed, False) >= 0), None)
        if target is None:
            return False

        region = np.zeros(binary.shape, np.uint8)
        cv2.drawContours(region, [target], -1, 255, -1)
        arr[region == 255] = colour
        # begin_stroke took the undo snapshot already (one fill = one undo).
        self.mask_pixmap = argb_array_to_pixmap(arr)
        self.refresh_mask_display()
        return True

    def undo(self):
        super().undo()
        self._after_stroke()

    def redo(self):
        super().redo()
        self._after_stroke()

    def clear_mask(self):
        """Whole tracing to background. Undoable; Save writes the empty tracing."""
        if not self.mask_pixmap or self._edit_blocked():
            return
        self.save_for_undo()
        self.mask_pixmap.fill(Qt.GlobalColor.transparent)
        self.refresh_mask_display()
        self.mark_dirty()
        self._after_stroke()
        self.notice.emit("Tracing cleared. Ctrl+Z to undo.", "info")

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

    # ========================================================= load / save
    def maybe_save_changes(self) -> bool:
        """Offer to save unsaved GT. Returns True when it is safe to move on.

        True = saved, discarded, or nothing to save. When the user chose Save
        and the save failed (a notice says why), ask again: Discard, or Keep
        editing, which returns False; the caller must then stay on this image.
        """
        if super().maybe_save_changes():
            return True
        name = os.path.basename(self.current_image_path)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("GT not saved")
        box.setText(f"The GT for {name} could not be saved.")
        box.setInformativeText("Discard the edits, or keep editing? Discarded edits are lost.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Cancel).setText("Keep editing")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setEscapeButton(QMessageBox.StandardButton.Cancel)
        if box.exec() == QMessageBox.StandardButton.Discard:
            self._set_clean()
            return True
        return False

    def reset(self):
        """A new folder was opened: prompt, then drop the image and the registry.

        Keeps everything when the user chose to keep editing a tracing that
        could not be saved (``MainWindow.open_path`` cancels the open then).
        """
        if not self.maybe_save_changes():
            return
        super().reset()  # its own prompt is a no-op now: the tracing is clean
        self.current_image_path = ""
        self.image_pixmap = None
        self.mask_pixmap = None
        self.image_item.setPixmap(QPixmap())
        self.mask_item.setPixmap(QPixmap())
        self._ids_on_load = None
        self._reviewed = set()
        self._present = set()
        self._has_saved_mask = False
        self._remap_warnings = []
        self._gt_unreadable = False
        self._project_dir = None  # reload gt/classes.json with the next image
        self._sync_brush_preview()
        self._refresh_panel()
        self._update_status_chip()

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

    def load_image(self, image_path: str) -> None:
        """Open ``image_path`` with its GT tracing (``gt/<stem>.png``), or empty.

        Mirrors the image-loading part of the base ``load_image`` without
        calling it, because the base opens the ResNet mask from ``mask/``.
        """
        if image_path != self.current_image_path:
            if not self.maybe_save_changes():
                return  # its save failed and the user kept editing the open image
        elif self.has_unsaved_changes():
            return  # the open image was re-selected: keep editing, don't reload

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return

        project_dir = os.path.dirname(image_path)
        # New folder, or the registry was invalid: (re)read it, so a fixed
        # gt/classes.json takes effect on the next image load.
        if project_dir != self._project_dir or self._registry_error:
            self._load_registry(project_dir)

        self.current_image_path = image_path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.drawing = False
        self.last_point = None
        self.image_pixmap = pixmap

        tracing, warnings, changed, status = self._read_tracing(
            image_path, pixmap.height(), pixmap.width()
        )
        # Remapped ids are unsaved edits, unless nothing can be saved anyway
        # (invalid registry: the view is read-only and the file stays as is).
        dirty_on_load = changed and not self._registry_error
        self._remap_warnings = list(warnings) if dirty_on_load else []
        self._gt_unreadable = status == "unreadable"
        self._ids_on_load = tracing.ids.copy()
        self._reviewed = set(tracing.reviewed)
        self._present = present_keys(tracing, self.registry)
        self._has_saved_mask = status == "loaded"
        self.mask_pixmap = self._ids_to_pixmap(tracing.ids)

        self.update_display()
        self.graphics_view.set_content_rect(QRectF(self.image_pixmap.rect()))
        self._fit_pending = True
        self._fit_if_pending()
        self._refresh_panel()
        if dirty_on_load:
            self.mark_dirty()  # ids were remapped onto this project's registry
        else:
            self._set_clean()
        self._update_status_chip()
        if self.isVisible():
            self.graphics_view.setFocus()

    def save_mask(self, save_path=None) -> bool:
        """Write ``gt/<stem>.png`` (even when empty); ``save_path`` is ignored.

        ``reviewed`` = classes ticked by hand plus every class with pixels.
        ``gt/classes.json`` is written too if it does not exist yet.
        """
        if not (self.mask_pixmap and self.current_image_path):
            return False
        if self._registry_error:
            self.notice.emit(self._registry_problem("Not saved"), "danger")
            return False

        ids = self._pixmap_to_ids()
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

        self._ids_on_load = ids
        self._reviewed = reviewed
        self._has_saved_mask = True
        self._remap_warnings = []
        self._gt_unreadable = False
        self._set_clean()
        self._refresh_panel()
        self.gt_saved.emit(self.current_image_path)
        return True

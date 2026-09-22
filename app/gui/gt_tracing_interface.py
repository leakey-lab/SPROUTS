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
ask for the next / previous image (``navigate_requested``).
"""

from __future__ import annotations

import logging
import os
from collections import deque

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPen, QPixmap, QShortcut

from app.config import GT_BACKGROUND_ID, GT_CLASSES_FILENAME, GT_DIRNAME, GT_UNKNOWN_COLOR
from app.gt import (
    GtFileError,
    Registry,
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

        self._retitle_controls()

        self.class_panel = ClassPanel(self)
        self.class_panel.set_registry(self.registry)
        self.class_panel.active_changed.connect(self.set_active_class)
        self.class_panel.reviewed_changed.connect(self._on_reviewed_changed)
        self.class_panel.edit_requested.connect(self.edit_classes)
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
        """``name ● Saved · n/m checked`` / ``● Unsaved`` / ``● No GT``."""
        registry = getattr(self, "registry", None)
        if not hasattr(self, "state_label") or registry is None or not self.current_image_path:
            super()._update_status_chip()
            return
        if self.dirty:
            text, color = "Unsaved", tokens.WARN
        elif self._has_saved_mask:
            labelled = {c.key for c in registry.labelled()}
            checked = len(labelled & (self._reviewed | self._present))
            text, color = f"Saved · {checked}/{len(labelled)} checked", tokens.OK
        else:
            text, color = "No GT", tokens.TEXT_FAINT
        self.file_label.setText(os.path.basename(self.current_image_path))
        self.state_label.setText(f'<span style="color:{color}; font-size:11.5px;">● {text}</span>')
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

    def _refresh_panel(self):
        """Checked boxes = ticked by hand or has pixels; boxes of painted classes are locked."""
        self.class_panel.set_reviewed(set(self._reviewed), set(self._present))

    def _on_reviewed_changed(self, key: str, checked: bool):
        if not self.current_image_path or key in self._present:
            # Nothing open, or the class has pixels (its box is locked checked).
            self._refresh_panel()
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
                f"Fix {_REGISTRY_NAME} before editing classes: {self._registry_error}", "danger"
            )
            return
        if not self._project_dir:
            self.notice.emit("Open an image first; classes are saved with its folder.", "info")
            return
        from .class_editor_dialog import ClassEditorDialog

        dialog = ClassEditorDialog(self.registry, self)
        dialog.exec()
        new_registry = dialog.result_registry()
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
        if not self.mask_pixmap:
            return
        self.save_for_undo()
        self.mask_pixmap.fill(Qt.GlobalColor.transparent)
        self.refresh_mask_display()
        self.mark_dirty()
        self._after_stroke()
        self.notice.emit("Tracing cleared. Ctrl+Z to undo.", "info")

    def _navigate(self, delta: int):
        """PgDn / PgUp: save pending edits (no prompt), then ask for the next image."""
        if self.has_unsaved_changes() and not self.save_mask():
            return  # stay put so the failure notice is not lost
        self.navigate_requested.emit(delta)

    # ========================================================= load / save
    def reset(self):
        """A new folder was opened: prompt, then drop the image and the registry."""
        super().reset()
        self.current_image_path = ""
        self.image_pixmap = None
        self.mask_pixmap = None
        self.image_item.setPixmap(QPixmap())
        self.mask_item.setPixmap(QPixmap())
        self._ids_on_load = None
        self._reviewed = set()
        self._present = set()
        self._has_saved_mask = False
        self._project_dir = None  # reload gt/classes.json with the next image
        self._sync_brush_preview()
        self._refresh_panel()
        self._update_status_chip()

    def _load_registry(self, project_dir: str):
        self._project_dir = project_dir
        try:
            registry = load_registry(project_dir)
            self._registry_error = None
        except (ValueError, AttributeError, TypeError) as exc:  # RegistryError, bad JSON shape
            registry = default_registry()
            self._registry_error = str(exc)
            self.notice.emit(
                f"{_REGISTRY_NAME} is invalid ({exc}). Saving GT is off until it is fixed.",
                "danger",
            )
        self._set_registry(registry)

    def _read_tracing(self, image_path: str, height: int, width: int):
        """Return ``(tracing, changed_by_alignment, file_loaded)``; never raises."""
        path = gt_path(image_path)
        name = os.path.basename(path)
        kept = "Opened empty; the file is left as is unless you save."
        try:
            tracing = read_tracing(path)
        except GtFileError as exc:
            self.notice.emit(f"Could not read GT {name}: {exc}. {kept}", "danger")
            return empty_tracing(height, width), False, False
        if tracing is None:
            return empty_tracing(height, width), False, False
        if tracing.ids.shape != (height, width):
            fh, fw = tracing.ids.shape
            self.notice.emit(
                f"GT {name} is {fw}x{fh} but the image is {width}x{height}. {kept}", "danger"
            )
            return empty_tracing(height, width), False, False
        tracing, warnings, changed = align_to_registry(tracing, self.registry)
        for warning in warnings:
            self.notice.emit(f"GT {name}: {warning}", "warn")
        return tracing, changed, True

    def load_image(self, image_path: str) -> None:
        """Open ``image_path`` with its GT tracing (``gt/<stem>.png``), or empty.

        Mirrors the image-loading part of the base ``load_image`` without
        calling it, because the base opens the ResNet mask from ``mask/``.
        """
        if image_path != self.current_image_path:
            self.maybe_save_changes()
        elif self.has_unsaved_changes():
            return  # the open image was re-selected: keep editing, don't reload

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return

        project_dir = os.path.dirname(image_path)
        if project_dir != self._project_dir:
            self._load_registry(project_dir)

        self.current_image_path = image_path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.drawing = False
        self.last_point = None
        self.image_pixmap = pixmap

        tracing, changed, loaded = self._read_tracing(image_path, pixmap.height(), pixmap.width())
        self._ids_on_load = tracing.ids.copy()
        self._reviewed = set(tracing.reviewed)
        self._present = present_keys(tracing, self.registry)
        self._has_saved_mask = loaded
        self.mask_pixmap = self._ids_to_pixmap(tracing.ids)

        self.update_display()
        self.graphics_view.set_content_rect(QRectF(self.image_pixmap.rect()))
        self._fit_pending = True
        self._fit_if_pending()
        self._refresh_panel()
        if changed:
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
            self.notice.emit(
                f"Not saved: {_REGISTRY_NAME} is invalid ({self._registry_error}).", "danger"
            )
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
        self._set_clean()
        self._refresh_panel()
        self.gt_saved.emit(self.current_image_path)
        return True

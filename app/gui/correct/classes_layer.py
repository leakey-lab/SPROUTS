"""The class labels as an edit layer of the Correct workspace: :class:`ClassesLayer` (WG-T4).

``ClassesLayer`` is a :class:`~app.gui.correct.raster_layer.RasterLayer` (the canvas, the
item at z 1.2, Brush / Eraser / Fill, the ring, the history replay, the view state) over the
Qt-free :class:`~app.gui.correct.classes_document.ClassesDocument` (the class-id plane, the
"checked" ticks, the registry, ``gt/<stem>.png``). It paints in the active class's colour
(:attr:`ClassesLayer.brush_color`) while its ring, rail and row keep the layer's purple;
Fill reads the active class's outlines only; each class keeps its own brush size.

While ``gt/classes.json`` is invalid the layer is read-only: a press, Clear, a tick and the
page's Classes… are refused (:meth:`ClassesLayer.locked`), the first refusal on an image with
one danger notice; Save reports why. After an
open that sent unknown classes to Ignore, ``save_needs_prompt`` is True until a save, so
PgDn asks instead of saving silently (``GuardedSaveLayer``). The page hears of every change
the class panel shows through ``on_classes_changed``: an open, a save, Discard, a new
registry, a tick (also a refused one: the box goes back), the active class and every plane
change (a class gains or loses its pixels, so its box locks or unlocks).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

from app.gt import Registry
from app.gui.correct.classes_document import ClassesDocument, ClassesRead
from app.gui.correct.layer import (
    CLASSES,
    LayerHost,
    LayerStatus,
    Notice,
    SaveOutcome,
    project_label,
)
from app.gui.correct.raster_layer import RasterLayer
from app.gui.patch_history import Patch
from app.gui.widgets import tokens

__all__ = ["ClassesLayer"]


class ClassesLayer(RasterLayer):
    """The class labels of the open image as an edit layer (``EditLayer``,
    ``ReadAheadLayer``, ``DiskGuardedLayer``, ``GuardedSaveLayer``; the tools'
    ``MaskToolHost``). ``on_classes_changed`` is set by the page (None: nobody listens)."""

    layer_id = CLASSES
    noun = "class labels"
    color = tokens.ACCENT
    Z = 1.2
    DEFAULT_OPACITY = 0.60
    CLEARED_NOTICE = "Class labels cleared. Ctrl+Z to undo; Save writes an empty file."
    document: ClassesDocument

    def __init__(self, host: LayerHost, scene: QGraphicsScene) -> None:
        """Build the parts in ``scene``; nothing is open until :meth:`open`."""
        document = ClassesDocument()
        super().__init__(
            host,
            scene,
            document=document,
            lut=document.lut,
            to_plane=document.ids_from_argb,
            fill_source=document.fill_source,
        )
        self.on_classes_changed: Callable[[], None] | None = None
        self._refused = False  # the read-only notice was shown for the open image

    @property
    def brush_color(self) -> QColor:
        """The active class's colour: what the brush paints."""
        document = self.document
        return QColor(document.registry.by_key(document.active_key).color)

    # ------------------------------------------------------------------ classes
    def active_class(self) -> str:
        """The key of the class the brush paints."""
        return self.document.active_key

    def set_active_class(self, key: str) -> None:
        """Paint with class ``key`` at its own brush size; an unknown or retired key is
        ignored."""
        if self.document.set_active_class(key):
            self.set_size(self.document.brush_size(key))
            self._classes_changed()

    def set_size(self, value: int) -> None:
        """Set the brush size of the active class (``RasterLayer.set_size``)."""
        super().set_size(value)
        self.document.set_brush_size(self.document.active_key, self._size)

    def set_ticked(self, key: str, on: bool) -> bool:
        """Tick (or untick) ``key``'s "checked" box; False when refused (the class has
        pixels, Ignore, read-only). The page hears of it either way."""
        done = not self.locked() and self.document.set_ticked(key, on)
        if done:
            self._changed()
        self._classes_changed()
        return done

    def registry(self) -> Registry:
        """The classes in use (the defaults while ``gt/classes.json`` is invalid)."""
        return self.document.registry

    def apply_registry(self, registry: Registry) -> None:
        """Use ``registry`` (the class editor saved it): the canvas is recoloured, the ids and
        the history stay; a gone active class falls back to the first one."""
        self.finish_gesture()
        self.document.apply_registry(registry)
        self.set_size(self.document.brush_size(self.document.active_key))
        self._replaced()
        self._classes_changed()

    def is_read_only(self) -> bool:
        """True while ``gt/classes.json`` is invalid: nothing can be edited or saved."""
        return self.document.is_read_only()

    def save_needs_prompt(self) -> bool:
        """``GuardedSaveLayer``: True after an open that sent unknown classes to Ignore,
        until a save writes it (PgDn then asks instead of saving silently)."""
        return self.document.lossy_remap()

    # ------------------------------------------------------------------ document
    def read_ahead(self, image_path: str, images_base_folder: str | None):
        """``ReadAheadLayer``: a worker reader of ``image_path``'s class labels."""
        return partial(ClassesDocument.read, image_path)

    def read_back(self, stamps: tuple) -> ClassesRead | None:
        """The open labels as the read-ahead's payload while they are as on disk."""
        return self.document.read_back(stamps)

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        images_base_folder: str | None,
        ahead: object | None = None,
    ) -> list[Notice]:
        """Open the class labels of ``image_path`` (``size`` = (w, h)); the notices to show.

        A stroke in progress is committed first; ``images_base_folder`` only roots the file
        labels; ``ahead``: a :meth:`read_ahead` result.
        """
        self.finish_gesture()
        self._root = images_base_folder or os.path.dirname(image_path)
        read = ahead if isinstance(ahead, ClassesRead) else None
        notices = self.document.open(image_path, size, read, root=self._root)
        self._refused = False
        self.set_size(self.document.brush_size(self.document.active_key))
        self._replaced()
        self._classes_changed()
        return notices

    def close(self) -> None:
        """Forget the document (a stroke in progress is committed first)."""
        self.finish_gesture()
        self.document.close()
        self._replaced()
        self._classes_changed()

    def is_open(self) -> bool:
        """True while class labels are open."""
        return self.document.is_open()

    def file_stamps(self) -> tuple:
        """``gt/<stem>.png`` and ``gt/classes.json``, stamped or absent (``()`` closed)."""
        return self.document.file_stamps()

    def write_stamps(self) -> tuple:
        """``DiskGuardedLayer``: ``gt/<stem>.png``, the one file a save replaces."""
        return self.document.write_stamps()

    def matches_disk(self) -> bool:
        """``DiskGuardedLayer``: reading the files now would open the saved labels."""
        return self.document.matches_disk()

    def unloaded_label(self) -> str:
        """``DiskGuardedLayer``: ``"gt/<stem>.png"`` while that file exists and was not shown
        (it could not be read, or has another size): a save would replace it."""
        if not self.document.unloaded_file():
            return ""
        stamp = self.document.write_stamps()[0]
        return "" if stamp[1:] == (None,) else project_label(stamp[0], self._root)

    def is_dirty(self) -> bool:
        """True while a save would write something new (ids, ticks, a remap)."""
        return self.document.is_dirty()

    def save(self) -> SaveOutcome:
        """Write ``gt/<stem>.png`` (``ClassesDocument.save``); a stroke is committed first."""
        self.finish_gesture()
        document = self.document
        if not document.is_open():
            message = "No class labels are open"
            return SaveOutcome(self.layer_id, ok=False, message=message, kind="danger")
        result = document.save()
        self._changed()
        self._classes_changed()
        if result.ok:
            return SaveOutcome(self.layer_id, ok=True, path=result.path)
        if document.is_read_only():
            message = f"Not saved: gt/classes.json is invalid ({document.registry_error})"
        else:
            message = f"Could not save class labels to {result.path}: {result.error}"
        return SaveOutcome(self.layer_id, ok=False, message=message, kind="danger")

    def revert(self) -> list[Notice]:
        """Discard: back to the saved ids and ticks in memory, history cleared (no notices)."""
        self.finish_gesture()
        if self.document.is_open():
            self.document.revert()
            self._replaced()
            self._classes_changed()
        return []

    def status(self) -> LayerStatus:
        """``ClassesDocument.status``: "Read-only", "Not saved", unsaved ("· remapped"),
        "Class labels not shown", "GT · n/m checked" or "No class labels"."""
        return self.document.status()

    # ------------------------------------------------------------------ editing
    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """``RasterLayer.press``, refused while read-only (the ring still follows)."""
        if self.locked():
            self.finish_gesture()
            self._footprint.place(pos)
            return
        super().press(pos, modifiers)

    def clear(self) -> bool:
        """``RasterLayer.clear``; False while read-only (or nothing is open)."""
        return False if self.locked() else super().clear()

    def replace_plane(self, plane) -> bool:
        """``RasterLayer.replace_plane``; False while read-only (no history entry)."""
        return False if self.locked() else super().replace_plane(plane)

    def locked(self) -> bool:
        """True while an open document is read-only: the caller refuses its edit. The first
        refusal on an image says why (one danger notice), later ones say nothing (the status
        says it). The page's Classes… asks it too."""
        document = self.document
        if not (document.is_open() and document.is_read_only()):
            return False
        if not self._refused:
            self._refused = True
            notice = document.LOCKED_NOTICE.format(err=document.registry_error)
            self._host.notify(notice, "danger")
        return True

    # ------------------------------------------------------------------ internals
    def _on_patch(self, patch: Patch, forward: bool) -> None:
        """Every plane change: the class counts follow the patch first (dirty reads them)."""
        self.document.count_patch(patch, forward)
        super()._on_patch(patch, forward)
        self._classes_changed()

    def _classes_changed(self) -> None:
        if self.on_classes_changed is not None:
            self.on_classes_changed()

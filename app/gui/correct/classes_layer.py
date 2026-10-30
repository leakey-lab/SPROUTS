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

from collections.abc import Callable

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

from app.gt import Registry
from app.gui.correct.classes_document import ClassesDocument, ClassesRead
from app.gui.correct.layer import CLASSES, LayerHost, SaveOutcome
from app.gui.correct.raster_layer import RasterLayer
from app.gui.key_text import platform_keys
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
    CLEARED_NOTICE = platform_keys(
        "Class labels cleared. Ctrl+Z to undo; Save writes an empty file."
    )
    NOT_OPEN = "No class labels are open"
    READ = ClassesRead
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

    def is_read_only(self) -> bool:
        """True while ``gt/classes.json`` is invalid: nothing can be edited or saved."""
        return self.document.is_read_only()

    def save_needs_prompt(self) -> bool:
        """``GuardedSaveLayer``: True after an open that sent unknown classes to Ignore,
        until a save writes it (PgDn then asks instead of saving silently)."""
        return self.document.lossy_remap()

    # ------------------------------------------------------------------ document
    def save(self) -> SaveOutcome:
        """``RasterLayer.save`` (``ClassesDocument.outcome`` words it); the page hears of it."""
        opened = self.document.is_open()
        outcome = super().save()
        if opened:
            self._classes_changed()
        return outcome

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

    def _opened(self) -> None:
        """A new image: its first refused edit says why again; the active class's size."""
        self._refused = False
        self.set_size(self.document.brush_size(self.document.active_key))

    def _replaced(self) -> None:
        """Open, close, Discard, a new registry: the page hears of it after the redraw."""
        super()._replaced()
        self._classes_changed()

    def _classes_changed(self) -> None:
        if self.on_classes_changed is not None:
            self.on_classes_changed()

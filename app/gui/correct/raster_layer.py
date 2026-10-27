"""The generic raster layer of the Correct workspace: :class:`RasterLayer` (WG-T4).

A raster layer edits one ``uint8`` plane (the mask's 0/1, the class labels' ids) through
parts that each do one job: its document (the plane in a ``PatchHistory``, the saved state,
the files), the ``PaintCanvas`` (the ARGB canvas over the plane), the ``RasterLayerItem`` at
the layer's z and the Brush / Eraser / Fill tools and ring of :mod:`.mask_tools`. It passes
the document members of ``EditLayer`` (open, save, status ...) on to the document;
``MaskLayer`` and ``ClassesLayer`` bring the document (it words its save: ``outcome``) and
the identity.

The drag state lives here and in the canvas, never in a tool (C2): the layer forwards a drag
only to the tool that began it, and :meth:`RasterLayer.finish_gesture` commits a stroke in
progress, after which the rest of that drag only moves the ring. The canvas's ``on_patch``
hook is the one place that bumps the document's revision and reports a new action
(``layer_edited``, never for undo or redo); open, revert and close replace the plane through
the document and redraw the whole canvas.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Protocol

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage, QKeyEvent

from app.gui.canvas_layers.raster import RasterLayerItem
from app.gui.correct.layer import REFERENCE_DIM, SaveOutcome, SizeSpec, ToolSpec, project_label
from app.gui.correct.mask_tools import (
    FILL_MISS_NOTICE,
    TOOL_CLASSES,
    FootprintRing,
    MaskTool,
    build_tools,
)
from app.gui.paint_canvas import PaintCanvas, Rect
from app.gui.patch_history import Patch, PatchHistory

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.correct.layer import LayerHost, Notice

__all__ = ["RasterDocument", "RasterLayer"]


class RasterDocument(Protocol):
    """What a raster layer needs of its document (``revision``: +1 per ``touch``), and the
    members the layer passes on to it (``read``, ``open``, ``save``, ``outcome``, ...)."""

    history: PatchHistory  # the committed plane (None while closed) and its undo stacks
    revision: int

    def is_open(self) -> bool: ...

    def touch(self) -> None: ...


class RasterLayer:
    """The generic part of a raster edit layer (``EditLayer``, ``ReadAheadLayer``,
    ``DiskGuardedLayer``; the tools' ``MaskToolHost`` once a subclass gives it a
    ``brush_color``).

    ``document``, ``canvas``, ``item`` (in ``scene`` at ``Z``) and ``ring`` (the brush
    ring, a ``BrushPreview`` in the layer colour) are its parts; ``host`` hears of new
    actions and UI changes. A new layer is active and visible, with the brush at 8 px.
    """

    layer_id = ""
    noun = ""
    color = ""
    Z = 1.0
    DEFAULT_SIZE = 8
    SIZE_RANGE = (1, 100)
    DEFAULT_OPACITY = 0.60
    CLEARED_NOTICE = ""
    NOT_OPEN = ""  # the save's message while nothing is open
    READ: type = type(None)  # the read-ahead payload open adopts (MaskRead, ClassesRead)
    FILL_MISS_NOTICE = FILL_MISS_NOTICE
    #: The one dock slider of the brush and the eraser (they share the size).
    SIZE_SPEC = SizeSpec("brush", "Brush size ([ and ])", *SIZE_RANGE)
    tools: tuple[ToolSpec, ...] = tuple(cls.spec for cls in TOOL_CLASSES)

    def __init__(
        self,
        host: LayerHost,
        scene: QGraphicsScene,
        *,
        document: RasterDocument,
        lut: Callable[[], np.ndarray],
        to_plane: Callable[[np.ndarray, Rect], np.ndarray],
        fill_source: Callable[[np.ndarray], tuple[np.ndarray, int]],
    ) -> None:
        """Build the parts in ``scene`` over ``document`` with the plane's paint rules
        (``PaintCanvas``'s ``lut``, ``to_plane``, ``fill_source``); nothing is open yet."""
        self._host = host
        self.document = document
        # Calls the document answers as it is (so a subclass must not define these names).
        self.is_open, self.is_dirty = document.is_open, document.is_dirty
        self.status, self.file_stamps = document.status, document.file_stamps
        self.write_stamps, self.matches_disk = document.write_stamps, document.matches_disk
        self.read_back = document.read_back  # the image the page leaves, handed back
        self.item = RasterLayerItem(self._canvas_image, skip=self._draws_nothing)
        self.item.setZValue(self.Z)
        scene.addItem(self.item)
        self.canvas = PaintCanvas(
            document.history,
            lut=lut,
            to_plane=to_plane,
            fill_source=fill_source,
            on_region=self.item.canvas_changed,
            on_replaced=self.item.canvas_replaced,
            on_patch=self._on_patch,
        )
        self._footprint = FootprintRing(scene, self.color, host.is_panning, self.DEFAULT_SIZE)
        self.ring = self._footprint.preview
        self._tools = build_tools(self)
        self._tool: MaskTool = self._tools[self.tools[0].tool_id]
        self._drag: MaskTool | None = None  # the tool that began the drag in progress
        self._stepping = False  # an undo / redo is replaying a patch
        self._size = self.DEFAULT_SIZE
        self._opacity = self.DEFAULT_OPACITY
        self._active = True
        self._visible = True
        self._root = ""  # the project folder the file labels are relative to
        self._apply_opacity()

    @property
    def current_tool(self) -> str:
        """Id of the tool that takes the next press (change it with :meth:`set_tool`)."""
        return self._tool.tool_id

    # ------------------------------------------------------------------ document
    def read_ahead(self, image_path: str, images_base_folder: str | None):
        """``ReadAheadLayer``: a worker reader of ``image_path``'s saved document."""
        return partial(type(self.document).read, image_path)

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        images_base_folder: str | None,
        ahead: object | None = None,
    ) -> list[Notice]:
        """Open the document of ``image_path`` (``size`` = (w, h)); the notices to show.

        A stroke in progress is committed first. The files live next to the image, so
        ``images_base_folder`` only roots the file labels. ``ahead``: a :meth:`read_ahead`
        result (adopted when it is a ``READ``).
        """
        self.finish_gesture()
        self._root = images_base_folder or os.path.dirname(image_path)
        read = ahead if isinstance(ahead, self.READ) else None
        notices = self.document.open(image_path, size, read, root=self._root)
        self._opened()
        self._replaced()
        return notices

    def close(self) -> None:
        """Forget the document (a stroke in progress is committed first)."""
        self.finish_gesture()
        self.document.close()
        self._replaced()

    def revert(self) -> list[Notice]:
        """Discard: back to the saved state in memory, history cleared (no notices)."""
        self.finish_gesture()
        if self.document.is_open():
            self.document.revert()
            self._replaced()
        return []

    def save(self) -> SaveOutcome:
        """Write the document (``document.save``, worded by ``document.outcome``); a stroke is
        committed first."""
        self.finish_gesture()
        if not self.document.is_open():
            return SaveOutcome(self.layer_id, ok=False, message=self.NOT_OPEN, kind="danger")
        result = self.document.save()
        self._changed()
        return self.document.outcome(result)

    def unloaded_label(self) -> str:
        """``DiskGuardedLayer``: the file a save would replace (``"gt/<stem>.png"``) while it
        exists and was not shown (it could not be read, or has another size); else ``""``."""
        if not self.document.unloaded_file():
            return ""
        stamp = self.document.write_stamps()[0]
        gone = stamp[1:] == (None,)  # deleted since the open: nothing left to replace
        return "" if gone else project_label(stamp[0], self._root)

    # ------------------------------------------------------------------ plane
    def clear(self) -> bool:
        """Empty the plane as one undoable action (Save then writes an empty file)."""
        if not self.document.is_open():
            return False
        self.finish_gesture()  # a stroke in progress becomes its own action first
        self.canvas.clear()
        self._host.notify(self.CLEARED_NOTICE, "info")
        return True

    def replace_plane(self, plane: np.ndarray) -> bool:
        """Put ``plane`` in as one undoable action; False (nothing changes) while nothing is
        open or when the plane holds it already; ``ValueError`` for a plane of another shape.
        A stroke in progress is committed first."""
        self.finish_gesture()
        current = self.document.history.plane
        if current is None:
            return False
        if plane.shape != current.shape:
            raise ValueError(
                f"a plane of shape {plane.shape} cannot replace the open {current.shape}"
            )
        if np.array_equal(current, plane):
            return False
        height, width = current.shape
        patch = self.document.history.commit((0, 0, width, height), plane)
        self.canvas.rebuild()
        self._on_patch(patch, True)
        return True

    # ------------------------------------------------------------------ history
    def can_undo(self) -> bool:
        """True when the layer's own history has an action to undo."""
        return self.document.history.can_undo

    def can_redo(self) -> bool:
        """True when the layer's own history has an undone action to redo."""
        return self.document.history.can_redo

    def undo(self) -> bool:
        """Undo one action of the layer's own history; False only when it is empty.

        The workspace refuses undo mid-drag; called anyway, the stroke is committed first
        (nothing is dropped) and is then the action undone.
        """
        self.finish_gesture()
        return self._replay(self.canvas.undo)

    def redo(self) -> bool:
        """Redo one undone action; False only when nothing is left to redo."""
        self.finish_gesture()
        return self._replay(self.canvas.redo)

    def drop_redo(self) -> None:
        """Forget the redo branch: another layer's new action ended it."""
        history = self.document.history
        if history.can_redo:
            history.drop_redo()
            self._changed()

    def undo_in_gesture(self) -> bool:
        """A raster layer has no drafts: nothing to pop."""
        return False

    # ------------------------------------------------------------------ gestures
    def gesture_active(self) -> bool:
        """True while a stroke is being painted (a Fill press is no gesture)."""
        return self.canvas.in_stroke()

    def finish_gesture(self) -> None:
        """End a drag as its release would: a stroke is committed; the rest is ignored."""
        self._drag = None
        self.commit_stroke()

    def cancel_transient(self) -> None:
        """Esc: a raster layer has nothing to cancel, so a stroke in progress is committed."""
        self.finish_gesture()

    def commit_stroke(self) -> None:
        """End the stroke in progress as one undoable action (no-op without one)."""
        if self.canvas.in_stroke():
            self.canvas.commit()

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool_id: str) -> None:
        """Take ``tool_id`` ("brush", "eraser" or "fill"); a stroke is committed first."""
        tool = self._tools.get(tool_id)
        if tool is None:
            raise ValueError(f"the {self.noun} has no tool {tool_id!r}")
        self.finish_gesture()
        if tool is self._tool:
            return
        self._tool.deactivate()
        self._tool = tool
        self._footprint.set_tool(tool)
        self._refresh_ring()
        self._changed()

    def tool_hint(self) -> tuple[str, str]:
        """``(short hint, tooltip)`` of the current tool."""
        return self._tool.hint()

    def size_spec(self) -> SizeSpec | None:
        """The brush-size slider for the brush and the eraser; None for Fill."""
        return self.SIZE_SPEC if self._tool.spec.sized else None

    def size(self) -> int:
        """The brush size in image pixels (brush and eraser)."""
        return self._size

    def set_size(self, value: int) -> None:
        """Set the brush size, clamped to 1..100 px (the next stroke uses it)."""
        low, high = self.SIZE_RANGE
        size = min(high, max(low, int(value)))
        if size == self._size:
            return
        self._size = size
        self._footprint.set_diameter(size)
        self._changed()  # the dock slider shows it (B + wheel sizes it from the router)

    def step_size(self, direction: int) -> None:
        """Grow (``direction`` > 0) or shrink the brush by 12 %, at least 1 px, clamped."""
        step = max(1, round(self._size * 0.12))
        self.set_size(self._size + (step if direction > 0 else -step))

    # ------------------------------------------------------------------ input
    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """The current tool begins a drag at ``pos`` (a stroke still open is committed)."""
        self.finish_gesture()
        if self.document.is_open():
            self._drag = self._tool
            self._tool.press(pos, modifiers)
        self._footprint.place(pos)

    def move(self, pos: QPoint, dragging: bool) -> None:
        """Hover, or a drag move for the tool that began the drag; the ring follows."""
        if dragging and self._drag is not None:
            self._drag.move(pos)
        self._footprint.place(pos)

    def release(self, pos: QPoint) -> None:
        """End of the drag (nothing after a forced finish: that drag was already ended)."""
        tool, self._drag = self._drag, None
        if tool is not None:
            tool.release(pos)
        self._footprint.place(pos)

    def double_click(self, pos: QPoint) -> None:
        """Nothing is painted: a double-click paints one dab (its first press), as today."""
        self._tool.double_click(pos)
        self._footprint.place(pos)

    def key(self, event: QKeyEvent) -> bool:
        """The raster tools have no gesture keys (Esc is ``cancel_transient``)."""
        return self._tool.key(event)

    def hover(self, pos: QPoint | None) -> None:
        """Show the ring on ``pos``; None hides it (the pointer left, or a pan began)."""
        self._footprint.place(pos)

    def notify(self, message: str, kind: str = "info") -> None:
        """A tool's toast, passed to the workspace."""
        self._host.notify(message, kind)

    # ------------------------------------------------------------------ view
    def set_active(self, active: bool) -> None:
        """The edited layer (full opacity, ring) or the dimmed reference (no ring).

        Becoming the reference commits a stroke first; either way the ring waits for
        the next pointer position.
        """
        active = bool(active)
        if active == self._active:
            return
        if not active:
            self.finish_gesture()
        self._active = active
        self._apply_opacity()
        self._refresh_ring(forget=True)

    def is_active(self) -> bool:
        """True while this is the edited layer."""
        return self._active

    def set_visible(self, visible: bool) -> None:
        """Show or hide the layer and its ring (hiding commits a stroke first)."""
        visible = bool(visible)
        if visible == self._visible:
            return
        if not visible:
            self.finish_gesture()
        self._visible = visible
        self.item.setVisible(visible)
        self._refresh_ring(forget=True)

    def is_visible(self) -> bool:
        """True while the layer is shown."""
        return self._visible

    def opacity(self) -> float:
        """The user's opacity 0..1 (the reference is drawn at half of it)."""
        return self._opacity

    def set_opacity(self, value: float) -> None:
        """Set the user's opacity, clamped to 0..1."""
        self._opacity = min(1.0, max(0.0, float(value)))
        self._apply_opacity()

    # ------------------------------------------------------------------ internals
    def _canvas_image(self) -> QImage | None:
        """The canvas the item draws, read at paint time (so no second reference exists)."""
        canvas = getattr(self, "canvas", None)  # None only while __init__ builds the item
        return None if canvas is None else canvas.image

    def _draws_nothing(self) -> bool:
        """A blank canvas (no labels, no mask) of the reference layer: its item skips the paint
        and the warm-up. The edited layer is drawn as ever, so its tiles are made ahead."""
        return self.canvas.blank and not self._active

    def _on_patch(self, patch: Patch, forward: bool) -> None:
        """Every plane change: a new document revision; a new action unless replayed."""
        self.document.touch()
        if not self._stepping:
            self._host.layer_edited(self.layer_id)
        self._changed()

    def _replay(self, step: Callable[[], Patch | None]) -> bool:
        """Run an undo or redo of the canvas; True iff it stepped."""
        self._stepping = True
        try:
            return step() is not None
        finally:
            self._stepping = False

    def _opened(self) -> None:
        """A document was just opened, before the redraw (a hook for subclasses)."""

    def _replaced(self) -> None:
        """The document was opened, closed or reverted: redraw the canvas, tell the UI."""
        self.canvas.rebuild()
        self._refresh_ring()
        self._changed()

    def _changed(self) -> None:
        self._host.layer_changed(self.layer_id)

    def _apply_opacity(self) -> None:
        self.item.setOpacity(self._opacity * (1.0 if self._active else REFERENCE_DIM))

    def _refresh_ring(self, *, forget: bool = False) -> None:
        """The ring may show while active, visible, open and with a sized tool."""
        allowed = self._active and self._visible and self.document.is_open()
        self._footprint.allow(allowed and self._tool.spec.sized, forget=forget)

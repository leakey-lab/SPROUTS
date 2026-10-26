"""The skeleton layer of the Correct workspace: :class:`SkeletonLayer` (contract C14).

An ``EditLayer`` over the Qt-free ``SkeletonDocument``, drawn by a ``SkeletonLayerItem`` at
z 1.5 under every decoration (preview and Connect line 2, markers and handles 3, highlight
3.5), and the ``ToolHost`` of ``app.gui.skeleton_tools``. The item gets the model's changed
boxes (``take_changed_box``'s only caller); eraser drags repaint once per RENDER_INTERVAL_MS;
``layer_changed`` fires only when what the host reads changed. A drag belongs to the tool that
began it (transitions finish it first). The reference layer is dimmed, markers hidden.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QGraphicsScene

from app.gui.brush_preview import BrushPreview
from app.gui.canvas_layers.skeleton import SkeletonLayerItem
from app.gui.correct.layer import REFERENCE_DIM, SKELETON, SaveOutcome, SizeSpec
from app.gui.correct.skeleton_document import GeneratedResolver, SkeletonDocument, SkeletonRead
from app.gui.key_text import platform_keys
from app.gui.skeleton_overlay import SkeletonDecorations
from app.gui.skeleton_tools import (
    CLOSED_HINT,
    CONNECT,
    EMPTY_HINT,
    ERASER,
    POLYLINE,
    SELECT,
    TOOL_SPECS,
    DraftState,
    PolylineDraft,
    build_tools,
)
from app.gui.widgets import tokens

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.layer import LayerHost, LayerStatus, Notice
    from app.gui.skeleton_tools import SkeletonTool

__all__ = ["DraftState", "SkeletonLayer"]
_MOD = Qt.KeyboardModifier
_NO_KEY_MODS = _MOD.ControlModifier | _MOD.AltModifier | _MOD.MetaModifier  # S is not Smooth
_STEP = 0.12  # [ and ]: the eraser grows or shrinks by 12 %, at least 1 px


class SkeletonLayer:
    """The skeleton of the open image as an edit layer and the host of the skeleton tools."""

    layer_id = SKELETON
    noun = "skeleton"
    color = tokens.SKEL
    tools = TOOL_SPECS  # Select, Eraser (sized), Polyline, Connect endpoints
    DEFAULT_ERASER = 20
    ERASER_RANGE = (1, 80)
    DEFAULT_OPACITY = 0.85
    RENDER_INTERVAL_MS = 16
    Z = 1.5
    CLEARED_NOTICE = platform_keys("Skeleton cleared. Ctrl+Z to undo.")
    DRAFT_DISCARDED_NOTICE = "Polyline draft discarded"
    ERASER_SIZE = SizeSpec("erase", "Eraser size ([ and ])", *ERASER_RANGE)
    draw_thickness = 3  # ToolHost: width of drawn lines before thinning, px

    def __init__(
        self, host: LayerHost, scene: QGraphicsScene, *, resolver: GeneratedResolver | None = None
    ) -> None:
        """An empty layer on ``scene``: nothing open, the Select tool, active and shown."""
        self._host = host
        self.document = doc = SkeletonDocument(resolver)
        self.model = doc.model  # ToolHost; one object for the document's whole life
        self.model.on_action = lambda: host.layer_edited(SKELETON)
        # Calls the document, the model or the host answer as they are (EditLayer, ToolHost).
        self.is_open, self.notify = doc.is_open, host.notify
        self.can_undo, self.can_redo = (lambda: doc.model.can_undo), (lambda: doc.model.can_redo)
        self.file_stamps, self.set_resolver = doc.file_stamps, doc.set_resolver
        self.write_stamps, self.matches_disk = doc.write_stamps, doc.matches_disk
        # ReadAheadLayer: a worker reader of a document; the document handed back (paging).
        self.read_ahead, self.read_back = doc.plan_read, doc.read_back
        self.item = SkeletonLayerItem()
        self.item.setZValue(self.Z)
        scene.addItem(self.item)
        self.decorations = SkeletonDecorations(scene)  # markers device-cached (a blit a frame)
        self.eraser_ring = BrushPreview(scene)
        self.eraser_ring.set_style(tokens.SKEL, dashed=True)
        self.draft, self.current_tool = PolylineDraft(), SELECT
        self._tools: dict[str, SkeletonTool] = build_tools(self)
        self._eraser, self._opacity = self.DEFAULT_ERASER, self.DEFAULT_OPACITY
        self._active = self._visible = self._shown = True
        self._drag: SkeletonTool | None = None  # the tool that owns the drag in progress
        self._last_pos = QPoint()  # the pointer's last press / move / release, image px
        self._seen: tuple = ()  # what the host last heard of (see _changed)
        # Coalesced drag repaints; parented, so no frame fires once the scene is gone.
        self._timer = QTimer(scene, singleShot=True, interval=self.RENDER_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.render_skeleton(full=False))
        self.cancel_scheduled_render = self._timer.stop  # ToolHost
        self._sync_view()
        self._changed(tell=False)

    def _changed(self, *, tell: bool = True) -> None:
        # Tell the host once per change of what it reads, never for repeats or hover frames.
        model, doc, draft = self.model, self.document, self.draft
        seen = (model.revision, model.can_undo, model.can_redo, doc.source, doc.save_error)
        seen += (self.is_dirty(), self.current_tool, self.draft_state(), self._eraser)
        seen += (draft.edit_original is not None, self._tools[CONNECT].first)
        if seen != self._seen:
            self._seen = seen
            if tell:
                self._host.layer_changed(SKELETON)

    refresh_hint = _changed  # ToolHost: the host reads tool_hint() again if it changed

    def clamp_to_image(self, pt: QPoint) -> QPoint:
        """``pt`` clamped to the image (ToolHost)."""
        w, h = self.image_size()
        return QPoint(min(max(pt.x(), 0), max(w - 1, 0)), min(max(pt.y(), 0), max(h - 1, 0)))

    def image_size(self) -> tuple[int, int]:
        """``(w, h)`` of the open skeleton, the image size; ``(0, 0)`` when closed (ToolHost)."""
        return (0, 0) if (mask := self.model.mask) is None else (mask.shape[1], mask.shape[0])

    def eraser_footprint_radius(self) -> int:
        """Erase radius, image px: half the eraser diameter, as the ring shows (ToolHost)."""
        return max(1, int(round(self._eraser / 2.0)))

    def render_skeleton(self, *, full: bool = True) -> None:
        """Show the model (its changed box only); ``full``: markers too, host told (ToolHost)."""
        self.item.set_mask(self.model.mask, changed=self.model.take_changed_box())
        if full:
            self.decorations.set_endpoints(self.model.endpoint_array())
            self.decorations.set_endpoints_visible(self._shown)
            self._changed()

    def schedule_render(self) -> None:
        """Coalesce drag repaints: one item update per RENDER_INTERVAL_MS (ToolHost)."""
        if not self._timer.isActive():
            self._timer.start()

    def refresh_draft(self, cursor: QPoint | None = None) -> None:
        """Redraw the polyline preview and its handles (ToolHost)."""
        points = self.draft.points  # none: empty paths, as clear_polyline() gives
        self.decorations.show_polyline(points, cursor if points else None, smooth=self.draft.smooth)
        self._changed()

    def switch_tool(self, tool_id: str, *, keep_draft: bool = False) -> None:
        """A tool changes the tool (Select -> Polyline); its drag goes on in the new tool."""
        handover = self._drag is not None
        self._activate_tool(tool_id, keep_draft)
        self._drag = self._tools[tool_id] if handover else None

    def show_eraser_ring(self, pos: QPoint | None) -> None:
        """The eraser footprint at ``pos``; hidden for None, other tools, panning, reference."""
        ring, show = self.eraser_ring, pos is not None and self.current_tool == ERASER
        show = show and self._shown and self.model.mask is not None and not self._host.is_panning()
        if show:
            ring.set_diameter(self._eraser)
            ring.move_to_pixel(pos)
        ring.set_visible(show)

    hover = show_eraser_ring  # EditLayer: the ring is the layer's only hover feedback

    def open(self, image_path, size, images_base_folder, ahead=None) -> list[Notice]:
        """Open the skeleton of ``image_path``: the GT skeleton, else the prediction."""
        read = ahead if isinstance(ahead, SkeletonRead) else None  # a read_ahead payload
        return self._reload(self.document.open, image_path, size, images_base_folder, read)

    def close(self) -> None:
        """Forget the image (a drag in progress is finished first)."""
        self._reload(self.document.close)

    def revert(self) -> list[Notice]:
        """Discard: re-read the start file; the history and the draft are cleared. A skeleton
        as saved, with only a polyline draft pending, loses the draft and keeps its history."""
        self.finish_gesture()  # an eraser drag in progress is an edit of the skeleton
        if not self.draft.pending() or self.document.is_dirty():
            return self._reload(self.document.reopen)
        self.drop_transient()
        return []

    def load_file(self, path: str) -> list[Notice]:
        """Open another skeleton file, unsaved (history forgotten); unreadable: no change."""
        self.finish_gesture()
        notices = self.document.load_file(path)
        if self.document.last_load_ok:
            self.drop_transient()
            self.render_skeleton()
        return notices

    def load_prediction(self) -> list[Notice]:
        """Load prediction as one undoable action; unreadable or shown already: said, no change."""
        plane, notices = self.document.read_prediction()
        if plane is not None:
            self._reload(self.document.load_prediction, plane)
        for notice in notices:
            self.notify(notice.message, notice.kind)
        return notices

    def _reload(self, load: Callable, *args):
        self.finish_gesture()
        self.drop_transient()
        notices = load(*args)
        self.render_skeleton()
        return notices

    def drop_transient(self) -> None:  # the document is replaced: a lost draft is told
        notice = self.draft.drop_notice(self.DRAFT_DISCARDED_NOTICE)  # a plain pick loses nothing
        self.cancel_transient()
        if notice:
            self.notify(notice, "info")

    def is_dirty(self) -> bool:
        """The skeleton differs from its file, or a polyline draft is pending (S5)."""
        return self.document.is_dirty() or self.draft.pending()

    def status(self) -> LayerStatus:
        """The document's save state; a pending polyline draft reads as unsaved (S5)."""
        return self.document.status(pending_edit=self.draft.pending())

    def save(self) -> SaveOutcome:
        """Save the GT unless it is the prediction (O2); a drag ends and a draft commits first."""
        self.finish_gesture()
        if self.draft.pending():  # Save keeps what the user drew (S5)
            self._tools[POLYLINE].commit()
        result = self.document.save()
        self._changed()
        return self.document.outcome(result)

    def clear(self) -> bool:
        """Erase every skeleton line as one undoable action; False when nothing is open."""
        if not self.is_open():
            return False
        self.finish_gesture()
        self.cancel_transient()
        self.model.push_undo()
        self.model.clear()
        self.render_skeleton()
        self.notify(self.CLEARED_NOTICE, "info")
        return True

    def undo(self) -> bool:
        """Undo one snapshot; False only when there is none. A polyline draft is dropped."""
        return self._step(self.model.undo)

    def redo(self) -> bool:
        """Redo one snapshot; False only when there is none. A polyline draft is dropped."""
        return self._step(self.model.redo)

    def _step(self, step: Callable[[], bool]) -> bool:
        # Today's _step_history; a draft it drops while out of sight is announced.
        self.finish_gesture()
        hidden_draft = self.draft.holds_work() and not self._shown
        if not step():
            return False
        self.cancel_transient()
        self.render_skeleton()
        if hidden_draft:
            self.notify(self.DRAFT_DISCARDED_NOTICE, "info")
        return True

    def drop_redo(self) -> None:
        """Forget the redo steps: another layer's new action ended this branch."""
        self.model.drop_redo()
        self._changed()

    def undo_in_gesture(self) -> bool:
        """Mid-polyline undo: drop the draft's last point (True when there was one)."""
        if self.current_tool != POLYLINE or not self.draft.points:
            return False
        self.draft.points.pop()
        if not self.draft.points:
            self.draft.reset()
        self.refresh_draft(self.draft.points[-1] if self.draft.points else None)
        return True

    def gesture_active(self) -> bool:
        """True during an eraser drag, a Connect drag and a polyline point or line drag."""
        tool, draft = self._drag, self.draft
        return tool is not None and (
            (tool.tool_id == ERASER and tool.active)
            or (tool.tool_id == CONNECT and tool.first is not None)
            or (tool.tool_id == POLYLINE and (draft.drag_index is not None or draft.translating))
        )

    def finish_gesture(self) -> None:
        """End a drag as a release at its last position would; the rest of it is ignored."""
        if self.gesture_active():
            tool, self._drag = self._drag, None
            tool.release(QPoint(self._last_pos))

    def cancel_transient(self) -> None:
        """Esc, as today: an eraser drag keeps its erasure; a Connect drag and the draft go."""
        self._drag = None
        self._tools[self.current_tool].deactivate()
        self.draft.reset()
        self.decorations.clear_transient()
        self._timer.stop()
        self.refresh_draft()

    cancel_polyline = cancel_transient  # the prompt's Cancel: drop the draft

    def set_tool(self, tool_id: str) -> None:
        """Make ``tool_id`` current: a drag is finished first, the draft is reset."""
        if tool_id not in self._tools:
            raise ValueError(f"unknown skeleton tool {tool_id!r}")
        self.finish_gesture()
        self._drag = None
        self._activate_tool(tool_id, False)

    def tool_hint(self) -> tuple[str, str]:
        """``(hint, tooltip)`` for the chip: today's texts, the empty-skeleton hint included."""
        mask = self.model.mask
        if not self.is_open() or mask is None:
            return CLOSED_HINT
        if not self.draft.points and self.current_tool != POLYLINE and not mask.any():
            return EMPTY_HINT
        return self._tools[self.current_tool].hint()

    def size_spec(self) -> SizeSpec | None:
        """The eraser's size slider; None for the other tools."""
        return self.ERASER_SIZE if self.current_tool == ERASER else None

    def size(self) -> int:
        """The eraser diameter, image px."""
        return self._eraser

    def set_size(self, value: int) -> None:
        """Set the eraser diameter, clamped to ERASER_RANGE (the ring follows)."""
        self._eraser = max(self.ERASER_RANGE[0], min(self.ERASER_RANGE[1], int(value)))
        self.eraser_ring.set_diameter(self._eraser)
        self._changed()

    def step_size(self, direction: int) -> None:
        """Grow (+1) or shrink (-1) the eraser by 12 %, at least 1 px, clamped."""
        step = max(1, round(self._eraser * _STEP))
        self.set_size(self._eraser + (step if direction > 0 else -step))

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Mouse press of the current tool; the drag that follows belongs to it."""
        if self.model.mask is not None:
            self.finish_gesture()  # a drag whose release never came
            self._last_pos, self._drag = pos, self._tools[self.current_tool]
            self._drag.press(pos, modifiers)

    def move(self, pos: QPoint, dragging: bool) -> None:
        """Hover or drag to ``pos``; the rest of a finished drag only moves the ring."""
        if self.model.mask is not None:
            self._last_pos = pos
            if not dragging:
                self._tools[self.current_tool].move(pos)
            elif self._drag is not None:
                self._drag.move(pos)
            else:
                self.show_eraser_ring(pos)

    def release(self, pos: QPoint) -> None:
        """End the drag at ``pos`` (nothing after a forced finish)."""
        tool, self._drag = self._drag, None
        if tool is not None and self.model.mask is not None:
            self._last_pos = pos
            tool.release(pos)

    def double_click(self, pos: QPoint) -> None:
        """Double-click of the current tool (it finishes a polyline)."""
        if self.model.mask is not None:
            self._last_pos = pos
            self._tools[self.current_tool].double_click(pos)

    def key(self, event: QKeyEvent) -> bool:
        """S toggles Smooth in Polyline; Enter, Delete, Backspace go to the tool. True: used."""
        if self.model.mask is None:
            return False
        smooth = event.key() == Qt.Key.Key_S and not event.modifiers() & _NO_KEY_MODS
        if smooth and self.current_tool == POLYLINE:
            self.set_smooth(not self.draft.smooth)
            return True
        return self._tools[self.current_tool].key(event)

    def set_active(self, active: bool) -> None:
        """Edited layer (True) or dimmed reference: markers, previews, ring hidden, draft kept."""
        self._active = bool(active)
        self._sync_view()

    def is_active(self) -> bool:
        """True while this is the edited layer."""
        return self._active

    def set_visible(self, visible: bool) -> None:
        """Show or hide the layer (hiding finishes a drag first)."""
        if not visible:
            self.finish_gesture()
        self._visible = bool(visible)
        self._sync_view()

    def is_visible(self) -> bool:
        """True while the layer is shown."""
        return self._visible

    def opacity(self) -> float:
        """The user's opacity 0..1 (the reference layer is drawn at REFERENCE_DIM of it)."""
        return self._opacity

    def set_opacity(self, value: float) -> None:
        """Set the user's opacity, clamped to 0..1."""
        self._opacity = max(0.0, min(1.0, float(value)))
        self.item.setOpacity(self._opacity * (1.0 if self._active else REFERENCE_DIM))

    def dialog_start(self) -> str:
        """Where the Load dialog starts: the prediction, else skeletons/ ("" if closed)."""
        return self.document.files().dialog_start() if self.is_open() else ""

    def draft_state(self) -> DraftState:
        """The polyline draft as the prompt shows it."""
        draft = self.draft
        return DraftState(self.current_tool == POLYLINE, len(draft.points), draft.smooth)

    def finish_polyline(self) -> None:
        """Commit the draft (Enter, double-click, the prompt): Polyline with 2+ points."""
        if self.current_tool == POLYLINE and len(self.draft.points) >= 2:
            self._tools[POLYLINE].commit()

    def set_smooth(self, on: bool) -> None:
        """Set the draft's Smooth toggle; its preview follows."""
        self.draft.smooth = bool(on)
        self.refresh_draft(self.draft.points[-1] if self.draft.points else None)

    def _activate_tool(self, tool_id: str, keep_draft: bool) -> None:
        # Today's _activate_tool: the old tool drops its state, the draft resets unless kept.
        self._tools[self.current_tool].deactivate()
        if not keep_draft:
            self.draft.reset()
        self.current_tool = tool_id
        self.show_eraser_ring(None)
        self.decorations.clear_connect()
        self.decorations.highlight_endpoint(None)
        self.refresh_draft()

    def _sync_view(self) -> None:
        # Item visibility and opacity; markers, previews and ring only when shown and active.
        self._shown = shown = self._active and self._visible
        self.item.setVisible(self._visible)
        self.set_opacity(self._opacity)
        deco = self.decorations
        for item in (deco.polyline_item, deco.handles_item, deco.highlight_item):
            item.setVisible(shown)
        deco.set_endpoints_visible(shown)
        if not shown:
            deco.clear_connect()
            self.eraser_ring.set_visible(False)

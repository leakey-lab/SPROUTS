"""Editing tools of the skeleton editor (AR-12 step 2 and 4).

Each tool is a small State object: the editor forwards pointer and key input to the
active tool (``editor.tools[editor.current_tool]``) instead of branching on the tool
name in every handler. Tools act on the editor through the narrow :class:`ToolHost`
protocol, and keep their own transient state; the polyline being drawn or edited is a
:class:`PolylineDraft` shared with the editor (the Select tool fills it, the Polyline
tool edits and commits it).

Positions are image pixels (``QPoint``), already clamped by the view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QKeyEvent

from app.editing.geometry import (
    nearest_polyline_index,
    nearest_segment_index,
    nearest_vertex_index,
    sample_catmull_rom,
)

if TYPE_CHECKING:
    from app.gui.skeleton_graph_model import SkeletonCorrectionModel
    from app.gui.skeleton_overlay import SkeletonDecorations

SELECT = "select"
ERASER = "eraser"
POLYLINE = "polyline"
CONNECT = "connect"

# Single-letter tool keys (QuPath / napari style), shown in the rail tooltips.
TOOL_KEYS: dict[str, str] = {SELECT: "V", ERASER: "E", POLYLINE: "P", CONNECT: "C"}
SMOOTH_KEY = "S"

HANDLE_HIT_PX = 8  # grab a polyline point
BEND_HIT_PX = 8  # Ctrl+click a segment to add a bend
MOVE_HIT_PX = 10  # Shift+drag a segment to move the line
PICK_HIT_PX = 12  # Select: pick a skeleton branch
ENDPOINT_HIT_PX = 10  # Connect: grab an endpoint
SMOOTH_STEP_PX = 0.75


def _xy(p: QPoint) -> tuple[int, int]:
    return (p.x(), p.y())


@dataclass
class PolylineDraft:
    """The polyline being drawn, or a picked branch being edited.

    ``edit_original`` holds the picked branch's pixels (erased when the edit is
    committed or deleted). ``smooth`` mirrors the Smooth toggle and survives
    :meth:`reset`.
    """

    points: list[QPoint] = field(default_factory=list)
    drag_index: int | None = None
    translating: bool = False
    anchor: QPoint | None = None
    orig_points: list[QPoint] = field(default_factory=list)
    smooth: bool = False
    edit_original: list[tuple[int, int]] | None = None

    def reset(self) -> None:
        """Forget the line and any drag (keeps the Smooth setting)."""
        self.points = []
        self.edit_original = None
        self.end_drag()

    def end_drag(self) -> None:
        self.drag_index = None
        self.translating = False
        self.anchor = None
        self.orig_points = []

    def xy(self) -> list[tuple[int, int]]:
        return [_xy(p) for p in self.points]

    def raster_points(self, bounds: tuple[int, int]) -> list[tuple[int, int]]:
        """Pixels to draw: the straight polyline, or the sampled curve when smooth."""
        if self.smooth and len(self.points) >= 3:
            return sample_catmull_rom(self.xy(), step_px=SMOOTH_STEP_PX, bounds=bounds)
        return self.xy()


class ToolHost(Protocol):
    """What a tool may use of the editor."""

    model: SkeletonCorrectionModel
    draft: PolylineDraft
    decorations: SkeletonDecorations
    draw_thickness: int

    def clamp_to_image(self, pt: QPoint) -> QPoint: ...
    def image_size(self) -> tuple[int, int]: ...
    def eraser_footprint_radius(self) -> int: ...
    def render_skeleton(self, *, full: bool = True) -> None: ...
    def schedule_render(self) -> None: ...
    def cancel_scheduled_render(self) -> None: ...
    def refresh_draft(self, cursor: QPoint | None = None) -> None: ...
    def refresh_hint(self) -> None: ...
    def switch_tool(self, tool_id: str, *, keep_draft: bool = False) -> None: ...
    def show_eraser_ring(self, pos: QPoint | None) -> None: ...
    def notify(self, message: str, kind: str = "info") -> None: ...


class SkeletonTool:
    """Base tool: ignores every input. Subclasses override what they use."""

    tool_id = ""

    def __init__(self, host: ToolHost) -> None:
        self.host = host

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        pass

    def move(self, pos: QPoint) -> None:
        pass

    def release(self, pos: QPoint) -> None:
        pass

    def double_click(self, pos: QPoint) -> None:
        pass

    def key(self, event: QKeyEvent) -> bool:
        return False

    def deactivate(self) -> None:
        """Drop transient state when another tool is chosen."""

    def hint(self) -> tuple[str, str]:
        """``(short hint, tooltip)`` for the status chip."""
        return ("Ready to edit skeleton.", "")


class SelectTool(SkeletonTool):
    """Pick a skeleton branch; it opens in the Polyline tool for editing or Delete."""

    tool_id = SELECT

    def press(self, pos, modifiers):
        host = self.host
        topo = host.model.topology(simplify_epsilon=1.5)
        i = nearest_polyline_index(topo.polylines, _xy(pos), PICK_HIT_PX)
        if i is None:
            host.refresh_hint()
            return
        branch = [(int(x), int(y)) for x, y in topo.polylines[i]]
        host.draft.reset()
        host.draft.edit_original = branch
        host.draft.points = [QPoint(x, y) for x, y in branch]
        host.switch_tool(POLYLINE, keep_draft=True)
        host.refresh_draft()

    def hint(self):
        return (
            "Select · click a skeleton line to edit it",
            f"Click near a skeleton line to pick it: drag its points, press {SMOOTH_KEY} to "
            "smooth it and Enter to commit, or press Delete to remove the whole branch.",
        )


class EraserTool(SkeletonTool):
    """Drag to erase; each move erases the whole segment from the previous point (PE-02)."""

    tool_id = ERASER

    def __init__(self, host):
        super().__init__(host)
        self._last: tuple[int, int] | None = None

    @property
    def active(self) -> bool:
        return self._last is not None

    def press(self, pos, modifiers):
        host = self.host
        host.model.push_undo()
        self._last = _xy(pos)
        host.model.erase_segment(self._last, self._last, host.eraser_footprint_radius())
        host.decorations.set_endpoints_visible(False)
        host.render_skeleton(full=False)

    def move(self, pos):
        host = self.host
        host.show_eraser_ring(pos)
        if self._last is None:
            return
        pt = _xy(pos)
        host.model.erase_segment(self._last, pt, host.eraser_footprint_radius())
        self._last = pt
        host.schedule_render()

    def release(self, pos):
        if self._last is None:
            return
        self._last = None
        host = self.host
        host.cancel_scheduled_render()
        host.model.skeletonize()  # clean to 1 px for stable endpoints
        host.render_skeleton(full=True)

    def deactivate(self):
        if self._last is not None:
            self.release(QPoint(*self._last))
        self.host.show_eraser_ring(None)

    def hint(self):
        return (
            "Eraser · drag over the skeleton to erase it",
            "Click and drag to erase skeleton pixels. [ and ] change the eraser size.",
        )


class PolylineTool(SkeletonTool):
    """Click points, drag them, Ctrl+click to bend, Shift+drag to move; Enter commits."""

    tool_id = POLYLINE

    def press(self, pos, modifiers):
        host, draft = self.host, self.host.draft
        pts = draft.xy()
        hit = nearest_vertex_index(pts, _xy(pos), HANDLE_HIT_PX)
        if hit is not None:
            draft.end_drag()
            draft.drag_index = hit
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier and len(pts) >= 2:
            seg = nearest_segment_index(pts, _xy(pos), BEND_HIT_PX)
            if seg is not None:
                draft.points.insert(seg + 1, QPoint(pos))
                host.refresh_draft(pos)
                return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and len(pts) >= 2:
            if nearest_segment_index(pts, _xy(pos), MOVE_HIT_PX) is not None:
                draft.end_drag()
                draft.translating = True
                draft.anchor = QPoint(pos)
                draft.orig_points = [QPoint(p) for p in draft.points]
                return
        draft.points.append(QPoint(pos))
        host.refresh_draft(pos)

    def move(self, pos):
        host, draft = self.host, self.host.draft
        if not draft.points:
            return
        if draft.drag_index is not None:
            draft.points[draft.drag_index] = QPoint(pos)
        elif draft.translating and draft.anchor is not None:
            dx, dy = pos.x() - draft.anchor.x(), pos.y() - draft.anchor.y()
            draft.points = [
                host.clamp_to_image(QPoint(p.x() + dx, p.y() + dy)) for p in draft.orig_points
            ]
        host.refresh_draft(pos)

    def release(self, pos):
        draft = self.host.draft
        draft.end_drag()
        if draft.points:
            self.host.refresh_draft(pos)

    def double_click(self, pos):
        draft = self.host.draft
        if not draft.points:
            return
        if pos != draft.points[-1]:
            draft.points.append(QPoint(pos))
        self.commit()

    def key(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and len(self.host.draft.points) >= 2:
            self.commit()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.host.draft.edit_original:
            self.delete_branch()
            return True
        return False

    def _erase_thickness(self) -> int:
        return max(5, int(self.host.draw_thickness) + 6)

    def commit(self) -> None:
        """Draw the draft into the skeleton (replacing the picked branch) as one undo step."""
        host, draft = self.host, self.host.draft
        if len(draft.points) < 2:
            draft.reset()
            host.refresh_draft()
            return
        pts = draft.raster_points(host.image_size())
        host.model.push_undo()
        if draft.edit_original and len(draft.edit_original) >= 2:
            host.model.erase_polyline(draft.edit_original, self._erase_thickness())
        host.model.draw_polyline(pts, thickness=host.draw_thickness)
        host.model.skeletonize()
        draft.reset()
        host.refresh_draft()
        host.render_skeleton(full=True)

    def delete_branch(self) -> None:
        """Remove the picked branch (UXE-12): one undo step."""
        host, draft = self.host, self.host.draft
        branch = draft.edit_original
        if not branch:
            return
        host.model.push_undo()
        if len(branch) >= 2:
            host.model.erase_polyline(branch, self._erase_thickness())
        else:
            host.model.erase_segment(branch[0], branch[0], self._erase_thickness() // 2)
        host.model.skeletonize()
        draft.reset()
        host.refresh_draft()
        host.render_skeleton(full=True)
        host.notify("Branch deleted · Ctrl+Z to undo", "info")

    def deactivate(self):
        self.host.draft.reset()

    def hint(self):
        draft = self.host.draft
        n = len(draft.points)
        help_text = (
            "Click to place points. Drag points to adjust. Ctrl+click a segment to add a "
            "bend. Shift+drag a segment to move the whole line. Enter or double-click "
            f"finishes the line, Esc cancels it, {SMOOTH_KEY} smooths it into a curve "
            "(3+ points)."
        )
        if draft.edit_original:
            text = (
                f"Editing branch · {SMOOTH_KEY} smooths · Enter commits · Delete removes it · "
                "Esc cancels"
            )
        elif n == 0:
            text = "Polyline · click to place points · Ctrl+click a segment adds a bend"
        elif n == 1:
            text = "Polyline · 1 point · click to add more"
        else:
            text = f"Polyline · {n} points · drag points to adjust"
        if draft.smooth and n < 3:
            text += " · Smooth needs 3+ points"
        return (text, help_text)


class ConnectTool(SkeletonTool):
    """Drag from an endpoint to join it to another point with a straight line."""

    tool_id = CONNECT

    def __init__(self, host):
        super().__init__(host)
        self.first: QPoint | None = None

    def _nearest_endpoint(self, pos: QPoint) -> QPoint | None:
        eps = self.host.model.endpoints()
        i = nearest_vertex_index(eps, _xy(pos), ENDPOINT_HIT_PX)
        return None if i is None else QPoint(*eps[i])

    def press(self, pos, modifiers):
        ep = self._nearest_endpoint(pos)
        if ep is None:
            return
        self.first = ep
        self.host.decorations.highlight_endpoint(ep)
        self.host.decorations.show_connect(ep, pos)
        self.host.refresh_hint()

    def move(self, pos):
        if self.first is not None:
            self.host.decorations.show_connect(self.first, pos)

    def release(self, pos):
        if self.first is None:
            return
        first, self.first = self.first, None
        host = self.host
        host.decorations.clear_connect()
        host.decorations.highlight_endpoint(None)
        host.model.push_undo()
        host.model.draw_polyline([_xy(first), _xy(pos)], thickness=host.draw_thickness)
        host.model.skeletonize()
        host.render_skeleton(full=True)
        host.refresh_hint()

    def key(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.first is not None:
            self.deactivate()
            self.host.refresh_hint()
            return True
        return False

    def deactivate(self):
        self.first = None
        self.host.decorations.clear_connect()
        self.host.decorations.highlight_endpoint(None)

    def hint(self):
        if self.first is None:
            return ("Connect · drag from an endpoint (orange dot)", "")
        return ("Connect · release on the point to join", "")


def build_tools(host: ToolHost) -> dict[str, SkeletonTool]:
    """One instance of every tool, keyed by tool id."""
    return {cls.tool_id: cls(host) for cls in (SelectTool, EraserTool, PolylineTool, ConnectTool)}

"""Contract between the Correct workspace and its edit layers (Qt-free at import).

The workspace (session, router, editor) drives each layer only through EditLayer; a
layer calls back only through LayerHost. Positions are image pixels already clamped
by the canvas view.

Rules every layer follows:

- ``LayerHost.layer_edited`` fires once per undoable action, when the action is
  recorded in the layer's own history; never for undo, redo, open, revert or save.
- ``LayerHost.layer_changed`` fires after anything the UI shows changed (dirty state,
  status, tool, hint, draft, can_undo / can_redo): at the end of a gesture and after a
  full render, never for the intermediate renders of a drag.
- ``undo()`` / ``redo()`` step exactly one entry of the layer's own history and return
  True iff they did; False only when that stack is empty (the undo journal reads False
  as "evicted" and drops the entry). They never pop draft points; only
  ``undo_in_gesture()`` does.
- ``set_active(False)`` hides rings, markers and previews but keeps a polyline draft.
- Mid-gesture rule: a gesture is a mouse-down drag. ``finish_gesture()`` ends it as a
  release at the last pointer position would, and the layer then ignores the rest of
  that drag until the next ``press``. A layer's own ``set_tool``, ``set_visible(False)``,
  ``save``, ``revert``, ``open`` and ``close`` finish a gesture first themselves.
  Nothing is ever dropped silently.
- Optional (``ReadAheadLayer``): a layer with ``read_ahead`` lets the page read the
  document of the next image on a worker thread and open it from that payload.
- Optional (``DiskGuardedLayer``): a layer with ``unloaded_label`` names a file it found
  but did not show; the session asks before a save replaces it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QKeyEvent

MASK = "mask"
SKELETON = "skeleton"
LAYER_IDS: tuple[str, ...] = (MASK, SKELETON)  # scene order bottom-to-top, panel order, keys 1 / 2
REFERENCE_DIM = 0.5  # the inactive layer is drawn at its opacity times this


@dataclass(frozen=True)
class Notice:
    """A transient message for the user (shown as a toast)."""

    message: str
    kind: str = "info"


@dataclass(frozen=True)
class LayerStatus:
    """Save state of one layer: StatusChip state + optional label and tooltip."""

    state: str  # "none" | "clean" | "dirty" | "error"
    label: str | None = None
    tooltip: str = ""


@dataclass(frozen=True)
class SaveOutcome:
    """What one layer's save did."""

    layer_id: str
    ok: bool
    path: str | None = None  # the file written; None when nothing was written
    cleared: bool = False  # an empty mask removed the file, or a skeleton correction was removed
    message: str = ""  # detail for a toast ("" = nothing beyond the summary)
    kind: str = "success"
    declined: bool = False  # the user declined the write (ok is False); nothing was written


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the rail shows it."""

    tool_id: str
    icon: str  # resources/icons/sprouts/<icon>.svg
    label: str
    key: str  # its registry key, shown in the tooltip
    tip: str  # tooltip tail
    sized: bool  # the dock size slider applies to this tool


@dataclass(frozen=True)
class SizeSpec:
    """The dock size slider of the current tool."""

    icon: str
    tooltip: str
    minimum: int
    maximum: int
    fmt: str = "{} px"


class LayerHost(Protocol):
    """What a layer may ask of the workspace."""

    def notify(self, message: str, kind: str = "info") -> None:
        """Show ``message`` as a toast; ``kind`` is success, info, warn or danger."""

    def layer_edited(self, layer_id: str) -> None:
        """A new undoable action was recorded in the layer's own history."""

    def layer_changed(self, layer_id: str) -> None:
        """Something the UI shows of the layer changed (never per drag frame)."""

    def is_panning(self) -> bool:
        """True while the canvas pans instead of editing (rings stay hidden)."""


class LayerTool(Protocol):
    """One tool of a layer (app.gui.skeleton_tools.SkeletonTool already has this shape)."""

    tool_id: str

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Mouse press at ``pos`` (image pixels)."""

    def move(self, pos: QPoint) -> None:
        """Mouse move to ``pos``."""

    def release(self, pos: QPoint) -> None:
        """Mouse release at ``pos``."""

    def double_click(self, pos: QPoint) -> None:
        """Double-click at ``pos``."""

    def key(self, event: QKeyEvent) -> bool:
        """A gesture key; True when the tool consumed it."""

    def deactivate(self) -> None:
        """The tool is being left: end or drop its transient state."""

    def hint(self) -> tuple[str, str]:
        """``(short hint, tooltip)`` for the status chip."""


class EditLayer(Protocol):
    """One editable document drawn on the shared canvas (the mask or the skeleton)."""

    layer_id: str
    noun: str  # "mask" | "skeleton" (prompts and toasts)
    color: str  # tokens.MASK | tokens.SKEL
    tools: tuple[ToolSpec, ...]
    current_tool: str

    # document
    def open(
        self, image_path: str, size: tuple[int, int], images_base_folder: str | None
    ) -> list[Notice]:
        """Open the layer's document for an image of ``size`` ``(w, h)``; notices to show."""

    def close(self) -> None:
        """Forget the document (a gesture in progress is finished first)."""

    def is_open(self) -> bool:
        """True while a document is open."""

    def file_stamps(self) -> tuple:
        """The pinned file set of C13 / C14, read afresh per call (``()`` when closed)."""

    def is_dirty(self) -> bool:
        """True when the document differs from its saved state."""

    def save(self) -> SaveOutcome:
        """Write the document (a gesture in progress is finished first)."""

    def revert(self) -> list[Notice]:
        """Discard: back to the saved state, history cleared; notices to show."""

    def status(self) -> LayerStatus:
        """The save state the Layers row and the status chip show."""

    def clear(self) -> bool:
        """Undoable Clear; False when nothing is open."""

    # history (the layer's own stack)
    def can_undo(self) -> bool:
        """True when the layer's own undo stack has an entry."""

    def can_redo(self) -> bool:
        """True when the layer's own redo stack has an entry."""

    def undo(self) -> bool:
        """Undo exactly one entry of the layer's own stack; False only when it is empty."""

    def redo(self) -> bool:
        """Redo exactly one entry of the layer's own redo stack; False only when empty."""

    def drop_redo(self) -> None:
        """Forget the redo stack: another layer's new action ended this branch."""

    def undo_in_gesture(self) -> bool:
        """Polyline draft: pop its last point (True if done)."""

    # gestures (the mid-gesture rule of the module docstring)
    def gesture_active(self) -> bool:
        """True while a mouse-down drag is in progress."""

    def finish_gesture(self) -> None:
        """End the drag as a release at the last position would; no-op without one."""

    def cancel_transient(self) -> None:
        """Esc: the mask commits a stroke; the skeleton drops drafts and selections."""

    # tools and input (image pixels)
    def set_tool(self, tool_id: str) -> None:
        """Switch to ``tool_id`` (a gesture in progress is finished first)."""

    def tool_hint(self) -> tuple[str, str]:
        """``(short hint, tooltip)`` of the current tool, for the chip."""

    def size_spec(self) -> SizeSpec | None:
        """The dock size slider of the current tool; None when the tool has no size."""

    def size(self) -> int:
        """Mask: brush size; skeleton: eraser diameter (px)."""

    def set_size(self, value: int) -> None:
        """Set the size, clamped to the layer's range."""

    def step_size(self, direction: int) -> None:
        """Grow (+1) or shrink (-1) the size by 12 %, at least 1 px, clamped."""

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Mouse press of the current tool at ``pos``."""

    def move(self, pos: QPoint, dragging: bool) -> None:
        """Hover (``dragging`` False) or drag (True) to ``pos``."""

    def release(self, pos: QPoint) -> None:
        """Mouse release at ``pos`` (ignored after a forced finish)."""

    def double_click(self, pos: QPoint) -> None:
        """Double-click at ``pos``."""

    def key(self, event: QKeyEvent) -> bool:
        """Gesture keys (S, Enter, Delete, Backspace); True when consumed."""

    def hover(self, pos: QPoint | None) -> None:
        """Show the tool ring at ``pos``; None hides it."""

    # view
    def set_active(self, active: bool) -> None:
        """Edited layer (True) or reference layer (dimmed; rings and previews hidden)."""

    def is_active(self) -> bool:
        """True while this is the edited layer."""

    def set_visible(self, visible: bool) -> None:
        """Show or hide the layer (hiding finishes a gesture first)."""

    def is_visible(self) -> bool:
        """True while the layer is shown."""

    def opacity(self) -> float:
        """The user's opacity 0..1 (not the dimmed one)."""

    def set_opacity(self, value: float) -> None:
        """Set the user's opacity 0..1."""


class ReadAheadLayer(Protocol):
    """Optional part of an ``EditLayer``: its document read ahead, off the GUI thread.

    ``read_ahead`` runs on the GUI thread (it may ask the page, e.g. a resolver) and returns
    a reader for the worker of :mod:`app.gui.correct.read_ahead`, or None. The reader,
    ``reader((w, h))``, reads the files ``open`` would read and computes what ``open`` would
    compute from them; it must not touch the layer, nor make or hold a Qt object (the
    worker thread drops it). Its payload comes back as ``open(..., ahead=payload)``, which
    adopts it only while it still matches the files on disk (the layer checks) and
    otherwise reads them itself, as without it.
    """

    def read_ahead(
        self, image_path: str, images_base_folder: str | None
    ) -> Callable[[tuple[int, int]], object] | None:
        """A worker reader of ``image_path``'s document, or None (nothing to read ahead)."""

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        images_base_folder: str | None,
        ahead: object | None = None,
    ) -> list[Notice]:
        """``EditLayer.open``, from ``ahead`` (a reader's payload) when it is current."""


class DiskGuardedLayer(Protocol):
    """Optional part of an ``EditLayer``: a file on disk its document did not show.

    A layer that could not load a file it found (a mask of another size than the photo,
    an undecodable one) names the file a save would replace without having shown it, so
    the session asks first (``app.gui.correct.save_gate``).
    """

    def unloaded_label(self) -> str:
        """The project-relative file (``"mask/a.png"``, ``/`` separators) a save would
        replace unshown; ``""`` when none (also while the plane is empty: that save keeps
        the file)."""


def _declared_members(protocol: type) -> dict[str, bool]:
    """Public names ``protocol`` and its protocol bases declare -> True for methods."""
    members: dict[str, bool] = {}
    for cls in reversed(protocol.__mro__):
        if cls is object or cls.__module__ == "typing":
            continue
        for name in vars(cls).get("__annotations__", {}):
            if not name.startswith("_"):
                members.setdefault(name, False)
        for name, value in vars(cls).items():
            if not name.startswith("_"):
                members.setdefault(name, callable(value) and not isinstance(value, property))
    return members


def missing_members(obj: object, protocol: type = EditLayer) -> list[str]:
    """Names declared by ``protocol`` (attributes and methods) that ``obj`` lacks.

    In declaration order. A declared method counts as missing when ``obj`` has no
    callable of that name; a declared attribute may hold any value.
    """
    missing = []
    for name, is_method in _declared_members(protocol).items():
        try:
            value = getattr(obj, name)
        except AttributeError:
            missing.append(name)
            continue
        if is_method and not callable(value):
            missing.append(name)
    return missing

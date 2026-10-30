"""Test doubles for the Correct workspace (plan C18); Qt-free.

``FakeLayer`` implements ``app.gui.correct.layer.EditLayer``:

- **Call recording.** Every command is appended to ``calls`` as ``(name, *args)``; the
  side-effect-free queries (``is_open``, ``is_dirty``, ``file_stamps``, ``write_stamps``,
  ``matches_disk``, ``status``, ``can_undo``, ``can_redo``, ``gesture_active``,
  ``tool_hint``, ``size_spec``, ``size``, ``is_active``, ``is_visible``, ``opacity``) are
  not. ``names()`` lists the names only.
- **Scriptable results:** ``dirty``, ``stamps`` (``file_stamps`` and ``write_stamps``),
  ``open_notices``, ``revert_notices``, ``save_outcomes`` (consumed in order; then a
  default ok outcome with a path), ``key_result``, ``status_override``, ``draft_points``
  (what ``undo_in_gesture`` pops), ``unloaded`` (what ``unloaded_label`` names: a file a
  save would replace unshown), ``disk_same`` (what ``matches_disk`` says: reading the
  files now would open the saved state).
- **A bounded fake history** (``history_limit``, 25 like the skeleton's): ``act()``
  records one undoable action and reports it to the host; past the limit the oldest
  action is evicted, so ``undo()`` returns False once the stack is empty while a journal
  may still hold entries for it.
- **A scriptable gesture:** ``start_gesture(dirty_on_finish=...)`` makes
  ``gesture_active()`` True until ``finish_gesture()`` (or ``cancel_transient()``); with
  ``dirty_on_finish`` the finish records an action (``act()``), as a committed stroke
  does.

The fake never finishes its own gesture inside ``open`` / ``save`` / ``revert`` /
``close``, so a test proves that the *workspace* finishes gestures first.

``FakeHost`` is a recording ``LayerHost`` that can forward ``layer_edited`` /
``layer_changed`` to a session; ``FakePrompts`` answers the session's prompts from
scripted queues.
"""

from __future__ import annotations

from app.design import tokens
from app.gui.correct.layer import (
    CLASSES,
    MASK,
    SKELETON,
    LayerStatus,
    Notice,
    SaveOutcome,
    SizeSpec,
    ToolSpec,
)

MASK_TOOLS = (
    ToolSpec("brush", "brush", "Brush", "B", "hold B + scroll to resize", True),
    ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True),
    ToolSpec("fill", "fill", "Fill a closed outline", "F", "click inside a closed outline", False),
)
SKELETON_TOOLS = (
    ToolSpec("select", "cursor", "Select", "V", "pick a branch, Delete removes it", False),
    ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True),
    ToolSpec("polyline", "polyline", "Polyline", "P", "Enter finish · Esc cancel", False),
    ToolSpec("connect", "link", "Connect endpoints", "C", "drag from an endpoint", False),
)
MASK_SIZE = SizeSpec("brush", "Brush size", 1, 100)
SKELETON_SIZE = SizeSpec("erase", "Eraser diameter", 1, 80)

_NOUNS = {CLASSES: "class labels"}  # the real layers' nouns where they differ from the id
# layer id -> (tools, size spec, default size, default opacity, colour)
_KINDS = {
    MASK: (MASK_TOOLS, MASK_SIZE, 8, 0.60, tokens.MASK),
    SKELETON: (SKELETON_TOOLS, SKELETON_SIZE, 20, 0.85, tokens.SKEL),
    CLASSES: (MASK_TOOLS, MASK_SIZE, 8, 0.60, tokens.ACCENT),  # a raster layer, as the mask
}


class FakeLayer:
    """A scriptable, call-recording ``EditLayer`` (see the module docstring)."""

    def __init__(self, layer_id: str = MASK, *, host=None, history_limit: int = 25) -> None:
        tools, size_spec, size, opacity, color = _KINDS.get(layer_id, _KINDS[MASK])
        self.layer_id = layer_id
        self.noun = _NOUNS.get(layer_id, layer_id)
        self.color = color
        self.tools = tools
        self.current_tool = tools[0].tool_id
        self.host = host
        self.calls: list[tuple] = []
        # scriptable results
        self.dirty = False
        self.stamps: tuple = ()
        self.open_notices: list[Notice] = []
        self.revert_notices: list[Notice] = []
        self.save_outcomes: list[SaveOutcome] = []
        self.key_result = False
        self.status_override: LayerStatus | None = None
        self.draft_points = 0
        self.unloaded = ""
        self.disk_same = False
        self.gesture = False
        self.dirty_on_finish = False
        # state
        self.history_limit = history_limit
        self.undo_depth = 0
        self.redo_depth = 0
        self.opened = False
        self.active = False
        self.visible = True
        self._opacity = opacity
        self._size = size
        self._size_spec = size_spec

    # ------------------------------------------------------------ test helpers
    def names(self) -> list[str]:
        """The names of the recorded calls, in order."""
        return [call[0] for call in self.calls]

    def act(self) -> None:
        """One undoable action (a committed stroke, a polyline ...), reported to the host."""
        self.undo_depth = min(self.undo_depth + 1, self.history_limit)
        self.redo_depth = 0
        self.dirty = True
        if self.host is not None:
            self.host.layer_edited(self.layer_id)
        self._changed()

    def start_gesture(self, *, dirty_on_finish: bool = False) -> None:
        """A mouse-down drag begins; ``dirty_on_finish``: finishing it records an action."""
        self.gesture = True
        self.dirty_on_finish = dirty_on_finish

    def _record(self, name: str, *args) -> None:
        self.calls.append((name, *args))

    def _changed(self) -> None:
        if self.host is not None:
            self.host.layer_changed(self.layer_id)

    def _spec(self) -> ToolSpec:
        return next(t for t in self.tools if t.tool_id == self.current_tool)

    # ---------------------------------------------------------------- document
    def open(self, image_path, size, images_base_folder):
        """Open: clean, empty history; returns ``open_notices``."""
        self._record("open", image_path, size, images_base_folder)
        self.opened = True
        self.dirty = False
        self.undo_depth = self.redo_depth = self.draft_points = 0
        self._changed()
        return list(self.open_notices)

    def close(self):
        """Close: nothing open, clean, empty history."""
        self._record("close")
        self.opened = False
        self.dirty = False
        self.undo_depth = self.redo_depth = self.draft_points = 0
        self._changed()

    def is_open(self):
        """True between ``open`` and ``close``."""
        return self.opened

    def file_stamps(self):
        """The scripted ``stamps``."""
        return self.stamps

    def is_dirty(self):
        """The scripted ``dirty`` flag."""
        return self.dirty

    def save(self):
        """The next scripted outcome (default: ok with a path); ok clears ``dirty``."""
        self._record("save")
        if self.save_outcomes:
            outcome = self.save_outcomes.pop(0)
        else:
            outcome = SaveOutcome(self.layer_id, ok=True, path=f"{self.layer_id}.png")
        if outcome.ok:
            self.dirty = False
        self._changed()
        return outcome

    def unloaded_label(self):
        """``DiskGuardedLayer``: the scripted ``unloaded`` label."""
        return self.unloaded

    def write_stamps(self):
        """``DiskGuardedLayer``: the scripted ``stamps`` (the files a save writes)."""
        return self.stamps

    def matches_disk(self):
        """``DiskGuardedLayer``: the scripted ``disk_same`` flag."""
        return self.disk_same

    def revert(self):
        """Discard: clean, empty history, no draft; returns ``revert_notices``."""
        self._record("revert")
        self.dirty = False
        self.undo_depth = self.redo_depth = self.draft_points = 0
        self._changed()
        return list(self.revert_notices)

    def status(self):
        """``status_override``, else dirty / clean / none from the scripted state."""
        if self.status_override is not None:
            return self.status_override
        return LayerStatus("dirty" if self.dirty else "clean" if self.opened else "none")

    def clear(self):
        """An undoable Clear (one action); False when nothing is open."""
        self._record("clear")
        if not self.opened:
            return False
        self.act()
        return True

    # ----------------------------------------------------------------- history
    def can_undo(self):
        """True when the bounded fake history holds an action."""
        return self.undo_depth > 0

    def can_redo(self):
        """True when an undone action can be redone."""
        return self.redo_depth > 0

    def undo(self):
        """One step of the fake history; False only when it is empty."""
        self._record("undo")
        if self.undo_depth == 0:
            return False
        self.undo_depth -= 1
        self.redo_depth += 1
        self._changed()
        return True

    def redo(self):
        """One redo step; False only when nothing is undone."""
        self._record("redo")
        if self.redo_depth == 0:
            return False
        self.redo_depth -= 1
        self.undo_depth += 1
        self._changed()
        return True

    def drop_redo(self):
        """Forget the redo steps."""
        self._record("drop_redo")
        if self.redo_depth:
            self.redo_depth = 0
            self._changed()

    def undo_in_gesture(self):
        """Pop one of ``draft_points``; False when there is none."""
        self._record("undo_in_gesture")
        if self.draft_points == 0:
            return False
        self.draft_points -= 1
        self._changed()
        return True

    # ---------------------------------------------------------------- gestures
    def gesture_active(self):
        """The scripted gesture flag."""
        return self.gesture

    def finish_gesture(self):
        """End the gesture; with ``dirty_on_finish`` it records an action."""
        self._record("finish_gesture")
        if self.gesture:
            self.gesture = False
            if self.dirty_on_finish:
                self.act()

    def cancel_transient(self):
        """Drop the gesture and the draft."""
        self._record("cancel_transient")
        self.gesture = False
        self.draft_points = 0

    # ------------------------------------------------------------------- tools
    def set_tool(self, tool_id):
        """Switch ``current_tool``."""
        self._record("set_tool", tool_id)
        self.current_tool = tool_id
        self._changed()

    def tool_hint(self):
        """``(label, tip)`` of the current tool."""
        spec = self._spec()
        return spec.label, spec.tip

    def size_spec(self):
        """The layer's size spec for a sized tool, else None."""
        return self._size_spec if self._spec().sized else None

    def size(self):
        """The current size in px."""
        return self._size

    def set_size(self, value):
        """Set the size, clamped to the size spec."""
        self._record("set_size", value)
        self._size = self._clamp(value)

    def step_size(self, direction):
        """+-12 %, at least 1 px, clamped."""
        self._record("step_size", direction)
        step = max(1, round(self._size * 0.12))
        self._size = self._clamp(self._size + (step if direction > 0 else -step))

    def _clamp(self, value: int) -> int:
        return max(self._size_spec.minimum, min(self._size_spec.maximum, int(value)))

    # ------------------------------------------------------------------- input
    def press(self, pos, modifiers):
        """Recorded."""
        self._record("press", pos, modifiers)

    def move(self, pos, dragging):
        """Recorded."""
        self._record("move", pos, dragging)

    def release(self, pos):
        """Recorded."""
        self._record("release", pos)

    def double_click(self, pos):
        """Recorded."""
        self._record("double_click", pos)

    def key(self, event):
        """Recorded; returns ``key_result``."""
        self._record("key", event)
        return self.key_result

    def hover(self, pos):
        """Recorded."""
        self._record("hover", pos)

    # -------------------------------------------------------------------- view
    def set_active(self, active):
        """Recorded; sets ``active``."""
        self._record("set_active", active)
        self.active = bool(active)

    def is_active(self):
        """The ``active`` flag."""
        return self.active

    def set_visible(self, visible):
        """Recorded; sets ``visible``."""
        self._record("set_visible", visible)
        self.visible = bool(visible)

    def is_visible(self):
        """The ``visible`` flag."""
        return self.visible

    def opacity(self):
        """The user's opacity 0..1."""
        return self._opacity

    def set_opacity(self, value):
        """Recorded; clamped to 0..1."""
        self._record("set_opacity", value)
        self._opacity = max(0.0, min(1.0, float(value)))


class FakeHost:
    """A recording ``LayerHost``; forwards ``layer_edited`` / ``layer_changed`` to ``session``."""

    def __init__(self, session=None, *, panning: bool = False) -> None:
        self.session = session
        self.panning = panning
        self.calls: list[tuple] = []

    def notify(self, message, kind="info"):
        """Recorded as ``("notify", message, kind)``."""
        self.calls.append(("notify", message, kind))

    def layer_edited(self, layer_id):
        """Recorded; forwarded to the session."""
        self.calls.append(("layer_edited", layer_id))
        if self.session is not None:
            self.session.layer_edited(layer_id)

    def layer_changed(self, layer_id):
        """Recorded; forwarded to the session."""
        self.calls.append(("layer_changed", layer_id))
        if self.session is not None:
            self.session.layer_changed(layer_id)

    def is_panning(self):
        """The scripted ``panning`` flag."""
        return self.panning


class FakePrompts:
    """The session's prompts with scripted answers; records every question.

    ``unsaved``, ``after_failed``, ``replace`` and ``changed`` are queues of answers ("save",
    "discard", "cancel", "keep", "replace", "overwrite", "reload"); ``asked`` holds
    ``(prompt, parent, noun, name)`` per question (``name`` is the file label for "replace"
    and "changed").
    """

    def __init__(self, unsaved=(), after_failed=(), replace=(), changed=()) -> None:
        self.unsaved = list(unsaved)
        self.after_failed = list(after_failed)
        self.replace = list(replace)
        self.changed = list(changed)
        self.asked: list[tuple] = []

    def ask_unsaved(self, parent, noun, name):
        """The next ``unsaved`` answer ("cancel" when none is left)."""
        self.asked.append(("unsaved", parent, noun, name))
        return self.unsaved.pop(0) if self.unsaved else "cancel"

    def ask_after_failed_save(self, parent, noun, name):
        """The next ``after_failed`` answer ("keep" when none is left)."""
        self.asked.append(("after_failed", parent, noun, name))
        return self.after_failed.pop(0) if self.after_failed else "keep"

    def ask_replace_unloaded(self, parent, noun, label):
        """The next ``replace`` answer ("cancel", i.e. Keep it, when none is left)."""
        self.asked.append(("replace", parent, noun, label))
        return self.replace.pop(0) if self.replace else "cancel"

    def ask_changed_on_disk(self, parent, noun, label):
        """The next ``changed`` answer ("cancel" when none is left)."""
        self.asked.append(("changed", parent, noun, label))
        return self.changed.pop(0) if self.changed else "cancel"

"""Declarative keyboard-shortcut registry: the one table of every binding.

Each :class:`ShortcutSpec` names an action, its keys (Qt ``PortableText``), a
one-line description and the context (*scope*) it applies in. The F1 sheet
(:class:`app.gui.empty_state.ShortcutsDialog`) is generated from
:data:`REGISTRY`, and editors install their ``QShortcut`` objects with
:func:`install_shortcuts`, so a binding cannot exist without its documentation
(UXS-10 / UXE-07). ``tests/test_shell_info_shortcuts.py`` pins that every live
editor ``QShortcut`` appears here and that no two rows in overlapping scopes
share a key (two ``QShortcut`` on one key fire ``activatedAmbiguously`` and do
nothing — the old dead Ctrl+Y).

Kinds:

* ``shortcut`` — a ``QShortcut`` (installed through :func:`install_shortcuts`).
* ``key`` — handled in a ``keyPressEvent`` / event filter (Space-pan, hold-B,
  and the gesture keys S, Enter, Esc, Delete of the Correct canvas).
* ``gesture`` — mouse or wheel gestures, documentation only.

Scopes: :data:`GLOBAL` shortcuts are window-wide and therefore overlap every
other scope; the editor scopes (:data:`TRACE_GT`, :data:`CORRECT`) are
``WidgetWithChildrenShortcut`` on their own page, so only the visible editor's
keys are active and editors never collide. Stage accelerators are
``Alt+1 … Alt+6``, one per ribbon stage (not ``Ctrl+digit``: ``Ctrl+0`` /
``Ctrl+1`` are the canvas fit / 100 % keys and a window-level ``Ctrl+1`` would
make them ambiguous).

The module is Qt-free (the registry is plain data); only
:func:`install_shortcuts` imports PyQt6, lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtGui import QShortcut
    from PyQt6.QtWidgets import QWidget

__all__ = [
    "GLOBAL",
    "LIBRARY",
    "TRACE_GT",
    "CORRECT",
    "SCOPES",
    "STAGE_KEYS",
    "ShortcutSpec",
    "REGISTRY",
    "specs_for",
    "find_spec",
    "keys_for",
    "tooltip",
    "scope_for_stage",
    "find_conflicts",
    "install_shortcuts",
]

# Scope names equal the ribbon stage keys (app.gui.shell_chrome._STAGES).
GLOBAL = "Global"
LIBRARY = "Library"
TRACE_GT = "Trace GT"
CORRECT = "Correct"
SCOPES: tuple[str, ...] = (GLOBAL, LIBRARY, TRACE_GT, CORRECT)

Kind = Literal["shortcut", "key", "gesture"]
SHORTCUT: Kind = "shortcut"
KEY: Kind = "key"
GESTURE: Kind = "gesture"

#: Alt+1 … Alt+6 select the six ribbon stages in order (``shell_chrome.STAGES``).
STAGE_KEYS: tuple[str, ...] = tuple(f"Alt+{i}" for i in range(1, 7))


@dataclass(frozen=True)
class ShortcutSpec:
    """One documented binding.

    ``keys`` are Qt ``PortableText`` sequences (``"Ctrl+Shift+Z"``); for
    gestures they are free text (``"Ctrl+wheel"``). ``display`` overrides the
    sheet's key column when the key list is long (``"1–9, 0"``).
    """

    action: str
    keys: tuple[str, ...]
    description: str
    scope: str
    kind: Kind = SHORTCUT
    display: str | None = None

    @property
    def label(self) -> str:
        """Key column text for the F1 sheet."""
        return self.display or " / ".join(self.keys)


def _rows(scope: str, rows: Iterable[tuple]) -> tuple[ShortcutSpec, ...]:
    return tuple(ShortcutSpec(*row[:3], scope, *row[3:]) for row in rows)


def _history_rows() -> list[tuple]:
    return [
        ("undo", ("Ctrl+Z",), "Undo"),
        ("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo"),
    ]


def _canvas_rows(kind: Kind) -> list[tuple]:
    """Zoom / pan keys every canvas has (CanvasViewMixin handles them as view keys)."""
    return [
        ("fit", ("Ctrl+0",), "Fit the image to the window", kind),
        ("zoom_100", ("Ctrl+1",), "Zoom to 100 %", kind),
        ("zoom_in", ("Ctrl+=", "Ctrl++"), "Zoom in", kind),
        ("zoom_out", ("Ctrl+-",), "Zoom out", kind),
        ("pan", ("Space",), "Hold and drag to pan (middle-drag also pans)", KEY, "Space (hold)"),
        ("zoom_wheel", ("Ctrl+wheel",), "Zoom about the cursor", GESTURE),
    ]


def _paint_editor_rows() -> list[tuple]:
    """Trace GT's painting bindings (its ``RasterEditorBase`` installs them)."""
    return [
        *_history_rows(),
        ("save", ("Ctrl+S",), "Save"),
        ("brush", ("B",), "Brush (tap); hold B and scroll to resize the brush", KEY),
        ("eraser", ("E",), "Eraser"),
        ("fill", ("F",), "Fill a closed outline"),
        ("toggle_overlay", ("H",), "Show or hide the overlay"),
        ("brush_smaller", ("[",), "Smaller brush"),
        ("brush_larger", ("]",), "Larger brush"),
        *_canvas_rows(SHORTCUT),
    ]


def _correct_rows() -> list[tuple]:
    """The unified Correct stage: both layers, one scope (spec section 6)."""
    return [
        *_history_rows(),
        ("save", ("Ctrl+S",), "Save the unsaved layers (nothing unsaved: the active layer)"),
        ("mask_layer", ("1",), "Edit the mask (root area)"),
        ("skeleton_layer", ("2",), "Edit the skeleton (root length)"),
        ("swap_layer", ("X",), "Switch between the mask and the skeleton"),
        ("brush", ("B",), "Mask brush (tap); hold B and scroll to resize the brush", KEY),
        ("fill", ("F",), "Mask fill: fill a closed outline"),
        ("select_tool", ("V",), "Skeleton select: pick a branch"),
        ("polyline", ("P",), "Skeleton polyline"),
        ("connect", ("C",), "Skeleton connect: drag from an endpoint to another point"),
        ("eraser", ("E",), "Eraser of the active layer"),
        ("size_smaller", ("[",), "Smaller brush or eraser"),
        ("size_larger", ("]",), "Larger brush or eraser"),
        ("toggle_layers", ("H",), "Show or hide both layers (see the photo)"),
        ("toggle_reference", ("Shift+H",), "Show or hide the other layer"),
        ("smooth", ("S",), "Smooth the polyline (Polyline tool)", KEY),
        *_canvas_rows(SHORTCUT),
        ("finish_line", ("Enter",), "Finish the polyline", KEY),
        ("cancel", ("Esc",), "Cancel the current line or selection", KEY),
        (
            "delete_branch",
            ("Delete", "Backspace"),
            "Delete the picked branch (Select, then Delete); clears a Connect selection",
            KEY,
        ),
        ("add_bend", ("Ctrl+click",), "Add a bend to a polyline segment", GESTURE),
        ("move_line", ("Shift+drag",), "Move the whole polyline", GESTURE),
        ("finish_line_click", ("Double-click",), "Finish the polyline", GESTURE),
    ]


REGISTRY: tuple[ShortcutSpec, ...] = (
    *_rows(
        GLOBAL,
        [
            ("help", ("F1", "?"), "Show this keyboard-shortcut sheet"),
            ("open_folder", ("Ctrl+O",), "Open a folder of root images"),
            (
                "go_to_stage",
                STAGE_KEYS,
                "Go to stage 1–6 in ribbon order (Library … Visualize)",
                SHORTCUT,
                "Alt+1 … Alt+6",
            ),
            ("next_image", ("PgDown",), "Next image (editors save or ask first)"),
            ("previous_image", ("PgUp",), "Previous image"),
        ],
    ),
    *_rows(
        LIBRARY,
        [
            ("select_adjacent", ("Up", "Down"), "Select the previous / next image", KEY),
            ("expand", ("Left", "Right"), "Collapse / expand a folder", KEY),
        ],
    ),
    *_rows(
        TRACE_GT,
        [
            *_paint_editor_rows(),
            (
                "select_class",
                tuple(str(n) for n in (*range(1, 10), 0)),
                "Pick the n-th labelled class; 0 = Ignore",
                SHORTCUT,
                "1–9, 0",
            ),
        ],
    ),
    *_rows(CORRECT, _correct_rows()),
)


# ------------------------------------------------------------------ queries
def specs_for(scope: str, registry: Iterable[ShortcutSpec] = REGISTRY) -> tuple[ShortcutSpec, ...]:
    """Rows of one scope, in declaration order."""
    return tuple(s for s in registry if s.scope == scope)


def find_spec(action: str, scope: str) -> ShortcutSpec | None:
    """The row for ``action`` in ``scope`` (``None`` when there is none)."""
    return next((s for s in REGISTRY if s.action == action and s.scope == scope), None)


def keys_for(action: str, scope: str) -> tuple[str, ...]:
    """Keys of ``action`` in ``scope`` (empty when unbound)."""
    spec = find_spec(action, scope)
    return spec.keys if spec is not None else ()


def tooltip(text: str, action: str, scope: str) -> str:
    """``"Eraser (E)"`` — ``text`` plus the action's first key, if it has one."""
    keys = keys_for(action, scope)
    return f"{text} ({keys[0]})" if keys else text


def scope_for_stage(stage_key: str | None) -> str | None:
    """Registry scope of a ribbon stage key (``None`` for stages without keys)."""
    return stage_key if stage_key in SCOPES and stage_key != GLOBAL else None


def _overlap(a: str, b: str) -> bool:
    return a == b or GLOBAL in (a, b)


def find_conflicts(
    registry: Iterable[ShortcutSpec] = REGISTRY,
) -> list[tuple[str, ShortcutSpec, ShortcutSpec]]:
    """``(KEY, spec_a, spec_b)`` for every key bound twice in overlapping scopes.

    Gestures are ignored. Keys compare case-insensitively.
    """
    seen: list[tuple[str, ShortcutSpec]] = []
    conflicts = []
    for spec in registry:
        if spec.kind == GESTURE:
            continue
        for key in spec.keys:
            norm = key.upper()
            for other_key, other in seen:
                if other_key == norm and _overlap(spec.scope, other.scope):
                    conflicts.append((norm, other, spec))
            seen.append((norm, spec))
    return conflicts


# ------------------------------------------------------------------ Qt adapter
def install_shortcuts(
    widget: QWidget,
    scope: str,
    handlers: Mapping[str, Callable[[], object] | Sequence[Callable[[], object]]],
    *,
    context=None,
) -> dict[str, list[QShortcut]]:
    """Create one ``QShortcut`` per registry key for each action in ``handlers``.

    A handler is one callable for every key of the action, or a sequence with
    one callable per key (``go_to_stage``: Alt+1 … Alt+6).

    ``context`` defaults to ``WidgetWithChildrenShortcut`` (editor pages); pass
    ``Qt.ShortcutContext.WindowShortcut`` for :data:`GLOBAL`. Raises
    ``KeyError`` for an action without a row in ``scope`` and ``ValueError`` for
    a row that is not a ``shortcut`` (those are handled in key events).
    Returns ``{action: [QShortcut, ...]}`` in key order.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence, QShortcut

    if context is None:
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
    installed: dict[str, list[QShortcut]] = {}
    for action, slot in handlers.items():
        spec = find_spec(action, scope)
        if spec is None:
            raise KeyError(f"no shortcut row {action!r} in scope {scope!r}")
        if spec.kind != SHORTCUT:
            raise ValueError(f"{action!r} in {scope!r} is a {spec.kind!r}, not a QShortcut")
        slots = list(slot) if isinstance(slot, Sequence) else [slot] * len(spec.keys)
        if len(slots) != len(spec.keys):
            raise ValueError(f"{action!r} needs {len(spec.keys)} slots, got {len(slots)}")
        bound = []
        for key, key_slot in zip(spec.keys, slots, strict=True):
            shortcut = QShortcut(QKeySequence(key), widget)
            shortcut.setContext(context)
            shortcut.activated.connect(key_slot)
            bound.append(shortcut)
        installed[action] = bound
    return installed

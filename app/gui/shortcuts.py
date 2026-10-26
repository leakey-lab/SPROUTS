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
other scope. The page scopes (:data:`LIBRARY` on the display canvas, :data:`CORRECT`
on its editor page) are window-wide too, but Qt ignores the
shortcuts of a hidden widget, so only the page on screen has live keys: they work
wherever the keyboard is in the window (the library tree keeps it while the arrow keys
browse), the pages never collide, and a text field still gets its typing (it takes the
key first, through ``ShortcutOverride``). PgDn / PgUp are the exception: every owner
binds them ``WidgetWithChildrenShortcut`` (MainWindow on the library, the chrome and
the display; each editor on its own page), so exactly one is live. Stage accelerators
are ``Alt+1 … Alt+5``, one per ribbon stage (not ``Ctrl+digit``: ``Ctrl+0`` /
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
    "CORRECT",
    "SCOPES",
    "STAGE_KEYS",
    "CLASS_KEYS",
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
CORRECT = "Correct"
SCOPES: tuple[str, ...] = (GLOBAL, LIBRARY, CORRECT)

Kind = Literal["shortcut", "key", "gesture"]
SHORTCUT: Kind = "shortcut"
KEY: Kind = "key"
GESTURE: Kind = "gesture"

#: Alt+1 … Alt+5 select the five ribbon stages in order (``shell_chrome.STAGES``).
STAGE_KEYS: tuple[str, ...] = tuple(f"Alt+{i}" for i in range(1, 6))

#: 1 … 9, then 0: the class keys (the n-th labelled class; 0 = Ignore).
CLASS_KEYS: tuple[str, ...] = tuple(str(n) for n in (*range(1, 10), 0))


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


def _zoom_rows(kind: Kind) -> list[tuple]:
    """Zoom keys every canvas has (CanvasViewMixin also handles them as view keys)."""
    return [
        ("fit", ("Ctrl+0",), "Fit the image to the window", kind),
        ("zoom_100", ("Ctrl+1",), "Zoom to 100 %", kind),
        ("zoom_in", ("Ctrl+=", "Ctrl++"), "Zoom in", kind),
        ("zoom_out", ("Ctrl+-",), "Zoom out", kind),
    ]


def _canvas_rows(kind: Kind) -> list[tuple]:
    """Zoom / pan keys of an editor canvas."""
    return [
        *_zoom_rows(kind),
        ("pixel_grid", ("Ctrl+'",), "Show or hide the pixel grid (at 800 % and above)", kind),
        ("pan", ("Space",), "Hold and drag to pan (middle-drag also pans)", KEY, "Space (hold)"),
        ("zoom_wheel", ("Ctrl+wheel",), "Zoom about the cursor", GESTURE),
    ]


def _library_rows() -> list[tuple]:
    """The Library: browse the tree, and the display canvas' zoom keys (its shortcuts
    work wherever the keyboard is, the tree included)."""
    return [
        ("select_adjacent", ("Up", "Down"), "Select the previous / next image", KEY),
        ("expand", ("Left", "Right"), "Collapse / expand a folder", KEY),
        *_zoom_rows(SHORTCUT),
        ("pan", ("Drag",), "Drag to pan (middle-drag too)", GESTURE),
        ("zoom_wheel", ("Ctrl+wheel",), "Zoom about the cursor", GESTURE),
    ]


def _correct_rows() -> list[tuple]:
    """The unified Correct stage: the mask, the skeleton and the class labels, one scope
    (spec section 6; the layer keys are letters, the digits pick a class: one key, one
    meaning)."""
    return [
        *_history_rows(),
        ("save", ("Ctrl+S",), "Save the unsaved layers (nothing unsaved: the active layer)"),
        ("mask_layer", ("M",), "Edit the mask (root area)"),
        ("skeleton_layer", ("L",), "Edit the skeleton (root length)"),
        ("classes_layer", ("G",), "Edit the class labels (GT for training)"),
        ("swap_layer", ("X",), "Switch to the previous layer"),
        (
            "select_class",
            CLASS_KEYS,
            "Pick the n-th class; 0 = Ignore (class labels layer only)",
            SHORTCUT,
            "1–9, 0",
        ),
        (
            "brush",
            ("B",),
            "Brush of the mask or the class labels (tap; from the skeleton: the mask's); "
            "hold B and scroll to resize it",
            KEY,
        ),
        (
            "fill",
            ("F",),
            "Fill a closed outline of the mask or the class labels (from the skeleton: the mask's)",
        ),
        ("select_tool", ("V",), "Skeleton select: pick a branch"),
        ("polyline", ("P",), "Skeleton polyline"),
        ("connect", ("C",), "Skeleton connect: drag from an endpoint to another point"),
        ("eraser", ("E",), "Eraser of the active layer"),
        ("size_smaller", ("[",), "Smaller brush or eraser"),
        ("size_larger", ("]",), "Larger brush or eraser"),
        ("toggle_layers", ("H",), "Show or hide every layer (see the photo)"),
        ("toggle_reference", ("Shift+H",), "Show or hide the other layers"),
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
                "Go to stage 1–5 in ribbon order (Library … Visualize)",
                SHORTCUT,
                "Alt+1 … Alt+5",
            ),
            ("next_image", ("PgDown",), "Next image (editors save or ask first)"),
            ("previous_image", ("PgUp",), "Previous image"),
        ],
    ),
    *_rows(LIBRARY, _library_rows()),
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
    one callable per key (``go_to_stage``: Alt+1 … Alt+5).

    ``context`` defaults to ``WindowShortcut``: the keys work wherever the keyboard is
    in ``widget``'s window while ``widget`` is on screen (Qt ignores the shortcuts of a
    hidden widget, so a page's keys die with its page, and pages never collide). Keys
    that several owners bind (PgDn / PgUp) pass ``WidgetWithChildrenShortcut``, so only
    the owner holding the keyboard has them. Raises ``KeyError`` for an action without a
    row in ``scope`` and ``ValueError`` for a row that is not a ``shortcut`` (those are
    handled in key events). Returns ``{action: [QShortcut, ...]}`` in key order.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence, QShortcut

    if context is None:
        context = Qt.ShortcutContext.WindowShortcut
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

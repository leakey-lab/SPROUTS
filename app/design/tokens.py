"""SPROUTS design tokens: the single, Qt-free source of UI colours and metrics.

Mirrored by ``resources/themes/dark_theme.qss`` (every colour in it is one of these
values; ``tests/test_design_system_qss.py`` enforces that) and re-exported for Qt code as
``app.gui.widgets.tokens``. Direction: Guided + comfy + purple accent. Mask stays green,
skeleton orange; purple is accent + selection only.

Text tiers (WCAG 2.x contrast on BG_0 / BG_1 / BG_2 / BG_3):

* ``TEXT``          15.6 / 14.6 / 13.5 / 12.3 : 1 - primary text.
* ``TEXT_MUTED``     6.32 / 5.94 / 5.47 / 4.98 : 1 - secondary text.
* ``TEXT_FAINT``     5.72 / 5.37 / 4.95 / 4.51 : 1 - tertiary / metadata text. Was #686c82
  (3.49 / 3.28 / 3.02 / 2.75 : 1, failing AA) while carrying status-bar messages, metric
  labels, tree headers and paths; raised to the lightest value that passes AA on every
  surface, so every existing call site became compliant without edits.
* ``TEXT_DISABLED``  #686c82 - disabled controls ONLY (WCAG exempts inactive components)
  and pure decoration. Never use it for information.

Hierarchy between MUTED and FAINT is now carried mostly by size, weight and
letter-spacing (their contrast with each other is only 1.1 : 1).

Focus: ``FOCUS`` (accent) rings neutral controls; ``FOCUS_ON_FILL`` (text) rings
elements whose own fill is the accent or an arbitrary colour. Use :func:`focus_rule` in
widget-level stylesheets (see its docstring for why the app QSS cannot do it for you).
"""

from __future__ import annotations

# --- surfaces ---
BG_0 = "#15161c"  # deepest — canvas / app behind
BG_1 = "#1b1c24"  # chrome / rails / dialogs
BG_2 = "#21232e"  # panels
BG_3 = "#282a37"  # raised cards / inputs
BG_HOVER = "#2f3242"
BG_ACTIVE = "#363a4d"

BORDER = "#2a2c39"
BORDER_STRONG = "#373a4c"
BORDER_HOVER = "#454963"  # hovered button border (design styles.css .btn:hover)

# --- text ---
TEXT = "#eceef5"
TEXT_MUTED = "#9498ad"
TEXT_FAINT = "#8c90a6"  # AA on BG_0..BG_3 (see module docstring)
TEXT_DISABLED = "#686c82"  # disabled controls / decoration only (fails AA by design)

# --- accent (purple, chosen direction) ---
ACCENT = "#c39af6"
ACCENT_BRIGHT = "#d3b0fa"  # lifted accent (slider handle hover)
ACCENT_PRESS = "#ab87d8"  # primary hover (design --accent-press)
ACCENT_DEEP = "#9a78c6"  # primary pressed, one step below ACCENT_PRESS
ACCENT_INK = "#1a1322"  # dark text on accent fills
ACCENT_DISABLED = "#3a3050"  # disabled primary fill
ACCENT_DISABLED_INK = "#8a7fa6"  # disabled primary text
ACCENT_SOFT = "rgba(195, 154, 246, 0.14)"
ACCENT_LINE = "rgba(195, 154, 246, 0.30)"

# --- selection (purple) ---
SEL = "#b794f6"
SEL_SOFT = "rgba(183, 148, 246, 0.16)"

# --- keyboard focus ---
FOCUS = ACCENT  # ring on neutral surfaces (7.5:1 on BG_1)
FOCUS_ON_FILL = TEXT  # ring on accent-filled or arbitrary-colour elements

# --- semantic / status ---
WARN = "#e8b25e"
INFO = "#79c0e8"
DANGER = "#ec6a78"
MASK = "#5fd6a0"  # mask overlay = green
SKEL = "#f0a868"  # skeleton = warm orange
OK = "#5fd6a0"

# --- radii ---
RADIUS = 10
RADIUS_SM = 7
RADIUS_LG = 14

# --- comfy density ---
ROW_H = 40
PAD = 18
UI = 14.5

# --- fonts ---
SANS = '"IBM Plex Sans", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", sans-serif'
MONO = '"IBM Plex Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Liberation Mono", monospace'


def rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` + alpha -> ``rgba(r, g, b, a)`` string for QSS."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def focus_rule(selector: str, color: str = FOCUS, *, width: int = 1) -> str:
    """QSS rule that draws the keyboard-focus ring on every selector in ``selector``.

    A widget's own stylesheet always wins over the application stylesheet for the same
    property (Qt weighs a rule's origin before its specificity), so a widget-level QSS
    that sets ``border`` hides the app-level ``:focus`` ring. Append this rule AFTER the
    ``:hover`` / ``:checked`` rules of that same stylesheet, and give the base rule a
    ``border: 1px solid transparent`` so the ring does not shift the content.

    >>> focus_rule("QToolButton#stage")
    'QToolButton#stage:focus { border: 1px solid #c39af6; }'
    """
    selectors = ", ".join(f"{part.strip()}:focus" for part in selector.split(","))
    return f"{selectors} {{ border: {width}px solid {color}; }}"


__all__ = [
    "BG_0",
    "BG_1",
    "BG_2",
    "BG_3",
    "BG_HOVER",
    "BG_ACTIVE",
    "BORDER",
    "BORDER_STRONG",
    "BORDER_HOVER",
    "TEXT",
    "TEXT_MUTED",
    "TEXT_FAINT",
    "TEXT_DISABLED",
    "ACCENT",
    "ACCENT_BRIGHT",
    "ACCENT_PRESS",
    "ACCENT_DEEP",
    "ACCENT_INK",
    "ACCENT_DISABLED",
    "ACCENT_DISABLED_INK",
    "ACCENT_SOFT",
    "ACCENT_LINE",
    "SEL",
    "SEL_SOFT",
    "FOCUS",
    "FOCUS_ON_FILL",
    "WARN",
    "INFO",
    "DANGER",
    "MASK",
    "SKEL",
    "OK",
    "RADIUS",
    "RADIUS_SM",
    "RADIUS_LG",
    "ROW_H",
    "PAD",
    "UI",
    "SANS",
    "MONO",
    "rgba",
    "focus_rule",
]

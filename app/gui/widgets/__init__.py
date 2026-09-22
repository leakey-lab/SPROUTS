"""SPROUTS design-system widgets — reusable, presentation-only primitives.

Built for the Guided + comfy + purple-accent direction. All widgets wrap
standard Qt and expose plain signals so callers forward to existing handlers
without behaviour changes. See ``tokens`` for the colour/density values.
"""

from __future__ import annotations

from . import tokens
from .controls import DockSlider, IconButton, SegmentedControl
from .effects import drop_shadow, pop_shadow
from .icons import has_icon, icon_path, load_icon, load_pixmap
from .overlays import (
    CornerPanel,
    EnhancePopover,
    FloatingDock,
    ProgressOverlay,
    Toast,
    ToastManager,
    ToolRail,
)

__all__ = [
    "tokens",
    "load_icon",
    "load_pixmap",
    "has_icon",
    "icon_path",
    "drop_shadow",
    "pop_shadow",
    "SegmentedControl",
    "IconButton",
    "DockSlider",
    "CornerPanel",
    "ToolRail",
    "FloatingDock",
    "EnhancePopover",
    "Toast",
    "ToastManager",
    "ProgressOverlay",
]

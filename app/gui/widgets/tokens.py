"""SPROUTS design tokens for Qt code (``from app.gui.widgets import tokens``).

Re-exports :mod:`app.design.tokens`, the single Qt-free source of every UI colour and
metric (see its docstring for the text-contrast tiers and the focus-ring rules), so
existing ``tokens.X`` call sites keep working unchanged. Add new tokens THERE, not here.
"""

from __future__ import annotations

from app.design.tokens import *  # noqa: F403  (compat re-export of the canonical tokens)
from app.design.tokens import __all__  # noqa: F401

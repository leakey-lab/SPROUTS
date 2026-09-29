"""Design-token contracts: text contrast (WCAG 2.x AA), the Qt-free token module, focus rules.

Pure Python except for the subprocess import check; no QApplication needed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SURFACES = ("BG_0", "BG_1", "BG_2", "BG_3")
# Text tiers that carry information and so must reach WCAG 2.x AA (4.5:1) on every surface.
INFORMATIONAL_TEXT = ("TEXT", "TEXT_MUTED", "TEXT_FAINT")


def _channel(c8: int) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of ``#rrggbb``."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_helper_matches_known_wcag_values():
    # Hand-checked anchors: black/white is 21:1, identical colours 1:1, and the old
    # TEXT_FAINT #686c82 on BG_1 #1b1c24 was measured at 3.28:1 in the audit (UXS-09).
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#1b1c24", "#1b1c24") == pytest.approx(1.0)
    assert contrast("#686c82", "#1b1c24") == pytest.approx(3.28, abs=0.01)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", INFORMATIONAL_TEXT)
def test_informational_text_meets_wcag_aa(text, surface):
    from app.gui.widgets import tokens

    ratio = contrast(getattr(tokens, text), getattr(tokens, surface))
    assert ratio >= 4.5, f"{text} on {surface} is {ratio:.2f}:1 (< 4.5:1)"


def test_text_tiers_keep_their_order():
    """Disabled text reads dimmer than every informational tier, and the tiers stay ordered."""
    from app.gui.widgets import tokens

    tiers = [tokens.TEXT_DISABLED, tokens.TEXT_FAINT, tokens.TEXT_MUTED, tokens.TEXT]
    luminances = [_luminance(c) for c in tiers]
    assert luminances == sorted(luminances)
    assert len(set(tiers)) == len(tiers)


def test_design_tokens_import_without_qt():
    """``app.design.tokens`` is the Qt-free source (Dash theme, palette builders, tests)."""
    code = (
        "import sys; import app.design.tokens as t; "
        "assert t.ACCENT.startswith('#'); "
        "heavy = [m for m in ('PyQt6', 'torch', 'dash') if m in sys.modules]; "
        "assert not heavy, heavy"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


def test_widget_tokens_module_reexports_every_design_token():
    """Old import path keeps every name and value (compat for ~40 importers)."""
    import app.design.tokens as design
    from app.gui.widgets import tokens

    for name in design.__all__:
        assert getattr(tokens, name) == getattr(design, name), name


def test_focus_rule_draws_the_accent_ring():
    from app.gui.widgets import tokens

    assert tokens.focus_rule("QToolButton") == "QToolButton:focus { border: 1px solid #c39af6; }"


def test_focus_rule_applies_to_every_selector_of_a_list():
    from app.gui.widgets import tokens

    rule = tokens.focus_rule("QPushButton#a, QPushButton#b", "#eceef5", width=2)
    assert rule == "QPushButton#a:focus, QPushButton#b:focus { border: 2px solid #eceef5; }"

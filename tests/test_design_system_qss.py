"""Contracts of ``resources/themes/dark_theme.qss`` against the design tokens.

Pure text parsing, no Qt: the stylesheet may only use token colours, must load under the
Windows default codec (any ``open(path)`` without an encoding - main.py itself did until
this change), and may only use the disabled grey on ``:disabled`` rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import app.design.tokens as tokens

QSS_PATH = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _qss_without_comments() -> str:
    return _COMMENT.sub("", QSS_PATH.read_text(encoding="utf-8"))


def _rules() -> list[tuple[list[str], dict[str, str]]]:
    """``[(selectors, {property: value})]`` for every rule of the stylesheet."""
    rules = []
    for selector_text, body in _RULE.findall(_qss_without_comments()):
        selectors = [s.strip() for s in selector_text.split(",") if s.strip()]
        decls = {}
        for decl in body.split(";"):
            if ":" in decl:
                prop, value = decl.split(":", 1)
                decls[prop.strip().lower()] = value.strip()
        rules.append((selectors, decls))
    return rules


def _token_hexes() -> set[str]:
    return {
        getattr(tokens, name).lower()
        for name in tokens.__all__
        if isinstance(getattr(tokens, name), str) and getattr(tokens, name).startswith("#")
    }


def _token_rgbs() -> set[tuple[int, int, int]]:
    return {tuple(int(h[i : i + 2], 16) for i in (1, 3, 5)) for h in _token_hexes()}


def test_parser_sees_the_stylesheet():
    """Guards the other tests against a parser that silently finds nothing."""
    selectors = {s for sels, _ in _rules() for s in sels}
    assert {"QWidget", "QPushButton", "QTreeView::item:selected"} <= selectors


def test_every_hex_colour_is_a_token_value():
    unknown = sorted(set(_HEX.findall(_qss_without_comments().lower())) - _token_hexes())
    assert not unknown, f"QSS colours that are not tokens: {unknown} (add a token first)"


def test_every_rgba_colour_uses_a_token_rgb():
    rgbs = {tuple(map(int, m)) for m in _RGBA.findall(_qss_without_comments())}
    assert rgbs, "expected the translucent accent/selection fills"
    assert rgbs <= _token_rgbs(), sorted(rgbs - _token_rgbs())


def test_stylesheet_decodes_identically_under_the_windows_default_codec():
    """A reader using open(path) gets cp1252 on Windows: a UTF-8 '”' or '‐' in the QSS
    then raises UnicodeDecodeError (an app that cannot start), other non-ASCII mojibake."""
    raw = QSS_PATH.read_bytes()
    assert raw.decode("cp1252", errors="replace") == raw.decode("utf-8")


def test_disabled_grey_is_only_used_by_disabled_rules():
    """TEXT_DISABLED fails AA on purpose; information must never be drawn in it."""
    offenders = [
        sels
        for sels, decls in _rules()
        if decls.get("color", "").lower() == tokens.TEXT_DISABLED
        and not all(":disabled" in s for s in sels)
    ]
    assert not offenders, offenders


def test_informational_chrome_text_passes_aa():
    """Status bar, tree headers and group titles use an AA text tier (UXS-09)."""
    informational = {"QStatusBar", "QHeaderView::section", "QGroupBox::title"}
    aa_tiers = {tokens.TEXT, tokens.TEXT_MUTED, tokens.TEXT_FAINT}
    colours = {
        s: decls["color"].lower()
        for sels, decls in _rules()
        for s in sels
        if s in informational and "color" in decls
    }
    assert set(colours) == informational
    assert set(colours.values()) <= aa_tiers, colours


def test_qss_font_families_are_the_token_chains():
    qss = QSS_PATH.read_text(encoding="utf-8")
    assert f"font: 10pt {tokens.SANS};" in qss
    assert f"font: 9pt {tokens.MONO};" in qss
    assert "DejaVu Sans Mono" in tokens.MONO and "Menlo" in tokens.MONO

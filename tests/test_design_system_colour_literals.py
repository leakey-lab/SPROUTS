"""Ratchet: GUI colours come from the design tokens, not literals (audit AR-17 / UXS-09).

``app/design/tokens.py`` is the single source of UI colours (``app.gui.widgets.tokens``
re-exports it). This counts colour literals in every module under ``app/gui``: hex
strings, ``QColor("name")`` and ``QColor(r, g, b, ...)`` with literal channels. A module
may only go DOWN from its count when the token work started (commit a1599e6); a module
that is not listed starts at zero. When you remove literals, lower its number here.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_GUI = Path(__file__).resolve().parents[1] / "app" / "gui"

_LITERALS = re.compile(
    r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b"  # "#rrggbb" / "#rrggbbaa"
    r"|#[0-9a-fA-F]{3}\b"  # "#rgb"
    r"|QColor\(\s*[\"'][A-Za-z]+[\"']\s*\)"  # QColor("green")
    r"|QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+"  # QColor(20, 255, 57, ...)
)

# Colour literals per module at a1599e6. Only ever lower these numbers.
BASELINE = {
    "class_editor_dialog.py": 10,  # _ADD_PALETTE: default GT class colours (data, not chrome)
    "display_controller.py": 1,
    "empty_state.py": 12,
    "file_tree_manager.py": 7,
    "main_window.py": 6,
    "mask_tracing_interface.py": 3,
    "skeleton_correction_interface.py": 23,
    "task_progress.py": 7,
    "ui_panels.py": 6,
    "widgets/controls.py": 1,
    "widgets/effects.py": 1,  # QColor(0, 0, 0, alpha): drop-shadow black
}


def colour_literals(source: str) -> list[str]:
    return _LITERALS.findall(source)


def test_scanner_finds_each_literal_kind_and_ignores_tokens():
    source = (
        'a = "#5fd6a0"; b = QColor("green"); c = QColor(20, 255, 57, 128)\n'
        "d = tokens.MASK; e = Qt.GlobalColor.transparent; f = QColor(tokens.SKEL)\n"
        "# section 1 heading, issue #12\n"
    )
    assert colour_literals(source) == ["#5fd6a0", 'QColor("green")', "QColor(20, 255, 57"]


def test_no_module_gains_colour_literals():
    grown = {}
    for path in sorted(APP_GUI.rglob("*.py")):
        rel = path.relative_to(APP_GUI).as_posix()
        if rel == "widgets/tokens.py":
            continue
        found = colour_literals(path.read_text(encoding="utf-8"))
        if len(found) > BASELINE.get(rel, 0):
            grown[rel] = (len(found), BASELINE.get(rel, 0), found)
    assert not grown, (
        "colour literals added (use app.gui.widgets.tokens; add a token to "
        f"app/design/tokens.py if none fits): {grown}"
    )

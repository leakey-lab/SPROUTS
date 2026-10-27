"""Platform key text (O21): Cmd / Option on macOS, the macOS stage keys, the Pinch row."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.gui import shortcuts as sc
from app.gui.key_text import platform_keys

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "app" / "gui"
KEY_TOKEN = re.compile(r"\b(Ctrl|Alt)\+")
NOT_WRAPPED_FILES = {"shortcuts.py", "key_text.py"}  # the registry is data; key_text is the map


# ------------------------------------------------------------------ platform_keys
def test_ctrl_and_alt_become_cmd_and_option_on_macos():
    assert platform_keys("Ctrl+Shift+Z", "darwin") == "Cmd+Shift+Z"
    assert platform_keys("Alt+2", "darwin") == "Option+2"
    assert platform_keys("Ctrl+Alt+2", "darwin") == "Cmd+Option+2"
    assert platform_keys("Ctrl+= / Ctrl+- zoom · Ctrl+wheel", "darwin") == (
        "Cmd+= / Cmd+- zoom · Cmd+wheel"
    )


@pytest.mark.parametrize("platform", ["win32", "linux", "cygwin"])
def test_other_platforms_keep_the_text(platform):
    assert platform_keys("Ctrl+wheel", platform) == "Ctrl+wheel"
    assert platform_keys("Alt+F4", platform) == "Alt+F4"


def test_only_whole_tokens_before_a_plus_are_replaced():
    assert platform_keys("Control panel", "darwin") == "Control panel"
    assert platform_keys("Ctrl is a key", "darwin") == "Ctrl is a key"
    assert platform_keys("Alternative+1 Ctrls+1 MyCtrl+1", "darwin") == (
        "Alternative+1 Ctrls+1 MyCtrl+1"
    )


def test_the_platform_defaults_to_sys_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert platform_keys("Ctrl+Z") == "Cmd+Z"
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform_keys("Ctrl+Z") == "Ctrl+Z"


def test_key_text_is_qt_free():
    code = "import sys, app.gui.key_text; print('PyQt6' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "False"


# ------------------------------------------------------------------ the sheet on macOS
def test_the_undo_row_of_the_sheet_shows_cmd_z_on_macos(monkeypatch):
    undo = sc.find_spec("undo", sc.CORRECT)
    redo = sc.find_spec("redo", sc.CORRECT)
    assert undo is not None and redo is not None
    monkeypatch.setattr(sys, "platform", "win32")
    assert (undo.label, redo.label) == ("Ctrl+Z", "Ctrl+Y / Ctrl+Shift+Z")

    monkeypatch.setattr(sys, "platform", "darwin")

    assert (undo.label, redo.label) == ("Cmd+Z", "Cmd+Y / Cmd+Shift+Z")
    assert sc.find_spec("go_to_stage", sc.GLOBAL).label == "Option+1 … Option+5"


def test_tooltips_show_the_platform_key_text(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    assert sc.tooltip("Redo", "redo", sc.CORRECT) == "Redo (Cmd+Y)"
    assert sc.tooltip("Eraser", "eraser", sc.CORRECT) == "Eraser (E)"


def test_the_sheet_dialog_shows_cmd_on_macos(app, monkeypatch):
    from PyQt6.QtWidgets import QLabel

    from app.gui.empty_state import ShortcutsDialog

    monkeypatch.setattr(sys, "platform", "darwin")

    dialog = ShortcutsDialog(current_scope=sc.CORRECT)
    try:
        text = " ".join(label.text() for label in dialog.findChildren(QLabel, "shortcutKeys"))
    finally:
        dialog.close()

    assert "Cmd+Z" in text and "Cmd+0" in text and "Ctrl+" not in text and "Alt+" not in text


# ------------------------------------------------------------------ stage keys
def test_stage_keys_stay_alt_n_and_add_cmd_option_n_on_macos():
    assert sc.STAGE_KEYS == tuple(f"Alt+{n}" for n in range(1, 6))
    assert sc.stage_keys("win32") == tuple((f"Alt+{n}",) for n in range(1, 6))
    assert sc.stage_keys("linux") == sc.stage_keys("win32")
    mac = sc.stage_keys("darwin")
    assert len(mac) == len(sc.STAGE_KEYS)
    assert mac[0] == ("Alt+1", "Ctrl+Alt+1")
    assert mac[1] == ("Alt+2", "Ctrl+Alt+2")


def test_stage_keys_default_to_sys_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sc.stage_keys()[1] == ("Alt+2", "Ctrl+Alt+2")


def test_the_macos_registry_binds_both_stage_keys_without_a_conflict():
    registry = sc.build_registry("darwin")
    row = next(s for s in registry if s.action == "go_to_stage")

    # Column-major: key i is stage i for i < 5, so the window's slot list is five slots twice.
    assert row.keys == (*sc.STAGE_KEYS, *(f"Ctrl+Alt+{n}" for n in range(1, 6)))
    assert sc.find_conflicts(registry) == []
    assert sc.find_conflicts(sc.build_registry("win32")) == []
    plain = next(s for s in sc.build_registry("win32") if s.action == "go_to_stage")
    assert plain.keys == sc.STAGE_KEYS


# ------------------------------------------------------------------ Pinch row
def test_the_canvas_scopes_list_the_pinch_gesture():
    row = ("zoom_pinch", ("Pinch",), "Zoom about the fingers", sc.GESTURE)
    for scope in (sc.LIBRARY, sc.CORRECT):
        specs = [
            (s.action, s.keys, s.description, s.kind)
            for s in sc.specs_for(scope)
            if s.action == "zoom_pinch"
        ]
        assert specs == [row], scope
    assert sc.find_conflicts() == []


# ------------------------------------------------------------------ ratchet
def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _wrapped_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "id", getattr(func, "attr", None)) == "platform_keys":
            ids.update(id(c) for a in node.args for c in ast.walk(a) if isinstance(c, ast.Constant))
    return ids


def _raw_key_texts() -> list[str]:
    found = []
    for path in sorted(GUI.rglob("*.py")):
        if path.name in NOT_WRAPPED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree) | _wrapped_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and KEY_TOKEN.search(node.value)
            ):
                found.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: {node.value!r}")
    return found


def test_no_raw_ctrl_text_outside_key_text():
    assert _raw_key_texts() == []

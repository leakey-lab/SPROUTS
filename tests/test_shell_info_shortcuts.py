"""Declarative shortcut registry (UXS-10 / UXE-07 / UI-21) and the F1 sheet built from it."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from app.gui import shortcuts as sc  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _portable(key: str) -> str:
    return QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)


def _live_keys(widget: QWidget) -> set[str]:
    return {
        s.key().toString(QKeySequence.SequenceFormat.PortableText)
        for s in widget.findChildren(QShortcut)
        if s.parent() is widget
    }


# ------------------------------------------------------------------ Qt-free core
def test_registry_module_is_qt_free():
    import subprocess
    import sys

    code = "import sys, app.gui.shortcuts; print('PyQt6' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_every_scope_has_rows_and_scopes_are_ordered():
    assert sc.SCOPES == (
        sc.GLOBAL,
        sc.LIBRARY,
        sc.TRACE_GT,
        sc.CORRECT_MASK,
        sc.CORRECT_SKELETON,
    )
    for scope in sc.SCOPES:
        assert sc.specs_for(scope), scope


def test_registry_has_no_conflicting_keys():
    assert sc.find_conflicts() == []


def test_duplicate_key_in_one_scope_is_reported():
    """The Ctrl+Y double binding (UXE-07) is exactly what this catches."""
    dup = sc.REGISTRY + (sc.ShortcutSpec("redo_again", ("Ctrl+Y",), "Redo", sc.CORRECT_MASK),)
    conflicts = sc.find_conflicts(dup)
    assert [c[0] for c in conflicts] == ["CTRL+Y"]


def test_global_keys_conflict_with_every_editor_scope():
    dup = sc.REGISTRY + (sc.ShortcutSpec("oops", ("F1",), "x", sc.CORRECT_SKELETON),)
    assert [c[0] for c in sc.find_conflicts(dup)] == ["F1"]


def test_redo_is_ctrl_y_and_ctrl_shift_z_in_every_editor():
    for scope in (sc.CORRECT_MASK, sc.TRACE_GT, sc.CORRECT_SKELETON):
        assert sc.keys_for("redo", scope) == ("Ctrl+Y", "Ctrl+Shift+Z")
        assert sc.keys_for("undo", scope) == ("Ctrl+Z",)


def test_global_keys_the_integrator_wires():
    assert sc.keys_for("help", sc.GLOBAL) == ("F1", "?")
    assert sc.keys_for("open_folder", sc.GLOBAL) == ("Ctrl+O",)
    assert sc.keys_for("next_image", sc.GLOBAL) == ("PgDown",)
    assert sc.keys_for("previous_image", sc.GLOBAL) == ("PgUp",)
    assert sc.STAGE_KEYS == tuple(f"Alt+{i}" for i in range(1, 8))
    assert sc.keys_for("go_to_stage", sc.GLOBAL) == sc.STAGE_KEYS


def test_skeleton_sheet_lists_its_real_keys_and_gestures():
    labels = " ".join(s.label for s in sc.specs_for(sc.CORRECT_SKELETON))
    for key in ("Enter", "Esc", "Delete", "Ctrl+click", "Shift+drag", "Double-click"):
        assert key in labels
    # Mask-only keys are NOT claimed for the skeleton editor; its own Ctrl+S is
    # handled in SkeletonCorrectionInterface.on_key_press (a KEY row, no QShortcut).
    assert sc.find_spec("toggle_overlay", sc.CORRECT_SKELETON) is None
    save = sc.find_spec("save", sc.CORRECT_SKELETON)
    assert save is not None and save.keys == ("Ctrl+S",) and save.kind == sc.KEY


def test_gt_sheet_lists_class_digits():
    spec = sc.find_spec("select_class", sc.TRACE_GT)
    assert spec is not None
    assert set(spec.keys) == {str(n) for n in range(10)}
    assert "Ignore" in spec.description


def test_tooltip_appends_the_first_key():
    assert sc.tooltip("Eraser", "eraser", sc.CORRECT_MASK) == "Eraser (E)"
    assert sc.tooltip("Redo", "redo", sc.CORRECT_SKELETON) == "Redo (Ctrl+Y)"
    assert sc.tooltip("Nothing", "no_such_action", sc.CORRECT_MASK) == "Nothing"


def test_scope_for_stage():
    assert sc.scope_for_stage("Trace GT") == sc.TRACE_GT
    assert sc.scope_for_stage("Generate") is None


# ------------------------------------------------------------------ Qt adapter
def test_install_shortcuts_binds_every_key_once(app):
    w = QWidget()
    hits = []
    installed = sc.install_shortcuts(
        w, sc.CORRECT_MASK, {"redo": lambda: hits.append("redo"), "undo": lambda: None}
    )
    assert sorted(installed) == ["redo", "undo"]
    assert [s.key().toString() for s in installed["redo"]] == ["Ctrl+Y", "Ctrl+Shift+Z"]
    assert _live_keys(w) == {"Ctrl+Y", "Ctrl+Shift+Z", "Ctrl+Z"}
    installed["redo"][1].activated.emit()
    assert hits == ["redo"]


def test_install_shortcuts_rejects_unknown_or_non_shortcut_actions(app):
    w = QWidget()
    with pytest.raises(KeyError):
        sc.install_shortcuts(w, sc.CORRECT_MASK, {"no_such_action": lambda: None})
    with pytest.raises(ValueError):
        sc.install_shortcuts(w, sc.CORRECT_SKELETON, {"finish_line": lambda: None})


def test_every_live_editor_shortcut_is_documented(app):
    """No binding may exist without its row on the F1 sheet."""
    from app.gui.gt_tracing_interface import GtTracingInterface
    from app.gui.mask_tracing_interface import MaskTracingInterface
    from app.gui.skeleton_correction_interface import SkeletonCorrectionInterface

    global_keys = {_portable(k) for s in sc.specs_for(sc.GLOBAL) for k in s.keys}
    for cls, scope in (
        (MaskTracingInterface, sc.CORRECT_MASK),
        (GtTracingInterface, sc.TRACE_GT),
        (SkeletonCorrectionInterface, sc.CORRECT_SKELETON),
    ):
        w = cls()
        try:
            documented = {_portable(k) for s in sc.specs_for(scope) for k in s.keys}
            undocumented = _live_keys(w) - documented - global_keys
            assert not undocumented, (scope, undocumented)
        finally:
            w.deleteLater()


# ------------------------------------------------------------------ F1 sheet
def test_shortcuts_dialog_has_one_row_per_spec(app):
    from app.gui.empty_state import ShortcutsDialog

    dlg = ShortcutsDialog()
    rows = dlg.findChildren(QLabel, "shortcutKeys")
    assert len(rows) == len(sc.REGISTRY)
    text = " ".join(r.text() for r in rows)
    assert "PgDn" in text or "PgDown" in text
    assert "Enter" in text


def test_shortcuts_dialog_puts_current_scope_first(app):
    from app.gui.empty_state import ShortcutsDialog

    dlg = ShortcutsDialog(current_scope=sc.CORRECT_SKELETON)
    assert dlg.section_titles()[0] == sc.CORRECT_SKELETON
    assert sorted(dlg.section_titles()) == sorted(sc.SCOPES)


def test_shortcuts_dialog_offers_about_when_wired(app):
    from app.gui.empty_state import ShortcutsDialog

    opened = []
    dlg = ShortcutsDialog(on_about=lambda: opened.append(True))
    buttons = [b for b in dlg.findChildren(QPushButton) if "About" in b.text()]
    assert len(buttons) == 1
    buttons[0].click()
    assert opened == [True]
    plain = ShortcutsDialog()
    assert not [b for b in plain.findChildren(QPushButton) if "About" in b.text()]


def test_shortcuts_dialog_is_deleted_on_close(app):
    """F1 used to leak one hidden dialog child per press."""
    from PyQt6.QtCore import Qt

    from app.gui.empty_state import ShortcutsDialog

    assert ShortcutsDialog().testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)


def test_install_shortcuts_accepts_one_slot_per_key(app):
    """Alt+1 … Alt+7 each go to their own stage."""
    w = QWidget()
    hits = []
    slots = [lambda i=i: hits.append(i) for i in range(len(sc.STAGE_KEYS))]
    installed = sc.install_shortcuts(w, sc.GLOBAL, {"go_to_stage": slots})
    installed["go_to_stage"][2].activated.emit()
    assert hits == [2]
    with pytest.raises(ValueError):
        sc.install_shortcuts(w, sc.GLOBAL, {"go_to_stage": slots[:3]})

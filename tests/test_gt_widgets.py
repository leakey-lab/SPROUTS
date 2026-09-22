"""Tests for the GT class panel and class editor widgets (Task 3).

Covers ``app.gui.class_panel.ClassPanel`` (row rendering, active selection,
reviewed checkboxes, keyboard-shortcut lookup, scrolling/height cap) and
``app.gui.class_editor_dialog.ClassEditorDialog`` (add/rename/recolour/retire
against the real ``Registry`` API, inline validation errors, accept/cancel).

Runs under an offscreen QApplication (``QT_QPA_PLATFORM=offscreen``).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea, QWidget  # noqa: E402

from app.gt.registry import default_registry  # noqa: E402
from app.gui.class_editor_dialog import ClassEditorDialog  # noqa: E402
from app.gui.class_panel import ClassPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _host(w=400, h=600):
    host = QWidget()
    host.resize(w, h)
    return host


def _registry_with_many_classes(n_labelled: int = 12):
    reg = default_registry()
    for i in range(n_labelled - len(reg.labelled())):
        reg = reg.add(key=f"extra_{i}", name=f"Extra {i}", color=f"#{100 + i:06x}")
    return reg


# --------------------------------------------------------------------------- #
#  ClassPanel
# --------------------------------------------------------------------------- #
def test_panel_shows_one_row_per_active_class_in_order(app):
    host = _host()
    panel = ClassPanel(host)
    panel.set_registry(default_registry())

    assert list(panel._rows.keys()) == ["root", "nodule", "root_tip", "ignore"]
    assert panel._rows["ignore"].checkbox is None
    for key in ("root", "nodule", "root_tip"):
        assert panel._rows[key].checkbox is not None


def test_clicking_a_row_emits_active_changed_and_highlights(app):
    host = _host()
    panel = ClassPanel(host)
    panel.set_registry(default_registry())

    got = []
    panel.active_changed.connect(got.append)
    QTest.mouseClick(panel._rows["nodule"], Qt.MouseButton.LeftButton)

    assert got == ["nodule"]
    assert panel.active_key() == "nodule"
    assert panel._rows["nodule"]._active is True

    # set_active() must not emit.
    got.clear()
    panel.set_active("root")
    assert got == []
    assert panel.active_key() == "root"
    assert panel._rows["root"]._active is True
    assert panel._rows["nodule"]._active is False


def test_reviewed_checkbox_toggle_emits_and_set_reviewed_is_silent(app):
    host = _host()
    panel = ClassPanel(host)
    panel.set_registry(default_registry())

    got = []
    panel.reviewed_changed.connect(lambda k, v: got.append((k, v)))
    panel._rows["nodule"].checkbox.setChecked(True)
    assert got == [("nodule", True)]

    got.clear()
    panel.set_reviewed({"root"}, {"root"})
    assert got == []
    assert panel._rows["root"].checkbox.isChecked() is True
    assert panel._rows["root"].checkbox.isEnabled() is False


def test_key_for_shortcut_defaults(app):
    host = _host()
    panel = ClassPanel(host)
    panel.set_registry(default_registry())

    assert panel.key_for_shortcut(1) == "root"
    assert panel.key_for_shortcut(3) == "root_tip"
    assert panel.key_for_shortcut(0) == "ignore"
    assert panel.key_for_shortcut(9) is None


def test_panel_many_classes_scrolls(app):
    host = _host(400, 600)
    panel = ClassPanel(host)
    # Enough rows that the natural (unscrolled) content height genuinely
    # exceeds the 60%-of-600 = 360px cap, so this exercises real clipping,
    # not just an assertion that happens to hold for a short list.
    reg = _registry_with_many_classes(25)
    panel.set_registry(reg)

    assert isinstance(panel._scroll, QScrollArea)
    assert panel._scroll.widget() is panel._rows_container
    assert len(panel._rows) == 26  # 25 labelled + Ignore

    panel.reposition()
    cap = int(host.height() * 0.6)
    assert panel.height() <= cap
    assert panel._rows_container.sizeHint().height() > panel._scroll.height(), (
        "content must be taller than the visible scroll viewport for this to be a real test "
        "of scrolling/clipping, not a no-op cap"
    )
    assert panel.key_for_shortcut(9) == reg.labelled()[8].key


def test_panel_shrinks_back_after_fewer_classes(app):
    """Regression: QScrollArea.sizeHint() caches the contained widget's size
    and does not reliably invalidate without a real show/resize event cycle,
    so a naive adjustSize()-based reposition() can get stuck at the capped
    height even after the registry shrinks back down (e.g. retiring classes
    via the editor). reposition() must measure the rows container directly.
    """
    host = _host(400, 600)
    panel = ClassPanel(host)
    panel.set_registry(_registry_with_many_classes(25))
    panel.reposition()
    grown_height = panel.height()

    panel.set_registry(default_registry())
    panel.reposition()
    assert panel.height() < grown_height
    assert panel.height() <= int(host.height() * 0.6)


def test_gear_button_emits_edit_requested(app):
    host = _host()
    panel = ClassPanel(host)
    panel.set_registry(default_registry())

    got = []
    panel.edit_requested.connect(lambda: got.append(1))
    panel._gear.click()
    assert got == [1]


# --------------------------------------------------------------------------- #
#  ClassEditorDialog
# --------------------------------------------------------------------------- #
def test_add_class_then_accept(app):
    dialog = ClassEditorDialog(default_registry())
    dialog._add_name_edit.setText("Dead root")
    dialog._set_add_color("#111111")
    assert dialog._add_key_edit.text() == "dead_root"

    dialog._on_add_clicked()
    assert dialog._error is None

    dialog._on_accept()
    reg = dialog.result_registry()
    assert reg is not None
    assert reg.by_key("dead_root").id == 4
    assert reg.by_key("dead_root").name == "Dead root"
    assert reg.by_key("dead_root").color == "#111111"


def test_rename_nodule_reflected(app):
    dialog = ClassEditorDialog(default_registry())
    row = dialog._rows["nodule"]
    row.name_edit.setText("Nodule X")
    row.name_edit.editingFinished.emit()
    assert dialog._error is None

    dialog._on_accept()
    reg = dialog.result_registry()
    assert reg is not None
    assert reg.by_key("nodule").name == "Nodule X"


def test_retire_nodule_bypassing_confirm(app, monkeypatch):
    import app.gui.class_editor_dialog as mod

    monkeypatch.setattr(
        mod.QMessageBox, "question", lambda *a, **k: mod.QMessageBox.StandardButton.Yes
    )

    dialog = ClassEditorDialog(default_registry())
    dialog._on_retire_clicked("nodule")
    assert "nodule" not in dialog._rows
    assert dialog._candidate.by_key("nodule").retired is True

    dialog._on_accept()
    reg = dialog.result_registry()
    assert reg is not None
    assert reg.by_key("nodule").retired is True
    assert "nodule" not in [c.key for c in reg.active()]


def test_ignore_row_has_no_retire_button(app):
    dialog = ClassEditorDialog(default_registry())
    assert dialog._rows["ignore"].retire_button is None
    for key in ("root", "nodule", "root_tip"):
        assert dialog._rows[key].retire_button is not None


def test_duplicate_colour_shows_inline_error_and_disables_ok(app):
    dialog = ClassEditorDialog(default_registry())
    ok_button = dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert ok_button.isEnabled()

    dialog._add_name_edit.setText("Copy Root")
    dialog._set_add_color(default_registry().by_key("root").color)
    dialog._on_add_clicked()

    assert dialog._error is not None
    assert dialog._error_label.text() != ""
    # The dialog is never shown in this test, so use the explicit-visibility
    # flag (isHidden) rather than isVisible (which requires a shown ancestor).
    assert not dialog._error_label.isHidden()
    assert not ok_button.isEnabled()

    # Fixing the colour clears the error and re-enables OK.
    dialog._set_add_color("#222222")
    dialog._on_add_clicked()
    assert dialog._error is None
    assert ok_button.isEnabled()


def test_cancel_returns_none(app):
    dialog = ClassEditorDialog(default_registry())
    dialog._add_name_edit.setText("Dead root")
    dialog._set_add_color("#333333")
    dialog._on_add_clicked()

    dialog.reject()
    assert dialog.result_registry() is None


def test_unchanged_registry_returns_none(app):
    dialog = ClassEditorDialog(default_registry())
    dialog._on_accept()
    assert dialog.result_registry() is None

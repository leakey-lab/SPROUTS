"""app/dialogs.py: run_modal and show_transient, driven through Qt's real exec() / open(),
and the rule that no other module runs a dialog's exec().

A stubbed ``exec()`` hid RB-16 (a ``WA_DeleteOnClose`` dialog touched after ``exec()``
deleted it): these tests answer the real modal loop with a timer instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from app.dialogs import run_modal, show_transient
from tests.qt_teardown import flush_deleted

ROOT = Path(__file__).resolve().parents[1]
HOME = "app/dialogs.py"
#: ``exec()`` calls that run no dialog: the application's event loop and a popup menu.
NOT_DIALOGS = {
    ("main.py", "app.exec"),
    ("app/gui/widgets/overlays.py", "menu.exec"),
}
_Button = QMessageBox.StandardButton


# --------------------------------------------------------------------------- the rule
def _exec_calls() -> set[tuple[str, str]]:
    """``(path, callee)`` of every ``.exec()`` / ``.exec_()`` call outside app/dialogs.py."""
    paths = [ROOT / "main.py", *sorted((ROOT / "app").rglob("*.py"))]
    paths += sorted((ROOT / "scripts").rglob("*.py"))
    found = set()
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        if name == HOME:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("exec", "exec_")
            ):
                found.add((name, ast.unparse(node.func)))
    return found


def test_no_module_but_app_dialogs_runs_a_dialog():
    # Run it with app.dialogs.run_modal (or show it with show_transient).
    assert _exec_calls() - NOT_DIALOGS == set()


def test_the_list_of_non_dialog_execs_has_no_stale_entry():
    assert NOT_DIALOGS <= _exec_calls()


# --------------------------------------------------------------------------- helpers
@pytest.fixture
def parent(qapp):
    """A long-lived parent, as the main window is for every prompt."""
    widget = QWidget()
    yield widget
    widget.deleteLater()
    del widget
    flush_deleted()


def _answer_when_shown(action) -> None:
    """``action(dialog)`` once ``exec()`` has shown its modal dialog."""

    def poll() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(5, poll)
            return
        action(dialog)

    QTimer.singleShot(0, poll)


def _deliver_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --------------------------------------------------------------------------- run_modal
def test_run_modal_returns_the_result_and_deletes_the_dialog_once_answered(parent):
    dialog = QDialog(parent)
    _answer_when_shown(lambda shown: shown.done(7))

    assert run_modal(dialog) == 7

    assert not sip.isdeleted(dialog)  # deleteLater(): gone at the next deferred delete
    _deliver_deletes()
    assert sip.isdeleted(dialog)  # although this test still holds it
    assert parent.findChildren(QDialog) == []


def test_read_runs_after_exec_while_the_dialog_still_exists(parent):
    box = QMessageBox(parent)
    yes = box.addButton(_Button.Yes)
    box.addButton(_Button.No)
    _answer_when_shown(lambda shown: shown.button(_Button.Yes).click())
    seen = []

    def read(answered):
        seen.append((answered.isVisible(), sip.isdeleted(answered)))
        return answered.clickedButton()

    assert run_modal(box, read) is yes
    assert seen == [(False, False)]  # closed, not deleted yet
    _deliver_deletes()
    assert parent.findChildren(QMessageBox) == []


def test_a_delete_on_close_dialog_is_deleted_by_exec_and_never_touched_after(parent):
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    _answer_when_shown(lambda shown: shown.accept())

    assert run_modal(dialog) == QDialog.DialogCode.Accepted.value  # no RuntimeError (RB-16)
    assert sip.isdeleted(dialog)


def test_reading_a_delete_on_close_dialog_is_refused_before_it_is_shown(parent):
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    with pytest.raises(ValueError, match="WA_DeleteOnClose"):
        run_modal(dialog, lambda answered: answered.result())

    assert not dialog.isVisible()
    dialog.deleteLater()


def test_a_dialog_is_deleted_even_when_reading_its_answer_fails(parent):
    dialog = QDialog(parent)
    _answer_when_shown(lambda shown: shown.reject())

    def broken(_answered):
        raise KeyError("no answer")

    with pytest.raises(KeyError):
        run_modal(dialog, broken)
    _deliver_deletes()
    assert sip.isdeleted(dialog)


# --------------------------------------------------------------------------- show_transient
def test_show_transient_opens_a_window_modal_box_deleted_once_answered(parent):
    parent.show()
    box = QMessageBox(parent)
    box.setStandardButtons(_Button.Retry | _Button.Cancel)
    answers = []

    assert show_transient(box, lambda result: answers.append((result, sip.isdeleted(box)))) is box

    assert box.isVisible()
    assert box.windowModality() == Qt.WindowModality.WindowModal
    assert QApplication.activeModalWidget() is box  # open() returned at once
    box.button(_Button.Retry).click()
    assert answers == [(_Button.Retry.value, False)]  # answered while the box exists
    _deliver_deletes()
    assert sip.isdeleted(box)
    assert parent.findChildren(QMessageBox) == []
    parent.hide()


def test_show_transient_modeless_box_blocks_nothing_and_goes_once_closed(parent):
    parent.show()
    box = QMessageBox(parent)  # a message box is modal by default

    show_transient(box, modal=False)

    assert box.isVisible()
    assert box.windowModality() == Qt.WindowModality.NonModal
    assert QApplication.activeModalWidget() is None
    box.close()
    _deliver_deletes()
    assert sip.isdeleted(box)
    parent.hide()

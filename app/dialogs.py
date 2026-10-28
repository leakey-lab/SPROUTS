"""Running dialogs: the app's one ``exec()`` of a dialog, and boxes that free themselves.

The rules kept here, once:

- ``QDialog.exec()`` deletes a ``WA_DeleteOnClose`` dialog before it returns; any later call
  on it, ``deleteLater()`` too, raises "wrapped C/C++ object ... has been deleted" (RB-16).
- Any other dialog stays after ``exec()``: PyQt hands it to Python, so it goes only with its
  last reference, and a lambda, a list or a test holding it keeps it a child of its parent
  (dialogs leaked under long-lived parents: 03f1276, 59096e3, 4af5b15). It is deleted once
  answered.
- A box parented to another dialog dies with that dialog (S10-01): parent a transient box
  to the top-level window.

:func:`run_modal` runs a dialog and frees it once its answer is read; :func:`show_transient`
shows a box that frees itself once answered. ``tests/test_dialogs.py`` checks that no other
module calls a dialog's ``exec()``.

Layer-neutral: it imports only PyQt6, so ``app.handlers`` and ``app.gui`` both use it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

__all__ = ["run_modal", "show_transient"]

D = TypeVar("D", bound=QDialog)
T = TypeVar("T")


def run_modal(dialog: D, read: Callable[[D], T] | None = None) -> T | int:
    """Run ``dialog`` modally (``exec()``) and free it once answered.

    Returns ``read(dialog)``, called after ``exec()`` returned and before the dialog is freed
    (``deleteLater()``), or without ``read`` the result code of ``exec()``. ``exec()`` deletes a
    ``WA_DeleteOnClose`` dialog itself, so nothing touches one after it, and ``read`` is refused
    for it (ValueError): nothing is left to read.
    """
    if dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose):
        if read is not None:
            raise ValueError("a WA_DeleteOnClose dialog is deleted by exec(): nothing to read")
        return dialog.exec()
    try:
        result = dialog.exec()
        return result if read is None else read(dialog)
    finally:
        dialog.deleteLater()


def show_transient(
    box: D, on_answer: Callable[[int], object] | None = None, *, modal: bool = True
) -> D:
    """Show ``box`` without waiting for it; it is deleted once closed (``WA_DeleteOnClose``).

    ``on_answer(result)`` runs on its ``finished`` signal, while the box still exists.
    ``modal``: window-modal (``open()``), else modeless (``show()``). Returns ``box``.
    """
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    if on_answer is not None:
        box.finished.connect(on_answer)
    if modal:
        box.open()
    else:
        box.setModal(False)
        box.show()
    return box

"""Mask generation handler: feedback during the pre-flight scan, and button wiring.

* R6: before a mask run the handler scans the project and reads the header of every
  existing mask on the GUI thread (about 1 s for 46k masks). A wait cursor now shows
  the click was taken; it is gone before the overwrite prompt and after a failed scan.
* R7: ``QPushButton.clicked`` passes its ``checked`` flag to the slot. The Generate
  Masks button is connected straight to ``generate_masks(on_success=None)``, so a click
  made ``on_success`` False and a completed run then called ``QTimer.singleShot(0,
  False)`` from a Qt slot (TypeError: with no excepthook, PyQt aborts the app). A
  non-callable ``on_success`` now chains nothing.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from app.handlers import mask_generation_handler as mgh
from app.inference.mask_origin import OverwritePolicy
from tests.test_generation_handlers import _save_traced, _wait, project, qapp  # noqa: F401


def _cursor_shape():
    cursor = QApplication.overrideCursor()
    return None if cursor is None else cursor.shape()


def test_the_existing_mask_scan_shows_a_wait_cursor(project, qapp, monkeypatch):  # noqa: F811
    (project.folder / "mask").mkdir()
    _save_traced(project.folder / "mask" / "a.png")
    during_scan, during_prompt = [], []
    real_scan = mgh.scan_existing_masks

    def spy_scan(layout, stems):
        during_scan.append(_cursor_shape())
        return real_scan(layout, stems)

    def confirm(existing, total):
        during_prompt.append(_cursor_shape())
        return OverwritePolicy.KEEP_EXISTING

    monkeypatch.setattr(mgh, "scan_existing_masks", spy_scan)
    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=confirm)
    assert handler.generate_masks() is True
    _wait(handler.generation_thread, qapp)

    assert during_scan == [Qt.CursorShape.WaitCursor]
    assert during_prompt == [None]  # the user answers the prompt with a normal cursor
    assert _cursor_shape() is None


def test_a_failed_scan_restores_the_cursor(project, qapp, monkeypatch):  # noqa: F811
    def unreadable(layout, stems):
        raise PermissionError(13, "Access is denied (simulated)")

    monkeypatch.setattr(mgh, "scan_existing_masks", unreadable)
    handler = mgh.MaskGenerationHandler(project.window)

    assert handler.generate_masks() is False
    assert _cursor_shape() is None
    assert [kind for kind, _args in project.dialogs] == ["critical"]


def test_the_generate_masks_button_click_chains_nothing(project, qapp, monkeypatch):  # noqa: F811
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc_info: errors.append(exc_info[1]))
    handler = mgh.MaskGenerationHandler(project.window)
    button = QPushButton("Generate Masks")
    button.clicked.connect(handler.generate_masks)  # as app/gui/ui_panels.py wires it

    button.click()  # clicked(checked=False) -> generate_masks(False)
    _wait(handler.generation_thread, qapp)

    assert handler.generation_thread.completed
    assert errors == []  # no TypeError from QTimer.singleShot(0, False) in a slot
    assert len(project.window.outputs_changed) == 1
    button.deleteLater()

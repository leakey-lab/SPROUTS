"""Save / Discard / Cancel end to end on the Correct page: open, edit both layers, save,
leave the stage and come back, switch images (KNOWN_ISSUES #6 review, item E5)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.gui.correct.layer import MASK, SKELETON
from tests.correct_support import (
    DISCARD,
    SAVE,
    bump,
    count_opens,
    draw_line,
    paint,
    plane,
    raster,
    write_mask,
)

pytestmark = pytest.mark.usefixtures("correct_guards")


def edit_both(editor) -> tuple[np.ndarray, np.ndarray]:
    """A mask stroke and a skeleton line; the mask plane and skeleton raster before them."""
    before = plane(editor).copy(), raster(editor).copy()
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    return before


def test_our_own_save_is_no_change_on_disk_coming_back_keeps_the_history(
    make_editor, project, monkeypatch
):
    editor = make_editor(project.a, project.folder)
    mask_before, skeleton_before = edit_both(editor)
    assert editor.save()
    opened = count_opens(editor, monkeypatch)

    editor.load_image(project.a, project.folder)  # leaving Correct and coming back

    assert opened == []  # nothing was read again
    assert editor.session.journal.undo_entries == (MASK, SKELETON)
    editor.undo()
    editor.undo()
    assert np.array_equal(raster(editor), skeleton_before)
    assert np.array_equal(plane(editor), mask_before)
    assert editor.session.dirty_layers() == [MASK, SKELETON]


def test_a_save_from_the_leave_prompt_keeps_the_history_for_the_way_back(
    make_editor, project, answers, monkeypatch
):
    editor = make_editor(project.a, project.folder)
    edit_both(editor)
    answers.will(SAVE)
    assert editor.maybe_save_changes() is True  # a stage switch, answered Save
    opened = count_opens(editor, monkeypatch)

    editor.load_image(project.a, project.folder)

    assert opened == [] and editor.session.can_undo()


def test_a_file_changed_by_someone_else_after_our_save_is_read_again(make_editor, project):
    editor = make_editor(project.a, project.folder)
    edit_both(editor)
    assert editor.save()
    other = np.zeros_like(plane(editor))
    other[5:25, 5:45] = 1
    write_mask(project.gt_mask(), other)  # another program, after our save (of the GT mask)
    bump(project.gt_mask())

    editor.load_image(project.a, project.folder)

    assert np.array_equal(plane(editor), other)
    assert not editor.session.can_undo()  # the documents were read again


def test_only_the_saved_layer_is_trusted_after_a_save(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    assert editor.save()  # the mask only
    write_mask(project.mask(), plane(editor))  # same pixels, but a new write by someone else
    bump(project.mask())

    editor.load_image(project.a, project.folder)

    assert not editor.session.can_undo()  # changed after our save: read again


def test_a_failed_save_trusts_nothing_new(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))

    def denied(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", denied)
    assert editor.save() is False
    monkeypatch.undo()

    assert not editor.session.files_changed()  # the stamps taken at open still hold
    assert editor.has_unsaved_changes()


def test_discard_on_leaving_then_coming_back_shows_the_files_without_reading_them(
    make_editor, project, answers, monkeypatch
):
    editor = make_editor(project.a, project.folder)
    mask_before, skeleton_before = edit_both(editor)
    answers.will(DISCARD)
    assert editor.maybe_save_changes() is True
    opened = count_opens(editor, monkeypatch)

    editor.load_image(project.a, project.folder)

    assert opened == []
    assert np.array_equal(plane(editor), mask_before)
    assert np.array_equal(raster(editor), skeleton_before)
    assert not editor.has_unsaved_changes() and not editor.session.can_undo()


# ---------------------------------------------------------------- focus and hold keys
def focus_out(view) -> None:
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtWidgets import QApplication

    QApplication.sendEvent(
        view, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason)
    )


def test_losing_the_keyboard_while_b_is_held_ends_the_hold(make_editor, project):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    from tests.correct_support import record

    editor = make_editor(project.a, project.folder)
    editor.request_tool(MASK, "eraser")
    view = editor.graphics_view
    holds = record(editor.b_key_status_changed)
    QTest.keyPress(view, Qt.Key.Key_B)
    assert editor.router.b_held and holds == [True]

    focus_out(view)  # e.g. Alt+Tab with B down: its release goes to another window

    assert not editor.router.b_held and holds == [True, False]
    assert editor.layers[MASK].current_tool == "eraser"  # not a tap: no brush


def test_losing_the_keyboard_while_space_is_held_ends_the_pan(make_editor, project):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert view.is_panning_mode()

    focus_out(view)

    assert not view.is_panning_mode()  # a left drag paints again

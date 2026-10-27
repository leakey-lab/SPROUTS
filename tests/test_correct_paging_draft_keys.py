"""PgDn / PgUp with a polyline draft on the skeleton while another layer is edited.

Paging never saves a draft that could be finished: the page stays and says "Finish the line
(Enter) or cancel it (Esc) first". While the mask is the edited layer the draft is kept but
hidden (no preview, no Finish / Cancel prompt) and Enter / Esc go to the mask, so that advice
could not be followed: every PgDn said it again. The page now brings the draft back (the
skeleton becomes the edited layer, shown if it was hidden), so the keys it names reach it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent

from app.gui.correct.layer import MASK, SKELETON
from tests.correct_support import click, raster, record

NO_MODS = Qt.KeyboardModifier.NoModifier


pytestmark = pytest.mark.usefixtures("correct_guards")


def draft(editor, *points) -> None:
    """A polyline draft of ``points`` on the skeleton, not finished."""
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in points:
        click(editor, point)


def key(editor, which) -> bool:
    """A key press on the canvas input (what the canvas does with the keys it gets)."""
    return editor.router.canvas_key_press(QKeyEvent(QEvent.Type.KeyPress, which, NO_MODS))


def test_paging_with_a_hidden_draft_brings_it_back_so_enter_finishes_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    editor.set_active_layer(MASK)  # the draft is kept, out of sight
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [] and notices == [(editor.FINISH_LINE_FIRST, "warn")]
    assert editor.active_layer_id == SKELETON
    assert editor.controls.prompt.isVisible()  # Finish line (Enter) / Cancel (Esc)
    assert key(editor, Qt.Key.Key_Return)  # the advice works: Enter finishes the line
    assert editor.layers[SKELETON].draft_state().points == 0
    assert raster(editor)[60:71, 40:151].any()
    editor.navigate(1)
    assert moved == [1]  # the finished line was saved and the page moved on


def test_esc_then_cancels_the_hidden_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    editor.set_active_layer(MASK)
    editor.navigate(1)

    assert key(editor, Qt.Key.Key_Escape)

    assert editor.layers[SKELETON].draft_state().points == 0
    assert not editor.has_unsaved_changes()


def test_a_draft_on_a_hidden_skeleton_is_shown_again(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    editor.set_active_layer(MASK)
    editor.set_layer_visible(SKELETON, False)

    editor.navigate(1)

    assert editor.layers[SKELETON].is_visible()
    assert editor.layer_panel.rows[SKELETON].eye_button.isChecked()
    assert editor.active_layer_id == SKELETON


def test_a_one_point_draft_does_not_hold_paging_or_switch_the_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60))
    editor.set_active_layer(MASK)
    moved = record(editor.navigate_requested)

    editor.navigate(1)

    assert moved == [1] and editor.active_layer_id == MASK

"""A stray letter in an editor stage does not switch the image being edited (V5).

The library tree keeps the keyboard in Correct while the arrow keys browse
(W1), so a letter that is no shortcut went to the tree's type-ahead: "L" jumped to the next
"L…" row and, 40 ms later, that image opened in the editor, or the unsaved-changes prompt
came up with nothing on screen to say why ("T" left the selection on a tube row while the
editor kept its image). While an editor page is on screen the type-ahead is now off: typing
never moves the selection (the search field is the way to find an image), and the arrows,
PgDn / PgUp, Home / End and the tree's own expand / collapse keys keep working. The Library
keeps its type-ahead.

Offscreen; every project is a tmp_path folder of four 200x150 photos ("L21 · …" to
"L24 · …" on one date), every window has its own INI settings.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.gui.main_window import TREE_LOAD_DELAY_MS
from tests.correct_support import paint, same_path
from tests.qt_teardown import delete_widgets
from tests.qt_wait import settle
from tests.qt_wait import wait_until as wait
from tests.test_correct_main_window import STEMS, leaf, new_project, open_project
from tests.test_shell_keys_after_tree_focus import click_row, in_stage
from tests.window_support import build_window, quiet_window

# No prompt or file dialog, no GL, no delayed sweeps.
pytestmark = pytest.mark.usefixtures("window_guards")

SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def window(app, tmp_path):
    """A shown 1280x800 window with the project open and image 0 shown in the Library."""
    made = [build_window(tmp_path / "settings.ini")]
    project = new_project(tmp_path)
    open_project(made[0], project)
    made[0].project = project
    click_row(made[0], 0)
    yield made[0]
    delete_widgets(made, prepare=quiet_window)


def type_keys(widget, *presses) -> None:
    """Press each ``(key, modifiers)`` on ``widget``, then let the load debounce run out."""
    for key, modifiers in presses:
        QTest.keyClick(widget, key, modifiers)
    QTest.qWait(TREE_LOAD_DELAY_MS * 4)
    settle()


def plain(*keys) -> list:
    return [(key, Qt.KeyboardModifier.NoModifier) for key in keys]


def edited_image(window, editor) -> str | None:
    return next(
        (
            stem
            for i, stem in enumerate(STEMS)
            if same_path(editor.current_image_path, window.project.image(i))
        ),
        None,
    )


# ===================================================================== editor stages
def test_a_stray_letter_neither_moves_the_selection_nor_switches_the_image(window):
    editor = in_stage(window, "Correct")
    tree = window.file_list

    type_keys(tree, *plain(Qt.Key.Key_L, Qt.Key.Key_T, Qt.Key.Key_G), (Qt.Key.Key_L, SHIFT))

    assert tree.currentItem() is leaf(window, 1)  # no jump to "L22 · …" or the "T1" row
    assert edited_image(window, editor) == STEMS[1]
    assert tree.hasFocus()  # the arrows still browse from here


def test_a_stray_letter_with_unsaved_edits_brings_up_no_prompt(window):
    """The prompt guard fails the test if 'Save changes…?' comes up."""
    editor = in_stage(window, "Correct")
    paint(editor, (20, 100), (60, 100))
    assert editor.has_unsaved_changes()
    tree = window.file_list
    tree.setFocus()
    settle()

    type_keys(tree, *plain(Qt.Key.Key_L, Qt.Key.Key_A))

    assert tree.currentItem() is leaf(window, 1)
    assert edited_image(window, editor) == STEMS[1]
    assert editor.has_unsaved_changes()  # nothing was saved or dropped behind the user's back


def test_typed_text_from_an_input_method_does_not_move_the_selection(window):
    in_stage(window, "Correct")
    tree = window.file_list
    event = QInputMethodEvent()
    event.setCommitString("L")

    QApplication.sendEvent(tree, event)
    QTest.qWait(TREE_LOAD_DELAY_MS * 4)
    settle()

    assert tree.currentItem() is leaf(window, 1)


def test_arrows_page_keys_and_home_end_keep_browsing_in_the_editor(window):
    editor = in_stage(window, "Correct")
    tree = window.file_list

    type_keys(tree, *plain(Qt.Key.Key_Down))
    assert tree.currentItem() is leaf(window, 2)
    assert wait(lambda: edited_image(window, editor) == STEMS[2], 2.0)
    type_keys(tree, *plain(Qt.Key.Key_Up))
    assert wait(lambda: edited_image(window, editor) == STEMS[1], 2.0)
    type_keys(tree, *plain(Qt.Key.Key_End))
    assert tree.currentItem() is leaf(window, 3)
    assert wait(lambda: edited_image(window, editor) == STEMS[3], 2.0)
    type_keys(tree, *plain(Qt.Key.Key_PageUp))  # a window shortcut: the previous image
    assert wait(lambda: edited_image(window, editor) == STEMS[2], 2.0)
    type_keys(tree, *plain(Qt.Key.Key_Home))
    assert tree.currentItem() is tree.topLevelItem(0)  # the first row (the field)


def test_the_tree_expand_and_collapse_keys_keep_working_in_the_editors(window):
    editor = in_stage(window, "Correct")
    tree = window.file_list
    date_row = leaf(window, 1).parent()
    tree.setCurrentItem(date_row)  # a group row: the editor keeps its image
    settle()

    type_keys(tree, *plain(Qt.Key.Key_Minus))
    assert not date_row.isExpanded()
    type_keys(tree, *plain(Qt.Key.Key_Plus))
    assert date_row.isExpanded()
    assert tree.currentItem() is date_row
    assert edited_image(window, editor) == STEMS[1]


# =========================================================================== Library
def test_the_library_keeps_its_type_ahead(window):
    in_stage(window, "Library", image=1)
    tree = window.file_list

    type_keys(tree, *plain(Qt.Key.Key_L))

    assert tree.currentItem() is leaf(window, 2)  # the next "L…" row
    assert wait(lambda: window.display_controller.current_name == STEMS[2], 2.0)

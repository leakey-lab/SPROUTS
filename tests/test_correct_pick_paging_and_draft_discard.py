"""PgDn after a plain Select pick, and Discard with only a polyline draft pending.

A branch picked with Select and left as it was is no unsaved work (S5): PgDn drops the pick
and asks for the next image, instead of saying "Finish the line (Enter) or cancel it (Esc)
first", and nothing announces a dropped draft. A picked branch whose points moved, or a new
line of two points, still keeps the page where it is.

When the skeleton is as saved and only a polyline draft is pending, the prompt's Discard
drops the draft and keeps the skeleton's undo history and its timeline entries; it used to
read the file again and forget both. A skeleton that differs from its file is still read
again, as before.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QPoint  # noqa: E402

import app.gui.correct.skeleton_document as skeleton_document  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.correct.skeleton_layer import SkeletonLayer  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    DISCARD,
    Mouse,
    answer_prompts,
    click,
    editor_factory,
    install_guards,
    new_project,
    paint,
    plane,
    qapp,
    raster,
    record,
)


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    return new_project(tmp_path)


@pytest.fixture
def answers(monkeypatch):
    return answer_prompts(monkeypatch)


def pick(editor) -> None:
    """Select the generated line of image a (row 110) with the Select tool."""
    editor.controls.tool_buttons[(SKELETON, "select")].click()
    click(editor, (100, 111))
    assert editor.layers[SKELETON].draft.edit_original is not None


def move_first_point(editor, dy: int = 12) -> None:
    """Drag the picked branch's first point ``dy`` px down."""
    first = editor.layers[SKELETON].draft.points[0]
    router, mouse = editor.router, Mouse()
    router.canvas_press(first, mouse)
    router.canvas_move(first + QPoint(0, dy), mouse, True)
    router.canvas_release(first + QPoint(0, dy), mouse)


def draft(editor, *points) -> None:
    """A polyline draft of ``points``, not finished."""
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in points:
        click(editor, point)


def committed_line(editor) -> None:
    """A polyline drawn and finished on the skeleton (one undoable action), then saved."""
    draft(editor, (40, 60), (150, 70))
    editor.layers[SKELETON].finish_polyline()
    assert editor.save() is True
    assert not editor.has_unsaved_changes()


# ---------------------------------------------------------------------------- PgDn, a pick
def test_paging_after_a_plain_pick_drops_the_pick_and_moves_on(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [1] and notices == []
    skeleton = editor.layers[SKELETON]
    assert skeleton.draft.points == [] and skeleton.draft.edit_original is None
    assert not os.path.exists(project.correction())  # nothing was saved
    assert not editor.has_unsaved_changes()


def test_the_next_image_opens_without_a_dropped_draft_notice(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    editor.navigate(1)
    notices = record(editor.notice)

    editor.load_image(project.b, project.folder)  # what MainWindow does next

    assert (SkeletonLayer.DRAFT_DISCARDED_NOTICE, "info") not in notices
    assert editor.current_image_path == project.b


def test_a_tree_click_after_a_plain_pick_says_nothing_about_a_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    notices = record(editor.notice)

    editor.load_image(project.b, project.folder)

    assert (SkeletonLayer.DRAFT_DISCARDED_NOTICE, "info") not in notices
    assert editor.layers[SKELETON].draft.points == []


def test_a_plain_pick_on_the_reference_layer_does_not_hold_paging(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    editor.set_active_layer(MASK)  # the pick is kept, out of sight
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [1] and notices == []
    assert editor.active_layer_id == MASK


def test_paging_after_a_pick_whose_points_moved_still_stays(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    move_first_point(editor)
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [] and notices == [(editor.FINISH_LINE_FIRST, "warn")]
    assert editor.layers[SKELETON].draft.edit_original is not None  # still being edited


def test_paging_with_a_new_two_point_line_still_stays(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [] and notices == [(editor.FINISH_LINE_FIRST, "warn")]
    assert editor.layers[SKELETON].draft_state().points == 2


# ----------------------------------------------------------------- Discard, a draft only
def test_discard_with_only_a_draft_pending_keeps_the_history(
    make_editor, project, answers, monkeypatch
):
    editor = make_editor(project.a, project.folder)
    committed_line(editor)
    before = raster(editor).copy()
    draft(editor, (30, 20), (120, 30))
    reads = []
    real_read = skeleton_document.imread_unicode
    monkeypatch.setattr(
        skeleton_document,
        "imread_unicode",
        lambda path, *args: reads.append(path) or real_read(path, *args),
    )
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True  # e.g. leaving the stage

    skeleton = editor.layers[SKELETON]
    assert skeleton.draft_state().points == 0
    assert np.array_equal(raster(editor), before)  # the saved skeleton, as it was
    assert reads == []  # the file was not read again
    assert editor.session.journal.undo_entries == (SKELETON,)
    assert editor.session.can_undo() and skeleton.can_undo()
    assert not editor.has_unsaved_changes()
    editor.undo()  # the line drawn before the draft can still be undone
    assert not raster(editor)[60:71, 40:151].any()


def test_discard_says_the_draft_went(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    committed_line(editor)
    draft(editor, (30, 20), (120, 30))
    notices = record(editor.notice)
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    assert (SkeletonLayer.DRAFT_DISCARDED_NOTICE, "info") in notices


def test_discard_with_the_mask_unsaved_and_a_skeleton_draft(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    committed_line(editor)
    paint(editor, (20, 100), (80, 110))
    draft(editor, (30, 20), (120, 30))
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    assert editor.session.journal.undo_entries == (SKELETON,)  # the mask's went
    assert not editor.layers[MASK].can_undo() and editor.layers[SKELETON].can_undo()
    assert not plane(editor)[100:111, 20:81].any()  # the mask is as saved again
    assert editor.layers[SKELETON].draft_state().points == 0


def test_discard_of_an_unsaved_skeleton_still_reads_the_file_again(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    editor.layers[SKELETON].finish_polyline()  # an unsaved edit of the skeleton itself
    draft(editor, (30, 20), (120, 30))
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    skeleton = editor.layers[SKELETON]
    assert not raster(editor)[60:71, 40:151].any()  # back to the generated skeleton
    assert raster(editor)[110, 20:180].any()
    assert not skeleton.can_undo() and editor.session.journal.undo_entries == ()
    assert skeleton.draft_state().points == 0

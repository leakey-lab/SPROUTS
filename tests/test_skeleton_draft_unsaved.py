"""A polyline draft that could be finished counts as unsaved (KNOWN_ISSUES #6, S5).

A draft of two or more points (a new line, or a picked branch whose points moved) makes the
skeleton layer dirty, so the one Save / Discard / Cancel prompt also covers a library tree
click, a stage switch, Load and quitting: Save commits the draft, then writes the correction;
Discard drops it; Cancel stays with it. PgDn still stays and says how to end the draft. A
single point, or a picked branch left as it was, is not unsaved.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.gui.correct.layer import SKELETON, LayerStatus  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    DISCARD,
    SAVE,
    answer_prompts,
    click,
    editor_factory,
    install_guards,
    new_project,
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


def draft(editor, *points) -> None:
    """A polyline draft of ``points``, not finished."""
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in points:
        click(editor, point)


def saved_correction(project) -> np.ndarray | None:
    path = project.correction()
    return cv2.imread(path, cv2.IMREAD_GRAYSCALE) if os.path.exists(path) else None


# ------------------------------------------------------------------------------ the layer
def test_a_draft_of_two_points_is_unsaved_and_one_point_is_not(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    dirty = record(editor.dirty_changed)

    draft(editor, (40, 60))
    assert not skeleton.is_dirty() and not editor.has_unsaved_changes()
    click(editor, (150, 70))

    assert skeleton.is_dirty() and editor.has_unsaved_changes()
    assert skeleton.status() == LayerStatus("dirty")
    assert dirty == [True]
    editor.controls.cancel_button.click()  # Esc / Cancel drops it: clean again
    assert not skeleton.is_dirty() and dirty == [True, False]


def test_a_picked_branch_is_unsaved_only_once_its_points_move(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    editor.controls.tool_buttons[(SKELETON, "select")].click()
    click(editor, (100, 111))  # the generated line at row 110
    assert skeleton.draft.edit_original is not None
    assert not skeleton.is_dirty()

    first = skeleton.draft.points[0]
    editor.router.canvas_press(first, _mouse())
    editor.router.canvas_move(first + _offset(0, 12), _mouse(), True)
    editor.router.canvas_release(first + _offset(0, 12), _mouse())

    assert skeleton.is_dirty()


def test_ctrl_s_commits_the_draft_and_writes_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))

    assert editor.save() is True

    saved = saved_correction(project)
    assert saved is not None and saved[60:71, 40:151].any()  # the line is in the file
    assert editor.layers[SKELETON].draft_state().points == 0
    assert not editor.has_unsaved_changes()
    assert editor.session.journal.undo_entries == (SKELETON,)  # the commit is undoable


# --------------------------------------------------------------------------- the prompt
def test_a_tree_click_with_a_draft_asks_and_save_keeps_the_line(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    answers.will(SAVE)

    editor.load_image(project.b, project.folder)

    assert len(answers.seen) == 1 and "skeleton" in answers.seen[0][1]
    saved = saved_correction(project)
    assert saved is not None and saved[60:71, 40:151].any()
    assert os.path.basename(editor.current_image_path) == "b.png"


def test_a_tree_click_with_a_draft_answered_discard_drops_it(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    answers.will(DISCARD)

    editor.load_image(project.b, project.folder)

    assert len(answers.seen) == 1
    assert saved_correction(project) is None  # nothing written
    assert os.path.basename(editor.current_image_path) == "b.png"
    assert editor.layers[SKELETON].draft_state().points == 0


def test_a_tree_click_with_a_draft_answered_cancel_stays_with_it(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    answers.will(CANCEL)

    editor.load_image(project.b, project.folder)

    assert os.path.basename(editor.current_image_path) == "a.png"
    assert editor.layers[SKELETON].draft_state().points == 2
    assert not raster(editor)[60:71, 40:151].any()  # still a draft, not committed


def test_leaving_the_stage_with_a_draft_asks_first(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))

    answers.will(CANCEL)
    assert editor.maybe_save_changes() is False  # the stage switch is refused
    answers.will(SAVE)
    assert editor.maybe_save_changes() is True

    saved = saved_correction(project)
    assert saved is not None and saved[60:71, 40:151].any()


def test_a_draft_on_the_reference_layer_is_still_asked_about(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    editor.set_active_layer("mask")  # the draft is kept while the mask is edited
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True
    assert len(answers.seen) == 1 and editor.layers[SKELETON].draft_state().points == 0


def test_paging_with_a_draft_still_stays_and_says_how_to_end_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draft(editor, (40, 60), (150, 70))
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [] and notices == [(editor.FINISH_LINE_FIRST, "warn")]
    assert saved_correction(project) is None


# ------------------------------------------------------------------------------- helpers
def _mouse():
    from tests.test_correct_editor import Mouse

    return Mouse()


def _offset(dx: int, dy: int):
    from PyQt6.QtCore import QPoint

    return QPoint(dx, dy)

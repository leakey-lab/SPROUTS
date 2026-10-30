"""The Correct page fixtures of conftest.py (tests/correct_support.py): the guards, the editor
factory, the a/b project and the prompt answers."""

import os
import sys

import numpy as np
import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.gui import canvas_gl
from tests.correct_support import (
    SAVE,
    UNDER_TEST,
    box_plane,
    draw_line,
    paint,
    plane,
    raster,
    same_path,
)

pytestmark = pytest.mark.usefixtures("correct_guards")


def _exc_info_from(filename: str):
    """``(kind, value, tb)`` of an error raised and caught in code of ``filename`` only."""
    source = "def capture():\n    try:\n        raise ValueError(NAME)\n"
    source += "    except ValueError:\n        return sys.exc_info()\n"
    namespace = {"sys": sys, "NAME": filename}
    exec(compile(source, filename, "exec"), namespace)  # its frames name ``filename``
    return namespace["capture"]()


def test_the_project_has_photo_a_with_a_mask_and_a_skeleton_and_photo_b(project):
    assert os.path.isfile(project.a) and os.path.isfile(project.b)
    assert os.path.isfile(project.mask()) and os.path.isfile(project.generated())
    assert not os.path.exists(project.mask("b"))
    assert not os.path.exists(project.gt_mask()) and not os.path.exists(project.correction())


def test_make_editor_opens_the_image_on_its_model_mask(make_editor, project):
    editor = make_editor(project.a, project.folder)

    assert same_path(editor.current_image_path, project.a)
    assert np.array_equal(plane(editor), box_plane())
    assert raster(editor).any()  # the generated skeleton


def test_answers_answer_the_prompt_of_a_save_then_open(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    answers.will(SAVE)

    editor.load_image(project.b, project.folder)

    assert len(answers.seen) == 1 and "a.png" in answers.seen[0][1]
    assert os.path.isfile(project.correction())
    assert same_path(editor.current_image_path, project.b) and not editor.has_unsaved_changes()


def test_an_unanswered_prompt_fails_the_test(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))
    with pytest.raises(pytest.fail.Exception, match="unexpected prompt"):
        editor.maybe_save_changes()


def test_the_guards_refuse_prompts_and_dialogs_and_turn_gl_off(app):
    with pytest.raises(pytest.fail.Exception, match="prompt: "):
        QMessageBox().exec()
    with pytest.raises(pytest.fail.Exception):
        QFileDialog.getOpenFileName(None, "Pick")
    assert os.environ[canvas_gl.ENV_VAR] == "0"


def test_the_guards_record_slot_errors_of_the_editor_only(correct_guards, capsys):
    editor_file = os.path.join("app", "gui", UNDER_TEST)
    sys.excepthook(*_exc_info_from(editor_file))
    sys.excepthook(*_exc_info_from(os.path.join("app", "gui", "correct", "session.py")))

    assert [str(error) for error in correct_guards] == [editor_file]
    assert "session.py" in capsys.readouterr().err
    correct_guards.clear()

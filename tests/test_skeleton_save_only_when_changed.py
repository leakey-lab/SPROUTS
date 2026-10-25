"""A skeleton Save writes a correction only when it differs from the generated one (O2, W1-T3).

Saving is not acceptance: Ctrl+S on an untouched skeleton writes nothing (it once turned a
generated 341 x 256 skeleton into a 640 x 480 "correction" of another length; since WG-T2 the
page saves only unsaved layers and says "Nothing unsaved"), a correction edited back to the
generated skeleton is removed, and an emptied skeleton over a non-empty generated one is a
real correction. Image ``a`` of the project has a generated skeleton on
row 110 (fixtures of ``tests/test_correct_editor.py``).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402

from app.gui.correct.layer import SKELETON  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    draw_line,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    record,
    write_skeleton,
)

UNCHANGED = ("Skeleton unchanged: nothing to save", "info")
NOTHING_UNSAVED = ("Nothing unsaved", "info")  # the page saves unsaved layers only (O2)
REMOVED = ("Skeleton is the generated one again: correction removed", "info")


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


def test_a_no_op_save_writes_nothing_and_says_so(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    saved = record(editor.skeleton_saved)
    notices = record(editor.notice)

    assert editor.save() is False

    assert not os.path.exists(project.correction())
    assert notices == [NOTHING_UNSAVED]
    assert saved == []
    assert editor.layers[SKELETON].document.source == "generated"
    assert editor.layers[SKELETON].save().message == UNCHANGED[0]  # the layer's own rule


def test_edit_undo_save_writes_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    editor.undo()
    notices = record(editor.notice)

    assert editor.save() is False

    assert not os.path.exists(project.correction())
    assert NOTHING_UNSAVED in notices
    assert not editor.has_unsaved_changes()


def test_a_correction_edited_back_to_the_generated_one_is_removed(make_editor, project):
    write_skeleton(project.correction(), row=40)
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source == "corrected"
    skeleton.load_file(project.generated())
    editor.set_active_layer(SKELETON)
    notices = record(editor.notice)

    assert editor.save() is True

    assert not os.path.exists(project.correction())
    assert REMOVED in notices
    assert skeleton.document.source == "generated" and not editor.has_unsaved_changes()
    assert skeleton.status().label == "Generated"


def test_an_emptied_skeleton_over_a_generated_one_is_written(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    editor.clear_active_layer()

    assert editor.save() is True

    saved = imread_unicode(project.correction(), cv2.IMREAD_UNCHANGED)
    assert saved is not None and int((saved > 0).sum()) == 0


def test_a_real_edit_is_written(make_editor, project):
    editor = make_editor(project.a, project.folder)
    saved = record(editor.skeleton_saved)
    draw_line(editor)

    assert editor.save() is True

    assert os.path.isfile(project.correction()) and len(saved) == 1
    assert editor.layers[SKELETON].document.source == "corrected"


def test_a_correction_that_cannot_be_removed_is_a_failed_save(make_editor, project, monkeypatch):
    write_skeleton(project.correction(), row=40)
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    skeleton.load_file(project.generated())
    editor.set_active_layer(SKELETON)

    def refuse(path):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "remove", refuse)
    notices = record(editor.notice)

    assert editor.save() is False

    assert os.path.isfile(project.correction())
    assert skeleton.is_dirty() and skeleton.status().state == "error"
    assert notices == [("Could not save a_skeleton.png: locked", "danger")]


def test_a_legacy_no_op_correction_is_removed_when_the_generated_one_is_loaded(
    make_editor, project
):
    write_skeleton(project.correction())  # an old Ctrl+S: the generated skeleton, as opened
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source == "corrected"
    skeleton.load_file(project.generated())
    assert not skeleton.is_dirty()  # the same pixels: nothing unsaved
    editor.set_active_layer(SKELETON)
    notices = record(editor.notice)
    assert editor.save() is False and notices == [NOTHING_UNSAVED]  # O2: the page writes nothing

    outcome = skeleton.save()  # the layer's own save still removes the no-op correction

    assert not os.path.exists(project.correction())
    assert outcome.ok and outcome.cleared and outcome.message == REMOVED[0]
    assert skeleton.document.source == "generated" and not editor.has_unsaved_changes()


def test_an_untouched_correction_is_never_removed(make_editor, project):
    write_skeleton(project.correction())  # equal to the generated one, but it is what is shown
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    notices = record(editor.notice)

    assert editor.save() is False
    assert editor.layers[SKELETON].save().message == UNCHANGED[0]  # nor does the layer's save

    assert os.path.isfile(project.correction()) and notices == [NOTHING_UNSAVED]


def test_an_unreadable_correction_is_not_removed_by_a_save(make_editor, project):
    os.makedirs(os.path.dirname(project.correction("b")), exist_ok=True)
    with open(project.correction("b"), "wb") as fh:
        fh.write(b"not a png")
    editor = make_editor(project.b, project.folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source is None  # it could not be read: an empty skeleton
    editor.set_active_layer(SKELETON)

    assert editor.save() is False
    assert skeleton.save().ok  # the layer's own save keeps it too

    assert os.path.isfile(project.correction("b"))


def test_an_emptied_correction_without_a_generated_skeleton_is_removed(make_editor, project):
    write_skeleton(project.correction("b"), row=40)  # image b has no generated skeleton
    editor = make_editor(project.b, project.folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source == "corrected"
    editor.set_active_layer(SKELETON)
    editor.clear_active_layer()
    notices = record(editor.notice)

    assert editor.save() is True

    assert not os.path.exists(project.correction("b"))
    assert REMOVED in notices
    assert skeleton.document.source is None
    assert skeleton.status().label == "No skeleton"


def test_after_a_removal_an_unreadable_generated_file_is_not_the_source(make_editor, project):
    write_skeleton(project.correction(), row=40)
    with open(project.generated(), "wb") as fh:
        fh.write(b"not a png")  # opens as an empty skeleton
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    editor.set_active_layer(SKELETON)
    editor.clear_active_layer()

    assert editor.save() is True

    assert not os.path.exists(project.correction())
    assert skeleton.document.source is None

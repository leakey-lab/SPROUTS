"""A GT skeleton the Correct page cannot read is never removed or replaced silently (final
review C1 / D2).

``gt/skeletons/a.png`` that does not decode (truncated by a copy tool, or unreadable at
open) opens as an empty skeleton with the status "Skeleton not shown". ``Ctrl+S`` with
nothing drawn writes nothing (O2); a save after drawing, or after Load prediction (whose save
removes the GT file), asks before it replaces or removes that file ("Replace" / "Keep it");
``PgDn`` stays on the image with a warning. The mask's rules (``test_correct_unloaded_mask``),
driven on the real page with the helpers of ``test_correct_editor.py``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402

from app.gui.correct.layer import SKELETON, LayerStatus  # noqa: E402
from app.gui.correct.save_gate import unloaded_label_of  # noqa: E402
from app.gui.key_text import platform_keys  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    SAVE,
    answer_prompts,
    draw_line,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    raster,
    record,
)

NOT_SHOWN = "Saved skeleton could not be read: it is not shown and is kept unless you save over it"
UNREADABLE = ("Could not read skeleton a.png", "danger")
NOTHING_UNSAVED = ("Nothing unsaved", "info")
PROMPT = (
    "Replace the saved skeleton?",
    "gt/skeletons/a.png exists but could not be shown. Replace it?",
)
KEPT = ("Skeleton not saved: gt/skeletons/a.png was kept", "info")
PAGING = (
    platform_keys("gt/skeletons/a.png could not be shown: save with Ctrl+S to replace it"),
    "warn",
)
TRUNCATED = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # a PNG cut short by a copy tool


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
def answers(monkeypatch):
    return answer_prompts(monkeypatch)


@pytest.fixture
def project(tmp_path):
    """Photo ``a`` with its prediction and an unreadable GT skeleton ``gt/skeletons/a.png``."""
    project = new_project(tmp_path)
    os.makedirs(os.path.dirname(project.correction()))
    with open(project.correction(), "wb") as fh:
        fh.write(TRUNCATED)
    return project


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def test_an_unreadable_gt_skeleton_is_kept_until_the_user_replaces_it(
    make_editor, project, answers
):
    gt = project.correction()
    editor = make_editor()
    notices = record(editor.notice)
    editor.load_image(project.a, project.folder)

    skeleton = editor.layers[SKELETON]
    assert UNREADABLE in notices
    assert skeleton.status() == LayerStatus("error", "Skeleton not shown", NOT_SHOWN)
    assert unloaded_label_of(skeleton) == "gt/skeletons/a.png"
    assert not raster(editor).any()
    assert editor.save() is False and read_bytes(gt) == TRUNCATED  # untouched Ctrl+S
    assert notices[-1] == NOTHING_UNSAVED

    draw_line(editor)
    answers.will(CANCEL)  # "Keep it"
    assert editor.save() is False and read_bytes(gt) == TRUNCATED
    assert answers.seen[-1] == PROMPT
    assert notices[-1] == KEPT

    moved = record(editor.navigate_requested)
    editor.navigate(1)
    assert moved == [] and read_bytes(gt) == TRUNCATED  # PgDn stays, and asks nothing
    assert notices[-1] == PAGING
    assert len(answers.seen) == 1

    answers.will(SAVE)  # "Replace"
    assert editor.save() is True
    assert answers.seen[-1] == PROMPT
    written = cv2.imread(gt, cv2.IMREAD_GRAYSCALE)
    assert written is not None and written.any()
    assert skeleton.status() == LayerStatus("clean", "GT", gt)
    assert unloaded_label_of(skeleton) == ""


def test_load_prediction_then_save_asks_before_it_removes_the_unread_gt(
    make_editor, project, answers
):
    gt = project.correction()
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    editor.set_active_layer(SKELETON)

    editor.load_prediction()
    assert raster(editor)[110].any() and skeleton.is_dirty()
    assert unloaded_label_of(skeleton) == "gt/skeletons/a.png"  # not cleared by the load

    answers.will(CANCEL)  # "Keep it"
    assert editor.save() is False and read_bytes(gt) == TRUNCATED
    assert answers.seen == [PROMPT]

    moved = record(editor.navigate_requested)
    notices = record(editor.notice)
    editor.navigate(1)
    assert moved == [] and read_bytes(gt) == TRUNCATED and notices[-1] == PAGING

    editor.undo()  # back to the empty skeleton: nothing unsaved, the file still not shown
    assert not skeleton.is_dirty()
    assert skeleton.status() == LayerStatus("error", "Skeleton not shown", NOT_SHOWN)

    editor.redo()
    answers.will(SAVE)  # "Replace": the skeleton is the prediction, so the GT file goes
    assert editor.save() is True
    assert not os.path.exists(gt)
    assert skeleton.status().label == "Prediction" and unloaded_label_of(skeleton) == ""


def test_a_gt_skeleton_removed_meanwhile_is_no_file_to_replace(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    assert unloaded_label_of(skeleton) == "gt/skeletons/a.png"
    os.remove(project.correction())

    assert unloaded_label_of(skeleton) == ""  # gone: the Replace question has no file to name


def test_discard_reads_the_file_again(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    draw_line(editor)
    skeleton.revert()
    assert skeleton.status() == LayerStatus("error", "Skeleton not shown", NOT_SHOWN)

    cv2.imwrite(project.correction(), raster(editor) * 0 + 0)  # readable now (empty)
    skeleton.revert()
    assert skeleton.status().state == "clean" and unloaded_label_of(skeleton) == ""

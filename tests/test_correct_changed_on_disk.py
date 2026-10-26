"""Files changed on disk while open in Correct (E2, O19): Discard reloads, Save asks.

A GT mask (``gt/mask/<stem>.png``, the file a mask save writes) or a GT skeleton
(``gt/skeletons/<stem>.png``) rewritten by another program after the page opened it: Discard shows the file, not a stale
copy; Save asks before overwriting it (Overwrite / Reload (lose my edits) / Cancel, Cancel by
default); a file rewritten with the same pixels asks nothing; a clean Ctrl+S writes nothing
(O2) and the image reloads when opened again; PgDn never asks. A Generate run, which
rewrites only the generated skeleton, never makes the skeleton save ask. Driven on the real
page, with the helpers of ``test_correct_editor.py``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from app.gui import editor_prompts  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.key_text import platform_keys  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    DISCARD,
    SAVE,
    H,
    W,
    answer_prompts,
    box_plane,
    bump,
    draw_line,
    editor_factory,
    install_guards,
    new_project,
    paint,
    plane,
    qapp,
    raster,
    record,
    write_image,
    write_mask,
    write_skeleton,
)
from tests.test_editor_prompts import ESCAPE, Answer  # noqa: E402

PROMPT = ("Mask changed on disk", "gt/mask/a.png changed on disk after you opened it.")
INFO = "Overwrite it with your edits, reload it from disk (your edits are lost), or cancel."
CORRECTION_PROMPT = (
    "Skeleton changed on disk",
    "gt/skeletons/a.png changed on disk after you opened it.",
)
PGDN = (
    platform_keys("gt/mask/a.png changed on disk after you opened it: press Ctrl+S to choose"),
    "warn",
)
CANCELLED = ("Mask not saved: gt/mask/a.png changed on disk", "info")
NOUNS = {MASK: "Mask", SKELETON: "Skeleton"}


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


def reloaded(noun: str) -> tuple[str, str]:
    """The notice of a layer read again because its file changed on disk."""
    return (f"{noun} reloaded: the file changed on disk", "info")


def rewrite_mask(project) -> np.ndarray:
    """Another program writes another GT mask (a box at [5:25, 5:45]); returns its plane."""
    other = np.zeros((H, W), np.uint8)
    other[5:25, 5:45] = 1
    write_mask(project.gt_mask(), other)
    bump(project.gt_mask())
    return other


def rewrite_skeleton(path: str, row: int) -> None:
    """Another program writes a skeleton file with one line on ``row``."""
    write_skeleton(path, row=row)
    bump(path)


def saved_plane(path: str) -> np.ndarray:
    """The 0 / 1 plane of a mask file (white on transparent, or 8-bit grey)."""
    pixels = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if pixels.ndim == 3:
        pixels = pixels[:, :, 3] if pixels.shape[2] == 4 else pixels[:, :, 0]
    return (pixels > 127).astype(np.uint8)


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def rows(editor) -> set[int]:
    """The rows of the skeleton raster that hold a line."""
    return set(np.flatnonzero(raster(editor).any(axis=1)).tolist())


# ---------------------------------------------------------------------------------- Discard
@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_discard_after_a_rewrite_shows_the_file_and_saved(make_editor, project, answers, layer_id):
    write_skeleton(project.correction(), row=40)
    editor = make_editor(project.a, project.folder)
    if layer_id == MASK:
        paint(editor, (20, 100), (80, 110))
        other = rewrite_mask(project)
    else:
        draw_line(editor)
        rewrite_skeleton(project.correction(), row=60)
    notices = record(editor.notice)
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    if layer_id == MASK:
        assert np.array_equal(plane(editor), other)
    else:
        assert rows(editor) == {60}
    assert editor.layers[layer_id].status().state == "clean"
    assert reloaded(NOUNS[layer_id]) in notices
    assert not editor.has_unsaved_changes() and not editor.session.can_undo()


def test_discard_without_a_rewrite_reverts_without_a_notice(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    notices = record(editor.notice)
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    assert np.array_equal(plane(editor), box_plane())
    assert reloaded("Mask") not in notices


# ------------------------------------------------------------------------------------- Save
def test_save_after_a_rewrite_asks_and_overwrite_writes_the_edits(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    rewrite_mask(project)
    answers.will(SAVE)

    assert editor.save() is True

    assert answers.seen == [PROMPT]
    assert np.array_equal(saved_plane(project.gt_mask()), plane(editor))
    assert not editor.has_unsaved_changes()


def test_save_after_a_rewrite_reload_shows_the_file_and_writes_nothing(
    make_editor, project, answers
):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    other = rewrite_mask(project)
    written = read_bytes(project.gt_mask())
    notices = record(editor.notice)
    answers.will(DISCARD)  # "Reload (lose my edits)"

    assert editor.save() is False

    assert np.array_equal(plane(editor), other) and read_bytes(project.gt_mask()) == written
    assert not editor.has_unsaved_changes() and not editor.session.can_undo()
    assert reloaded("Mask") in notices


def test_save_after_a_rewrite_cancel_keeps_the_edits_and_the_file(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    edited = plane(editor).copy()
    rewrite_mask(project)
    written = read_bytes(project.gt_mask())
    notices = record(editor.notice)
    answers.will(CANCEL)

    assert editor.save() is False

    assert editor.has_unsaved_changes() and np.array_equal(plane(editor), edited)
    assert read_bytes(project.gt_mask()) == written
    assert CANCELLED in notices


def test_pgdn_after_a_rewrite_stays_and_says_so(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    rewrite_mask(project)
    written = read_bytes(project.gt_mask())
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)  # the guard fails on any prompt

    assert moved == [] and read_bytes(project.gt_mask()) == written
    assert PGDN in notices and editor.has_unsaved_changes()


def test_equal_pixels_with_a_new_mtime_ask_nothing(make_editor, project):
    write_mask(project.gt_mask(), box_plane())  # a GT mask (the page opens it)
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    write_mask(project.gt_mask(), box_plane())  # the pixels the page opened, written again
    bump(project.gt_mask())

    assert editor.save() is True  # the guard fails on any prompt

    assert np.array_equal(saved_plane(project.gt_mask()), plane(editor))


def test_a_gt_skeleton_rewritten_as_it_was_asks_nothing(make_editor, project):
    write_skeleton(project.correction(), row=40)
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    rewrite_skeleton(project.correction(), row=40)  # the same line, written again

    assert editor.save() is True  # the guard fails on any prompt

    assert not editor.has_unsaved_changes()


def test_a_mask_that_appeared_unshowable_meanwhile_is_not_overwritten_unasked(
    make_editor, project, answers
):
    editor = make_editor(project.b, project.folder)  # b has no mask
    paint(editor, (20, 100), (80, 110))
    write_mask(project.gt_mask("b"), np.ones((10, 10), np.uint8))  # another size: not shown
    answers.will(CANCEL)

    assert editor.save() is False

    assert answers.seen == [("Mask changed on disk", PROMPT[1].replace("a.png", "b.png"))]
    assert saved_plane(project.gt_mask("b")).shape == (10, 10)


@pytest.mark.parametrize("nested", [False, True], ids=["in_the_project", "in_a_sub_folder"])
def test_an_unshown_mask_rewritten_meanwhile_asks_twice_naming_it_alike(
    make_editor, tmp_path, answers, nested
):
    folder = str(tmp_path / "project")
    images = os.path.join(folder, "tube1") if nested else folder
    photo = write_image(os.path.join(images, "a.png"))
    mask = write_mask(os.path.join(images, "gt", "mask", "a.png"), np.ones((10, 10), np.uint8))
    editor = make_editor(photo, folder)  # another size: the GT mask is not shown
    paint(editor, (20, 100), (80, 110))
    other = np.zeros((H, W), np.uint8)
    other[5:25, 5:45] = 1
    write_mask(mask, other)  # another program: a mask the page could show now
    bump(mask)
    written = read_bytes(mask)
    answers.will(SAVE, CANCEL)  # Replace, then Cancel

    assert editor.save() is False

    label = "tube1/gt/mask/a.png" if nested else "gt/mask/a.png"
    assert answers.seen == [
        ("Replace the saved mask?", f"{label} exists but could not be shown. Replace it?"),
        ("Mask changed on disk", f"{label} changed on disk after you opened it."),
    ]
    assert read_bytes(mask) == written and editor.has_unsaved_changes()


def test_a_clean_ctrl_s_after_a_rewrite_writes_nothing_and_reopening_reloads(
    make_editor, project
):  # O2: a save with nothing unsaved writes nothing
    editor = make_editor(project.a, project.folder)
    other = rewrite_mask(project)
    rewritten = read_bytes(project.gt_mask())
    notices = record(editor.notice)

    assert editor.save() is False

    assert read_bytes(project.gt_mask()) == rewritten
    assert notices == [("Nothing unsaved", "info")]
    editor.load_image(project.a, project.folder)  # the files changed: the documents reload
    assert np.array_equal(plane(editor), other)


def test_a_rewritten_gt_skeleton_asks_but_a_rewritten_prediction_does_not(
    make_editor, project, answers
):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    rewrite_skeleton(project.generated(), row=60)  # a Generate run

    assert editor.save() is True  # no prompt: the GT skeleton is written
    assert os.path.isfile(project.correction()) and answers.seen == []

    draw_line(editor, (40, 20), (160, 25))
    rewrite_skeleton(project.correction(), row=60)  # another program
    answers.will(CANCEL)

    assert editor.save() is False
    assert answers.seen == [CORRECTION_PROMPT] and editor.has_unsaved_changes()


# ------------------------------------------------------------------------------- the box
@pytest.mark.parametrize(
    ("pressed", "expected"),
    [
        (QMessageBox.StandardButton.Save, editor_prompts.OVERWRITE),
        (QMessageBox.StandardButton.Discard, editor_prompts.RELOAD),
        (QMessageBox.StandardButton.Cancel, editor_prompts.CANCEL),
        (ESCAPE, editor_prompts.CANCEL),
    ],
)
def test_the_changed_on_disk_box_names_the_file_and_cancels_by_default(
    app, monkeypatch, pressed, expected
):
    answer = Answer(monkeypatch, pressed)

    assert editor_prompts.ask_changed_on_disk(None, "mask", "gt/mask/a.png") == expected

    seen, button = answer.seen, QMessageBox.StandardButton
    assert (seen["title"], seen["text"]) == PROMPT
    assert seen["info"] == INFO
    assert set(seen["buttons"]) == {button.Save, button.Discard, button.Cancel}
    assert seen["buttons"][button.Save] == "Overwrite"
    assert seen["buttons"][button.Discard] == "Reload (lose my edits)"
    assert seen["default"] == seen["escape"] == button.Cancel

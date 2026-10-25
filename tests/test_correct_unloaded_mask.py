"""A saved (GT) mask the Correct page cannot show is never removed or replaced silently (M1-A).

A model prediction of another size opens stretched to the photo (O8, W3-T4). A 2048 x 1536
photo with a 640 x 480 GT mask ``gt/mask/a.png`` (or an undecodable one) opens
with the mask not shown and says so. ``Ctrl+S`` with nothing painted writes nothing (O2), a
save after painting asks before replacing it ("Replace" / "Keep it"), and ``PgDn`` stays on
the image with a warning. Driven on the real page, with the helpers of ``test_correct_editor.py``.
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
from app.gui.correct.layer import MASK, LayerStatus  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    SAVE,
    answer_prompts,
    editor_factory,
    install_guards,
    paint,
    qapp,
    record,
    write_image,
)
from tests.test_editor_prompts import ESCAPE, Answer  # noqa: E402

NOT_SHOWN_SIZE = (
    "Saved mask size does not match the image: it is not shown and is kept unless you save over it"
)
NOT_SHOWN_UNREADABLE = (
    "Saved mask could not be read: it is not shown and is kept unless you save over it"
)
RESIZED = (
    "Mask is 640 x 480; shown at the photo's 2048 x 1536. Saving stores it at the photo's size."
)
NOTHING_UNSAVED = ("Nothing unsaved", "info")
PROMPT = ("Replace the saved mask?", "gt/mask/a.png exists but could not be shown. Replace it?")


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


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def photo_and_mask(tmp_path) -> tuple[str, str, str]:
    """A 2048 x 1536 photo ``a.png`` in ``project/``; returns (photo, folder, GT mask path)."""
    folder = str(tmp_path / "project")
    photo = write_image(os.path.join(folder, "a.png"), 2048, 1536)
    return photo, folder, os.path.join(folder, "gt", "mask", "a.png")


def test_a_2048_photo_never_loses_its_unshown_mask(make_editor, tmp_path, answers):
    photo, folder, mask = photo_and_mask(tmp_path)
    on = np.zeros((480, 640), bool)
    on[100:200, 100:300] = True
    write_mask_file(mask, on)  # an editor-saved mask of the 640 x 480 model input
    before = read_bytes(mask)
    editor = make_editor()
    notices = record(editor.notice)
    editor.load_image(photo, folder)

    assert (NOT_SHOWN_SIZE, "warn") in notices
    assert editor.layers[MASK].status() == LayerStatus("error", "Mask not shown", NOT_SHOWN_SIZE)
    assert editor.save() is False and read_bytes(mask) == before  # untouched Ctrl+S
    assert notices[-1] == NOTHING_UNSAVED

    paint(editor, (100, 100), (300, 300))
    answers.will(CANCEL)  # "Keep it"
    assert editor.save() is False and read_bytes(mask) == before
    assert answers.seen[-1] == PROMPT
    assert notices[-1] == ("Mask not saved: gt/mask/a.png was kept", "info")

    moved = record(editor.navigate_requested)
    editor.navigate(1)
    assert moved == [] and read_bytes(mask) == before  # PgDn stays, and asks nothing
    assert notices[-1] == (
        "gt/mask/a.png could not be shown: save with Ctrl+S to replace it",
        "warn",
    )
    assert len(answers.seen) == 1

    answers.will(SAVE)  # "Replace"
    assert editor.save() is True
    assert cv2.imread(mask, cv2.IMREAD_UNCHANGED).shape[:2] == (1536, 2048)
    assert answers.seen[-1] == PROMPT
    assert editor.layers[MASK].status() == LayerStatus("clean", "GT", "gt/mask/a.png")


def test_a_2048_photo_shows_its_640_prediction(make_editor, tmp_path):  # O8
    photo, folder, gt = photo_and_mask(tmp_path)
    small = np.zeros((480, 640), np.uint8)
    small[100:200, 100:300] = 255
    prediction = os.path.join(folder, "mask", "a.png")
    os.makedirs(os.path.dirname(prediction))
    assert cv2.imwrite(prediction, small)
    before = read_bytes(prediction)
    editor = make_editor()
    notices = record(editor.notice)
    editor.load_image(photo, folder)

    layer = editor.layers[MASK]
    shown = layer.document.history.plane
    assert shown.shape == (1536, 2048)
    assert np.array_equal(
        shown, cv2.resize(small // 255, (2048, 1536), interpolation=cv2.INTER_NEAREST)
    )
    assert layer.status().label == "Prediction" and not layer.is_dirty()
    assert (RESIZED, "info") in notices

    assert editor.save() is False  # nothing to save: the prediction stays as it is
    assert notices[-1] == NOTHING_UNSAVED
    assert read_bytes(prediction) == before and not os.path.exists(gt)


def test_an_undecodable_mask_gives_a_notice_and_survives_ctrl_s(make_editor, tmp_path):
    photo, folder, mask = photo_and_mask(tmp_path)
    os.makedirs(os.path.dirname(mask))
    with open(mask, "wb") as fh:
        fh.write(b"not a png")
    editor = make_editor()
    notices = record(editor.notice)
    editor.load_image(photo, folder)

    assert (NOT_SHOWN_UNREADABLE, "warn") in notices
    status = editor.layers[MASK].status()
    assert status == LayerStatus("error", "Mask not shown", NOT_SHOWN_UNREADABLE)

    assert editor.save() is False  # nothing painted: no prompt (the guard fails on one)

    assert read_bytes(mask) == b"not a png"
    assert notices[-1] == NOTHING_UNSAVED


@pytest.mark.parametrize(
    ("pressed", "expected"),
    [
        (QMessageBox.StandardButton.Save, editor_prompts.REPLACE),
        (QMessageBox.StandardButton.Cancel, editor_prompts.CANCEL),
        (ESCAPE, editor_prompts.CANCEL),
    ],
)
def test_the_replace_box_names_the_file_and_keeps_it_by_default(
    app, monkeypatch, pressed, expected
):
    answer = Answer(monkeypatch, pressed)

    assert editor_prompts.ask_replace_unloaded(None, "mask", "gt/mask/a.png") == expected

    seen, button = answer.seen, QMessageBox.StandardButton
    assert (seen["title"], seen["text"]) == PROMPT
    assert seen["info"] == "Your edits replace the whole file. Keep it leaves the file as it is."
    assert seen["buttons"] == {button.Save: "Replace", button.Cancel: "Keep it"}
    assert seen["default"] == seen["escape"] == button.Cancel

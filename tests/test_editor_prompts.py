"""editor_prompts (C11): the one Save / Discard / Cancel box and the failed-save box of the
canvas editors, with today's wording word for word, answers mapped to plain strings, and the
box freed after use.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app.gui import editor_prompts  # noqa: E402
from app.gui.editor_prompts import (  # noqa: E402
    CANCEL,
    DISCARD,
    KEEP,
    SAVE,
    ask_after_failed_save,
    ask_unsaved,
    join_nouns,
)

Button = QMessageBox.StandardButton
ESCAPE = "escape"  # the scripted answer "the user pressed Esc"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def parent(app):
    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Answer:
    """Stands in for ``QMessageBox.exec``: records what the box shows, returns an answer."""

    def __init__(self, monkeypatch, answer):
        self.answer = answer
        self.seen: dict = {}
        monkeypatch.setattr(QMessageBox, "exec", lambda box: self._exec(box))

    def _exec(self, box):
        self.seen = {
            "icon": box.icon(),
            "title": box.windowTitle(),
            "text": box.text(),
            "info": box.informativeText(),
            "buttons": {box.standardButton(w): w.text() for w in box.buttons()},
            "default": box.standardButton(box.defaultButton()),
            "escape": box.standardButton(box.escapeButton()),
        }
        if self.answer == ESCAPE:  # Esc (or closing the box) answers with the escape button
            return int(box.standardButton(box.escapeButton()).value)
        return int(getattr(self.answer, "value", self.answer))


NOUNS = ["mask", "skeleton", "mask and skeleton", "GT"]


# --------------------------------------------------------------------------- the unsaved box
@pytest.mark.parametrize("noun", NOUNS)
def test_the_unsaved_box_keeps_todays_wording(monkeypatch, parent, noun):
    answer = Answer(monkeypatch, Button.Save)
    assert ask_unsaved(parent, noun, "plot_7.jpg") == SAVE
    assert answer.seen["icon"] == QMessageBox.Icon.Question
    assert answer.seen["title"] == f"Unsaved {noun}"
    assert answer.seen["text"] == f"Save changes to the {noun} for plot_7.jpg?"
    assert answer.seen["info"] == "Discarded edits cannot be recovered."
    assert set(answer.seen["buttons"]) == {Button.Save, Button.Discard, Button.Cancel}
    assert answer.seen["default"] == Button.Save
    assert answer.seen["escape"] == Button.Cancel


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (Button.Save, SAVE),
        (Button.Discard, DISCARD),
        (Button.Cancel, CANCEL),
        (ESCAPE, CANCEL),
        (0, CANCEL),  # closed some other way
    ],
)
def test_the_unsaved_box_maps_every_answer(monkeypatch, parent, answer, expected):
    Answer(monkeypatch, answer)
    assert ask_unsaved(parent, "mask", "a.jpg") == expected


# --------------------------------------------------------------------------- the failed box
@pytest.mark.parametrize("noun", NOUNS)
def test_the_failed_save_box_keeps_todays_wording(monkeypatch, parent, noun):
    answer = Answer(monkeypatch, Button.Discard)
    assert ask_after_failed_save(parent, noun, "plot_7.jpg") == DISCARD
    assert answer.seen["icon"] == QMessageBox.Icon.Warning
    assert answer.seen["title"] == f"{noun[:1].upper()}{noun[1:]} not saved"
    assert answer.seen["text"] == f"The {noun} for plot_7.jpg could not be saved."
    assert answer.seen["info"] == "Discard the edits, or keep editing? Discarded edits are lost."
    assert set(answer.seen["buttons"]) == {Button.Discard, Button.Cancel}
    assert answer.seen["buttons"][Button.Cancel] == "Keep editing"
    assert answer.seen["default"] == Button.Cancel  # Keep editing
    assert answer.seen["escape"] == Button.Cancel


def test_failed_box_titles_capitalise_only_the_first_letter(monkeypatch, parent):
    answer = Answer(monkeypatch, Button.Discard)
    ask_after_failed_save(parent, "mask and skeleton", "a.jpg")
    assert answer.seen["title"] == "Mask and skeleton not saved"
    ask_after_failed_save(parent, "GT", "a.jpg")
    assert answer.seen["title"] == "GT not saved"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(Button.Discard, DISCARD), (Button.Cancel, KEEP), (ESCAPE, KEEP), (0, KEEP)],
)
def test_the_failed_save_box_maps_every_answer(monkeypatch, parent, answer, expected):
    Answer(monkeypatch, answer)
    assert ask_after_failed_save(parent, "skeleton", "a.jpg") == expected


# --------------------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    ("nouns", "joined"),
    [
        ([], ""),
        (["mask"], "mask"),
        (["mask", "skeleton"], "mask and skeleton"),
        (("mask", "skeleton", "GT"), "mask, skeleton and GT"),
    ],
)
def test_join_nouns(nouns, joined):
    assert join_nouns(nouns) == joined


def test_the_answers_are_distinct_strings():
    assert {SAVE, DISCARD, CANCEL, KEEP} == {"save", "discard", "cancel", "keep"}
    assert set(editor_prompts.__all__) >= {"ask_unsaved", "ask_after_failed_save", "join_nouns"}


@pytest.mark.parametrize("ask", [ask_unsaved, ask_after_failed_save])
def test_every_box_is_freed_after_use(monkeypatch, parent, ask):
    Answer(monkeypatch, Button.Discard)
    for _ in range(3):
        ask(parent, "mask", "a.jpg")
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(QMessageBox) == []
    assert not [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox)]

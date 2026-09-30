"""Trace GT: Discard reverts to the last saved tracing (G1, 2026-09-30).

Before, Discard only marked the tracing saved and left the discarded strokes on screen
(the old mask editor's flag semantics). The canvas then said "Saved" while it showed edits
the user had thrown away, and the next edit + Save wrote them into ``gt/<stem>.png``: e.g.
Trace GT -> paint -> Export dataset -> Discard -> close the export -> paint -> Ctrl+S.
Discard now restores the tracing as last saved (or as opened, empty when there is no GT
file) and clears the history, like the Correct editor's Discard.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gt import Tracing, default_registry, gt_path, read_tracing, write_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402

H, W = 60, 80
_SAVE = QMessageBox.StandardButton.Save
_DISCARD = QMessageBox.StandardButton.Discard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def editor(app):
    """A GT editor closed and deleted deterministically after the test."""
    made = []

    def _make(image_path=None):
        iface = GtTracingInterface()
        made.append(iface)
        iface.resize(900, 700)
        if image_path:
            iface.load_image(image_path)
        return iface

    yield _make
    for iface in made:
        iface.close()
        iface.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def image(tmp_path):
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    return path


def _stroke(iface, *points, size=6):
    iface.size_slider.setValue(size)
    iface.begin_stroke(QPoint(*points[0]))
    for p in points[1:]:
        iface.continue_stroke(QPoint(*p))
    iface.end_stroke()


def _answer(monkeypatch, *answers):
    """Answer the next prompts in order; returns the window titles seen."""
    seen, queue = [], list(answers)

    def _exec(box):
        seen.append(box.windowTitle())
        if not queue:
            pytest.fail(f"unexpected prompt {box.text()!r}")
        return queue.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return seen


def _tick(iface, key):
    iface.class_panel._rows[key].checkbox.setChecked(True)


def test_discard_restores_the_saved_tracing_and_its_checks(editor, image, monkeypatch):
    iface = editor(image)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30))
    assert iface.save_mask()
    saved = iface._pixmap_to_ids()
    assert saved[30, 40] == 1

    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55))
    _tick(iface, "root_tip")
    assert iface.has_unsaved_changes()
    seen = _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert seen == ["Unsaved GT"]

    assert not iface.has_unsaved_changes()
    assert np.array_equal(iface._pixmap_to_ids(), saved)  # the canvas shows the saved tracing
    assert np.array_equal(iface.edits.plane, saved)
    assert iface._reviewed == {"root"} and iface._present == {"root"}
    assert not iface.class_panel._rows["root_tip"].checkbox.isChecked()
    assert not iface.class_panel._rows["nodule"].checkbox.isChecked()
    assert not iface.edits.can_undo and not iface.edits.can_redo  # like Correct: history cleared
    assert not iface.undo_button.isEnabled()
    assert "Saved · 1/3 checked" in iface.state_label.text()


def test_an_edit_after_discard_never_saves_the_discarded_strokes(editor, image, monkeypatch):
    """The failure: Discard, then any edit + Save wrote the discarded strokes too."""
    iface = editor(image)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30))
    assert iface.save_mask()

    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55))  # to be discarded
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    _stroke(iface, (5, 50), (20, 50))  # a new nodule stroke, kept
    assert iface.save_mask()

    ids = read_tracing(gt_path(image)).ids
    assert ids[50, 10] == 2  # the new stroke
    assert ids[10, 40] == 0  # the discarded stroke is not in the file
    assert ids[30, 40] == 1  # the root crossing is back to root


def test_discard_without_a_gt_file_empties_the_canvas(editor, image, monkeypatch):
    iface = editor(image)
    _stroke(iface, (10, 10), (40, 10))
    _tick(iface, "nodule")
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert not iface._pixmap_to_ids().any() and not iface.edits.plane.any()
    assert iface._reviewed == set() and iface._present == set()
    assert "No GT" in iface.state_label.text()
    assert not os.path.exists(gt_path(image))


def test_discard_goes_back_to_the_last_save_not_to_the_opened_state(editor, image, monkeypatch):
    iface = editor(image)
    _stroke(iface, (10, 10), (40, 10))
    assert iface.save_mask()
    first = iface.edits.plane.copy()
    _stroke(iface, (10, 40), (40, 40))
    assert iface.save_mask()
    second = iface.edits.plane.copy()
    assert not np.array_equal(first, second)
    _stroke(iface, (60, 5), (60, 55))
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert np.array_equal(iface._pixmap_to_ids(), second)


def test_discard_after_a_failed_save_reverts_too(editor, image, monkeypatch):
    import app.gui.gt_tracing_interface as gti

    iface = editor(image)
    _stroke(iface, (10, 10), (40, 10))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gti, "write_tracing", _boom)
    seen = _answer(monkeypatch, _SAVE, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert seen == ["Unsaved GT", "GT not saved"]
    assert not iface.has_unsaved_changes()
    assert not iface._pixmap_to_ids().any()


def test_discard_of_a_lossy_remap_leaves_the_file_and_shows_it_as_opened(
    editor, image, monkeypatch
):
    """A remap on load is an unsaved change; Discard keeps the file and the opened view."""
    other = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    ids = np.zeros((H, W), np.uint8)
    ids[5:15, 5:15] = 4  # 'hypha', unknown here -> Ignore on load
    ids[30:40, 30:40] = 1
    path = gt_path(image)
    write_tracing(path, Tracing(ids, frozenset({"root"})), other)
    with open(path, "rb") as fh:
        before = fh.read()

    iface = editor(image)
    assert iface.has_unsaved_changes()
    opened = iface.edits.plane.copy()
    _stroke(iface, (60, 50), (75, 50))
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert not iface.has_unsaved_changes()
    assert np.array_equal(iface._pixmap_to_ids(), opened)
    with open(path, "rb") as fh:
        assert fh.read() == before


def test_discard_of_the_class_checks_alone(editor, image, monkeypatch):
    """A tick is an edit of the tracing too: Discard unticks it."""
    iface = editor(image)
    _tick(iface, "nodule")
    assert iface.has_unsaved_changes() and iface._reviewed == {"nodule"}
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert iface._reviewed == set()
    assert not iface.class_panel._rows["nodule"].checkbox.isChecked()

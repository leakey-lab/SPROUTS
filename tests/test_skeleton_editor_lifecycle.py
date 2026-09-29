"""Skeleton editor document lifecycle: opening, unsaved changes, saving.

PE-01 / AR-02 / RB-05 / UXE-02 (unsaved-changes guard), PE-11 / AR-09 / UI-11
(the generated skeleton opens without a file dialog), RB-11 (atomic save) and
UXE-11 (file / state chip, save notice).
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from app.gui.skeleton_correction_interface import SkeletonCorrectionInterface  # noqa: E402

W, H = 200, 150


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _write_image(path, w=W, h=H):
    img = np.full((h, w, 3), 90, np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _write_skeleton(path, row=75, w=W, h=H):
    sk = np.zeros((h, w), np.uint8)
    sk[row, 20 : w - 20] = 255
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(str(path), sk)
    return str(path)


@pytest.fixture()
def project(tmp_path):
    a = _write_image(tmp_path / "a.png")
    b = _write_image(tmp_path / "b.png")
    return tmp_path, a, b


@pytest.fixture()
def prompts(monkeypatch):
    """Record every QMessageBox shown and answer with ``prompts.answer``."""

    class Recorder(list):
        answer = QMessageBox.StandardButton.Discard

    rec = Recorder()

    def fake_exec(box):
        rec.append(box.text())
        return rec.answer

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return rec


@pytest.fixture()
def no_dialog(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a file dialog was opened")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", boom)


def _editor(app):
    iface = SkeletonCorrectionInterface()
    iface.resize(900, 700)
    return iface


def _draw_line(iface):
    """One user edit: commit a polyline through the Polyline tool."""
    iface.polyline_button.click()
    iface.on_tool_mouse_press(QPoint(30, 30), None)
    iface.on_tool_mouse_release(QPoint(30, 30), None)
    iface.on_tool_mouse_press(QPoint(150, 120), None)
    iface.on_tool_mouse_release(QPoint(150, 120), None)
    iface.finish_polyline()


# ------------------------------------------------------------------ opening
def test_generated_skeleton_opens_without_a_dialog(app, project, no_dialog):
    root, a, _b = project
    _write_skeleton(os.path.join(root, "skeletons", "a_fake.png"))
    iface = _editor(app)
    iface.load_image(a, str(root))
    assert iface.model.mask.sum() > 0
    assert iface.skeleton_source == "generated"
    assert not iface.has_unsaved_changes()


def test_saved_correction_wins_over_the_generated_skeleton(app, project, no_dialog):
    root, a, _b = project
    _write_skeleton(os.path.join(root, "skeletons", "a_fake.png"), row=30)
    _write_skeleton(os.path.join(root, "skeleton_corrections", "a_skeleton.png"), row=100)
    iface = _editor(app)
    iface.load_image(a, str(root))
    assert iface.skeleton_source == "corrected"
    assert iface.model.mask[100].any() and not iface.model.mask[30].any()


def test_injected_resolver_finds_the_generated_skeleton(app, project, no_dialog):
    root, a, _b = project
    elsewhere = _write_skeleton(os.path.join(root, "out", "a_fake.png"))
    iface = _editor(app)
    iface.set_generated_skeleton_resolver(lambda image_path: elsewhere)
    iface.load_image(a, str(root))
    assert iface.skeleton_source == "generated"
    assert iface.model.mask.sum() > 0


def test_empty_state_hint_says_there_is_no_skeleton(app, project):
    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    assert iface.skeleton_source is None
    assert "No skeleton" in iface.status_label.text()
    assert "No skeleton" in iface.hint_panel.state_label.text()
    assert iface.hint_panel.text() == "a.png"


def test_undecodable_image_is_reported_not_opened(app, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    iface = _editor(app)
    notices = []
    iface.notice.connect(lambda msg, kind: notices.append(kind))
    iface.load_image(str(bad), str(tmp_path))
    assert notices == ["danger"]
    assert iface.current_image_path == ""


# ------------------------------------------------------------------ unsaved guard
def test_an_edit_marks_the_document_unsaved(app, project):
    root, a, _b = project
    iface = _editor(app)
    seen = []
    iface.dirty_changed.connect(seen.append)
    iface.load_image(a, str(root))
    _draw_line(iface)
    assert iface.has_unsaved_changes()
    assert seen == [True]
    assert iface.hint_panel.state() == "dirty"
    iface.undo()
    assert not iface.has_unsaved_changes()
    assert seen == [True, False]


def test_switching_image_prompts_and_discard_loads_the_next(app, project, prompts):
    root, a, b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    prompts.answer = QMessageBox.StandardButton.Discard
    iface.load_image(b, str(root))
    assert len(prompts) == 1 and "a.png" in prompts[0]
    assert iface.current_image_path == b
    assert not os.path.exists(os.path.join(root, "skeleton_corrections", "a_skeleton.png"))


def test_switching_image_with_save_writes_the_correction(app, project, prompts):
    root, a, b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    prompts.answer = QMessageBox.StandardButton.Save
    iface.load_image(b, str(root))
    out = os.path.join(root, "skeleton_corrections", "a_skeleton.png")
    assert os.path.isfile(out)
    assert iface.current_image_path == b and not iface.has_unsaved_changes()


def test_cancel_keeps_the_open_image_and_its_edit(app, project, prompts):
    root, a, b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    pixels = int((iface.model.mask > 0).sum())
    prompts.answer = QMessageBox.StandardButton.Cancel
    iface.load_image(b, str(root))
    assert iface.current_image_path == a
    assert int((iface.model.mask > 0).sum()) == pixels
    assert iface.has_unsaved_changes()
    assert iface.maybe_save_changes() is False


def test_reselecting_the_open_image_keeps_edits_and_history(app, project, prompts):
    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    pixels = int((iface.model.mask > 0).sum())
    iface.load_image(a, str(root))
    assert prompts == []
    assert int((iface.model.mask > 0).sum()) == pixels
    assert iface.model.can_undo


def test_discard_on_stage_leave_reverts_to_the_file_on_disk(app, project, prompts):
    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    prompts.answer = QMessageBox.StandardButton.Discard
    assert iface.maybe_save_changes() is True
    assert not iface.has_unsaved_changes()
    assert not iface.model.mask.any()


def test_clean_editor_never_prompts(app, project, prompts):
    root, a, b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    iface.load_image(b, str(root))
    assert iface.maybe_save_changes() is True
    assert iface.reset() is True
    assert prompts == []
    assert iface.current_image_path == ""


def test_reset_honours_cancel(app, project, prompts):
    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    prompts.answer = QMessageBox.StandardButton.Cancel
    assert iface.reset() is False
    assert iface.current_image_path == a


# ------------------------------------------------------------------ saving
def test_save_reports_through_notice_and_chip(app, project):
    root, a, _b = project
    iface = _editor(app)
    notices, saved = [], []
    iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    iface.skeleton_saved.connect(saved.append)
    iface.load_image(a, str(root))
    _draw_line(iface)
    assert iface.save_skeleton() is True
    out = os.path.join(root, "skeleton_corrections", "a_skeleton.png")
    assert saved == [os.path.normpath(out)] or saved == [out]
    assert len(notices) == 1 and notices[0][1] == "success"
    assert iface.hint_panel.state() == "clean"
    assert iface.skeleton_source == "corrected"
    written = cv2.imread(out, cv2.IMREAD_GRAYSCALE)
    assert set(np.unique(written)) <= {0, 255}


def test_save_without_a_loaded_raster_uses_the_configured_output_size(app, project):
    from app.config import SKELETON_OUTPUT_SIZE

    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    assert iface.save_skeleton()
    out = cv2.imread(
        os.path.join(root, "skeleton_corrections", "a_skeleton.png"), cv2.IMREAD_GRAYSCALE
    )
    assert (out.shape[1], out.shape[0]) == tuple(SKELETON_OUTPUT_SIZE)


def test_failed_save_keeps_the_old_file_and_stays_unsaved(app, project, monkeypatch):
    root, a, _b = project
    old = _write_skeleton(os.path.join(root, "skeleton_corrections", "a_skeleton.png"))
    before = open(old, "rb").read()
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    notices = []
    iface.notice.connect(lambda msg, kind: notices.append(kind))

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    assert iface.save_skeleton() is False
    monkeypatch.undo()
    assert open(old, "rb").read() == before
    assert not [f for f in os.listdir(os.path.dirname(old)) if f.endswith(".tmp")]
    assert notices == ["danger"]
    assert iface.has_unsaved_changes()
    assert iface.hint_panel.state() == "error"


def test_unwritable_corrections_folder_does_not_raise(app, project, monkeypatch):
    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)

    def deny(*_a, **_k):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", deny)
    assert iface.save_skeleton() is False


def test_ctrl_s_saves_from_the_canvas(app, project):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    root, a, _b = project
    iface = _editor(app)
    iface.load_image(a, str(root))
    _draw_line(iface)
    QTest.keyClick(iface.graphics_view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert os.path.isfile(os.path.join(root, "skeleton_corrections", "a_skeleton.png"))

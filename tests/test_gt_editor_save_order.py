"""Trace GT save: a failed save writes nothing the user was told was not saved (G4).

The first save in a folder also creates ``gt/classes.json``. The tracing used to be written
first: when the registry then failed (locked, permission denied, disk full), Save reported
"Could not save GT" and the editor stayed dirty, yet ``gt/<stem>.png`` had already been
replaced, so a later Discard left the "unsaved" strokes in the file. The registry is
written first now; a failure there leaves the tracing file untouched.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import app.gui.gt_tracing_interface as gti  # noqa: E402
from app.gt import Tracing, default_registry, gt_path, read_tracing, write_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def editor(app):
    made = []

    def _make(image_path):
        iface = GtTracingInterface()
        made.append(iface)
        iface.load_image(image_path)
        return iface

    yield _make
    delete_widgets(made)


def _registry_write_fails(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("classes.json is locked")

    monkeypatch.setattr(gti, "save_registry", _boom)


def test_a_failed_registry_write_leaves_the_tracing_file_alone(editor, tmp_path, monkeypatch):
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    ids = np.zeros((H, W), np.uint8)
    ids[5:10, 5:10] = 2
    write_tracing(gt_path(path), Tracing(ids, frozenset({"nodule"})), default_registry())
    with open(gt_path(path), "rb") as fh:
        before = fh.read()  # a tracing saved by another copy: no classes.json yet

    notices = []
    iface = editor(path)
    iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    iface.begin_stroke(QPoint(40, 30))
    iface.end_stroke()
    _registry_write_fails(monkeypatch)

    assert iface.save_mask() is False
    assert any(kind == "danger" and "locked" in msg for msg, kind in notices)
    assert iface.has_unsaved_changes()
    with open(gt_path(path), "rb") as fh:
        assert fh.read() == before  # the "not saved" strokes are not in the file


def test_a_first_save_without_a_gt_file_writes_nothing_when_the_registry_fails(
    editor, tmp_path, monkeypatch
):
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    iface = editor(path)
    iface.begin_stroke(QPoint(40, 30))
    iface.end_stroke()
    _registry_write_fails(monkeypatch)
    assert iface.save_mask() is False
    assert not os.path.exists(gt_path(path))

    monkeypatch.undo()  # the registry can be written again: the next save writes both
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    assert iface.save_mask() is True
    assert read_tracing(gt_path(path)).ids[30, 40] == 1
    assert os.path.exists(os.path.join(str(tmp_path), "gt", "classes.json"))

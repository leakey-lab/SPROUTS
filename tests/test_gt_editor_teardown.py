"""A deleted Trace GT editor (and its dialogs) leaves no live Python wrapper behind.

A dead wrapper keeps the wrappers of its C++-made children (scroll bars, viewports) in sip's
address map. sip then handed one of them out for a new object at the same address, and runs
of the GT test files ended in an access violation about every other time (a QBoxLayout read
as a QWidget by keep_canvas_focus in a new ClassPanel). The wrappers stayed because the
suite's teardown delivered only DeferredDelete events, while PyQt frees the signal proxies of
deleted senders through queued calls, and a lambda that captures the editor
(``lambda: self.save_mask()``) kept it alive. Once the deletions and the queued calls are
delivered and the garbage is collected, nothing may keep them (a module-level registry, a
cache or a timer without a parent would).
"""

from __future__ import annotations

import gc
import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gt import default_registry  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.class_editor_dialog import ClassEditorDialog  # noqa: E402
from app.gui.export_dataset_dialog import ExportDatasetDialog  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


def _flush() -> None:
    """Delete what deleteLater() queued, then deliver the queued calls that free the signal
    proxies of deleted widgets, and collect: three rounds, as one round can queue more."""
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.sendPostedEvents()
    gc.collect()


def _image(tmp_path) -> str:
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    return path


@pytest.mark.parametrize("used", [False, True], ids=["new", "used"])
def test_a_deleted_editor_leaves_no_wrapper(app, tmp_path, used):
    _flush()
    iface = GtTracingInterface()
    if used:
        iface.show()
        iface.load_image(_image(tmp_path))
        iface.begin_stroke(QPoint(20, 20))
        iface.end_stroke()
        assert iface.save_mask()
        iface.undo()
    ref = weakref.ref(iface)
    iface.close()
    iface.deleteLater()
    del iface

    _flush()

    assert ref() is None


def test_the_class_editor_goes_with_its_editor(app, tmp_path, monkeypatch):
    _flush()
    iface = GtTracingInterface()
    iface.load_image(_image(tmp_path))
    opened = []

    def _cancel(dialog):
        opened.append(weakref.ref(dialog))
        return 0

    monkeypatch.setattr(ClassEditorDialog, "exec", _cancel)
    iface.edit_classes()
    ref = weakref.ref(iface)
    iface.close()
    iface.deleteLater()
    del iface

    _flush()

    assert len(opened) == 1 and opened[0]() is None
    assert ref() is None


def test_a_closed_export_dialog_leaves_no_wrapper(app, tmp_path):
    _flush()
    dialog = ExportDatasetDialog(str(tmp_path), {"plot_001": _image(tmp_path)}, default_registry())
    ref = weakref.ref(dialog)
    dialog.reject()
    dialog.deleteLater()
    del dialog

    _flush()

    assert ref() is None

"""A deleted Correct page (and its dialogs) leaves no live Python wrapper behind.

Ported from the Trace GT editor's teardown guards (WG-T11), whose class panel now lives on
the Correct page. A dead wrapper keeps the wrappers of its C++-made children (scroll bars,
viewports) in sip's address map. sip then handed one of them out for a new object at the
same address, and runs of the GT test files ended in an access violation about every other
time (a QBoxLayout read as a QWidget by keep_canvas_focus in a new ClassPanel). The wrappers
stayed because the suite's teardown delivered only DeferredDelete events, while PyQt frees
the signal proxies of deleted senders through queued calls, and a lambda that captures the
editor kept it alive. Once the deletions and the queued calls are delivered and the garbage
is collected, nothing may keep them (a module-level registry, a cache or a timer without a
parent would).
"""

from __future__ import annotations

import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gt import default_registry  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.class_editor_dialog import ClassEditorDialog  # noqa: E402
from app.gui.correct.editor import CorrectEditor  # noqa: E402
from app.gui.correct.layer import CLASSES, MASK  # noqa: E402
from app.gui.export_dataset_dialog import ExportDatasetDialog  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import drag  # noqa: E402

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


def _image(tmp_path) -> str:
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    return path


def _delete(editor) -> weakref.ref:
    """Close and delete ``editor`` as the suite's teardown does; its weak reference."""
    ref = weakref.ref(editor)
    editor.close()
    editor.deleteLater()
    return ref


@pytest.mark.parametrize("layer", [None, MASK, CLASSES], ids=["new", "mask", "class_labels"])
def test_a_deleted_editor_leaves_no_wrapper(app, tmp_path, layer):
    flush_deleted()
    editor = CorrectEditor()
    if layer is not None:  # used: an image, a stroke on the layer, a save, an undo
        editor.show()
        editor.load_image(_image(tmp_path), images_base_folder=str(tmp_path))
        editor.set_active_layer(layer)
        drag(editor, (20, 20), (40, 20))
        assert editor.save()
        editor.undo()
    ref = _delete(editor)
    del editor

    flush_deleted()

    assert ref() is None


def test_the_class_editor_goes_with_its_editor(app, tmp_path, monkeypatch):
    flush_deleted()
    editor = CorrectEditor()
    editor.load_image(_image(tmp_path), images_base_folder=str(tmp_path))
    editor.set_active_layer(CLASSES)
    opened = []

    def _cancel(dialog):
        opened.append(weakref.ref(dialog))
        return 0

    monkeypatch.setattr(ClassEditorDialog, "exec", _cancel)
    editor.edit_classes()
    ref = _delete(editor)
    del editor

    flush_deleted()

    assert len(opened) == 1 and opened[0]() is None
    assert ref() is None


def test_a_closed_export_dialog_leaves_no_wrapper(app, tmp_path):
    flush_deleted()
    dialog = ExportDatasetDialog(str(tmp_path), {"plot_001": _image(tmp_path)}, default_registry())
    ref = weakref.ref(dialog)
    dialog.reject()
    dialog.deleteLater()
    del dialog

    flush_deleted()

    assert ref() is None

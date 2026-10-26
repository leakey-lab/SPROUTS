"""The class-label goldens of the Trace GT editor, replayed through ClassesLayer (WG-T4) and
through the Correct page (WG-T5).

``tests/data/gt_goldens.json`` holds what ``GtTracingInterface`` did for the scripted sessions
of ``tests/gt_goldens_support.GT_OPS`` (WG-T0): the class-id plane, the ``reviewed`` ticks
and the unsaved flag after every op, and the saved ``gt/<stem>.png``. ``LayerDriver`` runs the
very same scripts through the new ``ClassesLayer`` with a recording ``FakeHost`` and must
reproduce every case. ``PageDriver`` runs them through the page as the user does: the class
panel's requests, the dock's size, the canvas input (the ``ToolRouter``), Clear, Undo, Redo
and Save of the page. The saved file's raw bytes hash is informational: a Pillow / zlib
upgrade may change the bytes with identical pixels, so it does not gate.

``empty_save`` is the one case the page does not replay: Save writes only the unsaved
layers (O2), so an untouched image writes no labels file on the page
(``tests/test_correct_classes_editor.py::test_an_untouched_image_writes_no_class_labels``).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.gui import canvas_gl  # noqa: E402
from app.gui.correct.classes_layer import ClassesLayer  # noqa: E402
from app.gui.correct.layer import CLASSES  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402
from tests.gt_goldens_support import GT_CASES, load_goldens, make_gt_project, replay  # noqa: E402
from tests.test_correct_editor import drag, editor_factory  # noqa: E402

INFORMATIONAL = {"file_sha256_informational"}  # encoder bytes may differ; pixels may not
PAGE_CASES = tuple(case for case in GT_CASES if case != "empty_save")  # O2: see the docstring
NO_MODIFIER = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


class LayerDriver:
    """``ClassesLayer`` through its public API (the driver contract of ``replay``)."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self.host = FakeHost()
        self.layer = ClassesLayer(self.host, scene)

    def open(self, image_path: str) -> None:
        with Image.open(image_path) as photo:  # the page passes the photo's size
            size = photo.size
        self.layer.open(image_path, size, os.path.dirname(image_path))

    def set_class(self, key: str) -> None:
        self.layer.set_active_class(key)

    def set_size(self, size: int) -> None:
        self.layer.set_size(size)

    def set_tool(self, name: str) -> None:
        self.layer.set_tool(name)

    def stroke(self, points) -> None:
        self.layer.press(QPoint(*points[0]), NO_MODIFIER)
        for point in points[1:]:
            self.layer.move(QPoint(*point), True)
        self.layer.release(QPoint(*points[-1]))

    def click(self, point) -> None:
        self.layer.press(QPoint(*point), NO_MODIFIER)
        self.layer.release(QPoint(*point))

    def clear(self) -> None:
        self.layer.clear()

    def undo(self) -> None:
        self.layer.undo()

    def redo(self) -> None:
        self.layer.redo()

    def tick(self, key: str, checked: bool) -> None:
        self.layer.set_ticked(key, checked)

    def save(self) -> bool:
        return self.layer.save().ok

    def ids(self) -> np.ndarray:
        return self.layer.document.history.plane

    def reviewed(self) -> set[str]:
        """What Save writes: the hand ticks and the classes with pixels."""
        return set(self.layer.document.reviewed())

    def dirty(self) -> bool:
        return self.layer.is_dirty()

    def warnings(self) -> int:
        return len(self.layer.document.warnings)


def replay_layer(case: str, root: str, scene: QGraphicsScene) -> dict:
    """The case record of ``ClassesLayer`` on a fresh project under ``root``."""
    image_path, _folder = make_gt_project(root, case)
    return replay(LayerDriver(scene), image_path, case)


def contract(record: dict) -> dict:
    """``record`` without the informational entries."""
    out = dict(record)
    out["file"] = {k: v for k, v in record["file"].items() if k not in INFORMATIONAL}
    return out


@pytest.mark.parametrize("case", GT_CASES)
def test_the_classes_layer_reproduces_the_gt_goldens(scene, tmp_path, case):
    expected = load_goldens()["cases"][case]

    recorded = replay_layer(case, str(tmp_path), scene)

    assert contract(recorded) == contract(expected)


class PageDriver:
    """The Correct page with the class labels edited, driven as the user drives it."""

    def __init__(self, editor) -> None:
        self.editor = editor
        self.layer = editor.layers[CLASSES]

    def open(self, image_path: str) -> None:
        self.editor.load_image(image_path, os.path.dirname(image_path))
        self.editor.set_active_layer(CLASSES)

    def set_class(self, key: str) -> None:
        self.editor.class_panel.active_changed.emit(key)  # a click on the class's row

    def set_size(self, size: int) -> None:
        self.editor.controls.size_changed.emit(size)  # the dock's size slider

    def set_tool(self, name: str) -> None:
        self.editor.request_tool(CLASSES, name)

    def stroke(self, points) -> None:
        drag(self.editor, *points)

    def click(self, point) -> None:
        drag(self.editor, point)

    def clear(self) -> None:
        self.editor.clear_active_layer()

    def undo(self) -> None:
        self.editor.undo()

    def redo(self) -> None:
        self.editor.redo()

    def tick(self, key: str, checked: bool) -> None:
        self.editor.class_panel.reviewed_changed.emit(key, checked)  # its "checked" box

    def save(self) -> bool:
        return self.editor.save()

    def ids(self) -> np.ndarray:
        return self.layer.document.history.plane

    def reviewed(self) -> set[str]:
        return set(self.layer.document.reviewed())

    def dirty(self) -> bool:
        return self.layer.is_dirty()

    def warnings(self) -> int:
        return len(self.layer.document.warnings)


@pytest.fixture
def make_editor(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    yield from editor_factory()


@pytest.mark.parametrize("case", PAGE_CASES)
def test_the_page_reproduces_the_gt_goldens(make_editor, tmp_path, case):
    expected = load_goldens()["cases"][case]
    image_path, _folder = make_gt_project(str(tmp_path), case)

    recorded = replay(PageDriver(make_editor(show=False)), image_path, case)

    assert contract(recorded) == contract(expected)

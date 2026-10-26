"""The class-label goldens of the Trace GT editor, replayed through ClassesLayer (WG-T4).

``tests/data/gt_goldens.json`` holds what ``GtTracingInterface`` did for the scripted sessions
of ``tests/gt_goldens_support.GT_OPS`` (WG-T0): the class-id plane, the ``reviewed`` ticks
and the unsaved flag after every op, and the saved ``gt/<stem>.png``. ``LayerDriver`` runs the
very same scripts through the new ``ClassesLayer`` with a recording ``FakeHost`` and must
reproduce every case. The saved file's raw bytes hash is informational: a Pillow / zlib
upgrade may change the bytes with identical pixels, so it does not gate.
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

from app.gui.correct.classes_layer import ClassesLayer  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402
from tests.gt_goldens_support import GT_CASES, load_goldens, make_gt_project, replay  # noqa: E402

INFORMATIONAL = {"file_sha256_informational"}  # encoder bytes may differ; pixels may not
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

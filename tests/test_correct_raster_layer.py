"""RasterLayer: the generic raster edit layer, lifted out of MaskLayer (WG-T4).

``RasterLayer`` holds what every raster layer of the Correct page shares: the canvas and its
render item, the Brush / Eraser / Fill tools and their input, the gestures, the history
replay, the brush ring and the view state. ``MaskLayer`` and ``ClassesLayer`` are raster
layers that bring their own document and identity. ``replace_plane`` puts a whole plane in
as one undoable action ("Load prediction" uses it).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.gui.correct.classes_layer import ClassesLayer  # noqa: E402
from app.gui.correct.layer import (  # noqa: E402
    DiskGuardedLayer,
    ReadAheadLayer,
    missing_members,
)
from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from app.gui.correct.raster_layer import RasterLayer  # noqa: E402
from app.gui.paint_canvas import pixmap_to_argb_array  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
W, H = 640, 480
NO_MODS = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


@pytest.fixture
def project(tmp_path):
    """Photo ``a.png`` (never decoded by a layer) and its prediction ``mask/a.png``."""
    folder = str(tmp_path / "project")
    os.makedirs(os.path.join(folder, "mask"))
    a = os.path.join(folder, "a.png")
    cv2.imwrite(a, np.full((H, W, 3), 90, np.uint8))
    prediction = np.zeros((H, W), np.uint8)
    prediction[30:70, 40:110] = 255
    cv2.imwrite(os.path.join(folder, "mask", "a.png"), prediction)
    return SimpleNamespace(folder=folder, a=a)


def block() -> np.ndarray:
    """A plane that differs from the prediction."""
    plane = np.zeros((H, W), np.uint8)
    plane[200:260, 300:420] = 1
    return plane


def canvas_alpha(layer) -> np.ndarray:
    return (pixmap_to_argb_array(layer.canvas.image)[:, :, 3] > 0).astype(np.uint8)


# ----------------------------------------------------------------------------- the classes
def test_mask_layer_is_a_raster_layer_and_misses_no_member(scene):
    layer = MaskLayer(FakeHost(), scene)

    assert isinstance(layer, RasterLayer)
    assert missing_members(layer) == []
    assert missing_members(layer, DiskGuardedLayer) == []
    assert missing_members(layer, ReadAheadLayer) == []
    lines = (ROOT / "app" / "gui" / "correct" / "mask_layer.py").read_text(encoding="utf-8")
    assert len(lines.splitlines()) <= 240


def test_the_classes_layer_is_a_raster_layer_at_its_own_z(scene):
    mask, classes = MaskLayer(FakeHost(), scene), ClassesLayer(FakeHost(), scene)

    assert isinstance(classes, RasterLayer)
    assert (mask.item.zValue(), classes.item.zValue()) == (1.0, 1.2)
    assert mask.tools == classes.tools  # the same Brush, Eraser and Fill
    assert mask.SIZE_SPEC == classes.SIZE_SPEC


# --------------------------------------------------------------------------- replace_plane
def test_replace_plane_is_one_undo_entry(scene, project):
    host = FakeHost()
    layer = MaskLayer(host, scene)
    layer.open(project.a, (W, H), project.folder)
    host.calls.clear()

    assert layer.replace_plane(block()) is True

    assert layer.can_undo() and (layer.document.history.plane == block()).all()
    assert len(layer.document.history.undo_stack) == 1
    assert host.calls.count(("layer_edited", "mask")) == 1
    assert np.array_equal(canvas_alpha(layer), block())  # the canvas shows the new plane
    assert layer.is_dirty()
    assert layer.undo() and not layer.is_dirty()
    assert not layer.can_undo() and np.array_equal(canvas_alpha(layer), layer.document.prediction())


def test_replace_plane_changes_nothing_when_closed_or_already_that_plane(scene, project):
    host = FakeHost()
    layer = MaskLayer(host, scene)
    assert layer.replace_plane(block()) is False  # nothing open

    layer.open(project.a, (W, H), project.folder)
    host.calls.clear()
    same = layer.document.history.plane.copy()

    assert layer.replace_plane(same) is False
    assert not layer.can_undo() and host.calls == []


def test_replace_plane_refuses_a_plane_of_another_shape(scene, project):
    host = FakeHost()
    layer = MaskLayer(host, scene)
    layer.open(project.a, (W, H), project.folder)
    host.calls.clear()
    before = layer.document.history.plane.copy()

    with pytest.raises(ValueError, match="shape"):
        layer.replace_plane(np.ones((H // 2, W // 2), np.uint8))

    assert np.array_equal(layer.document.history.plane, before) and not layer.can_undo()
    assert host.calls == []


def test_replace_plane_commits_a_stroke_in_progress_first(scene, project):
    host = FakeHost()
    layer = MaskLayer(host, scene)
    layer.open(project.a, (W, H), project.folder)
    host.calls.clear()
    layer.press(QPoint(10, 400), NO_MODS)
    layer.move(QPoint(60, 410), True)

    assert layer.replace_plane(block()) is True

    assert layer.gesture_active() is False
    assert host.calls.count(("layer_edited", "mask")) == 2  # the stroke, then the plane
    assert layer.undo() and layer.document.history.plane[400:411, 10:61].any()  # the stroke

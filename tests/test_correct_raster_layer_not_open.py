"""A raster layer's save with nothing open says so (``RasterLayer.NOT_OPEN``) and changes
nothing: no document save, no UI refresh, no class-panel call (the texts of the mask and
class-label layers before they shared ``RasterLayer.save``)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from PyQt6.QtWidgets import QGraphicsScene

from app.gui.correct.classes_layer import ClassesLayer
from app.gui.correct.layer import CLASSES, MASK, SaveOutcome
from app.gui.correct.mask_layer import MaskLayer
from tests.correct_fakes import FakeHost

W, H = 64, 48
CLOSED = {
    MaskLayer: SaveOutcome(MASK, ok=False, message="No mask is open", kind="danger"),
    ClassesLayer: SaveOutcome(CLASSES, ok=False, message="No class labels are open", kind="danger"),
}


pytestmark = pytest.mark.usefixtures("no_prompts")


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


def _build(cls, scene):
    """``(layer, host, class-panel calls)``: a layer that never opened anything."""
    host, panel = FakeHost(), []
    layer = cls(host, scene)
    if cls is ClassesLayer:
        layer.on_classes_changed = lambda: panel.append("changed")
    return layer, host, panel


def _refuse_document_save(layer, monkeypatch):
    monkeypatch.setattr(layer.document, "save", lambda: pytest.fail("the document saved"))


@pytest.mark.parametrize("cls", [MaskLayer, ClassesLayer], ids=["mask", "classes"])
def test_a_save_before_any_open_says_nothing_is_open(cls, scene, monkeypatch):
    layer, host, panel = _build(cls, scene)
    _refuse_document_save(layer, monkeypatch)

    assert layer.save() == CLOSED[cls]

    assert host.calls == [] and panel == []


@pytest.mark.parametrize("cls", [MaskLayer, ClassesLayer], ids=["mask", "classes"])
def test_a_save_after_a_close_says_nothing_is_open(cls, scene, tmp_path, monkeypatch):
    image = str(tmp_path / "a.png")
    cv2.imwrite(image, np.full((H, W, 3), 90, np.uint8))
    layer, host, panel = _build(cls, scene)
    layer.open(image, (W, H), str(tmp_path))
    layer.close()
    host.calls.clear()
    panel.clear()
    _refuse_document_save(layer, monkeypatch)

    assert layer.save() == CLOSED[cls]

    assert host.calls == [] and panel == []

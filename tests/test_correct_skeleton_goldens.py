"""Skeleton pixel goldens replayed through SkeletonLayer (plan P5b Step 3, C18).

``ops.skeleton`` and ``ops.skeleton_clear`` of ``tests/data/correct_goldens.json`` run on
both committed synthetic projects through the layer API, with the C18 op semantics. The
start state, the raster after every op and the saved correction must equal the recorded
goldens. They were captured from the pre-merge skeleton editor and re-recorded once for
Q1 (bilinear open, corrections saved at image resolution; CHANGELOG).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.gui.correct.skeleton_layer import SkeletonLayer  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402
from tests.correct_goldens_support import (  # noqa: E402
    SYNTH_CASES,
    SYNTH_STEM,
    describe_skeleton_file,
    load_goldens,
    make_synth_project,
    sha,
)

GOLDENS = load_goldens()
NO_MOD = Qt.KeyboardModifier.NoModifier
KEYS = {"Delete": Qt.Key.Key_Delete}
# What a saved correction must match (its bytes and the length are derived, informational).
SAVED_KEYS = ("exists", "shape", "dtype", "colour_type", "pixels_sha256", "on_count", "values")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_message_boxes(monkeypatch):
    def refuse(box):
        raise AssertionError(f"unexpected message box: {box.text()}")

    monkeypatch.setattr(QMessageBox, "exec", refuse)


def _drag(layer, points):
    """Press at the first point, a dragging move per further point, release at the last."""
    first, *rest = [QPoint(*p) for p in points]
    layer.press(first, NO_MOD)
    for point in rest:
        layer.move(point, True)
    layer.release(rest[-1] if rest else first)


def apply(layer: SkeletonLayer, op: list):
    """One golden op through the layer (C18); returns the save's result for ``save``."""
    kind = op[0]
    if kind == "tool":
        layer.set_tool(op[1])
    elif kind == "smooth":
        layer.set_smooth(bool(op[1]))
    elif kind == "eraser_size":
        layer.set_size(op[1])
    elif kind == "click":
        _drag(layer, [op[1]])
    elif kind == "double_click":
        layer.double_click(QPoint(*op[1]))
    elif kind == "drag":
        _drag(layer, op[1])
    elif kind == "connect_endpoints":
        ends = sorted(layer.document.model.endpoints())
        if len(ends) > max(op[1], op[2]):
            _drag(layer, [ends[op[1]], ends[op[2]]])
    elif kind == "finish":
        layer.finish_polyline()
    elif kind == "key":
        layer.key(QKeyEvent(QEvent.Type.KeyPress, KEYS[op[1]], NO_MOD))
    elif kind == "clear":
        layer.clear()
    elif kind == "undo":
        if not layer.undo_in_gesture():
            layer.undo()
    elif kind == "redo":
        layer.redo()
    elif kind == "save":
        return layer.save().ok
    else:
        raise ValueError(f"unknown golden op {op!r}")
    return None


@pytest.mark.parametrize("script", ["skeleton", "skeleton_clear"])
@pytest.mark.parametrize("case", sorted(SYNTH_CASES))
def test_skeleton_ops_through_the_layer_match_the_goldens(app, tmp_path, case, script):
    want = GOLDENS["cases"][case][script]
    ops = GOLDENS["ops"][script]
    folder, image_path = make_synth_project(str(tmp_path), case)
    height, width = imread_unicode(image_path).shape[:2]
    scene = QGraphicsScene()
    layer = SkeletonLayer(FakeHost(), scene)

    assert layer.open(image_path, (width, height), folder) == []
    doc = layer.document
    start = {
        "skeleton_source": doc.source,
        "raster_sha256": sha(doc.model.mask),
        "raster_shape": list(doc.model.mask.shape),
        "save_size": list(doc.save_size) if doc.save_size else None,
    }
    assert start == want["start"]

    saved = None
    for index, (op, expected) in enumerate(zip(ops, want["per_op_raster_sha256"], strict=True)):
        result = apply(layer, op)
        if op[0] == "save":
            saved = result
        assert sha(doc.model.mask) == expected, f"op {index} {op} diverges from the golden"

    assert saved == want["save_returned"]
    path = ProjectLayout(folder).gt_skeleton_path(SYNTH_STEM)
    got = describe_skeleton_file(path)
    assert {key: got.get(key) for key in SAVED_KEYS} == {
        key: want["saved"].get(key) for key in SAVED_KEYS
    }
    layer.cancel_scheduled_render()

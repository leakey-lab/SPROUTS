"""The mask pixel goldens of the pre-merge editor, replayed through MaskLayer (C13, C18).

``tests/data/correct_goldens.json`` holds what today's ``MaskTracingInterface`` did for two
fully synthetic projects: the plane after every op of the ``mask`` and ``mask_clear``
scripts and the saved file (pixel sha256, shape, PNG colour type 6, the "on" pixels and
the area Measure would write). The same scripts, driven through the layer's
``EditLayer`` input with the C18 op semantics, must leave the same planes and write the
same files. The replay is repeated with every stroke ended early by Esc or a forced
finish before its release (the mid-gesture rule: a forced finish commits exactly what
the release would), and with hover moves, reference / active switches and no-op
finishes between the ops, none of which may change a pixel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402
from tests.correct_goldens_support import (  # noqa: E402
    SYNTH_CASES,
    SYNTH_STEM,
    describe_mask_file,
    load_goldens,
    make_synth_project,
    sha,
)

GOLDENS = load_goldens()
PHOTO_SIZE = (640, 480)  # (w, h) of the synthetic photo: the layer never decodes it
NO_MODS = Qt.KeyboardModifier.NoModifier
INFORMATIONAL = {"file_sha256_informational"}  # encoder bytes may differ; pixels may not


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


# --------------------------------------------------------------------------- the driver
def _end_stroke(layer: MaskLayer, last: QPoint, ending: str) -> None:
    """End a stroke: its release, or Esc / a forced finish first (the release then does
    nothing, the rest of the drag being ignored)."""
    if ending == "esc":
        layer.cancel_transient()
    elif ending == "finish":
        layer.finish_gesture()
    layer.release(last)


def apply(layer: MaskLayer, op: list, ending: str = "release"):
    """One op of a golden script (C18 semantics); the save's outcome for ``["save"]``."""
    kind = op[0]
    if kind in ("brush", "eraser"):
        layer.set_tool(kind)
        layer.set_size(op[1])
    elif kind == "fill":
        layer.set_tool("fill")
    elif kind == "stroke":
        points = [QPoint(*p) for p in op[1]]
        layer.press(points[0], NO_MODS)
        for p in points[1:]:
            layer.move(p, True)
        _end_stroke(layer, points[-1], ending)
    elif kind == "click":
        point = QPoint(*op[1])
        layer.press(point, NO_MODS)
        _end_stroke(layer, point, ending)
    elif kind == "clear":
        layer.clear()
    elif kind == "undo":
        if not layer.undo_in_gesture():
            layer.undo()
    elif kind == "redo":
        layer.redo()
    elif kind == "save":
        return layer.save()
    else:
        raise ValueError(f"not a mask op: {op!r}")
    return None


def _between_ops(layer: MaskLayer, index: int) -> None:
    """View-state noise between two ops; none of it may change a pixel."""
    layer.hover(QPoint((37 * index) % 640, (53 * index) % 480))
    layer.move(QPoint((41 * index) % 640, (29 * index) % 480), False)
    layer.set_active(False)
    layer.hover(QPoint(5, 5))
    layer.set_active(True)
    layer.finish_gesture()
    layer.cancel_transient()
    layer.hover(None)


def replay(tmp_path, scene, case: str, script: str, *, ending="release", noise=False):
    """Replay ``script`` on a fresh copy of ``case``: per-op plane shas, save outcome, file."""
    folder, image = make_synth_project(str(tmp_path), case)
    layer = MaskLayer(FakeHost(), scene)
    assert layer.open(image, PHOTO_SIZE, folder) == []
    per_op, outcome = [], None
    for index, op in enumerate(GOLDENS["ops"][script]):
        if noise:
            _between_ops(layer, index)
        result = apply(layer, op, ending)
        if op[0] == "save":
            outcome = result
        per_op.append(sha(layer.document.history.plane))
    saved = describe_mask_file(os.path.join(folder, "mask", f"{SYNTH_STEM}.png"))
    return per_op, outcome, saved


def assert_matches(golden: dict, script: str, per_op, outcome, saved) -> None:
    ops = GOLDENS["ops"][script]
    expected = golden["per_op_plane_sha256"]
    assert len(per_op) == len(expected) == len(ops)
    pairs = enumerate(zip(per_op, expected, strict=True))
    diverged = next((i for i, (a, b) in pairs if a != b), None)
    assert diverged is None, f"the plane first differs after op {diverged}: {ops[diverged]}"
    assert outcome is not None and outcome.ok is golden["save_returned"]
    contract = {k: v for k, v in golden["saved"].items() if k not in INFORMATIONAL}
    assert {k: saved.get(k) for k in contract} == contract


# --------------------------------------------------------------------------- the goldens
@pytest.mark.parametrize("case", sorted(SYNTH_CASES))
def test_the_mask_script_matches_its_golden_op_by_op_and_on_disk(qapp, scene, tmp_path, case):
    per_op, outcome, saved = replay(tmp_path, scene, case, "mask")

    assert_matches(GOLDENS["cases"][case]["mask"], "mask", per_op, outcome, saved)
    assert saved["colour_type"] == 6  # RGBA, white on transparent
    assert outcome.path is not None and outcome.cleared is False


@pytest.mark.parametrize("case", sorted(SYNTH_CASES))
def test_clear_then_save_matches_its_golden(qapp, scene, tmp_path, case):
    per_op, outcome, saved = replay(tmp_path, scene, case, "mask_clear")

    assert_matches(GOLDENS["cases"][case]["mask_clear"], "mask_clear", per_op, outcome, saved)
    had_a_file = SYNTH_CASES[case][0]  # the model-mask case starts with a saved mask
    assert (outcome.path, outcome.cleared) == (None, had_a_file)


@pytest.mark.parametrize("ending", ["esc", "finish"])
@pytest.mark.parametrize("case", sorted(SYNTH_CASES))
def test_a_forced_finish_before_each_release_leaves_the_goldens_intact(
    qapp, scene, tmp_path, case, ending
):
    per_op, outcome, saved = replay(tmp_path, scene, case, "mask", ending=ending)

    assert_matches(GOLDENS["cases"][case]["mask"], "mask", per_op, outcome, saved)


@pytest.mark.parametrize("case", sorted(SYNTH_CASES))
def test_hover_reference_switches_and_idle_finishes_change_no_pixel(qapp, scene, tmp_path, case):
    per_op, outcome, saved = replay(tmp_path, scene, case, "mask", noise=True)

    assert_matches(GOLDENS["cases"][case]["mask"], "mask", per_op, outcome, saved)

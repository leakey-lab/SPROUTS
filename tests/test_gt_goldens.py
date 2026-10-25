"""Class-label goldens of today's Trace GT editor (plan WG-T0).

``tests/data/gt_goldens.json`` holds what ``GtTracingInterface`` did for the scripted sessions
of ``tests/gt_goldens_support.GT_OPS``: the class-id plane after every op, the class counts,
the ``reviewed`` ticks, and the saved ``gt/<stem>.png`` (pixels, palette, chunk). The merged
Correct editor (WG-T4 / WG-T5) must reproduce them through the same ``replay``.

``LegacyDriver`` is the one adapter to the editor; it dies with the old editor (WG-T11).
The saved file's raw bytes hash is informational: a Pillow / zlib upgrade may change the
bytes with identical pixels, so only the decoded pixels, ticks, palette and chunk gate.

Capture (once, before any GT code changes): ``$env:SPROUTS_WRITE_GT_GOLDENS = '1'`` and run
``test_capture_the_goldens``.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import PIL  # noqa: E402
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.gt_goldens_support import (  # noqa: E402
    GT_CASES,
    GT_OPS,
    load_goldens,
    make_gt_project,
    replay,
    write_goldens,
)
from tests.qt_teardown import delete_widgets  # noqa: E402

INFORMATIONAL = {"file_sha256_informational"}  # encoder bytes may differ; pixels may not
_MADE: list[GtTracingInterface] = []  # the editors a test made: deleted after it


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _delete_test_editors(app):
    yield
    delete_widgets(_MADE)


@pytest.fixture(autouse=True)
def no_modal_prompts(monkeypatch):
    """Fail loudly instead of blocking if any code path opens a modal box."""

    def _fail(self, *args, **kwargs):
        pytest.fail(f"unexpected modal dialog: {self.text()!r}")

    monkeypatch.setattr(QMessageBox, "exec", _fail)


# ============================================================================== the driver
class LegacyDriver:
    """``GtTracingInterface`` through its public API (the driver contract of ``replay``)."""

    def __init__(self) -> None:
        self.editor = GtTracingInterface()
        _MADE.append(self.editor)
        self.editor.resize(900, 700)

    def open(self, image_path: str) -> None:
        self.editor.load_image(image_path)

    def set_class(self, key: str) -> None:
        self.editor.set_active_class(key)

    def set_size(self, size: int) -> None:
        self.editor.size_slider.setValue(size)

    def set_tool(self, name: str) -> None:
        button = {
            "brush": self.editor.brush_button,
            "eraser": self.editor.eraser_button,
            "fill": self.editor.fill_button,
        }[name]
        button.setChecked(True)

    def stroke(self, points) -> None:
        self.editor.begin_stroke(QPoint(*points[0]))
        for point in points[1:]:
            self.editor.continue_stroke(QPoint(*point))
        self.editor.end_stroke()

    def click(self, point) -> None:
        self.editor.begin_stroke(QPoint(*point))
        self.editor.end_stroke()

    def clear(self) -> None:
        self.editor.clear_mask()

    def undo(self) -> None:
        self.editor.undo()

    def redo(self) -> None:
        self.editor.redo()

    def tick(self, key: str, checked: bool) -> None:
        self.editor._on_reviewed_changed(key, checked)

    def save(self) -> bool:
        return self.editor.save_mask()

    def ids(self) -> np.ndarray:
        return self.editor.edits.plane

    def reviewed(self) -> set[str]:
        """The checked boxes: ticked by hand, or the class has pixels (what Save writes)."""
        return set(self.editor._reviewed) | set(self.editor._present)

    def dirty(self) -> bool:
        return self.editor.has_unsaved_changes()

    def warnings(self) -> int:
        return len(self.editor._remap_warnings)


def replay_legacy(case: str, root: str) -> dict:
    """The case record of the Trace GT editor on a fresh project under ``root``."""
    image_path, _folder = make_gt_project(root, case)
    return replay(LegacyDriver(), image_path, case)


def contract(record: dict) -> dict:
    """``record`` without the informational entries."""
    out = dict(record)
    out["file"] = {k: v for k, v in record["file"].items() if k not in INFORMATIONAL}
    return out


# ============================================================================= the goldens
@pytest.mark.parametrize("case", GT_CASES)
def test_the_trace_gt_editor_reproduces_the_goldens(app, tmp_path, case):
    expected = load_goldens()["cases"][case]
    recorded = replay_legacy(case, str(tmp_path))
    assert contract(recorded) == contract(expected)


@pytest.mark.parametrize("case", GT_CASES)
def test_the_goldens_are_deterministic_across_two_runs(app, tmp_path, case):
    first = replay_legacy(case, str(tmp_path / "a"))
    second = replay_legacy(case, str(tmp_path / "b"))
    assert contract(first) == contract(second)


def test_the_golden_file_has_the_cases_and_pins_ids_ticks_and_the_file():
    data = load_goldens()
    assert set(data) == {"meta", "ops", "cases"} and set(data["cases"]) == set(GT_CASES)
    assert data["ops"] == json.loads(json.dumps(GT_OPS))
    assert data["meta"]["deterministic_across_two_runs"] is True
    case = data["cases"]["two_classes_crossing"]
    assert set(case) >= {
        "per_op_ids_sha256",
        "per_op_reviewed",
        "per_op_dirty",
        "ids_sha256",
        "counts",
        "reviewed",
        "dirty_before_save",
        "dirty_after_save",
        "save_returned",
        "warnings",
        "file",
    }
    assert len(case["per_op_ids_sha256"]) == len(GT_OPS["two_classes_crossing"])
    assert case["reviewed"] == ["nodule", "root", "root_tip"] and case["file"]["mode"] == "P"
    assert case["file"]["transparency"] == 0
    # root painted, nodule painted, root_tip ticked only
    assert case["counts"]["2"] > 0 and case["counts"]["1"] > case["counts"].get("3", 0) == 0
    empty = data["cases"]["empty_save"]
    assert empty["file"]["exists"] is True and empty["counts"] == {"0": 640 * 480}
    lossy = data["cases"]["lossy_remap_on_load"]
    assert lossy["warnings"] == 1 and lossy["dirty_before_save"] is True
    fill = data["cases"]["fill_active_class_only"]
    per_op = fill["per_op_ids_sha256"]
    ops = GT_OPS["fill_active_class_only"]
    misses = [i for i, op in enumerate(ops) if op[0] == "click" and per_op[i] == per_op[i - 1]]
    fills = [i for i, op in enumerate(ops) if op[0] == "click" and per_op[i] != per_op[i - 1]]
    assert len(misses) == 4 and len(fills) == 1  # only the fill inside the active class's outline
    assert fill["reviewed"] == ["nodule", "root"] and set(fill["counts"]) == {"0", "1", "2"}
    assert fill["counts"]["1"] > fill["counts"]["2"] > 0  # the root fill overwrote half the nodule
    for name, record in data["cases"].items():  # one entry per op; Save leaves nothing unsaved
        assert len(record["per_op_dirty"]) == len(record["per_op_reviewed"]) == len(GT_OPS[name])
        assert record["save_returned"] is True and record["dirty_after_save"] is False, name
    history = data["cases"]["dirty_and_history"]
    dirty = history["per_op_dirty"]
    saves = [i for i, op in enumerate(GT_OPS["dirty_and_history"]) if op[0] == "save"]
    assert [dirty[i] for i in saves] == [False, False] and history["dirty_before_save"] is True
    # tick (4) and its takeback (5); the locked box of a painted class (6) is no change
    assert (dirty[4], dirty[5], dirty[6]) == (True, False, False)
    # stroke (9), undo (10), redo (11), undo (12), undo past the save (13), redo (14), redo (15)
    assert dirty[9:16] == [True, False, True, False, True, False, True]
    # a saved tick unticked (18) and ticked again (19); Clear (20) and its Undo (21)
    assert dirty[18:22] == [True, False, True, False]
    assert history["per_op_reviewed"][6] == ["root"]  # the painted class stays checked
    ignore = data["cases"]["ignore_interplay"]
    assert set(ignore["counts"]) == {"0", "1", "255"} and ignore["reviewed"] == ["root"]
    assert ignore["file"]["classes"]["255"] == "ignore"


# =============================================================================== the capture
@pytest.mark.skipif(
    os.environ.get("SPROUTS_WRITE_GT_GOLDENS") != "1",
    reason="capture run: set SPROUTS_WRITE_GT_GOLDENS=1 to rewrite tests/data/gt_goldens.json",
)
def test_capture_the_goldens(app, tmp_path):
    cases = {case: replay_legacy(case, str(tmp_path / "first")) for case in GT_CASES}
    again = {case: replay_legacy(case, str(tmp_path / "second")) for case in GT_CASES}
    deterministic = all(contract(cases[c]) == contract(again[c]) for c in GT_CASES)
    assert deterministic
    write_goldens(
        {
            "meta": {
                "numpy": np.__version__,
                "opencv": cv2.__version__,
                "pillow": PIL.__version__,
                "pyqt": PYQT_VERSION_STR,
                "qt": QT_VERSION_STR,
                "purpose": (
                    "class-label goldens of the Trace GT editor before the Correct merge; "
                    "cases.* is the contract"
                ),
                "deterministic_across_two_runs": deterministic,
            },
            "ops": GT_OPS,
            "cases": cases,
        }
    )

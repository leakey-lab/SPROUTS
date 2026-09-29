"""Guards of the Correct workspace package (``app.gui.correct``).

- The layer contract (C2): ``missing_members`` finds what an object lacks of a
  protocol, so layer tests can assert ``missing_members(layer) == []``.
- Layering: the Qt-free modules load no PyQt6 when imported on their own.
- No god class: every ``app/gui/correct/*.py`` stays at or under 450 lines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gui.correct.layer import LayerHost, LayerTool, SizeSpec, missing_members

ROOT = Path(__file__).resolve().parents[1]
CORRECT = ROOT / "app" / "gui" / "correct"
MAX_LINES = 450

# Qt-free modules; the documents are skipped until their packages create them.
QT_FREE = [
    "app.editing.journal",
    "app.gui.correct.layer",
    "app.gui.correct.session",
    "app.gui.correct.mask_document",
    "app.gui.correct.skeleton_document",
]

_PROBE = (
    "import importlib, sys; importlib.import_module(sys.argv[1]); "
    "print(sorted(m for m in sys.modules if m == 'PyQt6' or m.startswith('PyQt6.')))"
)


@pytest.mark.parametrize("module", QT_FREE)
def test_qt_free_modules_load_no_pyqt(module):
    path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if not path.exists():
        pytest.skip(f"{path.name} is not written yet")

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", f"{module} loads {result.stdout.strip()}"


@pytest.mark.parametrize("path", sorted(CORRECT.glob("*.py")), ids=lambda path: path.name)
def test_no_correct_module_grows_past_the_line_cap(path):
    lines = len(path.read_text(encoding="utf-8").splitlines())

    assert lines <= MAX_LINES, f"{path.name} has {lines} lines: split it (the cap is {MAX_LINES})"


# C2's EditLayer, in declaration order: five attributes, then the methods.
_EDIT_LAYER_MEMBERS = [
    "layer_id",
    "noun",
    "color",
    "tools",
    "current_tool",
    "open",
    "close",
    "is_open",
    "file_stamps",
    "is_dirty",
    "save",
    "revert",
    "status",
    "clear",
    "can_undo",
    "can_redo",
    "undo",
    "redo",
    "drop_redo",
    "undo_in_gesture",
    "gesture_active",
    "finish_gesture",
    "cancel_transient",
    "set_tool",
    "tool_hint",
    "size_spec",
    "size",
    "set_size",
    "step_size",
    "press",
    "move",
    "release",
    "double_click",
    "key",
    "hover",
    "set_active",
    "is_active",
    "set_visible",
    "is_visible",
    "opacity",
    "set_opacity",
]


def _noop(*_args, **_kwargs) -> None:
    return None


def _complete_layer() -> SimpleNamespace:
    return SimpleNamespace(**{name: _noop for name in _EDIT_LAYER_MEMBERS})


def test_an_empty_object_misses_every_edit_layer_member_in_declaration_order():
    assert missing_members(object()) == _EDIT_LAYER_MEMBERS


def test_a_complete_layer_misses_nothing():
    assert missing_members(_complete_layer()) == []


def test_only_the_absent_members_are_reported():
    layer = _complete_layer()
    del layer.press
    del layer.layer_id
    layer.tools = ()  # a declared attribute may hold any value

    assert missing_members(layer) == ["layer_id", "press"]


def test_a_declared_method_that_is_not_callable_counts_as_missing():
    layer = _complete_layer()
    layer.undo = True

    assert missing_members(layer) == ["undo"]


def test_other_protocols_are_checked_by_their_own_declarations():
    host = SimpleNamespace(notify=_noop, layer_edited=_noop, layer_changed=_noop)
    assert missing_members(host, LayerHost) == ["is_panning"]

    assert missing_members(object(), LayerTool) == [
        "tool_id",
        "press",
        "move",
        "release",
        "double_click",
        "key",
        "deactivate",
        "hint",
    ]


def test_the_test_fakes_implement_the_protocols():
    from tests.correct_fakes import FakeHost, FakeLayer

    assert missing_members(FakeLayer("mask")) == []
    assert missing_members(FakeLayer("skeleton")) == []
    assert missing_members(FakeHost(), LayerHost) == []


def test_equal_size_specs_are_one_dict_key():
    # The Correct dock keeps one size slider per distinct SizeSpec (C20).
    brush = SizeSpec("brush", "Brush size", 1, 100)
    eraser = SizeSpec("brush", "Brush size", 1, 100)
    skeleton_eraser = SizeSpec("erase", "Eraser size", 1, 80)

    sliders = {brush: "slider A", eraser: "slider A", skeleton_eraser: "slider B"}

    assert len(sliders) == 2

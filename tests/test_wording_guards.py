"""Wording guards: one `_fake` constant, the Measure button names, the Generate sentence."""

from __future__ import annotations

import ast
import pathlib

from app import project_layout
from app.gui import shell_chrome
from app.inference import skeleton_inference
from tests.test_integration_main_window_wiring import (  # noqa: F401
    app,
    make_window,
    mw,
    no_delayed_temp_sweep,
    settings,
)

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _string_constants(path: pathlib.Path):
    """Every str constant of ``path`` (f-string parts included) that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_no_fake_png_literal_outside_project_layout():
    layout_file = APP / "project_layout.py"
    found = [
        f"{path.relative_to(APP)}:{line}"
        for path in APP.rglob("*.py")
        if path != layout_file
        for line, value in _string_constants(path)
        if "_fake.png" in value
    ]
    assert found == []


def test_skeleton_inference_uses_the_layout_suffix():
    assert skeleton_inference.SKELETON_SUFFIX is project_layout.SKELETON_SUFFIX


def test_the_measure_buttons_say_what_they_do(mw):  # noqa: F811
    assert mw.calculate_length_button.text() == "Measure Length"
    assert mw.calculate_area_button.text() == "Measure Area"
    length_tip = mw.calculate_length_button.toolTip()
    area_tip = mw.calculate_area_button.toolTip()
    assert "root_lengths.csv" in length_tip and "GT skeleton" in length_tip
    assert "root_areas.csv" in area_tip and "GT mask" in area_tip


def test_no_text_quotes_the_old_button_names():
    found = [
        f"{path.relative_to(APP)}:{line}"
        for path in APP.rglob("*.py")
        for line, value in _string_constants(path)
        if "Measure → Root Length" in value or "Measure → Root Area" in value
    ]
    assert found == []
    panels = [v for _, v in _string_constants(APP / "gui" / "ui_panels.py")]
    assert "Root Length" not in panels and "Root Area" not in panels


def test_the_generate_stage_says_masks_and_skeletons_are_independent():
    tips = dict((k, tip) for k, _, _, tip in shell_chrome.STAGES)
    assert "neither follows the other" in tips["Generate"]

"""Wording guards: one `_fake` constant, the Measure button names, the Generate sentence."""

from __future__ import annotations

import ast
import pathlib
import re

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


ROOT = APP.parent
README = ROOT / "README.md"
DATA_DICTIONARY = ROOT / "docs" / "DATA_DICTIONARY.md"


def files_with(pattern: str) -> list[pathlib.Path]:
    """The app modules with a string constant (not a docstring) matching ``pattern``."""
    regex = re.compile(pattern)
    return sorted(
        {
            path
            for path in APP.rglob("*.py")
            for _, value in _string_constants(path)
            if regex.search(value)
        }
    )


def hits(pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [
        f"{path.relative_to(APP)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        for line, value in _string_constants(path)
        if regex.search(value)
    ]


def test_no_hand_traced_under_app():
    assert hits(r"(?i)hand-(traced|corrected)") == []


def test_no_trace_gt_stage_name_under_app():
    assert hits(r"\bTrace GT\b") == []


def test_skeleton_corrections_is_named_nowhere_under_app():
    # the folder is retired (WG-T3): its constant and the export guard went with W5-T5
    assert files_with(r"skeleton_corrections") == []


def test_the_readme_has_the_which_layer_table():
    text = README.read_text(encoding="utf-8")
    assert "| Fix the model's mask of an image | Mask layer (area) |" in text
    assert "gt/mask/<stem>.png" in text
    assert "skeleton_corrections" not in text


def test_the_data_dictionary_documents_the_gt_folders():
    text = DATA_DICTIONARY.read_text(encoding="utf-8")
    assert all(s in text for s in ("`gt/mask/<stem>.png`", "`gt/skeletons/<stem>.png`", "`Source`"))


def test_the_four_strings_use_the_prediction_and_gt_vocabulary():
    assert hits(r"Mask -> area, skeleton -> length: the layers are independent\.")
    assert hits(r"Export the labelled images and their class-id label images as a training")
    assert hits(r"Copies the labelled images and their class-id label images \(masks/\)")
    assert hits(r" class labels\.$")
    assert hits(r"GT tracing") == []

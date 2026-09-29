"""The committed pixel goldens of the Correct editor are complete and reproducible (C18).

``tests/data/correct_goldens.json`` was captured from the pre-merge mask and skeleton
editors on inputs drawn by ``synth_inputs()``. The goldens prove pixel identity only
while those inputs regenerate exactly, so these checks pin the inputs themselves.
Qt-free (the support module must not load PyQt6 either).
"""

from __future__ import annotations

import os
import subprocess
import sys

import cv2
import numpy as np
import pytest

from app.io_utils import imread_unicode
from tests.correct_goldens_support import (
    SYNTH_STEM,
    load_goldens,
    make_synth_project,
    png_colour_type,
    sha,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = ("synth_model_mask_and_skeleton", "synth_blank_png")
SCRIPTS = ("mask", "mask_clear", "skeleton", "skeleton_clear")

PHOTO = f"{SYNTH_STEM}.png"
MODEL_MASK = os.path.join("mask", f"{SYNTH_STEM}.png")
GENERATED = os.path.join("skeletons", f"{SYNTH_STEM}_fake.png")
EXPECTED_FILES = {
    "synth_model_mask_and_skeleton": {PHOTO, MODEL_MASK, GENERATED},
    "synth_blank_png": {PHOTO},
}

# sha() of the decoded inputs the goldens were captured from (the contracts scout's
# capture folder; its photo and generated skeleton are byte-identical to a fresh run).
PHOTO_PIXELS_SHA = "e8df78c1d1ffd609665f38fb6c530ee2b6881de8803756f6b8a3ce8a8b5c14bb"
MODEL_MASK_PIXELS_SHA = "fddccd6d384baf0f59cd75ae52962428aa0f8d588ee9696e8724193f79b055ea"
GENERATED_PIXELS_SHA = "9dbdb73ebe211a5ac94d9a6671d35a3bb7d225ad53f00557fb4f40dad060a9f4"

# Every op kind a golden driver implements (C18 op semantics).
OP_KINDS = {
    "brush",
    "eraser",
    "fill",
    "stroke",
    "click",
    "tool",
    "smooth",
    "eraser_size",
    "drag",
    "connect_endpoints",
    "double_click",
    "key",
    "finish",
    "clear",
    "undo",
    "redo",
    "save",
}


def _tree(folder: str) -> dict[str, bytes]:
    """``relative path -> bytes`` of every file under ``folder``."""
    files = {}
    for dirpath, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                files[os.path.relpath(path, folder)] = fh.read()
    return files


@pytest.mark.parametrize("case", CASES)
def test_make_synth_project_regenerates_the_inputs_byte_for_byte(tmp_path, case):
    folder_a, image_a = make_synth_project(str(tmp_path / "a"), case)
    folder_b, image_b = make_synth_project(str(tmp_path / "b"), case)

    assert folder_a == os.path.join(str(tmp_path / "a"), case)
    assert image_a == os.path.join(folder_a, PHOTO)
    assert image_b == os.path.join(folder_b, PHOTO)
    first, second = _tree(folder_a), _tree(folder_b)
    assert set(first) == EXPECTED_FILES[case]
    assert first == second


def test_make_synth_project_starts_from_a_fresh_folder(tmp_path):
    folder, _ = make_synth_project(str(tmp_path), "synth_model_mask_and_skeleton")
    expected = _tree(folder)
    os.makedirs(os.path.join(folder, "skeleton_corrections"))
    with open(os.path.join(folder, "skeleton_corrections", "stale.png"), "wb") as fh:
        fh.write(b"left over by an earlier replay")
    with open(os.path.join(folder, MODEL_MASK), "wb") as fh:
        fh.write(b"overwritten by an earlier save")

    again, _ = make_synth_project(str(tmp_path), "synth_model_mask_and_skeleton")

    assert again == folder
    assert _tree(folder) == expected
    assert not os.path.exists(os.path.join(folder, "skeleton_corrections"))


def test_unknown_case_is_refused(tmp_path):
    with pytest.raises(KeyError):
        make_synth_project(str(tmp_path), "real_mask_and_skeleton")


def test_inputs_are_the_ones_the_goldens_were_captured_from(tmp_path):
    goldens = load_goldens()["cases"]
    folder, image = make_synth_project(str(tmp_path), "synth_model_mask_and_skeleton")

    assert sha(imread_unicode(image, cv2.IMREAD_UNCHANGED)) == PHOTO_PIXELS_SHA
    mask_path = os.path.join(folder, MODEL_MASK)
    model_mask = imread_unicode(mask_path, cv2.IMREAD_UNCHANGED)
    assert sha(model_mask) == MODEL_MASK_PIXELS_SHA
    assert png_colour_type(mask_path) == 0  # 8-bit grey: what the mask generator writes
    generated = imread_unicode(os.path.join(folder, GENERATED), cv2.IMREAD_UNCHANGED)
    assert sha(generated) == GENERATED_PIXELS_SHA
    # The first mask op only picks the brush, so its plane is the mask as opened
    # (grey > 200 -> 1): the committed model mask is the one the goldens saw.
    opened = (model_mask > 200).astype(np.uint8)
    assert sha(opened) == goldens["synth_model_mask_and_skeleton"]["mask"]["per_op_plane_sha256"][0]

    _, blank_image = make_synth_project(str(tmp_path), "synth_blank_png")
    assert sha(imread_unicode(blank_image, cv2.IMREAD_UNCHANGED)) == PHOTO_PIXELS_SHA
    empty = np.zeros((480, 640), np.uint8)
    assert sha(empty) == goldens["synth_blank_png"]["mask"]["per_op_plane_sha256"][0]


def test_goldens_hold_both_cases_and_all_four_op_scripts():
    goldens = load_goldens()

    assert set(goldens) == {"meta", "ops", "cases"}
    assert set(goldens["cases"]) == set(CASES)
    assert set(goldens["ops"]) == set(SCRIPTS)
    for script, ops in goldens["ops"].items():
        assert ops, script
        assert {op[0] for op in ops} <= OP_KINDS, script
    for case in CASES:
        record = goldens["cases"][case]
        assert record["image"] == PHOTO
        for script in SCRIPTS:
            result = record[script]
            per_op = result[
                "per_op_plane_sha256" if script.startswith("mask") else "per_op_raster_sha256"
            ]
            assert len(per_op) == len(goldens["ops"][script]), (case, script)
            assert "exists" in result["saved"], (case, script)
        for script in ("skeleton", "skeleton_clear"):
            assert {"skeleton_source", "raster_sha256", "raster_shape", "save_size"} <= set(
                record[script]["start"]
            )


def test_goldens_saved_masks_are_hand_traced_rgba():
    """The contract every mask replay checks: RGBA colour type 6, and Clear + Save deletes."""
    for case in CASES:
        record = load_goldens()["cases"][case]
        saved = record["mask"]["saved"]
        assert saved["exists"] and saved["colour_type"] == 6
        assert saved["shape"] == [480, 640, 4]
        assert record["mask_clear"]["saved"] == {"exists": False}


def test_support_module_imports_no_qt():
    code = (
        "import sys; import tests.correct_goldens_support; "
        "qt = [m for m in sys.modules if m.split('.')[0] == 'PyQt6']; "
        "assert not qt, qt"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr

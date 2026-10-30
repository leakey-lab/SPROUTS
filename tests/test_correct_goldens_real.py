"""The Correct goldens on three real photos: the pixel gate of ``docs/examples`` (O35, F4).

``tests/test_correct_goldens.py`` replays the four op scripts on drawn inputs. These cases
replay the same scripts, through the same driver, on real minirhizotron photos with their
production model outputs (``docs/examples``, the files the 46k project made):

- ``real_mask_and_skeleton`` (T001 L044): a model mask and a generated skeleton;
- ``real_empty_generated`` (T001 L050): a model mask and an empty (all black) skeleton;
- ``real_existing_correction`` (T012 L047): the photo and the generated skeleton, no mask,
  and a saved correction: a GT skeleton with the left third of the generated one erased,
  seeded the way Correct stores an edit (``ProjectLayout.gt_skeleton_path``).

``tests/data/correct_goldens_real.json`` holds pixel keys only (no measured values, no file
hashes), so a calibration change or another PNG encoder cannot break the gate. Record it
again, after a reviewed change of saved pixels only, with ``SPROUTS_RECORD_REAL_GOLDENS=1``:
the recording test writes the file and then fails ("recorded; review the diff").
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402

from app.editing.thinning import thin_raster  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.io_utils import imread_unicode, imwrite_unicode  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests import test_correct_goldens as drivers  # noqa: E402
from tests.correct_goldens_support import (  # noqa: E402
    describe_mask_file,
    describe_skeleton_file,
    sha,
)
from tests.test_correct_goldens import (  # noqa: E402
    OPS,
    apply,
    assert_mask_file,
    assert_skeleton_file,
    files,
    replay,
    skeleton_start,
    state_of,
)

# The page's fixtures, as the synthetic goldens use them (pytest finds them by these names).
app = drivers.app
guards = drivers.guards
make_editor = drivers.make_editor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO_ROOT, "docs", "examples")
GOLDEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "correct_goldens_real.json"
)
RECORD = "SPROUTS_RECORD_REAL_GOLDENS"

#: case -> (image stem, with the model mask, with the saved correction)
CASES: dict[str, tuple[str, bool, bool]] = {
    "real_mask_and_skeleton": ("24EFCAM1_T001_L044_2024.07.18_111530_001_SMM", True, False),
    "real_empty_generated": ("24EFCAM1_T001_L050_2024.07.18_111522_001_SMM", True, False),
    "real_existing_correction": ("24EFCAM1_T012_L047_2024.07.18_101909_001_SMM", False, True),
}
SCRIPTS = ("mask", "mask_clear", "skeleton", "skeleton_clear")
MASK_SAVED = ("exists", "shape", "dtype", "colour_type", "pixels_sha256", "on_sha256", "on_count")
SKELETON_SAVED = ("exists", "shape", "dtype", "colour_type", "pixels_sha256", "on_count", "values")

checking = pytest.mark.skipif(bool(os.environ.get(RECORD)), reason=f"{RECORD} is set")


def load_golden() -> dict:
    with open(GOLDEN_FILE, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def make_real_project(root: str, case: str) -> tuple[str, str, str]:
    """A fresh project ``<root>/<case>`` of the example files the case needs, and its saved
    correction when it has one. Returns ``(folder, image path, stem)``."""
    stem, with_mask, with_correction = CASES[case]
    folder = os.path.join(root, case)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "skeletons"))
    image = os.path.join(folder, f"{stem}.jpg")
    shutil.copyfile(os.path.join(EXAMPLES, f"{stem}.jpg"), image)
    generated = f"{stem}_fake.png"
    shutil.copyfile(
        os.path.join(EXAMPLES, "skeletons", generated), os.path.join(folder, "skeletons", generated)
    )
    if with_mask:
        os.makedirs(os.path.join(folder, "mask"))
        shutil.copyfile(
            os.path.join(EXAMPLES, "mask", f"{stem}.png"),
            os.path.join(folder, "mask", f"{stem}.png"),
        )
    if with_correction:  # the generated skeleton as Correct opens it, its left third erased
        gray = imread_unicode(os.path.join(folder, "skeletons", generated), cv2.IMREAD_GRAYSCALE)
        correction = thin_raster(gray, (640, 480))
        correction[:, : correction.shape[1] // 3] = 0
        path = ProjectLayout(folder).gt_skeleton_path(stem)
        os.makedirs(os.path.dirname(path))
        imwrite_unicode(path, correction)
    return folder, image, stem


@pytest.fixture(scope="module", autouse=True)
def the_examples_are_there():
    assert os.path.isdir(EXAMPLES), "docs/examples is missing"


# ============================================================================= the goldens
@checking
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_mask_then_the_skeleton_script_in_one_page_match_their_goldens(
    make_editor, tmp_path, case
):
    golden = load_golden()[case]
    folder, image, stem = make_real_project(str(tmp_path), case)
    mask_file, correction = files(folder, stem)
    editor = make_editor(image, folder)
    assert skeleton_start(editor) == golden["skeleton"]["start"]

    saved = replay(editor, MASK, "mask", golden["mask"]["per_op_plane_sha256"])
    assert saved is golden["mask"]["save_returned"]
    assert_mask_file(mask_file, golden["mask"]["saved"])

    assert skeleton_start(editor) == golden["skeleton"]["start"]  # untouched by the mask ops
    saved = replay(editor, SKELETON, "skeleton", golden["skeleton"]["per_op_raster_sha256"])
    assert saved is golden["skeleton"]["save_returned"]
    assert_skeleton_file(correction, golden["skeleton"]["saved"])
    assert_mask_file(mask_file, golden["mask"]["saved"])


@checking
@pytest.mark.parametrize("script", ["mask_clear", "skeleton_clear"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_clear_scripts_on_a_fresh_project_match_their_goldens(
    make_editor, tmp_path, case, script
):
    golden = load_golden()[case][script]
    folder, image, stem = make_real_project(str(tmp_path), case)
    mask_file, correction = files(folder, stem)
    editor = make_editor(image, folder)
    layer_id = MASK if script == "mask_clear" else SKELETON
    key = "per_op_plane_sha256" if layer_id == MASK else "per_op_raster_sha256"
    if layer_id == SKELETON:
        assert skeleton_start(editor) == golden["start"]

    assert replay(editor, layer_id, script, golden[key]) is golden["save_returned"]

    if layer_id == MASK:
        assert_mask_file(mask_file, golden["saved"])
    else:
        assert_skeleton_file(correction, golden["saved"])


# ============================================================================== recording
def record(editor, layer_id: str, script: str) -> dict:
    """Run ``script`` on ``layer_id``; the sha of the layer after every op and the save result."""
    editor.set_active_layer(layer_id)
    per_op, returned = [], None
    for op in OPS[script]:
        result = apply(editor, op)
        if op[0] == "save":
            returned = result
        per_op.append(sha(state_of(editor, layer_id)))
    key = "per_op_plane_sha256" if layer_id == MASK else "per_op_raster_sha256"
    return {key: per_op, "save_returned": returned}


def keep(described: dict, keys: tuple[str, ...]) -> dict:
    return {key: described[key] for key in keys if key in described}


def record_the_real_goldens(make_editor, tmp_path) -> None:
    cases: dict[str, dict] = {}
    for case in sorted(CASES):
        folder, image, stem = make_real_project(str(tmp_path / "main"), case)
        mask_file, correction = files(folder, stem)
        editor = make_editor(image, folder)
        record_case = {}
        record_case["skeleton"] = {"start": skeleton_start(editor)}
        record_case["mask"] = record(editor, MASK, "mask")
        record_case["mask"]["saved"] = keep(describe_mask_file(mask_file), MASK_SAVED)
        record_case["skeleton"].update(record(editor, SKELETON, "skeleton"))
        record_case["skeleton"]["saved"] = keep(describe_skeleton_file(correction), SKELETON_SAVED)
        for script in ("mask_clear", "skeleton_clear"):
            layer_id = MASK if script == "mask_clear" else SKELETON
            folder, image, stem = make_real_project(str(tmp_path / script), case)
            mask_file, correction = files(folder, stem)
            editor = make_editor(image, folder)
            entry = {}
            if layer_id == SKELETON:
                entry["start"] = skeleton_start(editor)
            entry.update(record(editor, layer_id, script))
            if layer_id == MASK:
                entry["saved"] = keep(describe_mask_file(mask_file), MASK_SAVED)
            else:
                entry["saved"] = keep(describe_skeleton_file(correction), SKELETON_SAVED)
            record_case[script] = entry
        cases[case] = record_case
    meta = {
        "purpose": "pixel goldens of the Correct page on the docs/examples photos; "
        "saved.* is the contract, per_op_* name the first diverging op",
        "scripts": list(SCRIPTS),
    }
    with open(GOLDEN_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"meta": meta, "cases": cases}, fh, indent=1, sort_keys=True)
        fh.write("\n")


def test_the_real_goldens_hold_every_case_and_script(make_editor, tmp_path):
    """With ``SPROUTS_RECORD_REAL_GOLDENS=1`` this test records the file and then fails."""
    if os.environ.get(RECORD):
        record_the_real_goldens(make_editor, tmp_path)
        pytest.fail("recorded; review the diff")
    cases = load_golden()
    assert sorted(cases) == sorted(CASES)
    for case, golden in cases.items():
        for script in SCRIPTS:
            entry = golden[script]
            per_op = "per_op_plane_sha256" if script.startswith("mask") else "per_op_raster_sha256"
            assert len(entry[per_op]) == len(OPS[script]), (case, script)
            assert "exists" in entry["saved"] and "save_returned" in entry, (case, script)

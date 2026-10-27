"""Smooth JPEG blocks is display only (O22): saved pixels, the models and Measure never see it."""

from __future__ import annotations

import ast
import os
import pathlib
import time

import cv2
import numpy as np
import pytest

from app import enhancement as enh
from app.gui.correct.layer import MASK, SKELETON
from tests.correct_goldens_support import make_synth_project
from tests.test_correct_goldens import (
    GOLDENS,
    assert_mask_file,
    assert_skeleton_file,
    files,
    replay,
    skeleton_start,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The Correct page's guards, as for the goldens this module replays.
pytestmark = pytest.mark.usefixtures("correct_guards")

#: Packages that write masks, skeletons, ground truth or measurements: none may use Enhance.
FORBIDDEN = ("inference", "handlers", "gt", "masks")


def _imports_enhancement(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[:2] == ["app", "enhancement"] for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and (
                mod == "app.enhancement"
                or (mod == "app" and any(a.name == "enhancement" for a in node.names))
            ):
                return True
            if node.level > 0 and (
                mod.split(".")[-1] == "enhancement"
                or any(a.name == "enhancement" for a in node.names)
            ):
                return True
    return False


@pytest.mark.parametrize("package", FORBIDDEN)
def test_nothing_that_writes_or_measures_imports_the_enhancement_module(package):
    sources = sorted((ROOT / "app" / package).rglob("*.py"))
    assert sources, package
    offenders = [str(p) for p in sources if _imports_enhancement(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_the_import_scan_detects_the_import_forms():
    for src in (
        "import app.enhancement",
        "from app.enhancement import enhance",
        "from app import enhancement",
        "from .. import enhancement",
        "from ..enhancement import clahe",
    ):
        assert _imports_enhancement(src), src
    assert not _imports_enhancement("import os\nfrom app.io_utils import imread_unicode")


def _blocky_jpeg_instead_of(png: str) -> str:
    """Replace the gradient photo ``png`` by a JPEG of it with per-8x8-block offsets of -4..4."""
    photo = cv2.imdecode(np.fromfile(png, np.uint8), cv2.IMREAD_COLOR).astype(np.int16)
    rng = np.random.default_rng(5)
    blocks = rng.integers(-4, 5, (photo.shape[0] // 8, photo.shape[1] // 8), dtype=np.int16)
    offset = np.kron(blocks, np.ones((8, 8), np.int16))[:, :, None]
    blocky = np.clip(photo + offset, 0, 255).astype(np.uint8)
    jpg = os.path.splitext(png)[0] + ".jpg"
    ok, buf = cv2.imencode(".jpg", blocky, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    buf.tofile(jpg)
    os.remove(png)
    return jpg


def test_the_correct_goldens_replay_identically_with_the_box_on(make_editor, tmp_path):
    case = "synth_model_mask_and_skeleton"
    golden = GOLDENS["cases"][case]
    folder, png = make_synth_project(str(tmp_path), case)
    jpg = _blocky_jpeg_instead_of(png)
    editor = make_editor(jpg, folder)
    controls = editor.norm_controls
    assert controls.deblock_check.isVisibleTo(controls)
    raw = editor.photo_item.pixmap().toImage()
    controls.deblock_check.setChecked(True)
    editor.apply_normalization()
    assert editor.photo_item.pixmap().toImage() != raw  # the box really changed the picture
    mask_file, correction = files(folder)
    assert skeleton_start(editor) == golden["skeleton"]["start"]
    saved = replay(editor, MASK, "mask", golden["mask"]["per_op_plane_sha256"])
    assert saved is golden["mask"]["save_returned"]
    assert_mask_file(mask_file, golden["mask"]["saved"])
    saved = replay(editor, SKELETON, "skeleton", golden["skeleton"]["per_op_raster_sha256"])
    assert saved is golden["skeleton"]["save_returned"]
    assert_skeleton_file(correction, golden["skeleton"]["saved"])


@pytest.mark.skipif(os.environ.get("SPROUTS_PERF_TESTS") != "1", reason="wall-clock budget")
def test_deblock_of_a_640x480_photo_takes_under_30_ms():
    img = (np.random.default_rng(1).random((480, 640, 3)) * 255).astype(np.uint8)
    times = []
    for _ in range(9):
        start = time.perf_counter()
        enh.deblock(img)
        times.append(time.perf_counter() - start)
    assert sorted(times)[4] < 0.030

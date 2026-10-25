"""The skeleton thinning helpers of ``app.editing.thinning`` (Qt-free, W1-T3, O2).

``thin_raster`` (a skeleton file as Correct opens it) and the lazy scikit-image import moved
here from ``app.gui.skeleton_graph_model``, which imports them back under the same names.
``is_generated_as_opened`` says whether a skeleton is the generated one as Correct opens it:
a skeleton Save writes a correction only when it is not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

import app.gui.skeleton_graph_model as sgm
from app.editing import thinning
from app.editing.thinning import is_generated_as_opened, thin_raster

ROOT = Path(__file__).resolve().parents[1]
PHOTO = (640, 480)  # (w, h)


def _line(row: int) -> np.ndarray:
    """A generated skeleton raster at SKELETON_OUTPUT_SIZE (341 x 256): one line on ``row``."""
    gray = np.zeros((256, 341), np.uint8)
    gray[row, 30:300] = 255
    return gray


def _generated(tmp_path) -> tuple[str, np.ndarray]:
    """The generated skeleton file of the tests (one line on row 100) and its raster."""
    gray = _line(100)
    path = str(tmp_path / "a_fake.png")
    cv2.imwrite(path, gray)
    return path, gray


def test_the_thinning_helpers_live_in_app_editing_and_load_no_qt():
    probe = (
        "import sys, app.editing.thinning; "
        "print(sorted(m for m in sys.modules if m == 'PyQt6' or m.startswith('PyQt6.') "
        "or m == 'skimage' or m.startswith('skimage.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_skeleton_graph_model_reexports_the_same_functions():
    assert sgm.thin_raster is thinning.thin_raster
    assert sgm.prewarm_skeletonize is thinning.prewarm_skeletonize
    assert sgm._skeletonize_uint8 is thinning._skeletonize_uint8
    assert sgm._ensure_uint8_binary is thinning._ensure_uint8_binary


def test_is_generated_as_opened(tmp_path):
    path, gray = _generated(tmp_path)
    opened = thin_raster(gray, PHOTO)
    other = thin_raster(_line(40), PHOTO)
    empty = np.zeros((PHOTO[1], PHOTO[0]), np.uint8)

    assert opened.any()
    assert is_generated_as_opened(opened, path, PHOTO)
    assert not is_generated_as_opened(other, path, PHOTO)
    assert is_generated_as_opened(empty, None, PHOTO)
    assert not is_generated_as_opened(empty, path, PHOTO)  # emptied: a real correction
    assert is_generated_as_opened(empty, str(tmp_path / "missing.png"), PHOTO)
    assert not is_generated_as_opened(opened, str(tmp_path / "missing.png"), PHOTO)


def test_an_unreadable_generated_file_counts_as_empty(tmp_path):
    path = tmp_path / "broken_fake.png"
    path.write_bytes(b"not a png")
    empty = np.zeros((PHOTO[1], PHOTO[0]), np.uint8)

    assert is_generated_as_opened(empty, str(path), PHOTO)
    assert not is_generated_as_opened(empty + 255, str(path), PHOTO)


def test_the_values_of_the_mask_do_not_matter_only_where_it_is_set(tmp_path):
    path, gray = _generated(tmp_path)
    opened = thin_raster(gray, PHOTO)

    assert is_generated_as_opened((opened > 0).astype(np.uint8), path, PHOTO)

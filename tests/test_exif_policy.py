"""Orientation policy (M3): every stage uses the stored pixel grid, EXIF orientation is never applied.

One JPEG is stored 640 x 480 with EXIF Orientation = 6 and a bright block in the stored top-left
corner. A reader that applied the tag would return 480 x 640 with the block top-right. Generation
resizes to fixed sizes, so the block position (not the shape alone) proves the grid is kept.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import torch
from PIL import Image

from app.enhancement import DECODE_FLAGS
from app.inference import mask_inference
from app.inference import skeleton_inference as si
from app.io_utils import imread_unicode

STEM = "CAM1_T001_L010_2024.07.18_101010_001_EXIF6"


def _tagged_jpeg(path) -> str:
    """640 x 480 RGB, a 160 x 120 block of 235 in the stored top-left, EXIF Orientation = 6."""
    img = np.full((480, 640, 3), 60, np.uint8)
    img[:120, :160] = 235
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.fromarray(img).save(path, quality=95, exif=exif.tobytes())
    return str(path)


def _corner(gray: np.ndarray) -> str:
    """The brightest quarter-corner of a 2-D array."""
    h, w = gray.shape[:2]
    a = np.asarray(gray, np.float64)
    quads = {
        "top-left": a[: h // 4, : w // 4].mean(),
        "top-right": a[: h // 4, -(w // 4) :].mean(),
        "bottom-left": a[-(h // 4) :, : w // 4].mean(),
        "bottom-right": a[-(h // 4) :, -(w // 4) :].mean(),
    }
    return max(quads, key=quads.get)


@pytest.fixture
def tagged(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    return src, _tagged_jpeg(src / (STEM + ".jpg"))


class _Stub(torch.nn.Module):
    def forward(self, x):
        return (x[:, 0:1] > 1.0).float()


class _SkelStub:
    def run_batch(self, x):
        return x


def test_the_tag_is_real(tagged):
    _, path = tagged
    decoded = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (640, 480, 3)  # OpenCV's default applies the tag


def test_mask_generation_keeps_the_stored_grid(tagged, cpu_runtime):
    src, _ = tagged
    out = src / "mask"
    mask_inference.generate_masks(str(src), str(out), model=_Stub())
    mask = cv2.imread(str(out / (STEM + ".png")), cv2.IMREAD_GRAYSCALE)
    assert mask.shape == (480, 640)
    assert _corner(mask) == "top-left"


def test_skeleton_generation_keeps_the_stored_grid(tagged, cpu_runtime):
    src, _ = tagged
    out = src / "skeletons"
    si.run_skeleton_generation(str(src), str(out), model=_SkelStub(), batch_size=1)
    fake = cv2.imread(str(out / (STEM + "_fake.png")), cv2.IMREAD_GRAYSCALE)
    assert fake.shape == (256, 341)
    assert _corner(fake) == "top-left"


def test_the_library_and_correct_decode_keep_the_stored_grid(tagged):
    _, path = tagged
    decoded = imread_unicode(path, DECODE_FLAGS)
    assert decoded.shape == (480, 640, 3)
    assert _corner(decoded[..., 0]) == "top-left"

"""R6: the faster Measure-length preprocessing returns the same skeleton, pixel for pixel.

``metrics.preprocess_image`` (Otsu, invert if mostly white, thin) spent more time on
temporaries than on the thinning: ``skeletonize(binary / 255)`` made a float64 copy that
skimage then turned back into booleans, ``(skeleton * 255).astype(np.uint8)`` went through
int64, and ``np.mean`` summed every pixel as float64 to decide the inversion. The same
results come from ``skeletonize(binary > 0)``, a uint8 view and an exact integer count
(about 1.5 ms less per 640 x 480 image). The reference below is the pre-R6 function,
verbatim; the Measure CSV itself stays pinned by test_measure_core's legacy pipeline.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.inference import metrics
from app.io_utils import imread_unicode


def _pre_r6_preprocess_image(image_path):
    """``metrics.preprocess_image`` before R6, verbatim."""
    from skimage.morphology import skeletonize

    img = imread_unicode(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(binary) > 127:
        binary = 255 - binary
    skeleton = skeletonize(binary / 255)
    return (skeleton * 255).astype(np.uint8)


def _roots(h, w, *, seed, thickness=3, background=0, colour=255, channels=3):
    rng = np.random.default_rng(seed)
    img = np.full((h, w), background, np.uint8)
    for _ in range(6):
        pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0) + (w / 2, h / 2)
        cv2.polylines(img, [pts.astype(np.int32)], False, colour, thickness=thickness)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return img if channels == 1 else np.dstack([img] * channels)


def _half_white(h, w, white):
    """A 0/255 image with exactly ``white`` white pixels (the inversion boundary)."""
    flat = np.zeros(h * w, np.uint8)
    flat[np.random.default_rng(white).permutation(h * w)[:white]] = 255
    return flat.reshape(h, w)


IMAGES = {
    "generated_rgb": lambda: _roots(480, 640, seed=1),
    "generated_341x256": lambda: _roots(256, 341, seed=2),
    "mostly_white_inverted": lambda: _roots(480, 640, seed=3, background=255, colour=0),
    "thick_roots": lambda: _roots(480, 640, seed=4, thickness=9),
    "grey": lambda: _roots(120, 160, seed=5, channels=1),
    "rgba": lambda: np.dstack([_roots(96, 128, seed=6), np.full((96, 128), 255, np.uint8)]),
    "grey16": lambda: _roots(64, 80, seed=7, channels=1).astype(np.uint16) * 257,
    "all_black": lambda: np.zeros((48, 64, 3), np.uint8),
    "all_white": lambda: np.full((48, 64, 3), 255, np.uint8),
    "noise": lambda: np.random.default_rng(8).integers(0, 256, (50, 70, 3), dtype=np.uint8),
    "one_pixel": lambda: np.full((1, 1), 200, np.uint8),
    "one_row": lambda: _roots(1, 97, seed=9, channels=1),
    # 15 x 17 = 255 pixels: 127 white is a mean of exactly 127 (kept), 128 is above (inverted).
    "mean_exactly_127": lambda: _half_white(15, 17, 127),
    "mean_just_above_127": lambda: _half_white(15, 17, 128),
}


@pytest.mark.parametrize("name", sorted(IMAGES))
@pytest.mark.parametrize("ext", [".png", ".jpg"])
def test_preprocess_image_is_unchanged(tmp_path, name, ext):
    image = IMAGES[name]()
    if ext == ".jpg" and (image.dtype != np.uint8 or (image.ndim == 3 and image.shape[2] == 4)):
        pytest.skip("JPEG holds neither 16-bit nor alpha")
    path = str(tmp_path / f"Données_{name}{ext}")  # non-ASCII: imread_unicode's path
    assert cv2.imencode(ext, image)[0]
    cv2.imencode(ext, image)[1].tofile(path)

    expected = _pre_r6_preprocess_image(path)
    actual = metrics.preprocess_image(path)

    assert actual.dtype == np.uint8 and actual.shape == expected.shape
    assert np.array_equal(actual, expected)
    assert set(np.unique(actual)) <= {0, 255}


def test_the_inversion_rule_matches_the_float_mean_at_its_boundary():
    for white in range(120, 136):
        binary = _half_white(15, 17, white)
        assert metrics._mostly_white(binary) == (np.mean(binary) > 127), white


def test_the_measured_length_is_unchanged(tmp_path):
    for seed in range(20):
        image = _roots(480, 640, seed=100 + seed, thickness=int(2 + seed % 3))
        path = str(tmp_path / f"{seed}.png")
        cv2.imwrite(path, image)
        before = metrics.root_length_mm(_pre_r6_preprocess_image(path))
        assert metrics.root_length_mm(metrics.preprocess_image(path)) == before

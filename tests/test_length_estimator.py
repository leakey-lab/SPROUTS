"""The corner-count length estimator (O12, W3-T8; audit X1).

Root length is Vossepoel & Smeulders (1982): ``0.980 n_e + 1.406 n_o - 0.091 n_c`` on the
m-adjacent 8-connected skeleton, with the per-axis pixel pitch. The cases and values are the
research memo's (``o3-o12-calibration-length.md``, "Test cases"); plain numpy inputs, so they
do not depend on OpenCV's rasteriser, except where a case says ``cv2``. The vectorised counts
are checked against an independent pure-Python reference (``tests/length_reference.py``).
"""

import math

import cv2
import numpy as np
import pytest
from skimage.morphology import skeletonize

from app.config import Calibration
from app.inference import metrics
from tests import length_reference

CAL = Calibration(18.0, 13.5)  # O3: square pixels, 0.028125 mm at 640 x 480
CAL_18_13 = Calibration(18.0, 13.0)  # non-square: tells horizontal from vertical steps apart
P = 18.0 / 640


def _canvas(h=480, w=640):
    return np.zeros((h, w), np.uint8)


def _digital_line(n, angle_deg, x0=50, y0=50):
    """Grid-intersect digital line with n columns; true length of the continuous line, px."""
    sk = _canvas()
    x = np.arange(n + 1)
    y = np.floor(x * math.tan(math.radians(angle_deg)) + 0.5).astype(int)
    sk[y0 + y, x0 + x] = 255
    return sk, n / math.cos(math.radians(angle_deg))


def test_the_constants_are_vossepoel_smeulders():
    assert (metrics.VS_ORTHOGONAL, metrics.VS_DIAGONAL, metrics.VS_CORNER) == (0.980, 1.406, 0.091)


def test_axis_parallel_and_diagonal_lines():
    row = _canvas()
    row[240, 100:201] = 255  # 101 px = 100 steps
    col = _canvas()
    col[100:201, 320] = 255
    dia = _canvas()
    i = np.arange(101)
    dia[100 + i, 100 + i] = 255
    assert metrics.skeleton_arc_length_px(row) == (100, 0, 0, 0)
    assert metrics.skeleton_arc_length_px(col) == (0, 100, 0, 0)
    assert metrics.skeleton_arc_length_px(dia) == (0, 0, 100, 0)
    # 2.75625 mm (-2.0 %), 2.75625 mm, 3.954375 mm (-0.58 %)
    assert metrics.root_length_mm(row, calibration=CAL) == pytest.approx(98.0 * P, rel=1e-12)
    assert metrics.root_length_mm(col, calibration=CAL) == pytest.approx(98.0 * P, rel=1e-12)
    assert metrics.root_length_mm(dia, calibration=CAL) == pytest.approx(140.6 * P, rel=1e-12)


@pytest.mark.parametrize(
    "angle, steps, px",
    [
        (10.0, (165, 0, 35, 70), 204.540),  # true 203.085 px, +0.72 %
        (22.5, (117, 0, 83, 165), 216.343),  # true 216.478 px, -0.06 %
        (30.0, (85, 0, 115, 169), 229.611),  # true 230.940 px, -0.58 %
    ],
)
def test_oblique_digital_lines(angle, steps, px):
    sk, true_px = _digital_line(200, angle)
    assert metrics.skeleton_arc_length_px(sk) == steps
    assert metrics.root_length_mm(sk, calibration=CAL) == pytest.approx(px * P, rel=1e-12)
    assert metrics.root_length_mm(sk, calibration=CAL) == pytest.approx(true_px * P, rel=0.008)


def test_junction_diagonals_are_not_counted_twice():
    sk = _canvas()
    sk[100, 100:121] = 255  # 20 steps
    sk[101:111, 110] = 255  # 10 steps below the middle pixel
    # today's estimator adds the 2 junction diagonals: 32.83 px
    assert metrics.skeleton_arc_length_px(sk) == (20, 10, 0, 0)
    assert metrics.root_length_mm(sk, calibration=CAL) == pytest.approx(29.4 * P, rel=1e-12)


def test_a_thinned_ring_measures_its_circumference():
    img = _canvas()
    cv2.circle(img, (320, 240), 100, 255, 9)
    sk = skeletonize(img > 0)  # measured +0.09 %
    assert metrics.root_length_mm(sk, calibration=CAL) == pytest.approx(
        2 * math.pi * 100 * P, rel=0.005
    )


def test_empty_and_single_pixel():
    sk = _canvas()
    assert metrics.root_length_mm(sk, calibration=CAL) == 0.0
    sk[10, 10] = 255
    assert metrics.root_length_mm(sk, calibration=CAL) == 0.0


def test_generated_skeleton_size_uses_its_own_pitch():
    row = _canvas(256, 341)
    row[128, 100:201] = 255  # 5.173020528 mm
    assert metrics.root_length_mm(row, calibration=CAL) == pytest.approx(
        0.98 * 100 * 18 / 341, rel=1e-12
    )
    col = _canvas(256, 341)
    col[100:201, 170] = 255  # 5.167968750 mm
    assert metrics.root_length_mm(col, calibration=CAL) == pytest.approx(
        0.98 * 100 * 13.5 / 256, rel=1e-12
    )


def test_the_memo_cv2_cases():
    circle = _canvas()
    cv2.circle(circle, (320, 240), 50, 255, 1)
    assert metrics.skeleton_arc_length_px(circle) == (80, 80, 120, 148)
    assert metrics.root_length_mm(circle, calibration=CAL) == pytest.approx(312.052 * P, rel=1e-6)
    line = _canvas()
    cv2.line(line, (100, 100), (300, 183), 255, 1)
    assert metrics.skeleton_arc_length_px(line) == (117, 0, 83, 166)
    assert metrics.root_length_mm(line, calibration=CAL) == pytest.approx(216.252 * P, rel=1e-6)


@pytest.mark.parametrize("seed", range(20))
def test_the_vectorised_counts_equal_the_reference(seed):
    rng = np.random.default_rng(seed)
    img = _canvas()
    for _ in range(6):
        p1 = (int(rng.integers(0, 640)), int(rng.integers(0, 480)))
        p2 = (int(rng.integers(0, 640)), int(rng.integers(0, 480)))
        cv2.line(img, p1, p2, 255, int(rng.integers(1, 10)))
    for _ in range(2):
        centre = (int(rng.integers(0, 640)), int(rng.integers(0, 480)))
        cv2.circle(img, centre, int(rng.integers(10, 150)), 255, int(rng.integers(1, 10)))
    sk = skeletonize(img > 0)
    assert sk.any()
    assert tuple(metrics.skeleton_arc_length_px(sk)) == length_reference.vs_counts(sk)
    for cal in (CAL, CAL_18_13):
        assert metrics.root_length_mm(sk, calibration=cal) == length_reference.vs_length_mm(
            sk, *cal.mm_per_px(640, 480)
        )

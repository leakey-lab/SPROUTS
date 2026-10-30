"""
Baseline regression suite for Root-Mask-and-Skeletons.

These tests PIN the *current* behavior of the scientifically load-bearing code
paths so that the refactor/upgrade work (deps, memory, performance, dedup, OOP)
can proceed without silently changing measured outputs.

IMPORTANT: The calibration constants encoded as golden values below reflect the
behavior at the start of the hardening branch. The R5 scientific-correctness fix
(arc-length calibration) will INTENTIONALLY change the length golden values; when
that happens, update LEN_PER_PX here together with a documented justification in
the commit message. Until then, every other phase must keep these green.

O12 (W3-T8, audit X1): length moved from the Freeman weights (1 per orthogonal step,
sqrt(2) per diagonal step), which read about 5 % long on real roots, to the corner-count
estimator of Vossepoel & Smeulders (1982): 0.980 per orthogonal step, 1.406 per diagonal
step, -0.091 per change of direction. The straight-line length goldens below carry those
weights; a straight line has no corner, so an axis-parallel line reads 2.0 % short.
"""

import csv

import numpy as np
import pytest

from app.data_processing.data_processor import DataProcessor
from app.gui.skeleton_graph_model import SkeletonCorrectionModel, vectorize_skeleton
from app.inference.metrics import (
    calculate_root_area,
    calculate_root_length,
    length_row,
    parse_image_name,
)

# --- Calibration (physical field of view). Metrics are now resolution-independent:
# they map the FOV onto the *actual* image/mask shape. See R5 / metrics.py. ---
FOV_W_MM = 18.0
FOV_H_MM = 13.5


def _row_skeleton(n_px, h=20, w=120):
    sk = np.zeros((h, w), dtype=np.uint8)
    sk[h // 2, 0:n_px] = 255
    return sk


# ---------------- Length (corner-count estimator, O12) ----------------


@pytest.mark.parametrize("n_px", [2, 10, 25, 100])
def test_horizontal_length_uses_x_pitch(n_px):
    # A horizontal line of n_px pixels has n_px-1 orthogonal steps of pitch 18/W mm,
    # each weighted 0.98 (O12).
    w = 120
    sk = _row_skeleton(n_px, h=20, w=w)
    assert calculate_root_length(sk) == pytest.approx((n_px - 1) * 0.98 * FOV_W_MM / w, rel=1e-9)


def test_length_empty_is_zero():
    assert calculate_root_length(np.zeros((10, 10), dtype=np.uint8)) == 0.0


def test_diagonal_uses_euclidean_step_and_exceeds_orthogonal():
    # Arc-length fix: a diagonal of N pixels costs (N-1) Euclidean diagonal steps, each
    # weighted 1.406 / sqrt(2) (O12), which is strictly more than the old raw pixel count
    # would have given.
    import math

    n = 20
    horiz = np.zeros((40, 40), dtype=np.uint8)
    horiz[20, 0:n] = 255
    diag = np.zeros((40, 40), dtype=np.uint8)
    for i in range(n):
        diag[i, i] = 255
    lh = calculate_root_length(horiz)
    ld = calculate_root_length(diag)
    diag_step = math.hypot(FOV_W_MM / 40, FOV_H_MM / 40)
    assert ld == pytest.approx((n - 1) * 1.406 / math.sqrt(2) * diag_step, rel=1e-9)
    assert ld > lh  # diagonals cost more than orthogonal steps (the bug fixed)


def test_length_resolution_invariant_full_width_line():
    # A line spanning the full width is ~0.98 x FOV width regardless of resolution (F-003;
    # 0.98: the orthogonal weight of O12).
    for w in (320, 640, 1280):
        sk = np.zeros((50, w), dtype=np.uint8)
        sk[25, :] = 255
        assert calculate_root_length(sk) == pytest.approx(0.98 * FOV_W_MM * (w - 1) / w, rel=1e-9)


# ---------------- Area (resolution-independent, R5 corrected F-002) ----------------


@pytest.mark.parametrize("n_px", [1, 100, 200, 1000])
def test_area_scales_with_pixel_count_at_fixed_resolution(n_px):
    w = h = 40
    m = np.zeros((h, w), dtype=np.uint8)
    m.ravel()[:n_px] = 255
    expected = n_px * (FOV_W_MM / w) * (FOV_H_MM / h)
    assert calculate_root_area(m) == pytest.approx(expected, rel=1e-9)


def test_area_resolution_invariant_full_mask():
    # A fully-root mask always measures the whole FOV area, at ANY resolution.
    # This is the core F-002 fix: previously a non-640x480 mask was wrong by the
    # square of the resolution ratio.
    for h, w in [(480, 640), (960, 1280), (240, 320)]:
        m = np.full((h, w), 255, dtype=np.uint8)
        assert calculate_root_area(m) == pytest.approx(FOV_W_MM * FOV_H_MM, rel=1e-9)


# ---------------- Filename parsing ----------------


def test_parse_image_name_full():
    info = parse_image_name("scan_T005_L030_2024.03.15_143022_fake.png")
    assert info == {
        "original_name": "scan_T005_L030_2024.03.15_143022_fake.png",
        "tube_number": 5,
        "length_position": 30,
        "date": "2024.03.15",
        "time": "14:30:22",
    }


def test_parse_image_name_missing_fields():
    info = parse_image_name("nothing_here.png")
    assert info["tube_number"] is None
    assert info["date"] is None


# ---------------- Skeleton vectorization topology ----------------


def test_vectorize_cross_topology_endpoints():
    cr = np.zeros((11, 11), dtype=np.uint8)
    cr[5, 1:10] = 255
    cr[1:10, 5] = 255
    topo = vectorize_skeleton(cr, simplify_epsilon=1.0)
    assert sorted(topo.endpoints) == [(1, 5), (5, 1), (5, 9), (9, 5)]
    assert len(topo.polylines) >= 4


def test_skeleton_model_undo_redo_roundtrip():
    model = SkeletonCorrectionModel()
    model.set_empty((30, 30))
    model.push_undo()
    model.draw_polyline([(2, 2), (20, 20)], thickness=2)
    drawn = int((model.mask == 255).sum())
    assert drawn > 0
    assert model.undo() is True
    assert int((model.mask == 255).sum()) == 0
    assert model.redo() is True
    assert int((model.mask == 255).sum()) == drawn


def test_undo_stack_is_bounded():
    model = SkeletonCorrectionModel()
    model.set_empty((10, 10))
    for _ in range(model.max_stack_size + 20):
        model.push_undo()
    assert len(model._undo_stack) <= model.max_stack_size


# ---------------- CSV schema (length pipeline) ----------------


def test_length_row_schema(tmp_path):
    import cv2

    # length_row reads from disk; write a binary image it will threshold/skeletonize
    img = np.zeros((20, 120), dtype=np.uint8)
    img[10, 0:40] = 255
    p = tmp_path / "x_T1_L2_2024.01.02_010203_fake.png"
    cv2.imwrite(str(p), img)
    row = length_row(p.name, str(p))
    assert set(row.keys()) >= {"Image", "Tube", "Position", "Date", "Time", "Length (mm)"}
    assert row["Tube"] == 1 and row["Position"] == 2


# ---------------- DataProcessor ----------------


def test_data_processor_loads_and_filters(tmp_path):
    csv_path = tmp_path / "root_lengths.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["Image", "Tube", "Position", "Date", "Time", "Length (mm)"]
        )
        w.writeheader()
        w.writerow(
            {
                "Image": "a",
                "Tube": 1,
                "Position": 10,
                "Date": "2024.01.01",
                "Time": "00:00:01",
                "Length (mm)": 5.5,
            }
        )
        w.writerow(
            {
                "Image": "b",
                "Tube": 2,
                "Position": 20,
                "Date": "2024.01.02",
                "Time": "00:00:02",
                "Length (mm)": 7.0,
            }
        )
    dp = DataProcessor(str(csv_path))
    assert list(dp.get_unique_tubes()) == [1, 2]
    assert len(dp.get_unique_positions()) == 2


def test_data_processor_bad_path_returns_empty():
    dp = DataProcessor("does_not_exist_12345.csv")
    assert dp.df.empty


def test_data_processor_area_loads_utf8_csv(tmp_path):
    """Area CSVs are written UTF-8 with a superscript-2 header; must not read as latin-1."""
    from app.config import AREA_CSV_HEADERS
    from app.data_processing.data_processor_area import DataProcessorArea

    csv_path = tmp_path / "root_areas.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AREA_CSV_HEADERS)
        w.writeheader()
        w.writerow(
            {
                "Image": "img",
                "Tube": 1,
                "Position": 2,
                "Date": "2024.01.01",
                "Time": "00:00:01",
                "Area (mm²)": 3.25,
                "Error": "",
            }
        )
    dp = DataProcessorArea(str(csv_path))
    assert not dp.df.empty
    assert list(dp.get_unique_tubes()) == [1]
    assert dp.get_unique_positions() == [2]

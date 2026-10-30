"""The metrics bar on a prediction named in another case than its photo (O7).

Photo ``A.jpg`` with ``skeletons/a_fake.png`` and ``mask/a.png``; ``C.jpg`` named exactly.
Measure names a row by its prediction's stem (``a``), and the bar looks the Library's
stem (``A``) up through the case fallback. Its freshness and its "measured from" file
follow the file Measure read (``a_fake.png``), not one rebuilt from the Library stem, which
is absent on a case-sensitive disk and has no entry in the record of the inputs. On a
case-sensitive folder (``fsutil`` on Windows, skipped where that is not possible; the
ubuntu CI is case-sensitive), a second prediction named exactly like the image outdates the
value: the next Measure reads that one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import cv2
import numpy as np
import pytest

from app.calibration import load_project_calibration
from app.data_processing import measurement_index
from app.data_processing.measurement_index import MeasurementIndex
from app.inference.input_split import split_inputs
from app.inference.measure_inputs import inputs_record_path
from app.inference.measure_run import AREA, LENGTH, MeasureRun, scan_png_files
from app.project_layout import SKELETON_SUFFIX, ProjectLayout


def _skeleton(column: int | None = None) -> np.ndarray:
    plane = np.zeros((48, 64), np.uint8)
    if column is None:
        plane[20, 5:60] = 255
    else:
        plane[5:40, column] = 255
    return plane


def _project(root: str) -> ProjectLayout:
    """``A.jpg`` (predictions ``a_fake.png``, ``a.png``) and ``C.jpg`` (named exactly)."""
    layout = ProjectLayout(root)
    os.makedirs(layout.skeletons_dir)
    os.makedirs(layout.mask_dir)
    mask = np.zeros((48, 64), np.uint8)
    mask[10:30, 10:40] = 255
    for stem in ("A", "C"):
        cv2.imwrite(os.path.join(root, stem + ".jpg"), np.full((48, 64, 3), 128, np.uint8))
    for stem in ("a", "C"):
        cv2.imwrite(os.path.join(layout.skeletons_dir, stem + SKELETON_SUFFIX), _skeleton())
        cv2.imwrite(os.path.join(layout.mask_dir, stem + ".png"), mask)
    old = time.time_ns() - 300 * 10**9  # well before the CSVs
    for folder in (layout.skeletons_dir, layout.mask_dir):
        for entry in os.scandir(folder):
            os.utime(entry.path, ns=(old, old))
    return layout


def _measure(layout: ProjectLayout) -> list[str]:
    """Both metrics measured as the Measure handler builds its runs; the CSV paths."""
    csvs = []
    for spec, folder, scan, gt_folder in (
        (
            LENGTH,
            layout.skeletons_dir,
            lambda d: (layout.scan_skeletons(d), []),
            layout.gt_skeletons_dir,
        ),
        (AREA, layout.mask_dir, scan_png_files, layout.gt_mask_dir),
    ):
        images, shadowed = scan(folder)
        gt_files, gt_shadowed = scan_png_files(gt_folder)
        photos = layout.scan_images()
        run = MeasureRun(
            spec,
            split_inputs(images, photos, shadowed).kept,
            folder,
            project_root=layout.root,
            gt_files=split_inputs(gt_files, photos, gt_shadowed).kept,
            photos=photos,
            calibration=load_project_calibration(layout.root),
        )
        run.compute()
        assert sorted(row["Image"] for row in run.rows) == ["C", "a"]  # the predictions' stems
        csvs.append(run.write())
    return csvs


def _index(layout: ProjectLayout) -> MeasurementIndex:
    index = MeasurementIndex(layout.root)
    index.prewarm()
    return index


def test_a_case_variant_prediction_is_current_right_after_measure(tmp_path):
    layout = _project(str(tmp_path / "p"))
    _measure(layout)
    index = _index(layout)

    measured = index.lookup("A", load=False)
    assert measured.length_mm is not None and measured.area_mm2 is not None
    assert index.outdated("A") == {"length": False, "area": False}
    assert index.measured_from("A") == {
        "length": ("skeletons/a" + SKELETON_SUFFIX, "prediction"),
        "area": ("mask/a.png", "prediction"),
    }
    assert index.outdated("C") == {"length": False, "area": False}


def test_a_changed_case_variant_prediction_outdates_its_own_metric(tmp_path):
    layout = _project(str(tmp_path / "p"))
    _measure(layout)
    index = _index(layout)
    cv2.imwrite(os.path.join(layout.skeletons_dir, "a" + SKELETON_SUFFIX), _skeleton(30))

    assert index.outdated("A") == {"length": True, "area": False}


def test_an_exact_named_row_costs_no_extra_file_check(tmp_path, monkeypatch):
    layout = _project(str(tmp_path / "p"))
    _measure(layout)
    index = _index(layout)
    calls = []
    real = os.path.samefile
    monkeypatch.setattr(
        measurement_index.os.path, "samefile", lambda *a: calls.append(a) or real(*a)
    )

    assert index.outdated("C") == {"length": False, "area": False}
    assert calls == []  # the row is named like the image: no second name to look for


def _case_sensitive(folder: str) -> bool:
    probe = os.path.join(folder, "Probe")
    with open(probe, "wb"):
        pass
    try:
        return not os.path.exists(os.path.join(folder, "probe"))
    finally:
        os.remove(probe)


@pytest.fixture
def case_sensitive_root(tmp_path) -> str:
    root = str(tmp_path / "cs")
    os.makedirs(root)
    if sys.platform == "win32":
        subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", root, "enable"],
            capture_output=True,
            check=False,
        )
    if not _case_sensitive(root):
        pytest.skip("needs a case-sensitive folder (fsutil on Windows; the ubuntu CI)")
    return root


def test_without_a_record_a_changed_case_variant_is_outdated(case_sensitive_root):
    layout = _project(case_sensitive_root)
    for csv_path in _measure(layout):
        os.remove(inputs_record_path(csv_path))  # by file time, as a CSV from before R5
    later = time.time_ns() + 120 * 10**9
    for path in (
        os.path.join(layout.skeletons_dir, "a" + SKELETON_SUFFIX),
        os.path.join(layout.mask_dir, "a.png"),
    ):
        os.utime(path, ns=(later, later))

    assert _index(layout).outdated("A") == {"length": True, "area": True}


def test_a_second_prediction_named_like_the_image_outdates_the_value(case_sensitive_root):
    layout = _project(case_sensitive_root)
    _measure(layout)
    # Measure would read this one now (split_inputs keeps the name like the image).
    cv2.imwrite(os.path.join(layout.skeletons_dir, "A" + SKELETON_SUFFIX), _skeleton(30))
    index = _index(layout)

    assert index.outdated("A") == {"length": True, "area": False}
    assert index.measured_from("A")["length"] == ("skeletons/a" + SKELETON_SUFFIX, "prediction")

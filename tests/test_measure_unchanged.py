"""``MeasureRun.unchanged()``: nothing that decides the CSV changed since it was written.

GT-first projects record the GT file of an image and, for a used GT file, not its prediction
(shadowed); the field of view and the length estimator of the CSV's sidecar must match too.
"""

from __future__ import annotations

import json
import os

import cv2
import numpy as np
import pytest

from app import calibration, provenance
from app.calibration import ProjectCalibration
from app.config import Calibration
from app.inference.measure_run import AREA, LENGTH, MeasureRun, scan_png_files
from app.project_layout import ProjectLayout

N = 4


def _line(i: int, offset: int = 0) -> np.ndarray:
    img = np.zeros((64, 80), np.uint8)
    cv2.line(img, (0, i), (79, 40 + i + offset), 255, 2)
    return img


def _stem(i: int) -> str:
    return f"x_T{i + 1}_L2_2024.01.02_01020{i}_001"


def _write(path, img) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)


def _project(root, kind: str) -> ProjectLayout:
    """N photos with a prediction each (mask/ or skeletons/ *_fake.png)."""
    for i in range(N):
        _write(root / f"{_stem(i)}.jpg", _line(i))
        if kind == "area":
            _write(root / "mask" / f"{_stem(i)}.png", _line(i))
        else:
            _write(root / "skeletons" / f"{_stem(i)}_fake.png", _line(i))
    return ProjectLayout(str(root))


def _gt_path(root, kind: str, i: int):
    folder = root / "gt" / ("mask" if kind == "area" else "skeletons")
    return folder / f"{_stem(i)}.png"


def _run(root, kind: str, *, fov=None) -> MeasureRun:
    layout = ProjectLayout(str(root))
    if kind == "area":
        spec, folder = AREA, layout.mask_dir
        images, gt = scan_png_files(folder)[0], scan_png_files(layout.gt_mask_dir)[0]
    else:
        spec, folder = LENGTH, layout.skeletons_dir
        images, gt = layout.scan_skeletons(folder), scan_png_files(layout.gt_skeletons_dir)[0]
    kwargs = {}
    if fov is not None:
        kwargs["calibration"] = ProjectCalibration(Calibration(*fov), "project", "test")
    return MeasureRun(
        spec,
        images,
        folder,
        project_root=str(root),
        gt_files=gt,
        photos=layout.scan_images(),
        **kwargs,
    )


def _measured(root, kind: str, **kwargs) -> MeasureRun:
    run = _run(root, kind, **kwargs)
    run.compute()
    run.write()
    return run


@pytest.fixture(params=["area", "length"])
def kind(request):
    return request.param


@pytest.fixture
def gt_project(tmp_path, kind):
    """Image 0 has a valid GT file (used)."""
    _project(tmp_path, kind)
    _write(_gt_path(tmp_path, kind, 0), _line(0, offset=5))
    return tmp_path


def test_a_used_gt_file_and_its_shadowed_prediction_are_unchanged(gt_project, kind):
    run = _measured(gt_project, kind)
    assert run.sources[_stem(0)] == "gt"
    assert _run(gt_project, kind).unchanged() is True


def test_a_skipped_gt_file_is_unchanged(tmp_path, kind):
    _project(tmp_path, kind)
    _write(_gt_path(tmp_path, kind, 0), np.zeros((10, 12), np.uint8))  # wrong size
    run = _measured(tmp_path, kind)
    assert run.sources[_stem(0)] == "prediction"
    assert _run(tmp_path, kind).unchanged() is True


def test_a_project_without_gt_is_unchanged(tmp_path, kind):
    _project(tmp_path, kind)
    _measured(tmp_path, kind)
    assert _run(tmp_path, kind).unchanged() is True


def _prediction(root, kind, i):
    if kind == "area":
        return root / "mask" / f"{_stem(i)}.png"
    return root / "skeletons" / f"{_stem(i)}_fake.png"


def _repaint_used_gt(root, kind):
    _write(_gt_path(root, kind, 0), _line(0, offset=9))


def _delete_used_gt(root, kind):
    os.remove(_gt_path(root, kind, 0))


def _add_gt_for_another_image(root, kind):
    _write(_gt_path(root, kind, 2), _line(2, offset=5))


def _repaint_prediction_without_gt(root, kind):
    _write(_prediction(root, kind, 3), _line(3, offset=9))


def _add_a_prediction(root, kind):
    i = N
    _write(root / f"{_stem(i)}.jpg", _line(i))
    _write(_prediction(root, kind, i), _line(i))


def _add_a_gt_only_image(root, kind):
    i = N
    _write(root / f"{_stem(i)}.jpg", _line(i))
    _write(_gt_path(root, kind, i), _line(i, offset=5))


def _delete_a_prediction_without_gt(root, kind):
    os.remove(_prediction(root, kind, 3))


@pytest.mark.parametrize(
    "change",
    [
        _repaint_used_gt,
        _delete_used_gt,
        _add_gt_for_another_image,
        _repaint_prediction_without_gt,
        _add_a_prediction,
        _add_a_gt_only_image,
        _delete_a_prediction_without_gt,
    ],
)
def test_a_change_to_the_inputs_is_not_unchanged(gt_project, kind, change):
    _measured(gt_project, kind)
    change(gt_project, kind)
    assert _run(gt_project, kind).unchanged() is False


# ------------------------------------------------------------------ field of view / method
def test_another_field_of_view_is_not_unchanged(tmp_path, kind):
    _project(tmp_path, kind)
    _measured(tmp_path, kind, fov=(18.0, 13.5))
    assert _run(tmp_path, kind, fov=(18.0, 13.5)).unchanged() is True
    assert _run(tmp_path, kind, fov=(20.0, 13.5)).unchanged() is False


def _sidecar_edit(run: MeasureRun, edit) -> None:
    path = provenance.sidecar_path(run.csv_path)
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    edit(record)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


def test_another_length_estimator_is_not_unchanged(tmp_path):
    _project(tmp_path, "length")
    run = _measured(tmp_path, "length")
    assert run.spec.parameters["estimator"] == calibration.LENGTH_METHOD
    assert _run(tmp_path, "length").unchanged() is True
    _sidecar_edit(run, lambda r: r["parameters"].update(estimator="an earlier method"))
    assert _run(tmp_path, "length").unchanged() is False


def test_the_estimator_does_not_matter_for_area(tmp_path):
    _project(tmp_path, "area")
    run = _measured(tmp_path, "area")
    _sidecar_edit(run, lambda r: r["parameters"].update(estimator="anything"))
    assert _run(tmp_path, "area").unchanged() is True


def test_a_csv_without_a_sidecar_keeps_the_input_rule(tmp_path, kind):
    _project(tmp_path, kind)
    run = _measured(tmp_path, kind)
    os.remove(provenance.sidecar_path(run.csv_path))
    assert _run(tmp_path, kind).unchanged() is True
    _write(_prediction(tmp_path, kind, 0), _line(0, offset=9))
    assert _run(tmp_path, kind).unchanged() is False


def test_a_sidecar_without_a_calibration_keeps_the_input_rule(tmp_path, kind):
    _project(tmp_path, kind)
    run = _measured(tmp_path, kind)
    _sidecar_edit(run, lambda r: r.pop("calibration", None))
    assert _run(tmp_path, kind).unchanged() is True

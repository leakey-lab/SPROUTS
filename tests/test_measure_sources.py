"""WG-T8 (O1): which file Measure reads per image, GT first, and the optional ``Source`` column.

``app.inference.measure_sources`` resolves, per image, the GT file (``gt/skeletons/<stem>.png``
for length, ``gt/mask/<stem>.png`` for area) when it is valid, else the prediction
(``skeletons/<stem>_fake.png``, ``mask/<stem>.png``). A GT skeleton is not used when it does
not decode, is not the photo's size, or equals the prediction as Correct opens it; a GT mask
is not used when it does not decode or is not the photo's size, and is used whatever its
pixels otherwise. ``MeasurementIndex`` reads the ``Source`` column
when a CSV has it. Nothing is wired in this commit.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pytest

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS, SKELETON_OUTPUT_SIZE
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.editing.thinning import thin_raster
from app.inference import measure_sources
from app.inference.measure_sources import (
    GT,
    PREDICTION,
    SIZE,
    UNEDITED,
    UNREADABLE,
    MeasureSource,
    decide_area_source,
    decide_length_source,
    resolve_area_sources,
    resolve_length_sources,
)
from app.inference.metrics import area_row, length_row, write_metric_csv
from app.masks.store import write_mask_file
from app.project_layout import ProjectLayout
from app.provenance import file_sha256

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHOTO_SIZE = (640, 480)  # (w, h)
#: p prediction only, g prediction + valid GT, o GT only, s GT skeleton of the prediction's
#: size, u GT skeleton equal to the prediction as opened, x undecodable GT, m GT mask equal
#: to the prediction mask.
STEMS = ("p", "g", "o", "s", "u", "x", "m")
NOT_A_PNG = b"this is not an image"


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _write_png(path: str, gray: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", gray)
    assert ok
    _write(path, buf.tobytes())


def write_photo(layout: ProjectLayout, stem: str, size: tuple[int, int] = PHOTO_SIZE) -> str:
    """A JPEG photo ``<root>/<stem>.jpg`` of ``size`` ``(w, h)`` (640 x 480)."""
    w, h = size
    img = np.full((h, w, 3), 90, np.uint8)
    cv2.circle(img, (w // 2, h // 2), 100, (160, 170, 180), -1)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path = os.path.join(layout.root, stem + ".jpg")
    _write(path, buf.tobytes())
    return path


def prediction_skeleton() -> np.ndarray:
    """A generated skeleton: a soft 3 px line on black at ``SKELETON_OUTPUT_SIZE``."""
    w, h = SKELETON_OUTPUT_SIZE
    gray = np.zeros((h, w), np.uint8)
    cv2.line(gray, (20, 30), (w - 40, h - 30), 255, 3)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def gt_skeleton(size: tuple[int, int] = PHOTO_SIZE) -> np.ndarray:
    """A hand-traced 1 px skeleton at ``size`` ``(w, h)``, unlike the prediction."""
    w, h = size
    gray = np.zeros((h, w), np.uint8)
    cv2.line(gray, (w // 10, h - h // 8), (w - w // 12, h // 8), 255, 1)
    return gray


def prediction_mask() -> np.ndarray:
    w, h = PHOTO_SIZE
    gray = np.zeros((h, w), np.uint8)
    cv2.rectangle(gray, (100, 120), (300, 200), 255, -1)
    return gray


def write_gt_skeleton(layout: ProjectLayout, stem: str, gray: np.ndarray | None = None) -> str:
    path = layout.gt_skeleton_path(stem)
    _write_png(path, gt_skeleton() if gray is None else gray)
    return path


def write_gt_mask(layout: ProjectLayout, stem: str, on: np.ndarray | None = None) -> str:
    """A GT mask as Correct saves it: white on transparent RGBA at the photo's size."""
    if on is None:
        on = np.zeros(PHOTO_SIZE[::-1], bool)
        on[300:400, 50:250] = True
    path = layout.gt_mask_path(stem)
    write_mask_file(path, on, durable=False)
    return path


def make_gt_project(root: str) -> tuple[ProjectLayout, dict[str, str]]:
    """The project of :data:`STEMS`: ``(layout, images)``."""
    layout = ProjectLayout(root)
    images = {stem: write_photo(layout, stem) for stem in STEMS}
    skeleton = prediction_skeleton()
    mask = prediction_mask()
    for stem in STEMS:
        if stem == "o":
            continue
        _write_png(layout.skeleton_path(stem), skeleton)
        _write_png(layout.mask_path(stem), mask)
    write_gt_skeleton(layout, "g")
    write_gt_skeleton(layout, "o")
    write_gt_skeleton(layout, "s", gt_skeleton(SKELETON_OUTPUT_SIZE))
    write_gt_skeleton(layout, "u", thin_raster(skeleton, PHOTO_SIZE))
    _write(layout.gt_skeleton_path("x"), NOT_A_PNG)
    write_gt_mask(layout, "o")
    write_gt_mask(layout, "m", mask > 127)
    _write(layout.gt_mask_path("x"), NOT_A_PNG)
    return layout, images


@pytest.fixture
def gt_project(tmp_path) -> tuple[ProjectLayout, dict[str, str]]:
    return make_gt_project(str(tmp_path))


def _length(layout: ProjectLayout, images: dict[str, str]):
    return resolve_length_sources(
        layout, images, layout.scan_skeletons(), layout.scan_gt_skeletons()
    )


def _area(layout: ProjectLayout, images: dict[str, str]):
    return resolve_area_sources(
        layout, images, layout.scan_masks(extensions=(".png",)), layout.scan_gt_masks()
    )


# ------------------------------------------------------------------ length
def test_gt_wins_when_valid_and_a_gt_only_stem_gets_a_row(gt_project):
    layout, images = gt_project
    sources, report = _length(layout, images)
    assert sources["g"] == MeasureSource(
        "g", layout.gt_skeleton_path("g"), GT, layout.gt_skeleton_path("g")
    )
    assert sources["o"].source == GT and sources["p"] == MeasureSource(
        "p", layout.skeleton_path("p"), PREDICTION
    )
    assert report.folder == "gt/skeletons" and set(report.files) == {"g", "o"}
    assert report.n_used == 2
    assert report.files["g"] == file_sha256(layout.gt_skeleton_path("g"))
    assert set(sources) == set(STEMS)  # one row per image: predictions and GT files


def test_wrong_size_unedited_and_unreadable_gt_skeletons_fall_back_with_a_reason(gt_project):
    layout, images = gt_project
    sources, report = _length(layout, images)
    assert (sources["s"].source, sources["s"].reason, sources["s"].path) == (
        PREDICTION,
        SIZE,
        layout.skeleton_path("s"),
    )
    assert sources["s"].gt_path == layout.gt_skeleton_path("s")  # the note names it
    assert sources["u"].reason == UNEDITED and sources["x"].reason == UNREADABLE
    assert set(report.skipped) == {("s", SIZE), ("u", UNEDITED), ("x", UNREADABLE)}
    assert report.skipped == (("s", SIZE), ("u", UNEDITED), ("x", UNREADABLE))  # by stem


def test_an_emptied_gt_skeleton_over_a_prediction_is_used(gt_project):
    # O9: clearing every root is an edit; its length is 0, not the prediction's.
    layout, images = gt_project
    write_gt_skeleton(layout, "p", np.zeros(PHOTO_SIZE[::-1], np.uint8))
    sources, _ = _length(layout, images)
    assert (sources["p"].source, sources["p"].path) == (GT, layout.gt_skeleton_path("p"))


def test_an_emptied_gt_skeleton_without_a_prediction_is_used_and_measures_zero(gt_project):
    # No prediction, so nothing it could equal: clearing every root is an edit (O9), and
    # length gives the image a row as area does for its emptied mask.
    layout, images = gt_project
    images["e"] = write_photo(layout, "e")
    path = write_gt_skeleton(layout, "e", np.zeros(PHOTO_SIZE[::-1], np.uint8))
    sources, report = _length(layout, images)
    assert sources["e"] == MeasureSource("e", path, GT, path)
    assert "e" in report.files and all(stem != "e" for stem, _ in report.skipped)
    assert length_row("e", path)[LENGTH_CSV_HEADERS[5]] == 0


def test_a_gt_skeleton_whose_photo_cannot_be_read_is_unreadable(gt_project):
    layout, images = gt_project
    _write(images["g"], NOT_A_PNG)  # the photo of g is damaged
    del images["o"]  # the photo of o is gone
    sources, report = _length(layout, images)
    assert (sources["g"].source, sources["g"].reason) == (PREDICTION, UNREADABLE)
    assert "o" not in sources and ("o", UNREADABLE) in report.skipped


def test_the_photo_is_the_exact_stem_else_the_first_image_of_its_key(tmp_path):
    layout = ProjectLayout(str(tmp_path))
    big = write_photo(layout, "big")  # 640 x 480, the size of the GT skeletons below
    small = write_photo(layout, "small", (320, 240))
    write_gt_skeleton(layout, "K")
    write_gt_skeleton(layout, "Zz")
    # As a case-sensitive disk can list them: the exact stem wins, else the first of the key
    # (split_inputs keeps the first image of a key too).
    images = {"K": big, "k": small, "ZZ": big, "zz": small}
    sources, report = resolve_length_sources(layout, images, {}, layout.scan_gt_skeletons())
    assert (sources["K"].source, sources["Zz"].source) == (GT, GT)
    assert report.skipped == ()


def test_an_unsplit_scan_with_two_files_of_one_image_is_refused(tmp_path):
    layout = ProjectLayout(str(tmp_path))
    with pytest.raises(ValueError, match="split_inputs"):
        resolve_area_sources(layout, {}, {"a": "mask/a.png", "A": "mask/A.png"}, {})
    with pytest.raises(ValueError, match="split_inputs"):
        resolve_length_sources(layout, {}, {}, {"b": "gt/b.png", "B": "gt/B.png"})


def test_images_of_names_each_image_as_its_row_will(tmp_path):
    """The job's images before compute(): an image with a prediction is named and found by
    it (its GT file only differs in case), a GT-only image by its GT file; the scans must be
    split (two files of one image are refused, the first two named in order)."""
    predictions = {"Img1": "skeletons/Img1_fake.png", "img2": "skeletons/img2_fake.png"}
    gt_files = {"IMG1": "gt/skeletons/IMG1.png", "only_gt": "gt/skeletons/only_gt.png"}
    assert measure_sources.images_of(predictions, gt_files) == {
        "Img1": "skeletons/Img1_fake.png",
        "img2": "skeletons/img2_fake.png",
        "only_gt": "gt/skeletons/only_gt.png",
    }
    assert measure_sources.images_of(predictions, {}) == predictions
    assert measure_sources.images_of({}, gt_files) == gt_files
    with pytest.raises(ValueError, match="'a' and 'A'"):
        measure_sources.images_of({"a": "a.png", "b": "b.png", "A": "A.png"}, {})
    with pytest.raises(ValueError, match="'g' and 'G'"):
        measure_sources.images_of(predictions, {"g": "gt/g.png", "G": "gt/G.png"})


def test_the_report_is_read_only(gt_project):
    layout, images = gt_project
    _, report = _length(layout, images)
    with pytest.raises(TypeError):
        report.files["z"] = "0" * 64


# ------------------------------------------------------------------ one image at a time
def test_one_image_is_decided_from_the_gt_bytes_read_once(gt_project, monkeypatch):
    layout, images = gt_project
    opened: list[str] = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        opened.append(os.path.normcase(os.fspath(path)))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(measure_sources, "open", counting_open, raising=False)
    gt = layout.gt_skeleton_path("g")
    decision = decide_length_source("g", layout.skeleton_path("g"), gt, images["g"])
    mask_gt = layout.gt_mask_path("m")
    mask_decision = decide_area_source("m", layout.mask_path("m"), mask_gt, images["m"])
    assert opened == [os.path.normcase(gt), os.path.normcase(mask_gt)]  # once each

    for d, path in ((decision, gt), (mask_decision, mask_gt)):
        with real_open(path, "rb") as fh:
            data = fh.read()
        st = os.stat(path)
        assert d.source == MeasureSource(d.stem, path, GT, path) and d.reason == ""
        assert d.data == data  # handed on: the measure pool need not read it again
        assert d.fingerprint == (st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())

    fallback = decide_length_source("s", layout.skeleton_path("s"), None, images["s"])
    assert fallback.source == MeasureSource("s", layout.skeleton_path("s"), PREDICTION)
    gt_s = layout.gt_skeleton_path("s")
    rejected = decide_length_source("s", layout.skeleton_path("s"), gt_s, images["s"])
    with real_open(gt_s, "rb") as fh:
        data = fh.read()
    st = os.stat(gt_s)
    # not used: no bytes handed on, but its fingerprint (Measure records every GT file read)
    assert (rejected.reason, rejected.data) == (SIZE, None)
    assert rejected.fingerprint == (st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())
    gone = decide_area_source("q", None, os.path.join(layout.gt_mask_dir, "q.png"), images["m"])
    assert (gone.source, gone.reason, gone.fingerprint) == (None, UNREADABLE, None)


def test_every_image_is_decided_through_the_given_map(gt_project):
    layout, images = gt_project
    calls = []

    def recording_map(fn, *columns):
        calls.append((fn, [list(column) for column in columns]))
        return map(fn, *columns)

    serial = _length(layout, images)
    args = (layout, images, layout.scan_skeletons(), layout.scan_gt_skeletons())
    with ThreadPoolExecutor(4) as pool:
        assert resolve_length_sources(*args, mapper=pool.map) == serial
    assert resolve_length_sources(*args, mapper=recording_map) == serial
    fn, columns = calls[0]
    # one call, one column per argument, every image (Measure measures each in its task)
    assert fn is decide_length_source and columns[0] == ["g", "m", "o", "p", "s", "u", "x"]
    assert len(calls) == 1 and len(columns) == 4

    area_args = (layout, images, layout.scan_masks(extensions=(".png",)), layout.scan_gt_masks())
    assert resolve_area_sources(*area_args, mapper=recording_map) == _area(layout, images)
    fn, columns = calls[1]
    assert fn is decide_area_source and columns[0] == ["g", "m", "o", "p", "s", "u", "x"]


def test_an_invalid_gt_file_without_a_prediction_gets_no_row_and_is_recorded(gt_project):
    layout, images = gt_project
    _write(layout.gt_skeleton_path("o"), NOT_A_PNG)
    _write(layout.gt_mask_path("o"), NOT_A_PNG)
    sources, report = _length(layout, images)
    assert "o" not in sources and ("o", UNREADABLE) in report.skipped
    area_sources, area_report = _area(layout, images)
    assert "o" not in area_sources and ("o", UNREADABLE) in area_report.skipped


# ------------------------------------------------------------------ area
def test_a_gt_mask_is_used_whatever_its_pixels_and_an_unreadable_one_is_not(gt_project):
    layout, images = gt_project
    write_gt_mask(layout, "p", np.zeros(PHOTO_SIZE[::-1], bool))  # cleared: area 0 (O9)
    sources, report = _area(layout, images)
    assert sources["m"].source == GT and sources["m"].reason == ""
    assert sources["x"].reason == UNREADABLE
    assert (sources["x"].source, sources["x"].path) == (PREDICTION, layout.mask_path("x"))
    assert (sources["p"].source, sources["p"].path) == (GT, layout.gt_mask_path("p"))
    assert sources["g"] == MeasureSource("g", layout.mask_path("g"), PREDICTION)
    assert report.folder == "gt/mask" and set(report.files) == {"m", "o", "p"}
    assert report.skipped == (("x", UNREADABLE),)


def test_a_gt_mask_of_another_size_than_its_photo_is_not_used(gt_project):
    # Correct refuses such a mask ("Mask not shown"); the CSV must not report it as GT.
    layout, images = gt_project
    small = np.ones((240, 320), bool)  # 320 x 240; the photo is 640 x 480
    write_gt_mask(layout, "g", small)  # has a prediction: falls back to it
    write_gt_mask(layout, "o", small)  # GT only: no row
    sources, report = _area(layout, images)
    assert (sources["g"].source, sources["g"].reason, sources["g"].path) == (
        PREDICTION,
        SIZE,
        layout.mask_path("g"),
    )
    assert sources["g"].gt_path == layout.gt_mask_path("g")  # the note names it
    assert "o" not in sources
    assert ("g", SIZE) in report.skipped and ("o", SIZE) in report.skipped
    assert "g" not in report.files and "o" not in report.files
    lone = decide_area_source("o", None, layout.gt_mask_path("o"), images["o"])
    assert (lone.source, lone.reason, lone.data) == (None, SIZE, None)
    assert lone.fingerprint is not None  # the GT file read is still recorded


def test_a_photo_sized_gt_mask_is_used_and_an_emptied_one_measures_zero(gt_project):
    layout, images = gt_project
    write_gt_mask(layout, "p", np.zeros(PHOTO_SIZE[::-1], bool))  # emptied: an edit (O9)
    sources, report = _area(layout, images)
    assert (sources["p"].source, sources["p"].reason) == (GT, "")
    assert sources["o"].source == GT and sources["m"].source == GT
    assert ("p", SIZE) not in report.skipped and "p" in report.files
    assert area_row("p", layout.gt_mask_path("p"))[AREA_CSV_HEADERS[5]] == 0


def test_a_gt_mask_whose_photo_cannot_be_read_is_unreadable(gt_project):
    layout, images = gt_project
    _write(images["m"], NOT_A_PNG)  # the photo of m is damaged
    del images["o"]  # the photo of o is gone
    sources, report = _area(layout, images)
    assert (sources["m"].source, sources["m"].reason) == (PREDICTION, UNREADABLE)
    assert "o" not in sources and ("o", UNREADABLE) in report.skipped


# ------------------------------------------------------------------ names
def test_stems_compare_without_case(tmp_path):
    # gt/skeletons/G.png for image g.jpg resolves to one row
    layout = ProjectLayout(str(tmp_path))
    images = {"g": write_photo(layout, "g")}
    _write_png(layout.skeleton_path("g"), prediction_skeleton())
    gt_path = os.path.join(layout.gt_skeletons_dir, "G.png")
    _write_png(gt_path, gt_skeleton())
    _write_png(layout.mask_path("g"), prediction_mask())
    mask_gt = os.path.join(layout.gt_mask_dir, "G.png")
    write_mask_file(mask_gt, prediction_mask() > 0, durable=False)

    sources, report = _length(layout, images)
    assert list(sources) == ["g"] and sources["g"].source == GT
    assert os.path.normcase(sources["g"].path) == os.path.normcase(gt_path)
    assert list(report.files) == ["g"]
    area_sources, _ = _area(layout, images)
    assert list(area_sources) == ["g"] and area_sources["g"].source == GT


# ------------------------------------------------------------------ layering
def test_the_resolver_is_qt_free():
    code = (
        "import sys, app.inference.measure_sources\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6', 'torch')"
        " or m.startswith(('app.gui', 'app.handlers')))\n"
        "print(bad)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()
    assert out == "[]"


# ------------------------------------------------------------------ the Source column
def _csv(folder, name: str, headers: list[str], row: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_metric_csv([row], str(folder / name), headers)


def _row(column: str, value: float, **extra) -> dict:
    row = {"Image": "a", "Tube": None, "Position": None, "Date": None, "Time": None}
    return {**row, column: value, **extra}


def test_measurement_reads_an_optional_source_column(tmp_path):
    assert Measurement(18.77, 16.41, None) == Measurement(18.77, 16.41, None, None, None)

    old = tmp_path / "old"  # a CSV written before the Source column
    length_row = _row(LENGTH_CSV_HEADERS[5], 1.5)
    area_row = _row(AREA_CSV_HEADERS[5], 2.5)
    _csv(old / "skeletons", "root_lengths.csv", LENGTH_CSV_HEADERS, length_row)
    _csv(old / "mask", "root_areas.csv", AREA_CSV_HEADERS, area_row)
    index = MeasurementIndex(str(old))
    assert index.lookup("a").length_source is None
    assert index.lookup("a") == Measurement(1.5, 2.5, None)

    new = tmp_path / "new"
    length_row = _row(LENGTH_CSV_HEADERS[5], 1.5, Source="gt")
    area_row = _row(AREA_CSV_HEADERS[5], 2.5, Source="prediction")
    _csv(new / "skeletons", "root_lengths.csv", [*LENGTH_CSV_HEADERS, "Source"], length_row)
    _csv(new / "mask", "root_areas.csv", [*AREA_CSV_HEADERS, "Source"], area_row)
    index = MeasurementIndex(str(new))
    assert index.lookup("a").length_source == "gt"
    assert index.lookup("a") == Measurement(1.5, 2.5, None, "gt", "prediction")

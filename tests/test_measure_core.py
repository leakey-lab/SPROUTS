"""Qt-free Measure core: metric rows, CSV writing, cancellation and provenance.

Pins the hard contract that the Measure CSVs are byte-identical to the
pre-refactor pipeline (legacy reference implementation inlined below), plus
RB-03/FR-06 (atomic CSV), RB-06 (non-ASCII paths), FR-15 (token-anchored
names) and FR-02 (provenance sidecars).

The one intended difference is the length estimator (O12, W3-T8): the legacy Freeman
weights (1, sqrt(2)) became the corner-count estimator of Vossepoel & Smeulders (1982).
The reference computes it with ``tests/length_reference.py``, a pure-Python version that
shares no code with ``app/inference/metrics.py``; everything else is the legacy pipeline.
"""

from __future__ import annotations

import csv
import json
import os
import re

import cv2
import numpy as np
import pytest

from app import provenance
from app.config import AREA_CSV_HEADERS, CALIBRATION, LENGTH_CSV_HEADERS
from app.inference import measure_inputs, measure_run, metrics
from app.inference.measure_run import AREA, LENGTH, MeasureRun
from tests import length_reference
from tests.real_data import real_data_dir

# --------------------------------------------------------------------------- legacy reference
# Verbatim logic of the pre-refactor pipeline (metrics.py @ a1599e6): cv2.imread,
# first-match T/L regexes, open(..., "w") + csv.DictWriter.


def _legacy_parse(name):
    info = {"original_name": name, "tube_number": None, "length_position": None}
    info.update(date=None, time=None)
    if m := re.search(r"T(\d+)", name):
        info["tube_number"] = int(m.group(1))
    if m := re.search(r"L(\d+)", name):
        info["length_position"] = int(m.group(1))
    if m := re.search(r"(\d{4})\.(\d{2})\.(\d{2})", name):
        info["date"] = ".".join(m.groups())
    if m := re.search(r"_(\d{6})(?:_|$)", name):
        t = m.group(1)
        info["time"] = f"{t[:2]}:{t[2:4]}:{t[4:]}"
    return info


def _legacy_row(name, path, kind):
    from skimage.morphology import skeletonize

    value_col = LENGTH_CSV_HEADERS[5] if kind == "length" else AREA_CSV_HEADERS[5]
    try:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            what = "image" if kind == "length" else "mask"
            # O38 (W3-T7): the file name only
            raise FileNotFoundError(f"Could not read {what}: {os.path.basename(path)}")
        h, w = img.shape[:2]
        if kind == "length":
            _, b = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if np.mean(b) > 127:
                b = 255 - b
            s = skeletonize(b / 255) > 0
            mx, my = CALIBRATION.mm_per_px(w, h)
            # O12 (W3-T8): the corner-count estimator, from the independent reference
            value = length_reference.vs_length_mm(s, mx, my)
        else:
            _, b = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
            value = int(np.count_nonzero(b == 255)) * CALIBRATION.area_per_px_mm2(w, h)
        info = _legacy_parse(name)
        return {
            "Image": info["original_name"],
            "Tube": info["tube_number"],
            "Position": info["length_position"],
            "Date": info["date"],
            "Time": info["time"],
            value_col: round(value, 2),
        }
    except Exception as e:  # noqa: BLE001 - mirrors the legacy worker
        row = {"Image": name, "Tube": None, "Position": None, "Date": None, "Time": None}
        return row | {value_col: 0, "Error": str(e)}


def _legacy_csv(images, kind, out_path, *, source=True):
    """The legacy CSV of ``images``. Every input is a prediction: ``source`` adds the last
    column ``Source`` = ``prediction`` (WG-T8, O1); without it, the seven columns written
    before."""
    headers = LENGTH_CSV_HEADERS if kind == "length" else AREA_CSV_HEADERS
    if not source:
        headers = headers[:7]
    rows = sorted(
        ({**_legacy_row(n, p, kind), "Source": "prediction"} for n, p in images.items()),
        key=lambda r: (
            r.get("Tube") or float("inf"),
            r.get("Date") or "",
            r.get("Time") or "",
            r.get("Position") or float("inf"),
            r.get("Image") or "",  # tie order by image name (WX-T9)
        ),
    )
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})
    return out_path


# --------------------------------------------------------------------------- fixtures

_NAMES = [
    "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM",
    "24EFCAM1_T001_L045_2024.07.18_111529_001_SMM",
    "24EFCAM1_T012_L043_2024.07.18_101914_001_SMM",
    "24EFCAM2_T005_L056_2024.08.26_085019_003_SMM",
    "24EFCAM3_T100_L049_2024.08.07_103103_002_SMM",
    "24EFCAM1_T150_L038_2024.08.27_084641_003_SMM",
]


def _skeleton_image(seed: int, shape=(256, 341)) -> np.ndarray:
    """A deterministic grey image with diagonal + orthogonal strokes."""
    rng = np.random.default_rng(seed)
    img = np.zeros(shape, np.uint8)
    for _ in range(6):
        p1 = tuple(int(v) for v in rng.integers(0, shape[1], 2) % (shape[1], shape[0]))
        p2 = tuple(int(v) for v in rng.integers(0, shape[1], 2) % (shape[1], shape[0]))
        cv2.line(img, p1, p2, int(rng.integers(160, 256)), int(rng.integers(1, 4)))
    return img


def _write_png(path, img):
    ok, buf = cv2.imencode(".png", img)
    assert ok
    buf.tofile(str(path))  # works for non-ASCII paths


def _make_dataset(folder, kind, names=_NAMES):
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for i, name in enumerate(names):
        img = _skeleton_image(i)
        if kind == "area":
            img = cv2.dilate(img, np.ones((5, 5), np.uint8))
            p = folder / f"{name}.png"
        else:
            p = folder / f"{name}_fake.png"
        _write_png(p, img)
        images[name] = str(p)
    return images


# --------------------------------------------------------------------------- byte identity


@pytest.mark.parametrize("spec", [LENGTH, AREA], ids=["length", "area"])
def test_measure_csv_is_byte_identical_to_legacy_pipeline(tmp_path, spec):
    images = _make_dataset(tmp_path / "in", spec.kind)
    # plus a corrupt file and a name with no metadata (Error row / None fields)
    bad = tmp_path / "in" / "corrupt_T001_L001.png"
    bad.write_bytes(b"not a png")
    images["corrupt_T001_L001"] = str(bad)
    images["IMG_0001"] = images[_NAMES[0]]

    legacy = _legacy_csv(images, spec.kind, str(tmp_path / "legacy.csv"))
    out = tmp_path / "out"
    out.mkdir()
    run = MeasureRun(spec, images, str(out))
    run.compute()
    csv_path = run.write()

    assert os.path.basename(csv_path) == spec.csv_name
    with open(legacy, "rb") as a, open(csv_path, "rb") as b:
        assert b.read() == a.read()


REAL = real_data_dir()


@pytest.mark.skipif(REAL is None, reason="real dataset not present")
@pytest.mark.parametrize("spec", [LENGTH, AREA], ids=["length", "area"])
def test_real_sample_is_byte_identical_to_legacy_pipeline(tmp_path, spec):
    sub, suffix = ("skeletons", "_fake.png") if spec.kind == "length" else ("mask", ".png")
    folder = os.path.join(REAL, sub)
    names = sorted(e.name for e in os.scandir(folder) if e.name.endswith(suffix))
    sample = names[:: max(1, len(names) // 60)][:60]
    images = {n[: -len(suffix)]: os.path.join(folder, n) for n in sample}
    legacy = _legacy_csv(images, spec.kind, str(tmp_path / "legacy.csv"), source=False)
    run = MeasureRun(spec, images, str(tmp_path), gt_files={})  # GT-first; no gt/ there
    run.compute()
    with open(legacy, "rb") as a, open(run.write(), "rb") as b:
        legacy_lines, lines = a.read().split(b"\r\n"), b.read().split(b"\r\n")
    # WG-T8: the first seven columns byte for byte, and the eighth is prediction everywhere
    assert [line.rsplit(b",", 1)[0] for line in lines] == legacy_lines
    assert [line.rsplit(b",", 1)[-1] for line in lines] == (
        [b"Source"] + [b"prediction"] * len(images) + [b""]
    )


# --------------------------------------------------------------------------- RB-06


@pytest.mark.parametrize("spec", [LENGTH, AREA], ids=["length", "area"])
def test_non_ascii_folder_measures_same_values(tmp_path, spec):
    ascii_images = _make_dataset(tmp_path / "plain", spec.kind, _NAMES[:2])
    uni_images = _make_dataset(tmp_path / "Données_根系", spec.kind, _NAMES[:2])
    for name in _NAMES[:2]:
        a = spec.row(name, ascii_images[name])
        u = spec.row(name, uni_images[name])
        assert "Error" not in u
        assert u == a
        assert u[spec.value_column] > 0


# --------------------------------------------------------------------------- FR-15


@pytest.mark.parametrize(
    "name, tube, position",
    [
        ("PLOT12_T001_L005_2024.07.18_111618_001_SMM", 1, 5),
        ("EXPT2_T014_L033_2024.07.18_111618_001_SMM", 14, 33),
        ("CAL1_T001_L011_2024.07.18_111618_001_SMM", 1, 11),
    ],
)
def test_parse_image_name_uses_standalone_tokens(name, tube, position):
    info = metrics.parse_image_name(name)
    assert (info["tube_number"], info["length_position"]) == (tube, position)
    assert info["original_name"] == name
    assert info["date"] == "2024.07.18" and info["time"] == "11:16:18"


@pytest.mark.parametrize("name", _NAMES + ["scan_T005_L030_2024.03.15_143022_fake.png"])
def test_parse_image_name_matches_legacy_for_canonical_names(name):
    assert metrics.parse_image_name(name) == _legacy_parse(name)


# --------------------------------------------------------------------------- RB-03 / FR-06


def test_failed_csv_replace_leaves_previous_csv_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "root_lengths.csv"
    target.write_bytes(b"previous,complete\r\n1,2\r\n")

    def boom(src, dst):
        raise PermissionError(13, "Permission denied", dst)

    monkeypatch.setattr("app.io_utils.os.replace", boom)
    rows = [{"Image": "a", "Tube": 1, "Length (mm)": 1.0}]
    with pytest.raises(OSError):
        metrics.write_metric_csv(rows, str(target), LENGTH_CSV_HEADERS)
    assert target.read_bytes() == b"previous,complete\r\n1,2\r\n"
    assert sorted(os.listdir(tmp_path)) == ["root_lengths.csv"]


# --------------------------------------------------------------------------- RB-07


def test_run_metric_pool_stops_early_when_asked():
    calls = []

    def worker(name, path):
        calls.append(name)
        return {"Image": name}

    images = {str(i): "p" for i in range(400)}
    rows = metrics.run_metric_pool(images, worker, should_stop=lambda: len(calls) >= 5)
    assert len(rows) < len(images)


def test_an_error_in_a_worker_ends_the_pool_without_measuring_the_rest():
    """The error is raised once the running images end: the pending ones are not measured
    first (a Cancel meanwhile would go unheard), and it is the worker's own error."""
    import time

    calls = []

    def worker(name, path):
        calls.append(name)
        if name == "0":
            raise MemoryError("simulated")
        time.sleep(0.005)
        return {"Image": name}

    images = {str(i): "p" for i in range(400)}
    with pytest.raises(MemoryError, match="simulated"):
        metrics.run_metric_pool(images, worker)
    assert len(calls) < len(images) // 2


@pytest.mark.parametrize("total", [5, 255, 256, 1000, 4100])
def test_the_pool_gives_the_rows_and_progress_of_one_image_at_a_time(total):
    """Images run in tasks of several (fewer futures); the rows, their order and the
    progress values are those of measuring the images one by one."""
    import random
    import time

    def worker(name, path):
        if random.random() < 0.01:
            time.sleep(0.001)
        return {"Image": name, "Tube": int(name) % 7 or None, "Path": path}

    images = {f"{i:05d}": f"p{i}" for i in random.Random(total).sample(range(10 * total), total)}
    progress = []
    rows = metrics.run_metric_pool(images, worker, progress_cb=progress.append)
    serial = sorted((worker(name, path) for name, path in images.items()), key=metrics._sort_key)
    assert rows == serial
    expected, last = [], -1
    for done in range(1, total + 1):
        pct = int(done / total * 100)
        if pct != last:
            expected.append(pct)
            last = pct
    assert progress == expected


def test_cancelled_compute_keeps_no_rows(tmp_path):
    images = _make_dataset(tmp_path / "in", "length", _NAMES[:3])
    run = MeasureRun(LENGTH, images, str(tmp_path))
    run.compute(should_stop=lambda: True)
    assert run.rows is None


def test_compute_is_skipped_when_rows_are_already_known(tmp_path, monkeypatch):
    images = _make_dataset(tmp_path / "in", "length", _NAMES[:2])
    run = MeasureRun(LENGTH, images, str(tmp_path))
    run.compute()
    monkeypatch.setattr(measure_run, "run_metric_pool", pytest.fail)
    run.compute()  # retry path: rows reused, nothing recomputed
    assert len(run.rows) == 2


# --------------------------------------------------------------------------- FR-02


def test_write_adds_provenance_sidecar_without_touching_csv(tmp_path):
    images = _make_dataset(tmp_path / "skeletons", "length", _NAMES[:3])
    bad = tmp_path / "skeletons" / "x_fake.png"
    bad.write_bytes(b"")
    images["x"] = str(bad)
    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"), project_root=str(tmp_path))
    run.compute()
    csv_path = run.write()
    first = open(csv_path, "rb").read()

    sidecar = provenance.sidecar_path(csv_path)
    assert os.path.basename(sidecar) == "root_lengths.csv.provenance.json"
    record = json.loads(open(sidecar, encoding="utf-8").read())
    assert record["kind"] == "root_length"
    assert record["calibration"] == {
        "fov_width_mm": 18.0,
        "fov_height_mm": 13.5,
        "source": "default",
    }
    assert record["inputs"]["folder"] == "skeletons"
    assert record["inputs"]["n_images"] == 4
    assert record["inputs"]["names_sha256"] == provenance.names_digest(images)
    assert record["outputs"]["csv"] == "root_lengths.csv"
    assert record["outputs"]["columns"] == LENGTH_CSV_HEADERS
    assert record["outputs"]["n_rows"] == 4
    assert record["outputs"]["n_errors"] == 1
    assert "D:" not in json.dumps(record["inputs"]) and str(tmp_path) not in json.dumps(record)

    os.remove(sidecar)
    run.write()
    assert open(csv_path, "rb").read() == first


def test_sidecar_failure_does_not_fail_the_measurement(tmp_path, monkeypatch):
    images = _make_dataset(tmp_path / "in", "area", _NAMES[:1])

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(measure_run.provenance, "write_sidecar", broken)
    run = MeasureRun(AREA, images, str(tmp_path))
    run.compute()
    assert os.path.isfile(run.write())


def test_the_record_does_not_touch_the_csv(tmp_path, monkeypatch):
    """The ``<csv>.inputs.json`` step (R5) changes no CSV byte, and its failure fails
    nothing."""
    images = _make_dataset(tmp_path / "in", "area", _NAMES[:3])
    recorded = MeasureRun(AREA, images, str(tmp_path / "recorded"), project_root=str(tmp_path))
    os.makedirs(recorded.output_dir)
    recorded.compute()
    with_record = open(recorded.write(), "rb").read()
    assert os.path.isfile(measure_inputs.inputs_record_path(recorded.csv_path))

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(measure_inputs, "write_inputs_record", broken)
    plain = MeasureRun(AREA, images, str(tmp_path / "plain"), project_root=str(tmp_path))
    os.makedirs(plain.output_dir)
    plain.compute()
    without_record = open(plain.write(), "rb").read()
    assert not os.path.exists(measure_inputs.inputs_record_path(plain.csv_path))
    assert without_record == with_record
    # Measured again with the same bytes: the earlier run's record goes (it may differ).
    assert open(recorded.write(), "rb").read() == with_record
    assert not os.path.exists(measure_inputs.inputs_record_path(recorded.csv_path))


def test_rows_with_equal_sort_keys_come_out_by_image_name():
    """Tied rows (no metadata, error rows) are ordered by image name, not by folder-scan
    order (which differs per OS) or thread completion (WX-T9)."""
    import random
    import time

    def worker(name, path):
        time.sleep(random.random() / 500)
        return {"Image": name}

    images = {name: "" for name in ("img07", "img02", "img11", "IMG_0001")}
    for _ in range(3):
        rows = metrics.run_metric_pool(images, worker)
        assert [r["Image"] for r in rows] == sorted(images)


# --------------------------------------------------------------------------- M2: sizes


def _masks_of_sizes(folder, sizes):
    """One mask per ``(name, (rows, cols))``: a white block in a black image."""
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for name, shape in sizes:
        img = np.zeros(shape, np.uint8)
        img[4:20, 4:30] = 255
        _write_png(folder / f"{name}.png", img)
        images[name] = str(folder / f"{name}.png")
    return images


def test_the_sidecar_records_the_measured_sizes(tmp_path):
    images = _masks_of_sizes(
        tmp_path / "mask", [(_NAMES[0], (64, 80)), (_NAMES[1], (64, 80)), (_NAMES[2], (48, 96))]
    )
    run = MeasureRun(AREA, images, str(tmp_path / "mask"), project_root=str(tmp_path))
    run.compute()
    assert all("_size_px" in row for row in run.rows)
    csv_path = run.write()

    record = json.loads(open(provenance.sidecar_path(csv_path), encoding="utf-8").read())
    assert record["inputs"]["sizes_px"] == {"80x64": 2, "96x48": 1}
    assert list(record["inputs"]["sizes_px"]) == ["80x64", "96x48"]  # most common first

    # The private key is never a column: the CSV is the one of rows without it.
    stripped = tmp_path / "stripped.csv"
    metrics.write_metric_csv(
        [{k: v for k, v in row.items() if k != "_size_px"} for row in run.rows],
        str(stripped),
        list(AREA.headers),
    )
    assert open(csv_path, "rb").read() == stripped.read_bytes()
    assert "_size_px" not in open(csv_path, encoding="utf-8").readline()


def test_an_error_row_has_no_size(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    row = metrics.area_row("bad", str(bad))
    assert "Error" in row and "_size_px" not in row
    assert measure_run.sizes_px([row]) == {}


def test_sizes_px_counts_the_rows_most_common_first():
    rows = [{"_size_px": "10x5"}, {"_size_px": "20x5"}, {"_size_px": "20x5"}, {"Error": "x"}]
    assert measure_run.sizes_px(rows) == {"20x5": 2, "10x5": 1}
    assert list(measure_run.sizes_px(rows)) == ["20x5", "10x5"]


def test_mixed_sizes_are_looked_for_within_a_source_group():
    """Photo-size GT files beside model-size predictions are not mixed (every file spans its
    photo's frame); two sizes among the GT files, or among the predictions, are."""
    gt = lambda size: {"Source": "gt", "_size_px": size}  # noqa: E731
    pred = lambda size: {"Source": "prediction", "_size_px": size}  # noqa: E731
    assert measure_run.mixed_sizes([gt("640x480"), gt("640x480"), pred("341x256")]) is None
    assert measure_run.mixed_sizes([gt("640x480"), gt("320x240"), gt("320x240")]) == {
        "320x240": 2,
        "640x480": 1,
    }
    assert measure_run.mixed_sizes([pred("341x256"), pred("100x100")]) is not None
    # A row without a Source is a prediction; GT is looked at first.
    assert measure_run.mixed_sizes([{"_size_px": "1x1"}, {"_size_px": "2x2"}]) is not None
    both = [gt("2x2"), gt("3x3"), pred("1x1"), pred("5x5"), pred("5x5")]
    assert set(measure_run.mixed_sizes(both)) == {"2x2", "3x3"}
    assert measure_run.mixed_sizes([]) is None


def test_read_sidecar_and_csv_calibration(tmp_path):
    csv_path = tmp_path / "root_lengths.csv"
    assert provenance.read_sidecar(csv_path) is None
    assert measure_run.csv_calibration(str(csv_path)) is None

    provenance.write_sidecar(
        csv_path, {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}}
    )
    assert provenance.read_sidecar(csv_path)["calibration"]["fov_height_mm"] == 13.5
    assert measure_run.csv_calibration(str(csv_path)) == {
        "fov_width_mm": 18.0,
        "fov_height_mm": 13.5,
    }

    sidecar = provenance.sidecar_path(csv_path)
    for text in (
        "{broken",
        "[1, 2]",
        '{"calibration": "x"}',
        '{"calibration": {"fov_width_mm": 1}}',
    ):
        with open(sidecar, "w", encoding="utf-8") as fh:
            fh.write(text)
        assert measure_run.csv_calibration(str(csv_path)) is None
        if text in ("{broken", "[1, 2]"):  # not a JSON object: no record at all
            assert provenance.read_sidecar(csv_path) is None


def test_the_index_reads_each_csvs_own_calibration(tmp_path):
    """M2: ``MeasurementIndex.calibrations()`` is what the CSV's sidecar recorded, not the
    running calibration; a CSV without a sidecar has none."""
    from app.data_processing.measurement_index import MeasurementIndex

    images = _masks_of_sizes(tmp_path / "mask", [(_NAMES[0], (64, 80))])
    run = MeasureRun(AREA, images, str(tmp_path / "mask"), project_root=str(tmp_path))
    run.compute()
    csv_path = run.write()
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    assert index.calibrations() == {
        "length": None,
        "area": {"fov_width_mm": 18.0, "fov_height_mm": 13.5},
    }

    record = provenance.read_sidecar(csv_path)
    record["calibration"] = {"fov_width_mm": 18.0, "fov_height_mm": 13.0}
    provenance.write_sidecar(csv_path, record)
    os.utime(csv_path, ns=(1, 2_000_000_000))  # the CSV changed: it is read again
    index.prewarm()
    assert index.calibrations()["area"] == {"fov_width_mm": 18.0, "fov_height_mm": 13.0}

    os.remove(provenance.sidecar_path(csv_path))
    os.utime(csv_path, ns=(1, 3_000_000_000))
    index.prewarm()
    assert index.calibrations()["area"] is None


@pytest.mark.parametrize(
    "width, height",
    [
        (float("nan"), 13.0),
        (18.0, float("inf")),
        (float("-inf"), 13.0),
        (0, 13.0),
        (18.0, -13.5),
        (True, 13.0),
        (18.0, False),
        ("18", 13.0),
        (None, 13.0),
        ([18.0], 13.0),
    ],
)
def test_csv_calibration_refuses_a_field_of_view_that_is_not_a_positive_number(
    tmp_path, width, height
):
    csv_path = tmp_path / "root_areas.csv"
    provenance.write_sidecar(
        csv_path, {"calibration": {"fov_width_mm": width, "fov_height_mm": height}}
    )
    assert measure_run.csv_calibration(str(csv_path)) is None


def test_csv_calibration_takes_integers_as_floats(tmp_path):
    csv_path = tmp_path / "root_areas.csv"
    provenance.write_sidecar(csv_path, {"calibration": {"fov_width_mm": 18, "fov_height_mm": 14}})
    assert measure_run.csv_calibration(str(csv_path)) == {
        "fov_width_mm": 18.0,
        "fov_height_mm": 14.0,
    }


# --------------------------------------------------------------------------- M5: non-binary masks


def test_the_non_binary_count_is_recorded(tmp_path):
    folder = tmp_path / "mask"
    images = _masks_of_sizes(folder, [(_NAMES[0], (64, 80)), (_NAMES[1], (64, 80))])
    grey = cv2.imread(images[_NAMES[1]], cv2.IMREAD_GRAYSCALE)
    grey[0, 0] = 100
    _write_png(folder / f"{_NAMES[1]}.png", grey)

    run = MeasureRun(AREA, images, str(folder), project_root=str(tmp_path))
    run.compute()
    assert sum(1 for row in run.rows if row.get("_non_binary")) == 1
    csv_path = run.write()

    record = json.loads(open(provenance.sidecar_path(csv_path), encoding="utf-8").read())
    assert record["outputs"]["n_non_binary"] == 1
    assert record["parameters"]["binarization"] == "gray > 127"

    stripped = tmp_path / "stripped.csv"
    metrics.write_metric_csv(
        [{k: v for k, v in row.items() if k != "_non_binary"} for row in run.rows],
        str(stripped),
        list(AREA.headers),
    )
    assert open(csv_path, "rb").read() == stripped.read_bytes()


def test_length_records_carry_no_non_binary_count(tmp_path):
    images = _make_dataset(tmp_path / "skeletons", "length")
    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"), project_root=str(tmp_path))
    run.compute()
    csv_path = run.write()
    record = json.loads(open(provenance.sidecar_path(csv_path), encoding="utf-8").read())
    assert "n_non_binary" not in record["outputs"]
    assert not any("_non_binary" in row for row in run.rows)


# --------------------------------------------------------------------------- the run's memo


def _same_files(folder, names, data, suffix):
    """One file per name, every one holding ``data``: ``name -> path``."""
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for name in names:
        path = folder / f"{name}{suffix}"
        path.write_bytes(data)
        images[name] = str(path)
    return images


def _serial_pool(monkeypatch):
    """Measure the run's images one after another (pool threads that miss the memo at the
    same moment each decode: harmless, but it makes a call count depend on the timing)."""

    def serial(images, worker, progress_cb=None, should_stop=None):
        rows = [worker(name, path) for name, path in images.items()]
        return sorted(rows, key=metrics._sort_key)

    monkeypatch.setattr(measure_run, "run_metric_pool", serial)


def _counting(monkeypatch, name):
    """Count the calls of ``metrics.<name>`` (the decode-and-measure step of a row)."""
    calls = []
    real = getattr(metrics, name)

    def counted(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(metrics, name, counted)
    return calls


def test_byte_identical_empty_files_are_measured_once_per_run(tmp_path, monkeypatch):
    """The empty predictions of a project are one file content: a run decodes it once, and
    every row is the row of its own file measured alone."""
    empty = cv2.imencode(".png", np.zeros((256, 341), np.uint8))[1].tobytes()
    assert len(empty) <= metrics._MEMO_MAX_BYTES
    images = _same_files(tmp_path / "skeletons", _NAMES[:4], empty, "_fake.png")
    images.update(_make_dataset(tmp_path / "skeletons", "length", _NAMES[4:]))
    expected = sorted((LENGTH.row(n, p) for n, p in images.items()), key=metrics._sort_key)
    _serial_pool(monkeypatch)
    calls = _counting(monkeypatch, "preprocess_image")

    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"), project_root=str(tmp_path))
    run.compute()

    assert len(calls) == 1 + len(_NAMES[4:])  # the empty content once, each other file once
    assert [{k: v for k, v in row.items() if k != "Source"} for row in run.rows] == expected
    assert run._memo is None  # nothing kept on the run (a failed write keeps it for Retry)


def test_the_memo_keeps_grey_levels_and_skips_large_files(tmp_path, monkeypatch):
    """A grey-level mask that measures 0 keeps its private ``_non_binary`` key on every row;
    a file above the size limit is decoded each time."""
    grey = cv2.imencode(".png", np.full((48, 64), 100, np.uint8))[1].tobytes()
    big = cv2.imencode(".png", np.zeros((256, 341), np.uint8), [cv2.IMWRITE_PNG_COMPRESSION, 0])[
        1
    ].tobytes()
    assert len(grey) <= metrics._MEMO_MAX_BYTES < len(big)
    images = _same_files(tmp_path / "mask", _NAMES[:3], grey, ".png")
    images.update(_same_files(tmp_path / "mask", _NAMES[3:], big, ".png"))
    _serial_pool(monkeypatch)
    calls = _counting(monkeypatch, "_load_mask_and_grey_flag")

    run = MeasureRun(AREA, images, str(tmp_path / "mask"), project_root=str(tmp_path))
    run.compute()
    csv_path = run.write()

    assert len(calls) == 1 + len(_NAMES[3:])
    assert sorted(r["Image"] for r in run.rows if r.get("_non_binary")) == sorted(_NAMES[:3])
    record = json.loads(open(provenance.sidecar_path(csv_path), encoding="utf-8").read())
    assert record["outputs"]["n_non_binary"] == 3
    assert record["outputs"]["n_zero"] == len(_NAMES)


def test_the_pool_threads_share_the_memo_and_give_the_same_rows(tmp_path, monkeypatch):
    empty = cv2.imencode(".png", np.zeros((256, 341), np.uint8))[1].tobytes()
    names = [f"img{i:03d}" for i in range(64)]
    images = _same_files(tmp_path / "skeletons", names, empty, "_fake.png")
    expected = sorted((LENGTH.row(n, p) for n, p in images.items()), key=metrics._sort_key)
    calls = _counting(monkeypatch, "preprocess_image")
    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"))
    run.compute()
    assert [{k: v for k, v in row.items() if k != "Source"} for row in run.rows] == expected
    assert 1 <= len(calls) < len(names)


def test_failures_are_not_kept_so_each_error_names_its_own_file(tmp_path):
    images = _same_files(tmp_path / "skeletons", _NAMES[:3], b"not a png", "_fake.png")
    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"), project_root=str(tmp_path))
    run.compute()
    errors = {row["Image"]: row["Error"] for row in run.rows}
    assert errors == {name: f"Could not read image: {name}_fake.png" for name in _NAMES[:3]}


def test_a_stopped_run_keeps_no_memo(tmp_path):
    empty = cv2.imencode(".png", np.zeros((48, 64), np.uint8))[1].tobytes()
    images = _same_files(tmp_path / "skeletons", _NAMES, empty, "_fake.png")
    run = MeasureRun(LENGTH, images, str(tmp_path / "skeletons"))
    assert run.compute(should_stop=lambda: True) is None
    assert run._memo is None and run.rows is None


def test_rows_without_a_memo_are_the_same(tmp_path):
    """``memo`` changes no row: the same bytes measured with and without it."""
    empty = cv2.imencode(".png", np.zeros((48, 64), np.uint8))[1].tobytes()
    for spec, suffix in ((LENGTH, "_fake.png"), (AREA, ".png")):
        path = tmp_path / f"{spec.kind}{suffix}"
        path.write_bytes(empty)
        memo: dict = {}
        plain = spec.row(_NAMES[0], str(path), data=empty)
        assert spec.row(_NAMES[0], str(path), data=empty, memo=memo) == plain
        assert len(memo) == 1
        assert spec.row(_NAMES[1], str(path), data=empty, memo=memo) == spec.row(
            _NAMES[1], str(path), data=empty
        )

"""Per-project field of view (O3, M2 step 2; W3-T2).

A project can state its own camera field of view in ``sprouts_project.json``; Measure uses it
and records it in each CSV's provenance, and a CSV measured with another field of view (or,
for length, another length method) is flagged outdated, never hidden.
"""

from __future__ import annotations

import csv
import inspect
import json
import os

import cv2
import numpy as np
import pytest

from app import calibration, config, provenance
from app.calibration import (
    LENGTH_METHOD,
    PROJECT_FILENAME,
    aspect_mismatches,
    calibration_record,
    default_calibration,
    fov_label,
    load_for_output,
    load_project_calibration,
    method_note,
)
from app.config import AREA_VALUE_COLUMN, Calibration
from app.data_processing.measurement_index import MeasurementIndex
from app.inference import metrics
from app.inference.measure_run import AREA, MeasureRun

PROJECT = {
    "schema": 1,
    "calibration": {
        "fov_width_mm": 18.0,
        "fov_height_mm": 13.5,
        "reference_size_px": [640, 480],
        "source": "Rajurkar et al. 2022",
    },
}


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _write_project(root, data, *, bom: bool = False) -> str:
    path = os.path.join(root, PROJECT_FILENAME)
    text = data if isinstance(data, str) else json.dumps(data)
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8") as fh:
        fh.write(text)
    return path


def test_no_project_file_gives_the_default(tmp_path):
    for root in (tmp_path, None):
        pc = load_project_calibration(root)
        assert pc.calibration == config.CALIBRATION
        assert pc.source == "default"
        assert pc.problem == ""
        assert pc.reference_size_px is None


def test_a_project_file_sets_the_field_of_view(tmp_path):
    for bom in (False, True):
        _write_project(tmp_path, PROJECT, bom=bom)
        pc = load_project_calibration(tmp_path)
        assert pc.calibration == Calibration(18.0, 13.5)
        assert pc.source == "project"
        assert pc.reference_size_px == (640, 480)
        assert pc.evidence == "Rajurkar et al. 2022"
        assert pc.problem == ""


def _with(**fields) -> str:
    cal = {"fov_width_mm": 18.0, "fov_height_mm": 13.5}
    cal.update(fields)
    return json.dumps({"schema": 1, "calibration": cal})


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("{", "it is not valid JSON"),
        (json.dumps({"schema": 2, "calibration": PROJECT["calibration"]}), '"schema" must be 1'),
        (json.dumps({"schema": 1}), '"calibration" is missing'),
        (_with(fov_width_mm="18"), '"calibration.fov_width_mm" must be a number above 0'),
        (_with(fov_width_mm=0), '"calibration.fov_width_mm" must be a number above 0'),
        (_with(fov_height_mm=-1), '"calibration.fov_height_mm" must be a number above 0'),
        (_with(fov_width_mm=True), '"calibration.fov_width_mm" must be a number above 0'),
        (
            '{"schema": 1, "calibration": {"fov_width_mm": NaN, "fov_height_mm": 13.5}}',
            '"calibration.fov_width_mm" must be a number above 0',
        ),
        (
            _with(reference_size_px=[640]),
            '"calibration.reference_size_px" must be two whole numbers above 0',
        ),
        (_with(source=3), '"calibration.source" must be text'),
        (
            '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": Infinity}}',
            '"calibration.fov_height_mm" must be a number above 0',
        ),
        (  # too large for a float: OverflowError must not escape
            '{"schema": 1, "calibration": {"fov_width_mm": 1'
            + "0" * 400
            + ', "fov_height_mm": 13}}',
            '"calibration.fov_width_mm" must be a number above 0',
        ),
        (
            _with(reference_size_px=[10**400, 480]),
            '"calibration.reference_size_px" must be two whole numbers above 0',
        ),
    ],
)
def test_an_invalid_project_file_falls_back_and_says_why(tmp_path, text, problem):
    _write_project(tmp_path, text)
    pc = load_project_calibration(tmp_path)
    assert pc.source == "default"
    assert pc.calibration == config.CALIBRATION
    assert pc.problem == problem


def test_the_file_is_read_again_when_it_changes(tmp_path):
    path = _write_project(tmp_path, _with(fov_height_mm=13.0))
    assert load_project_calibration(tmp_path).calibration == Calibration(18.0, 13.0)
    _write_project(tmp_path, _with(fov_height_mm=13.5))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert load_project_calibration(tmp_path).calibration == Calibration(18.0, 13.5)


def test_the_record_names_its_source(tmp_path):
    assert calibration_record(default_calibration()) == {
        "fov_width_mm": 18.0,
        "fov_height_mm": 13.0,
        "source": "default",
    }
    _write_project(tmp_path, PROJECT)
    assert calibration_record(load_project_calibration(tmp_path)) == {
        "fov_width_mm": 18.0,
        "fov_height_mm": 13.5,
        "source": "project",
        "file": "sprouts_project.json",
        "evidence": "Rajurkar et al. 2022",
        "reference_size_px": [640, 480],
    }


def test_metric_functions_keep_their_signatures():
    for function in (metrics.root_length_mm, metrics.root_area_mm2):
        params = list(inspect.signature(function).parameters.values())
        assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert params[1].name == "calibration"
        assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[1].default is None
    full = np.full((480, 640), 255, np.uint8)
    assert metrics.root_area_mm2(full) == pytest.approx(18.0 * 13.0)
    assert metrics.root_area_mm2(full, calibration=Calibration(18.0, 13.5)) == pytest.approx(243.0)


def _masks(folder, n=2) -> dict[str, str]:
    os.makedirs(folder, exist_ok=True)
    images = {}
    for i in range(n):
        img = np.zeros((64, 80), np.uint8)
        cv2.line(img, (0, i), (79, 40 + i), 255, 2)
        name = f"x_T{i + 1}_L2_2024.01.02_01020{i}_001"
        path = os.path.join(folder, f"{name}.png")
        cv2.imwrite(path, img)
        images[name] = path
    return images


def test_measure_uses_and_records_the_project_calibration(tmp_path):
    root = str(tmp_path)
    _write_project(root, PROJECT)
    pc = load_project_calibration(root)
    images = _masks(os.path.join(root, "mask"))
    run = MeasureRun(AREA, images, os.path.join(root, "mask"), project_root=root, calibration=pc)
    run.compute()
    csv_path = run.write()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        values = {row["Image"]: float(row[AREA_VALUE_COLUMN]) for row in csv.DictReader(fh)}
    for name, path in images.items():
        n_px = int(np.count_nonzero(cv2.imread(path, cv2.IMREAD_GRAYSCALE) > 127))
        assert values[name] == round(n_px * 18.0 / 80 * 13.5 / 64, 2)
    assert provenance.read_sidecar(csv_path)["calibration"] == calibration_record(pc)


_FOV_SENTENCE = (
    "Measured with field of view 18×13 mm; this project uses 18×13.5 mm. "
    "Measure again before comparing with new values."
)
_METHOD_SENTENCE = (
    "Measured with an earlier length method. Measure again before comparing with new values."
)


def test_method_note_texts(tmp_path):
    _write_project(tmp_path, PROJECT)
    project = load_project_calibration(tmp_path)
    default = default_calibration()
    old = {"fov_width_mm": 18.0, "fov_height_mm": 13.0}
    current_method = {"estimator": LENGTH_METHOD}

    sidecar = {"calibration": old, "parameters": current_method}
    assert method_note(sidecar, kind="length", current=project) == _FOV_SENTENCE
    assert method_note(sidecar, kind="area", current=project) == _FOV_SENTENCE

    earlier = {"calibration": old, "parameters": {"estimator": "old"}}
    assert method_note(earlier, kind="length", current=default) == _METHOD_SENTENCE
    assert method_note(earlier, kind="length", current=project) == (
        _FOV_SENTENCE + " " + _METHOD_SENTENCE
    )
    assert method_note(earlier, kind="area", current=default) == ""  # area has no estimator
    assert method_note(sidecar, kind="length", current=default) == ""
    # The app's default (no or an invalid project file) is "this app now uses" (I1).
    newer = {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}}
    app_note = (
        "Measured with field of view 18×13.5 mm; this app now uses 18×13 mm. "
        "Measure again before comparing with new values."
    )
    assert method_note(newer, kind="area", current=default) == app_note
    (tmp_path / "bad").mkdir()
    _write_project(tmp_path / "bad", "{")
    invalid = load_project_calibration(tmp_path / "bad")
    assert invalid.problem and invalid.source == "default"
    assert method_note(newer, kind="length", current=invalid) == app_note
    assert method_note(None, kind="length", current=project) == ""
    assert method_note({"parameters": {"estimator": "old"}}, kind="length", current=project) == ""
    assert fov_label(Calibration(18.0, 13.0)) == "18×13 mm"
    assert fov_label({"fov_width_mm": 18.0, "fov_height_mm": 13.5}) == "18×13.5 mm"


def test_aspect_mismatches():
    sizes = {"640x480": 3, "341x256": 2, "1920x1080": 1}
    assert aspect_mismatches(sizes, (640, 480)) == 1
    assert aspect_mismatches(sizes, None) == 0


def test_load_for_output_finds_a_legacy_skeleton_csv_and_takes_a_known_root(tmp_path):
    root = tmp_path / "project"
    deep = [
        root / "skeletonizer" / "test_latest" / "images",
        root / "output" / "skeletonizer" / "test_latest" / "images",
    ]
    for folder in deep:
        folder.mkdir(parents=True)
    _write_project(root, PROJECT)
    for folder in deep:
        found = load_for_output(str(folder / "root_lengths.csv"))
        assert found.source == "project" and found.calibration == Calibration(18.0, 13.5)
    # The caller's project root wins over the folders above the CSV.
    elsewhere = tmp_path / "elsewhere" / "skeletons"
    elsewhere.mkdir(parents=True)
    known = load_for_output(str(elsewhere / "root_lengths.csv"), project_root=str(root))
    assert known.source == "project"
    # Six levels up is too far: the default.
    six = tmp_path / "far" / "a" / "b" / "c" / "d" / "e" / "f"
    six.mkdir(parents=True)
    _write_project(tmp_path / "far", PROJECT)
    assert load_for_output(str(six / "x.csv")).source == "default"


def test_the_sidecar_is_read_once_per_csv_load(tmp_path, monkeypatch):
    from app.data_processing import measurement_index

    root = str(tmp_path)
    images = _masks(os.path.join(root, "mask"))
    run = MeasureRun(AREA, images, os.path.join(root, "mask"), project_root=root)
    run.compute()
    run.write()
    reads: list[str] = []
    read = provenance.read_sidecar

    def counting(path):
        reads.append(str(path))
        return read(path)

    monkeypatch.setattr(measurement_index, "read_sidecar", counting)
    monkeypatch.setattr(provenance, "read_sidecar", counting)  # csv_calibration's reader
    index = MeasurementIndex(root)
    index.prewarm()
    assert reads == [os.path.join(root, "mask", "root_areas.csv")]
    assert index.calibrations()["area"] == {"fov_width_mm": 18.0, "fov_height_mm": 13.0}
    index.prewarm()  # unchanged CSV: not read again
    assert len(reads) == 1


def test_the_measure_status_line_carries_the_method_note(tmp_path, qapp):
    """The Measure status line's pool count (StageStatus.check) carries the note."""
    import time
    from types import SimpleNamespace

    from app.gui.stage_status import StageStatus
    from app.project_layout import ProjectLayout

    root = str(tmp_path)
    images = _masks(os.path.join(root, "mask"))
    for name in images:
        cv2.imwrite(os.path.join(root, f"{name}.jpg"), np.zeros((64, 80), np.uint8))
    run = MeasureRun(AREA, images, os.path.join(root, "mask"), project_root=root)
    run.compute()
    run.write()

    def measure_line() -> str:
        status, index, done = StageStatus(), MeasurementIndex(root), []
        index.prewarm()
        status.changed.connect(lambda: done.append(True))
        status.check(index, ProjectLayout(root))
        end = time.monotonic() + 10
        while not done:
            assert time.monotonic() < end, "the pool count never came in"
            qapp.processEvents()
            time.sleep(0.005)
        manager = SimpleNamespace(images=dict(images), masks=set(images), skeleton_stems=set())
        return status.text("Measure", manager)

    assert measure_line() == "no skeletons to measure · 2 masks: measured, current"
    _write_project(root, _with(fov_height_mm=13.5))
    assert measure_line() == (
        "no skeletons to measure · "
        "2 masks: measured with another field of view or length method: measure again"
    )


def test_load_for_output_finds_the_project_two_or_three_levels_up(tmp_path):
    root = tmp_path / "project"
    (root / "skeletons").mkdir(parents=True)
    (root / "output" / "mask").mkdir(parents=True)
    _write_project(root, PROJECT)

    two = load_for_output(str(root / "skeletons" / "root_lengths.csv"))
    three = load_for_output(str(root / "output" / "mask" / "root_areas.csv"))
    assert two.source == three.source == "project"
    assert two.calibration == three.calibration == Calibration(18.0, 13.5)

    (tmp_path / "elsewhere" / "deep").mkdir(parents=True)
    assert load_for_output(str(tmp_path / "elsewhere" / "deep" / "x.csv")) == (
        calibration.default_calibration()
    )


def test_a_csv_measured_with_another_field_of_view_is_flagged(tmp_path):
    root = str(tmp_path)
    images = _masks(os.path.join(root, "mask"))
    for name in images:
        cv2.imwrite(os.path.join(root, f"{name}.jpg"), np.zeros((64, 80), np.uint8))
    run = MeasureRun(AREA, images, os.path.join(root, "mask"), project_root=root)
    run.compute()
    run.write()
    _write_project(root, _with(fov_height_mm=13.5))

    index = MeasurementIndex(root)
    index.prewarm()
    notes = index.method_notes(load_project_calibration(root))
    assert notes["area"].startswith(
        "Measured with field of view 18×13 mm; this project uses 18×13.5 mm."
    )
    assert notes["length"] == ""
    # Measured with the project's own field of view: nothing to say.
    assert index.method_notes(default_calibration()) == {"length": "", "area": ""}


def test_the_bar_and_about_compare_with_the_projects_calibration(tmp_path):
    """One rule (M2, O3): the metrics bar's FOV cell and About's "Measured with" compare a
    CSV's recorded field of view with the open project's, not with the app's default."""
    from types import SimpleNamespace

    from app.data_processing.measurement_index import Measurement
    from app.gui import about_dialog
    from app.gui.window.metrics import MetricsController, _shown_fov

    _write_project(tmp_path, _with(fov_height_mm=13.5))
    controller = MetricsController(SimpleNamespace())
    assert controller.current_calibration() == default_calibration()  # no project open
    controller.index = MeasurementIndex(str(tmp_path))
    current = controller.current_calibration()
    assert current.calibration == Calibration(18.0, 13.5) and current.source == "project"

    at_13_5 = {"fov_width_mm": 18.0, "fov_height_mm": 13.5}
    at_13 = {"fov_width_mm": 18.0, "fov_height_mm": 13.0}
    csvs = {"length": "D:/p/skeletons/root_lengths.csv"}
    shown = Measurement(1.0, None, None)
    assert _shown_fov(shown, {"length": at_13_5}, csvs, current) == (
        "18×13.5 mm",
        "Field of view recorded with root_lengths.csv",
    )
    assert _shown_fov(shown, {"length": at_13}, csvs, current)[1].endswith(
        "This project uses 18×13.5 mm"
    )
    assert _shown_fov(Measurement(None, None, None), {}, {}, current)[0] == "18×13.5 mm"

    def window(calibrations):
        index = SimpleNamespace(calibrations=lambda: calibrations, sources=lambda: csvs)
        return SimpleNamespace(
            metrics=SimpleNamespace(index=index, current_calibration=lambda: current)
        )

    assert about_dialog.measured_with_text(window({"length": at_13_5})) is None
    assert about_dialog.measured_with_text(window({"length": at_13})) == (
        "FOV 18×13 mm (root_lengths.csv)"
    )
    assert about_dialog.project_calibration(window({}))["fov_height_mm"] == 13.5
    assert (
        "Calibration FOV: 18×13.5 mm (this project's field of view)"
        in about_dialog.details_text(calibration=about_dialog.project_calibration(window({})))
    )


def test_the_texts_say_project_or_app(tmp_path):
    """One helper words the bar, the export and About alike: "project" for a project file's
    field of view; "app" for the default, an invalid file, or no project."""
    from app.calibration import calibration_words
    from app.data_processing.measurement_index import Measurement
    from app.gui import about_dialog
    from app.gui.window.metrics import _shown_fov
    from app.visualization import dash_export

    project_root = tmp_path / "project"
    (project_root / "skeletons").mkdir(parents=True)
    _write_project(project_root, _with(fov_height_mm=13.5))
    project = load_project_calibration(project_root)
    invalid_root = tmp_path / "invalid"
    (invalid_root / "skeletons").mkdir(parents=True)
    _write_project(invalid_root, "{")
    invalid = load_project_calibration(invalid_root)
    assert invalid.problem

    assert calibration_words(project) == ("this project's", "this project uses")
    assert calibration_words(calibration_record(project)) == calibration_words(project)
    for app_side in (default_calibration(), invalid, calibration_record(invalid), None):
        assert calibration_words(app_side) == ("this app's", "this app now uses")

    # The metrics bar: the second tooltip line, and the tooltip without a CSV record.
    other = {"fov_width_mm": 20.0, "fov_height_mm": 15.0}
    csvs = {"length": "root_lengths.csv"}
    shown = Measurement(1.0, None, None)
    assert _shown_fov(shown, {"length": other}, csvs, project)[1].endswith(
        "\nThis project uses 18×13.5 mm"
    )
    assert _shown_fov(shown, {"length": other}, csvs, invalid)[1].endswith(
        "\nThis app now uses 18×13 mm"
    )
    assert _shown_fov(shown, {}, {}, project) == ("18×13.5 mm", "This project's field of view")
    assert _shown_fov(shown, {}, {}, invalid) == ("18×13 mm", "This app's field of view")

    # The export: a differing record, and no record.
    for root, uses, owner in [
        (project_root, "; this project uses 18.00 x 13.50 mm", "this project's: no record"),
        (invalid_root, "; this app now uses 18.00 x 13.00 mm", "this app's: no record"),
    ]:
        source = root / "skeletons" / "root_lengths.csv"
        source.write_text("Image\n", encoding="utf-8")
        assert owner in dash_export.calibration_line(str(source))
        provenance.write_sidecar(source, {"calibration": other})
        assert dash_export.calibration_line(str(source)).endswith(uses)

    # About's Calibration row.
    assert about_dialog.calibration_text(calibration_record(project)) == (
        "18×13.5 mm (this project's field of view)"
    )
    assert about_dialog.calibration_text(calibration_record(invalid)) == (
        "18×13 mm (this app's field of view)"
    )
    assert about_dialog.calibration_text() == "18×13 mm (this app's field of view)"

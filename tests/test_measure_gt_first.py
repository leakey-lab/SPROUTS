"""WG-T8 (O1), commit 2: Measure reads GT first for both metrics, and each CSV says which.

Length measures ``gt/skeletons/<stem>.png`` where it is valid, else the prediction
``skeletons/<stem>_fake.png``; area measures ``gt/mask/<stem>.png`` where it decodes, else
``mask/<stem>.png`` (:mod:`app.inference.measure_sources`). The CSVs gain a last column
``Source`` (``gt`` / ``prediction``). Each image is decided and measured in one task on the
Measure job's worker (never on the GUI thread), each GT file read once. The provenance and
the record of the inputs follow the measured file, and so do the metrics bar's freshness,
its tooltips and the status line.
"""

from __future__ import annotations

import csv
import json
import os
import threading
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.gui.stage_summary import Freshness, input_freshness, measure_text  # noqa: E402
from app.inference import measure_run, measure_sources, metrics  # noqa: E402
from app.inference.measure_inputs import input_key, load_inputs_record  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from app.provenance import file_sha256  # noqa: E402
from tests.correct_goldens_support import (  # noqa: E402
    SYNTH_STEM,
    describe_skeleton_file,
    load_goldens,
    make_synth_project,
)
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_measure_core import _legacy_csv, _make_dataset  # noqa: E402
from tests.test_measure_handler import _handler, _wait_idle  # noqa: E402
from tests.test_measure_sources import (  # noqa: E402
    PHOTO_SIZE,
    STEMS,
    _write_png,
    make_gt_project,
    prediction_skeleton,
    write_gt_mask,
    write_gt_skeleton,
    write_photo,
)

LENGTH_COL, AREA_COL = LENGTH_CSV_HEADERS[5], AREA_CSV_HEADERS[5]
GOLDENS = load_goldens()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gt_project(tmp_path) -> tuple[ProjectLayout, dict[str, str]]:
    return make_gt_project(str(tmp_path))


@pytest.fixture(autouse=True)
def _no_message_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


def _csv_path(layout: ProjectLayout, kind: str) -> str:
    if kind == "length":
        return os.path.join(layout.skeletons_dir, "root_lengths.csv")
    return os.path.join(layout.mask_dir, "root_areas.csv")


def _rows(path: str) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["Image"]: row for row in csv.DictReader(fh)}


def _sidecar(csv_path: str) -> dict:
    with open(csv_path + ".provenance.json", encoding="utf-8") as fh:
        return json.load(fh)


def _notices(mw) -> list[tuple]:
    return [c.args for c in mw.feedback.calls if c.name == "notify"]


def _run(layout: ProjectLayout, qapp, kind: str):
    """Measure ``kind`` through the handler, as the Measure button does; the fake window."""
    handler, mw = _handler(layout.root, qapp)
    (handler.calculate_root_length if kind == "length" else handler.calculate_root_area)()
    _wait_idle(handler, qapp)
    return mw


def run_length(layout: ProjectLayout, qapp) -> dict[str, dict]:
    _run(layout, qapp, "length")
    return _rows(_csv_path(layout, "length"))


def run_area(layout: ProjectLayout, qapp) -> dict[str, dict]:
    _run(layout, qapp, "area")
    return _rows(_csv_path(layout, "area"))


def other_skeleton() -> np.ndarray:
    """Another hand-traced 1 px skeleton at the photo's size."""
    w, h = PHOTO_SIZE
    gray = np.zeros((h, w), np.uint8)
    cv2.line(gray, (30, 30), (w - 30, h // 2), 255, 1)
    return gray


# ------------------------------------------------------------------ the CSVs
def test_the_csvs_gain_a_last_source_column():
    assert LENGTH_CSV_HEADERS == [
        "Image",
        "Tube",
        "Position",
        "Date",
        "Time",
        "Length (mm)",
        "Error",
        "Source",
    ]
    assert AREA_CSV_HEADERS == [
        "Image",
        "Tube",
        "Position",
        "Date",
        "Time",
        "Area (mm²)",
        "Error",
        "Source",
    ]


def test_rows_say_gt_or_prediction_and_values_follow_the_measured_file(gt_project, qapp):
    layout, _ = gt_project
    rows = run_length(layout, qapp)

    assert set(rows) == set(STEMS)
    gt_g = metrics.length_row("g", layout.gt_skeleton_path("g"))[LENGTH_COL]
    assert gt_g != metrics.length_row("g", layout.skeleton_path("g"))[LENGTH_COL]
    assert rows["g"]["Source"] == "gt" and float(rows["g"][LENGTH_COL]) == gt_g
    prediction_s = metrics.length_row("s", layout.skeleton_path("s"))[LENGTH_COL]
    assert rows["s"]["Source"] == "prediction" and float(rows["s"][LENGTH_COL]) == prediction_s
    assert rows["o"]["Source"] == "gt" and rows["p"]["Source"] == "prediction"

    areas = run_area(layout, qapp)
    assert {stem: row["Source"] for stem, row in areas.items()} == {
        "p": "prediction",
        "g": "prediction",
        "o": "gt",
        "s": "prediction",
        "u": "prediction",
        "x": "prediction",
        "m": "gt",
    }
    gt_o = metrics.area_row("o", layout.gt_mask_path("o"))[AREA_COL]
    assert gt_o > 0 and float(areas["o"][AREA_COL]) == gt_o


def seed_golden_correction(root: str, case: str) -> ProjectLayout:
    """The goldens' synthetic project of ``case`` with its skeleton correction saved as GT:
    the golden ops replayed through the Correct skeleton layer, whose Save writes
    ``layout.gt_skeleton_path``; the pixels are the golden's."""
    from app.gui.correct.skeleton_layer import SkeletonLayer
    from tests.correct_fakes import FakeHost
    from tests.test_correct_skeleton_goldens import apply

    folder, image_path = make_synth_project(root, case)
    height, width = imread_unicode(image_path).shape[:2]
    layer = SkeletonLayer(FakeHost(), QGraphicsScene())
    assert layer.open(image_path, (width, height), folder) == []
    for op in GOLDENS["ops"]["skeleton"]:
        apply(layer, op)
    layer.cancel_scheduled_render()
    layout = ProjectLayout(folder)
    saved = describe_skeleton_file(layout.gt_skeleton_path(SYNTH_STEM))
    assert saved["pixels_sha256"] == GOLDENS["cases"][case]["skeleton"]["saved"]["pixels_sha256"]
    return layout


@pytest.mark.parametrize(
    "case, length", [("synth_blank_png", 27.97), ("synth_model_mask_and_skeleton", 58.87)]
)
def test_the_synthetic_goldens_corrections_measure_their_recorded_lengths(
    tmp_path, qapp, case, length
):
    layout = seed_golden_correction(str(tmp_path), case)
    recorded = GOLDENS["cases"][case]["skeleton"]["saved"]["length_mm_if_measured_informational"]
    assert recorded == length

    rows = run_length(layout, qapp)

    assert float(rows[SYNTH_STEM][LENGTH_COL]) == length and rows[SYNTH_STEM]["Source"] == "gt"


@pytest.mark.parametrize("spec", [measure_run.LENGTH, measure_run.AREA], ids=["length", "area"])
def test_prediction_rows_are_byte_identical_to_the_legacy_writer_in_the_first_seven_columns(
    tmp_path, spec
):
    images = _make_dataset(tmp_path / "in", spec.kind)
    bad = tmp_path / "in" / "corrupt_T001_L001.png"
    bad.write_bytes(b"not a png")
    images["corrupt_T001_L001"] = str(bad)
    legacy = _legacy_csv(images, spec.kind, str(tmp_path / "legacy.csv"), source=False)
    out = tmp_path / "out"
    out.mkdir()
    run = measure_run.MeasureRun(spec, images, str(out), gt_files={}, photos={})  # GT-first, no GT
    run.compute()

    with open(run.write(), "rb") as fh:
        lines = fh.read().split(b"\r\n")
    with open(legacy, "rb") as fh:
        legacy_lines = fh.read().split(b"\r\n")
    assert [line.rsplit(b",", 1)[0] for line in lines if line] == [
        line for line in legacy_lines if line
    ]
    assert [line.rsplit(b",", 1)[1] for line in lines if line] == [b"Source"] + [b"prediction"] * (
        len(images)
    )


# ------------------------------------------------------------------ inputs and records
def test_gt_files_without_an_image_are_skipped_and_recorded(gt_project, qapp):
    layout, _ = gt_project
    orphan = write_gt_skeleton(layout, "z")  # no photo z.jpg
    with open(orphan, "rb") as fh:
        before = fh.read()

    mw = _run(layout, qapp, "length")

    csv_path = _csv_path(layout, "length")
    assert "z" not in _rows(csv_path)
    with open(orphan, "rb") as fh:
        assert fh.read() == before  # never deleted or touched (O37)
    inputs = _sidecar(csv_path)["inputs"]
    assert inputs["n_without_image"] == 1 and inputs["without_image"] == ["gt/skeletons/z.png"]
    assert (
        "1 skeleton skipped: its image is no longer in the project "
        "(named in root_lengths.csv.provenance.json)",
        "warn",
    ) in _notices(mw)


def test_the_records_list_gt_files_skips_and_counts(gt_project, qapp):
    layout, _ = gt_project
    root = layout.root
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")

    rec = _sidecar(csv_path)
    assert rec["parameters"]["source_rule"] == "gt-first" and rec["inputs"]["gt"]["n_used"] == 2
    assert rec["parameters"]["input"] == (
        "gt/skeletons/<stem>.png where valid, else skeletons/<stem>_fake.png"
    )
    assert rec["inputs"]["gt"]["folder"] == "gt/skeletons"
    assert rec["inputs"]["gt"]["skipped"] == [
        {"stem": "s", "reason": "size"},
        {"stem": "u", "reason": "unedited"},
        {"stem": "x", "reason": "unreadable"},
    ]
    assert (rec["outputs"]["n_gt"], rec["outputs"]["n_prediction"]) == (2, 5)
    assert set(rec["inputs"]["gt"]["files"]) == {"g", "o"}
    assert rec["inputs"]["gt"]["files"]["g"] == file_sha256(layout.gt_skeleton_path("g"))
    recorded = load_inputs_record(csv_path).inputs
    assert input_key(layout.gt_skeleton_path("g"), root) in recorded  # the measured file
    assert input_key(layout.skeleton_path("g"), root) not in recorded  # not the one under it
    assert input_key(layout.gt_skeleton_path("s"), root) in recorded  # a GT file not used

    _run(layout, qapp, "area")
    rec = _sidecar(_csv_path(layout, "area"))
    assert rec["parameters"]["input"] == (
        "gt/mask/<stem>.png where readable and the photo's size, else mask/<stem>.png"
    )
    assert rec["inputs"]["gt"]["skipped"] == [{"stem": "x", "reason": "unreadable"}]
    assert (rec["outputs"]["n_gt"], rec["outputs"]["n_prediction"]) == (2, 5)


def test_a_gt_mask_of_another_size_is_measured_from_its_prediction_with_a_reason(gt_project, qapp):
    # Correct refuses such a mask ("Mask not shown"), so the CSV does not report it as GT (S1).
    layout, _ = gt_project
    small = np.ones((240, 320), bool)  # 320 x 240; the photos are 640 x 480
    write_gt_mask(layout, "g", small)  # has a prediction
    write_gt_mask(layout, "o", small)  # GT only
    write_gt_mask(layout, "p", np.zeros(PHOTO_SIZE[::-1], bool))  # photo-sized, emptied
    with open(layout.gt_mask_path("g"), "rb") as fh:
        wrong = fh.read()

    mw = _run(layout, qapp, "area")

    rows = _rows(_csv_path(layout, "area"))
    assert rows["g"]["Source"] == "prediction"
    assert float(rows["g"][AREA_COL]) == metrics.area_row("g", layout.mask_path("g"))[AREA_COL]
    assert "o" not in rows  # no prediction to fall back to: no row
    assert rows["p"]["Source"] == "gt" and float(rows["p"][AREA_COL]) == 0
    assert rows["m"]["Source"] == "gt"  # a photo-sized GT mask is still used
    rec = _sidecar(_csv_path(layout, "area"))
    assert rec["inputs"]["gt"]["skipped"] == [
        {"stem": "g", "reason": "size"},
        {"stem": "o", "reason": "size"},
        {"stem": "x", "reason": "unreadable"},
    ]
    assert set(rec["inputs"]["gt"]["files"]) == {"m", "p"}
    recorded = load_inputs_record(_csv_path(layout, "area")).inputs
    assert input_key(layout.gt_mask_path("g"), layout.root) in recorded  # read, not used
    assert (
        "3 GT masks not used: 2 not the photo's size, 1 unreadable "
        "(named in root_areas.csv.provenance.json)",
        "warn",
    ) in _notices(mw)
    with open(layout.gt_mask_path("g"), "rb") as fh:
        assert fh.read() == wrong  # the file is never touched


@pytest.mark.parametrize("spec", [measure_run.LENGTH, measure_run.AREA], ids=["length", "area"])
def test_a_gt_first_run_without_photos_measures_every_gt_file_as_unreadable(gt_project, spec):
    # The size check needs the photos; without them no GT file can be validated, so the
    # prediction is measured and each skip is recorded (the app always passes the photos).
    layout, _ = gt_project
    if spec.kind == "length":
        predictions, gt_files = layout.scan_skeletons(), layout.scan_gt_skeletons()
    else:
        predictions, gt_files = layout.scan_masks(extensions=(".png",)), layout.scan_gt_masks()
    run = measure_run.MeasureRun(spec, predictions, layout.root, gt_files=gt_files)

    run.compute()

    assert {source for source in run.sources.values()} == {"prediction"}
    skipped = dict(run.gt_report.skipped)
    assert skipped == {stem: "unreadable" for stem in gt_files}
    assert "o" not in run.sources  # GT only, nothing to fall back to: no row


def test_gt_files_are_decided_on_the_worker_and_read_once(gt_project, qapp, monkeypatch):
    layout, _ = gt_project
    threads: list[threading.Thread] = []
    real_decide = measure_sources.decide_length_source

    def decide(*args):
        threads.append(threading.current_thread())
        return real_decide(*args)

    opened: list[str] = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        opened.append(os.path.normcase(os.path.abspath(os.fspath(path))))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(measure_sources, "decide_length_source", decide)
    for module in (measure_sources, measure_run):
        monkeypatch.setattr(module, "open", counting_open, raising=False)

    _run(layout, qapp, "length")

    assert len(threads) == len(STEMS) and threading.main_thread() not in threads
    gt_dir = os.path.normcase(os.path.abspath(layout.gt_skeletons_dir))
    gt_reads = sorted(path for path in opened if path.startswith(gt_dir))
    expected = sorted(
        os.path.normcase(os.path.abspath(path)) for path in layout.scan_gt_skeletons().values()
    )
    assert gt_reads == expected  # each GT file once: validated, hashed and measured


# ------------------------------------------------------------------ freshness
def test_outdated_follows_the_measured_file(gt_project, qapp):
    layout, _ = gt_project

    def fresh() -> MeasurementIndex:
        _run(layout, qapp, "length")
        _run(layout, qapp, "area")
        made = MeasurementIndex(layout.root)
        made.prewarm()
        return made

    def again(index: MeasurementIndex) -> MeasurementIndex:
        index.invalidate()
        index.prewarm()
        return index

    index = fresh()
    assert index.outdated("g") == {"length": False, "area": False}
    assert index.outdated("s") == {"length": False, "area": False}  # its unused GT is recorded

    write_gt_skeleton(layout, "g", other_skeleton())
    assert again(index).outdated("g")["length"] is True  # the measured GT file changed
    write_gt_skeleton(layout, "p")
    assert again(index).outdated("p")["length"] is True  # a GT saved after the CSV
    os.remove(layout.gt_skeleton_path("o"))
    assert again(index).outdated("o")["length"] is True  # the measured GT file is gone

    index = fresh()
    _write_png(layout.skeleton_path("g"), np.roll(prediction_skeleton(), 9, axis=1))
    # a regenerated prediction under a gt row does not outdate it
    assert again(index).outdated("g") == {"length": False, "area": False}
    write_gt_mask(layout, "p")
    assert again(index).outdated("p") == {"length": False, "area": True}
    assert index.gt_skip_reason("s") == {"length": "size"}
    assert index.gt_skip_reason("x") == {"length": "unreadable", "area": "unreadable"}
    assert index.gt_skip_reason("g") == {}


def test_the_status_line_counts_gt_files():
    assert (
        measure_text(20, 20, Freshness(20, True), Freshness(20, False), gt_skeletons=2, gt_masks=3)
        == "20 skeletons (2 GT): measured, current · 20 masks (3 GT): not measured yet"
    )
    assert measure_text(20, 12) == "20 skeletons · 12 masks"  # no GT: as before


def test_the_status_line_follows_the_measured_files(gt_project, qapp):
    layout, images = gt_project
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")

    def freshness() -> Freshness:
        return input_freshness(
            layout.skeletons_dir,
            "_fake.png",
            csv_path,
            record=load_inputs_record(csv_path),
            project_root=layout.root,
            images=images,
            gt_folder=layout.gt_skeletons_dir,
        )

    assert freshness() == Freshness(len(STEMS), True, by_content=True, gt=5)
    _write_png(layout.skeleton_path("g"), np.roll(prediction_skeleton(), 9, axis=1))
    assert freshness().changed == 0 and freshness().new == 0  # measured from its GT file
    write_gt_skeleton(layout, "p")
    assert (freshness().new, freshness().gt) == (1, 6)  # a GT saved after the CSV
    _write_png(layout.skeleton_path("s"), np.roll(prediction_skeleton(), 9, axis=1))
    assert freshness().changed == 1  # the prediction measured under an unused GT file


# ------------------------------------------------------------------ what the user is told
def test_the_end_of_run_notice_names_skipped_gt_files(gt_project, qapp):
    layout, _ = gt_project

    mw = _run(layout, qapp, "length")
    assert (
        "3 GT skeletons not used: 1 not the photo's size, 1 equal to the prediction, "
        "1 unreadable (named in root_lengths.csv.provenance.json)",
        "warn",
    ) in _notices(mw)

    mw = _run(layout, qapp, "area")
    assert (
        "1 GT mask not used: 1 unreadable (named in root_areas.csv.provenance.json)",
        "warn",
    ) in _notices(mw)


def test_a_project_with_gt_files_and_no_predictions_is_measured_into_the_prediction_folder(
    tmp_path, qapp
):
    layout = ProjectLayout(str(tmp_path))
    for stem in ("a", "b"):
        write_photo(layout, stem)
        write_gt_skeleton(layout, stem)
        write_gt_mask(layout, stem)
    write_gt_skeleton(layout, "e", np.zeros(PHOTO_SIZE[::-1], np.uint8))  # emptied: 0 mm
    write_photo(layout, "e")
    assert not os.path.exists(layout.skeletons_dir) and not os.path.exists(layout.mask_dir)

    lengths = run_length(layout, qapp)
    areas = run_area(layout, qapp)

    assert {stem: row["Source"] for stem, row in lengths.items()} == {
        "a": "gt",
        "b": "gt",
        "e": "gt",
    }
    assert float(lengths["e"][LENGTH_COL]) == 0
    assert {stem: row["Source"] for stem, row in areas.items()} == {"a": "gt", "b": "gt"}


@pytest.fixture
def measured_window(qapp, tmp_path, monkeypatch):
    """A window on the measured ``gt_project``; ``calls`` records what the bar was given."""
    from app.gui import canvas_gl, image_manager
    from app.gui.main_window import MainWindow

    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    layout, _ = make_gt_project(str(tmp_path / "proj"))
    _run(layout, qapp, "length")
    _run(layout, qapp, "area")
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    window.open_path(layout.root)
    end = time.monotonic() + 10
    while time.monotonic() < end and len(window._image_leaves()) < len(STEMS):
        QApplication.processEvents()
    calls: list[dict] = []
    real = window.metrics_bar.set_metrics
    window.metrics_bar.set_metrics = lambda *a, **k: calls.append(k) or real(*a, **k)
    yield window, calls
    window.close()
    window.deleteLater()
    del window
    flush_deleted()
    QApplication.processEvents()


def _show(window, calls, stem) -> dict:
    """Select ``stem`` and wait for both values: the kwargs the bar was given last."""
    item = window.library_index.item_for(stem)
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        QApplication.processEvents()
        last = calls[-1] if calls else {}
        if last.get("name") == stem and last.get("length") is not None and last.get("area"):
            break
    return calls[-1]


def test_the_metrics_bar_names_the_source_and_the_skip_reason(measured_window):
    window, calls = measured_window
    bar = window.metrics_bar

    _show(window, calls, "g")
    assert "gt/skeletons/g.png (GT)" in bar._length_cell.toolTip()
    assert "mask/g.png (prediction)" in bar._area_cell.toolTip()
    assert bar._status_value.text() == "Mask: prediction · Skeleton: GT · No classes"
    assert bar._status_cell.toolTip().startswith("Overlay and Side by side draw the GT skeleton")
    assert not bar._length_value.property("stale")

    _show(window, calls, "p")
    assert "skeletons/p_fake.png (prediction)" in bar._length_cell.toolTip()
    assert calls[-1]["length_note"] is None and calls[-1]["area_note"] is None

    shown = _show(window, calls, "s")
    assert shown["length_note"] == (
        "A GT skeleton exists (gt/skeletons/) but was not used: it is not the photo's size. "
        "Measure read the prediction."
    )
    assert shown["length_note"] in bar._length_cell.toolTip()

    shown = _show(window, calls, "u")
    assert shown["length_note"] == (
        "A GT skeleton exists (gt/skeletons/) but was not used: it equals the prediction. "
        "Measure read the prediction."
    )

    shown = _show(window, calls, "x")
    assert shown["length_note"] == (
        "A GT skeleton exists (gt/skeletons/) but was not used: it or its photo could not be "
        "read. Measure read the prediction."
    )
    assert shown["area_note"] == (
        "A GT mask exists (gt/mask/) but was not used: it or its photo could not be read. "
        "Measure read the prediction."
    )
    assert shown["area_note"] in bar._area_cell.toolTip()


# ------------------------------------------------------------------ review round 1
class _NoteIndex:
    """A measured image whose sidecar lists a skipped GT file for both metrics (stale when
    the rows say ``gt``)."""

    root = None  # no project file is read: the default field of view (O3)

    def __init__(self, source: str) -> None:
        self.source = source

    def lookup(self, _name, load=False):
        from app.data_processing.measurement_index import Measurement

        return Measurement(1.0, 2.0, None, self.source, self.source)

    def needs_load(self) -> bool:
        return False

    def outdated(self, _name) -> dict:
        return {"length": False, "area": False}

    def sources(self) -> dict:
        return {"length": "root_lengths.csv", "area": "root_areas.csv"}

    def by_content(self) -> dict:
        return {"length": True, "area": True}

    def calibrations(self) -> dict:
        return {"length": None, "area": None}

    def method_notes(self, _current) -> dict:
        return {"length": "", "area": ""}

    def gt_skip_reason(self, _name) -> dict:
        return {"length": "size", "area": "unreadable"}

    def measured_from(self, _name) -> dict:
        return {"length": ("x", self.source), "area": ("y", self.source)}


def test_the_skip_note_shows_only_beside_a_prediction_row():
    from types import SimpleNamespace

    from app.gui.window.metrics import GT_NOT_USED, MetricsController

    shown: list[dict] = []
    window = SimpleNamespace(
        image_manager=SimpleNamespace(
            mask_state=lambda _n: "gt",
            skeleton_state=lambda _n: "gt",
            has_gt_labels=lambda _n: False,
        ),
        display_controller=SimpleNamespace(current_name="a"),
        metrics_bar=SimpleNamespace(
            set_metrics=lambda **k: shown.append(k), set_fov=lambda *a: None
        ),
    )
    controller = MetricsController(window)

    controller.index = _NoteIndex("gt")  # measured from the GT file: no "was not used"
    controller.refresh("a")
    assert (shown[-1]["length_note"], shown[-1]["area_note"]) == (None, None)

    controller.index = _NoteIndex("prediction")
    controller.refresh("a")
    assert shown[-1]["length_note"] == GT_NOT_USED.format(
        noun="skeleton", folder="skeletons", why="it is not the photo's size"
    )
    assert shown[-1]["area_note"] == GT_NOT_USED.format(
        noun="mask", folder="mask", why="it or its photo could not be read"
    )


def test_the_skip_note_for_a_gt_mask_of_another_size_says_so():
    from types import SimpleNamespace

    from app.gui.window.metrics import GT_NOT_USED, MetricsController

    class _SizeIndex(_NoteIndex):
        def gt_skip_reason(self, _name) -> dict:
            return {"area": "size"}

    shown: list[dict] = []
    window = SimpleNamespace(
        image_manager=SimpleNamespace(
            mask_state=lambda _n: "gt",
            skeleton_state=lambda _n: "gt",
            has_gt_labels=lambda _n: False,
        ),
        display_controller=SimpleNamespace(current_name="a"),
        metrics_bar=SimpleNamespace(
            set_metrics=lambda **k: shown.append(k), set_fov=lambda *a: None
        ),
    )
    controller = MetricsController(window)
    controller.index = _SizeIndex("prediction")
    controller.refresh("a")
    assert shown[-1]["area_note"] == GT_NOT_USED.format(
        noun="mask", folder="mask", why="it is not the photo's size"
    )
    assert shown[-1]["area_note"] == (
        "A GT mask exists (gt/mask/) but was not used: it is not the photo's size. "
        "Measure read the prediction."
    )


def test_a_failed_provenance_write_removes_the_earlier_sidecar(gt_project, qapp, monkeypatch):
    layout, _ = gt_project
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")
    assert _sidecar(csv_path)["inputs"]["gt"]["skipped"]

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(measure_run.provenance, "write_sidecar", broken)
    # Force a re-measure: nothing changed, so the run would stop at "Measure again?".
    os.remove(csv_path + ".inputs.json")
    _run(layout, qapp, "length")

    assert os.path.isfile(csv_path)
    # an earlier run's inputs.gt.skipped can never pair with the new CSV
    assert not os.path.exists(csv_path + ".provenance.json")
    index = MeasurementIndex(layout.root)
    index.prewarm()
    assert index.gt_skip_reason("s") == {}


@pytest.mark.parametrize(
    "spec, words",
    [(measure_run.LENGTH, "generated skeletons (*_fake.png)"), (measure_run.AREA, "masks (*.png)")],
    ids=["length", "area"],
)
def test_a_run_without_gt_inputs_records_the_prediction_rule(tmp_path, spec, words):
    """The legacy shims measure predictions only: their provenance says so."""
    images = _make_dataset(tmp_path / "in", spec.kind)
    run = measure_run.MeasureRun(spec, images, str(tmp_path / "out"))
    run.compute()
    rec = _sidecar(run.write())
    assert (rec["parameters"]["source_rule"], rec["parameters"]["input"]) == ("prediction", words)
    assert "gt" not in rec["inputs"]

    gt_first = measure_run.MeasureRun(spec, images, str(tmp_path / "gt"), gt_files={})
    gt_first.compute()
    assert _sidecar(gt_first.write())["parameters"]["source_rule"] == "gt-first"


def test_a_browse_step_does_not_list_the_gt_folders(gt_project, qapp, monkeypatch):
    """A case-sensitive disk (Linux) simulated: the GUI thread finds a GT file by its exact
    name, else in the listing read with the CSVs on the worker; it never lists gt/."""
    from app import project_layout

    layout, _ = gt_project
    os.remove(layout.gt_skeleton_path("o"))  # o's GT skeleton named O.png: a case variant
    _write_png(os.path.join(layout.gt_skeletons_dir, "O.png"), other_skeleton())
    _run(layout, qapp, "length")
    _run(layout, qapp, "area")
    index = MeasurementIndex(layout.root)
    index.prewarm()  # the worker's load lists gt/ (once)

    listed: list[str] = []
    real_scandir = os.scandir

    def scandir(path="."):
        listed.append(os.path.normcase(os.path.abspath(os.fspath(path))))
        return real_scandir(path)

    monkeypatch.setattr(project_layout, "_ignores_case", lambda _directory: False)
    monkeypatch.setattr(os, "scandir", scandir)
    for stem in ("p", "g", "s", "o", "x", "nothing"):
        index.outdated(stem)
        index.measured_from(stem)
        index.gt_skip_reason(stem)
    monkeypatch.setattr(os, "scandir", real_scandir)

    gt_dir = os.path.normcase(os.path.abspath(layout.gt_dir))
    assert [path for path in listed if path.startswith(gt_dir)] == []
    # the case variant is still found, by the name it has on disk
    assert index.measured_from("o")["length"] == ("gt/skeletons/O.png", "gt")
    assert index.outdated("o") == {"length": False, "area": False}


def _age(*folders: str, seconds: float = -60.0) -> None:
    """Move every file of ``folders`` back in time by ``seconds``."""
    for folder in folders:
        for entry in os.scandir(folder):
            st = entry.stat()
            os.utime(entry.path, ns=(st.st_atime_ns, st.st_mtime_ns + int(seconds * 1e9)))


def _by_time(layout: ProjectLayout, images: dict[str, str], csv_path: str) -> Freshness:
    """The status line's count of length without a record (file times)."""
    return input_freshness(
        layout.skeletons_dir,
        "_fake.png",
        csv_path,
        record=None,
        project_root=layout.root,
        images=images,
        gt_folder=layout.gt_skeletons_dir,
    )


def test_without_a_record_the_bar_and_the_status_line_compare_the_gt_file_only(gt_project, qapp):
    layout, images = gt_project
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")
    os.remove(csv_path + ".inputs.json")  # no record: file times
    _age(layout.skeletons_dir, layout.gt_skeletons_dir)
    measured_at = time.time_ns()
    os.utime(csv_path, ns=(measured_at, measured_at))  # the CSV is the newest file
    later = (measured_at + 60_000_000_000,) * 2  # explicit: file-time ticks are coarse

    def outdated(stem: str) -> bool:
        index = MeasurementIndex(layout.root)
        index.prewarm()
        return index.outdated(stem)["length"]

    assert _by_time(layout, images, csv_path).changed == 0
    assert not outdated("g") and not outdated("s")
    # a regenerated prediction of an image with a GT file: the GT file is what Measure reads
    _write_png(layout.skeleton_path("g"), np.roll(prediction_skeleton(), 9, axis=1))
    os.utime(layout.skeleton_path("g"), ns=later)
    assert _by_time(layout, images, csv_path).changed == 0 and not outdated("g")
    write_gt_skeleton(layout, "g", other_skeleton())  # the GT file changed after the CSV
    os.utime(layout.gt_skeleton_path("g"), ns=later)
    assert _by_time(layout, images, csv_path).changed == 1 and outdated("g")


def test_a_csv_without_source_is_outdated_beside_a_gt_file(tmp_path):
    """A CSV written before WG-T8 measured predictions only: where a GT file exists now,
    Measure would read it, whatever the file times."""
    layout = ProjectLayout(str(tmp_path))
    images = {stem: write_photo(layout, stem) for stem in ("a", "b")}
    for stem in ("a", "b"):
        _write_png(layout.skeleton_path(stem), prediction_skeleton())
    write_gt_skeleton(layout, "a")
    rows = [
        {"Image": stem, "Tube": None, "Position": None, "Date": None, "Time": None, LENGTH_COL: 1.0}
        for stem in ("a", "b")
    ]
    csv_path = _csv_path(layout, "length")
    metrics.write_metric_csv(rows, csv_path, LENGTH_CSV_HEADERS[:7])  # no Source column
    _age(layout.skeletons_dir, layout.gt_skeletons_dir)
    os.utime(csv_path)

    index = MeasurementIndex(layout.root)
    index.prewarm()
    assert index.outdated("a")["length"] is True and index.outdated("b")["length"] is False
    assert _by_time(layout, images, csv_path).changed == 1


def test_a_removed_gt_file_counts_its_image_once(gt_project, qapp):
    layout, images = gt_project
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")

    def freshness() -> Freshness:
        return input_freshness(
            layout.skeletons_dir,
            "_fake.png",
            csv_path,
            record=load_inputs_record(csv_path),
            project_root=layout.root,
            images=images,
            gt_folder=layout.gt_skeletons_dir,
        )

    os.remove(layout.gt_skeleton_path("g"))  # g is measured from its prediction now
    fresh = freshness()
    assert (fresh.new, fresh.removed) == (1, 0)  # one image, counted once
    os.remove(layout.gt_skeleton_path("o"))  # o has no prediction: it is no longer an input
    fresh = freshness()
    assert (fresh.inputs, fresh.new, fresh.removed) == (len(STEMS) - 1, 1, 1)


# ------------------------------------------------------------------ review round 3
def test_without_a_record_a_prediction_row_compares_its_prediction_too(gt_project, qapp):
    """s was measured from its prediction (its GT file is not the photo's size): a
    regenerated prediction outdates it, by file time too; a gt row ignores its prediction."""
    layout, _ = gt_project
    _run(layout, qapp, "length")
    csv_path = _csv_path(layout, "length")
    os.remove(csv_path + ".inputs.json")  # no record: file times
    _age(layout.skeletons_dir, layout.gt_skeletons_dir)
    measured_at = time.time_ns()
    os.utime(csv_path, ns=(measured_at, measured_at))
    for stem in ("s", "g"):  # both predictions regenerated after the CSV
        _write_png(layout.skeleton_path(stem), np.roll(prediction_skeleton(), 9, axis=1))
        os.utime(layout.skeleton_path(stem), ns=(measured_at + 60_000_000_000,) * 2)

    index = MeasurementIndex(layout.root)
    index.prewarm()

    assert index.outdated("s")["length"] is True  # the file its row was measured from
    assert index.outdated("g")["length"] is False  # a gt row: its GT file only


def _case_sensitive(folder: str) -> bool:
    probe = os.path.join(folder, "Probe")
    with open(probe, "wb"):
        pass
    try:
        return not os.path.exists(os.path.join(folder, "probe"))
    finally:
        os.remove(probe)


def test_with_both_names_the_bar_compares_the_file_measure_read(tmp_path, qapp):
    """A case-sensitive disk with gt/skeletons/o.png and O.png: Measure reads o.png (named
    like the image o.jpg, as split_inputs keeps it); the bar compares that file, not the
    listing's pick (O.png sorts first)."""
    layout = ProjectLayout(str(tmp_path))
    os.makedirs(layout.gt_skeletons_dir)
    if not _case_sensitive(layout.gt_skeletons_dir):
        pytest.skip("needs a case-sensitive file system (runs on the ubuntu CI)")
    write_photo(layout, "o")
    write_gt_skeleton(layout, "o")
    _write_png(os.path.join(layout.gt_skeletons_dir, "O.png"), other_skeleton())

    _run(layout, qapp, "length")
    index = MeasurementIndex(layout.root)
    index.prewarm()

    assert index.measured_from("o")["length"] == ("gt/skeletons/o.png", "gt")
    assert index.outdated("o")["length"] is False


def test_the_image_named_gt_file_wins_over_another_listed_one(gt_project, qapp):
    """The same rule on any disk: the listing names another file for o's stem key (what a
    case-sensitive disk lists for O.png beside o.png); the bar still compares o.png."""
    layout, _ = gt_project
    _run(layout, qapp, "length")
    index = MeasurementIndex(layout.root)
    index.prewarm()
    other = os.path.join(layout.gt_skeletons_dir, "o_listed.png")
    _write_png(other, other_skeleton())
    index._gt_listing["length"]["o"] = other  # as listed beside o.png

    assert index.measured_from("o")["length"] == ("gt/skeletons/o.png", "gt")
    assert index.outdated("o")["length"] is False
    os.remove(layout.gt_skeleton_path("o"))  # only the listed file left: it is the GT file
    assert index.measured_from("o")["length"] == ("gt/skeletons/o_listed.png", "gt")

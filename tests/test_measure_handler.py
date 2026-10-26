"""SkeletonHandler Measure flow: jobs, feedback, Retry, cancel, thread release, is_busy.

Covers RB-03/FR-06 (locked CSV -> Retry), RB-07 (one job at a time, Cancel),
RB-08 (thread released only after it finished), AR-07 (named feedback,
``is_busy(kind)``). Uses a fake main window: no MainWindow internals needed.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

from app import provenance
from app.gui.feedback import RecordingFeedback
from app.handlers.job_registry import JobRegistry
from app.handlers.skeleton_handler import SkeletonHandler
from app.handlers.window_feedback import WindowFeedback
from app.inference import measure_run
from app.io_utils import FileLockedError


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _IdleGeneration:
    def is_busy(self) -> bool:
        return False


def _project(tmp_path, n=4, skeletons=True, masks=True):
    for i in range(n):
        img = np.zeros((64, 80), np.uint8)
        cv2.line(img, (0, i), (79, 40 + i), 255, 2)
        name = f"x_T{i + 1}_L2_2024.01.02_01020{i}_001"
        cv2.imwrite(str(tmp_path / f"{name}.jpg"), img)
        if skeletons:
            (tmp_path / "skeletons").mkdir(exist_ok=True)
            cv2.imwrite(str(tmp_path / "skeletons" / f"{name}_fake.png"), img)
        if masks:
            (tmp_path / "mask").mkdir(exist_ok=True)
            cv2.imwrite(str(tmp_path / "mask" / f"{name}.png"), img)
    return tmp_path


def _handler(tmp_path, qapp, jobs=None):
    mw = SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=str(tmp_path), images={}),
        feedback=RecordingFeedback(),
        jobs=jobs if jobs is not None else JobRegistry(),
    )
    return SkeletonHandler(mw, generation=_IdleGeneration()), mw


def _wait_idle(handler, qapp, timeout=20.0):
    end = time.monotonic() + timeout
    while handler.is_busy() or handler.main_window.jobs.is_busy():
        assert time.monotonic() < end, "measure did not finish"
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


def test_measure_length_runs_as_named_job_with_feedback(tmp_path, qapp):
    _project(tmp_path)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    thread = handler.calculator_thread
    assert thread is not None
    assert mw.jobs.running_name() == "Measuring root length"
    _wait_idle(handler, qapp)

    assert handler.calculator_thread is None
    # RB-08: waited for, then deleteLater()'d; deleting a still-running QThread would
    # abort the process ("QThread: Destroyed while thread is still running").
    assert sip.isdeleted(thread)
    names = mw.feedback.names()
    assert names[0] == "task_started" and "task_finished" in names
    label = mw.feedback.calls[0].args[0]
    assert "root length" in label.lower() and "4" in label
    assert mw.feedback.calls[0].kwargs["cancelable"] is True
    assert os.path.isfile(tmp_path / "skeletons" / "root_lengths.csv")
    assert os.path.isfile(tmp_path / "skeletons" / "root_lengths.csv.provenance.json")


def test_measure_area_reads_png_masks(tmp_path, qapp):
    _project(tmp_path, skeletons=False)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    assert mw.jobs.running_name() == "Measuring root area"
    _wait_idle(handler, qapp)
    assert handler.area_calculator_thread is None
    assert os.path.isfile(tmp_path / "mask" / "root_areas.csv")


def test_is_busy_reports_each_kind(tmp_path, qapp):
    handler, _ = _handler(tmp_path, qapp)
    assert handler.is_busy() is False
    handler.calculator_thread = SimpleNamespace(isRunning=lambda: True)
    assert handler.is_busy("length") and handler.is_busy()
    assert not handler.is_busy("area")


def test_skeleton_generation_makes_length_busy(tmp_path, qapp):
    handler, _ = _handler(tmp_path, qapp)
    handler.skeleton_handler = SimpleNamespace(is_busy=lambda: True)
    assert handler.is_busy("length")
    assert not handler.is_busy("area")


def test_refused_while_another_job_runs(tmp_path, qapp):
    _project(tmp_path)
    jobs = SimpleNamespace(
        try_start=lambda name, thread: False,
        running_name=lambda: "Generating skeletons",
        is_busy=lambda: True,
    )
    handler, mw = _handler(tmp_path, qapp, jobs=jobs)
    handler.calculate_root_length()
    assert handler.calculator_thread is None
    assert mw.feedback.names() == ["notify"]
    message, kind = mw.feedback.calls[0].args
    assert "Generating skeletons" in message and kind == "warn"


def test_missing_skeletons_is_a_warning_not_a_job(tmp_path, qapp):
    _project(tmp_path, skeletons=False)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    assert handler.calculator_thread is None
    assert mw.feedback.calls[-1].args[1] == "warn"
    assert not mw.jobs.is_busy()


def test_locked_csv_fails_softly_and_retry_saves_kept_rows(tmp_path, qapp, monkeypatch):
    _project(tmp_path)
    handler, mw = _handler(tmp_path, qapp)
    prompts = []
    monkeypatch.setattr(handler, "_offer_retry", lambda kind, msg: prompts.append((kind, msg)))

    def locked(results, filename, headers):
        raise FileLockedError(filename)

    monkeypatch.setattr(measure_run, "write_metric_csv", locked)
    handler.calculate_root_length()
    _wait_idle(handler, qapp)
    assert "task_failed" in mw.feedback.names()
    assert prompts and prompts[0][0] == "length"
    assert "root_lengths.csv is open in another program" in prompts[0][1]
    assert handler.calculator_thread is None

    monkeypatch.undo()  # the user closed Excel
    monkeypatch.setattr(measure_run, "run_metric_pool", pytest.fail)  # no re-measuring
    assert handler.retry_measure("length") is True
    _wait_idle(handler, qapp)
    assert mw.feedback.names()[-2:] == ["task_finished", "notify"]
    assert os.path.isfile(tmp_path / "skeletons" / "root_lengths.csv")
    assert handler.retry_measure("length") is False  # nothing left to retry


def test_cancel_stops_the_run_without_writing(tmp_path, qapp):
    _project(tmp_path, n=12)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    mw.feedback.cancel()  # the Cancel button
    _wait_idle(handler, qapp)
    assert mw.feedback.names()[-1] == "task_finished"
    assert "cancel" in mw.feedback.last_message.lower()
    assert not os.path.exists(tmp_path / "skeletons" / "root_lengths.csv")


def test_window_feedback_drives_task_progress_and_status():
    calls = []
    tp = SimpleNamespace(
        start=lambda label, cancelable=False, on_cancel=None: calls.append(("start", label)),
        set_progress=lambda pct: calls.append(("pct", pct)),
        set_counts=lambda done, total: calls.append(("counts", done, total)),
        finish=lambda msg=None: calls.append(("finish", msg)),
        fail=lambda msg: calls.append(("fail", msg)),
    )
    bar = SimpleNamespace(showMessage=lambda msg, ms=0: calls.append(("status", msg)))
    mw = SimpleNamespace(task_progress=tp, status_bar=bar)
    fb = WindowFeedback(mw)
    fb.task_started("Measuring root length")
    fb.task_progress(42)
    fb.task_progress(3, 12)
    fb.task_finished("done")
    fb.notify("saved")  # no notify() on the window -> status bar
    assert ("start", "Measuring root length") in calls
    assert ("pct", 42) in calls and ("counts", 3, 12) in calls
    assert ("finish", "done") in calls and ("status", "saved") in calls
    assert any(c[0] == "status" and "42%" in c[1] for c in calls)


def test_retry_prompt_buttons_drive_retry(tmp_path, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    handler, _ = _handler(tmp_path, qapp)
    retried = []
    monkeypatch.setattr(handler, "retry_measure", retried.append)
    handler._retry_jobs["area"] = object()
    handler._offer_retry("length", "root_lengths.csv is open in another program")
    handler._retry_box.button(QMessageBox.StandardButton.Retry).click()
    handler._offer_retry("area", "root_areas.csv is open in another program")
    handler._retry_box.button(QMessageBox.StandardButton.Cancel).click()
    qapp.processEvents()
    assert retried == ["length"]
    assert "area" not in handler._retry_jobs and handler._retry_box is None


# --------------------------------------------------------------------------- M2: mixed sizes

_MIXED = "The measured files have"


def _mixed_warnings(mw):
    return [
        call.args[0]
        for call in mw.feedback.calls
        if call.name == "notify" and call.args[1] == "warn" and call.args[0].startswith(_MIXED)
    ]


def _extra_image(tmp_path, name, shape, folder, *, suffix=".png", photo_shape=None):
    """One more image of the project (``photo_shape``, default ``shape``) with its file in
    ``folder`` at ``shape`` (rows, cols)."""
    cv2.imwrite(str(tmp_path / f"{name}.jpg"), np.zeros(photo_shape or shape, np.uint8))
    img = np.zeros(shape, np.uint8)
    cv2.line(img, (0, 0), (shape[1] - 1, shape[0] - 1), 255, 2)
    (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(tmp_path / folder / f"{name}{suffix}"), img)


def test_mixed_sizes_warn_once(tmp_path, qapp):
    _project(tmp_path, n=2, skeletons=False)
    _extra_image(tmp_path, "y_T9_L2_2024.01.02_010209_001", (48, 96), "mask")
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    warnings = _mixed_warnings(mw)
    assert len(warnings) == 1
    assert warnings[0].startswith("The measured files have 2 sizes (80x64 ×2, 96x48 ×1): ")
    assert warnings[0].endswith("one 18×13 mm field of view was applied to each.")


def test_same_size_files_do_not_warn(tmp_path, qapp):
    _project(tmp_path, n=3)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    _wait_idle(handler, qapp)
    assert os.path.isfile(tmp_path / "skeletons" / "root_lengths.csv")
    assert _mixed_warnings(mw) == []


def test_at_most_three_sizes_are_listed(tmp_path, qapp):
    _project(tmp_path, n=2, skeletons=False)
    for i, shape in enumerate([(48, 96), (50, 96), (52, 96)]):
        _extra_image(tmp_path, f"z_T{i + 5}_L2_2024.01.02_01020{i}_001", shape, "mask")
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)
    (message,) = _mixed_warnings(mw)
    assert message.startswith("The measured files have 4 sizes (80x64 ×2, ")
    assert message.count(" ×") == 3 and "…" in message


def _photo_size_project(tmp_path, photo_shapes):
    """Photos of ``photo_shapes`` (rows, cols), each with a model-size prediction (256 x 341)
    and a GT skeleton at the photo's size (``None``: no GT file for that image)."""
    (tmp_path / "skeletons").mkdir()
    (tmp_path / "gt" / "skeletons").mkdir(parents=True)
    for i, (photo, has_gt) in enumerate(photo_shapes):
        name = f"w_T{i + 1}_L2_2024.01.02_01020{i}_001"
        cv2.imwrite(str(tmp_path / f"{name}.jpg"), np.zeros(photo, np.uint8))
        pred = np.zeros((256, 341), np.uint8)
        cv2.line(pred, (0, 0), (340, 100 + i), 255, 2)
        cv2.imwrite(str(tmp_path / "skeletons" / f"{name}_fake.png"), pred)
        if has_gt:
            gt = np.zeros(photo, np.uint8)
            cv2.line(gt, (0, 5), (photo[1] - 1, photo[0] // 2), 255, 3)
            cv2.imwrite(str(tmp_path / "gt" / "skeletons" / f"{name}.png"), gt)
    return tmp_path


def test_photo_size_gt_beside_model_size_predictions_is_not_mixed(tmp_path, qapp):
    """Every measured file spans its photo's frame, so one field of view is right for each:
    GT files (photo size) and predictions (model size) are not compared with each other."""
    _photo_size_project(
        tmp_path, [((480, 640), True), ((480, 640), True), ((480, 640), False), ((480, 640), False)]
    )
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    _wait_idle(handler, qapp)

    rows = open(tmp_path / "skeletons" / "root_lengths.csv", encoding="utf-8").read()
    assert rows.count(",gt") == 2 and rows.count(",prediction") == 2
    record = provenance.read_sidecar(tmp_path / "skeletons" / "root_lengths.csv")
    assert record["inputs"]["sizes_px"] == {"640x480": 2, "341x256": 2}
    assert _mixed_warnings(mw) == []


def test_gt_files_of_two_sizes_warn_once(tmp_path, qapp):
    """GT files of two sizes mean photos of two sizes (two cameras): that is the signal."""
    _photo_size_project(
        tmp_path, [((480, 640), True), ((240, 320), True), ((480, 640), False), ((480, 640), False)]
    )
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_length()
    _wait_idle(handler, qapp)

    (message,) = _mixed_warnings(mw)
    assert message.startswith("The measured files have 2 sizes (640x480 ×1, 320x240 ×1): ")


# --------------------------------------------------------------------------- M5: grey masks

_GREY = "grey levels: pixels above 127 were counted as root."


def _grey_warnings(mw):
    return [
        call.args[0]
        for call in mw.feedback.calls
        if call.name == "notify" and call.args[1] == "warn" and _GREY in call.args[0]
    ]


def test_grey_masks_warn_once(tmp_path, qapp):
    _project(tmp_path, n=3, skeletons=False)
    mask = next((tmp_path / "mask").iterdir())
    grey = cv2.imread(str(mask), cv2.IMREAD_GRAYSCALE)
    grey[0, 0] = 100
    cv2.imwrite(str(mask), grey)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)
    assert _grey_warnings(mw) == ["1 mask has grey levels: pixels above 127 were counted as root."]


def test_binary_masks_and_length_runs_do_not_warn_about_grey(tmp_path, qapp):
    _project(tmp_path, n=2)
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)
    handler.calculate_root_length()
    _wait_idle(handler, qapp)
    assert _grey_warnings(mw) == []


# --------------------------------------------------------------------------- O3: project FOV


def _write_project_file(tmp_path, text):
    (tmp_path / "sprouts_project.json").write_text(text, encoding="utf-8")


def _warnings(mw):
    return [c.args[0] for c in mw.feedback.calls if c.name == "notify" and c.args[1] == "warn"]


def test_measure_reads_the_project_file(tmp_path, qapp):
    import csv

    from app.config import AREA_VALUE_COLUMN, Calibration
    from app.inference import metrics

    _project(tmp_path, n=2, skeletons=False)
    _write_project_file(
        tmp_path, '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}}'
    )
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    csv_path = tmp_path / "mask" / "root_areas.csv"
    record = provenance.read_sidecar(csv_path)
    assert record["calibration"]["source"] == "project"
    assert record["calibration"]["fov_height_mm"] == 13.5
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    for row in rows:
        mask = metrics.load_mask(str(tmp_path / "mask" / f"{row['Image']}.png"))
        expected = metrics.root_area_mm2(mask, calibration=Calibration(18.0, 13.5))
        assert float(row[AREA_VALUE_COLUMN]) == round(expected, 2)
    assert not any("sprouts_project.json" in w for w in _warnings(mw))


def test_an_invalid_project_file_warns_once_and_measures_with_the_default(tmp_path, qapp):
    _project(tmp_path, n=2, skeletons=False)
    _write_project_file(tmp_path, "{")
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    project_warnings = [w for w in _warnings(mw) if "sprouts_project.json" in w]
    assert project_warnings == [
        "sprouts_project.json was not used (it is not valid JSON): "
        "measured with the default field of view 18×13 mm."
    ]
    record = provenance.read_sidecar(tmp_path / "mask" / "root_areas.csv")
    assert record["calibration"]["source"] == "default"


def test_files_of_another_shape_warn_once(tmp_path, qapp):
    _write_project_file(
        tmp_path,
        '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5, '
        '"reference_size_px": [640, 480]}}',
    )
    for i in range(2):
        _extra_image(tmp_path, f"v_T{i + 1}_L2_2024.01.02_01020{i}_001", (60, 80), "mask")
    _extra_image(tmp_path, "v_T9_L2_2024.01.02_010209_001", (48, 96), "mask")
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    # One odd file is one cause: the size and shape warnings are one (review M2).
    sizes = [w for w in _warnings(mw) if "another shape" in w or w.startswith(_MIXED)]
    assert sizes == [
        "The measured files have 2 sizes (80x60 ×2, 96x48 ×1), and 1 has another shape than "
        "the 640×480 reference in sprouts_project.json: one 18×13.5 mm field of view was "
        "applied to each whole image."
    ]


def test_files_all_of_another_shape_warn_once(tmp_path, qapp):
    _write_project_file(
        tmp_path,
        '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5, '
        '"reference_size_px": [640, 480]}}',
    )
    for i in range(2):
        _extra_image(tmp_path, f"v_T{i + 1}_L2_2024.01.02_01020{i}_001", (48, 96), "mask")
    handler, mw = _handler(tmp_path, qapp)
    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    sizes = [w for w in _warnings(mw) if "another shape" in w or w.startswith(_MIXED)]
    assert sizes == [
        "2 measured files have another shape than the 640×480 reference in "
        "sprouts_project.json: the field of view was applied to each whole image."
    ]


def test_a_refused_run_says_nothing_about_the_project_file(tmp_path, qapp):
    _project(tmp_path, n=2, skeletons=False)
    _write_project_file(tmp_path, "{")
    jobs = SimpleNamespace(
        try_start=lambda name, thread: False,
        running_name=lambda: "Generating skeletons",
        is_busy=lambda: True,
    )
    handler, mw = _handler(tmp_path, qapp, jobs=jobs)
    handler.calculate_root_area()
    assert handler.area_calculator_thread is None
    assert [w for w in _warnings(mw) if "sprouts_project.json" in w] == []
    assert any("Generating skeletons" in w for w in _warnings(mw))

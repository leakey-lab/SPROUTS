"""Measure calculator threads: error boundary, retry and cancellation (RB-03, AR-01, RB-07)."""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QCoreApplication, Qt

from app.handlers.worker_base import GuardedThread
from app.inference import measure_run
from app.inference.measure_run import LENGTH
from app.inference.metric_thread import MetricCalculatorThread
from app.inference.root_area_inference_handler import RootAreaCalculatorThread
from app.inference.root_length_inference_handler import RootLengthCalculatorThread
from app.io_utils import FileLockedError


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def _skeletons(folder, n=3):
    folder.mkdir(parents=True, exist_ok=True)
    images = {}
    for i in range(n):
        img = np.zeros((64, 80), np.uint8)
        cv2.line(img, (0, i), (79, 40 + i), 255, 1)
        name = f"x_T{i + 1}_L2_2024.01.02_010203_001"
        path = folder / f"{name}_fake.png"
        cv2.imwrite(str(path), img)
        images[name] = str(path)
    return images


def _run(thread, qapp):
    """Start ``thread`` and pump events until its queued signals were delivered."""
    got = {"finished": [], "failed": [], "cancelled": [], "progress": []}
    thread.finished.connect(got["finished"].append)
    thread.failed.connect(got["failed"].append)
    thread.cancelled.connect(lambda: got["cancelled"].append(True))
    thread.progress.connect(got["progress"].append)
    thread.start()
    assert thread.wait(20000)
    qapp.processEvents()
    return got


def _lock(monkeypatch):
    def locked(results, filename, headers):
        raise FileLockedError(filename)

    monkeypatch.setattr(measure_run, "write_metric_csv", locked)


def test_threads_keep_their_public_signals_and_constructors(tmp_path):
    length = RootLengthCalculatorThread({"a": "p"}, str(tmp_path))
    area = RootAreaCalculatorThread({"a": "p"}, str(tmp_path))
    for thread, attr in ((length, "fake_images"), (area, "mask_images")):
        assert isinstance(thread, GuardedThread)
        assert getattr(thread, attr) == {"a": "p"}
        assert thread.output_dir == str(tmp_path)
        for sig in ("finished", "progress", "failed", "cancelled"):
            assert hasattr(thread, sig)


def test_success_emits_finished_with_csv_path(tmp_path, qapp):
    images = _skeletons(tmp_path / "skeletons")
    got = _run(RootLengthCalculatorThread(images, str(tmp_path / "skeletons")), qapp)
    assert got["failed"] == [] and got["cancelled"] == []
    assert got["finished"] == [os.path.join(str(tmp_path / "skeletons"), "root_lengths.csv")]
    assert got["progress"][-1] == 100


def test_empty_input_finishes_with_empty_path(tmp_path, qapp):
    got = _run(RootAreaCalculatorThread({}, str(tmp_path)), qapp)
    assert got["finished"] == [""]


def test_locked_csv_emits_failed_instead_of_aborting(tmp_path, qapp, monkeypatch):
    images = _skeletons(tmp_path / "skeletons")
    _lock(monkeypatch)
    thread = RootLengthCalculatorThread(images, str(tmp_path / "skeletons"))
    got = _run(thread, qapp)
    assert got["finished"] == []
    assert len(got["failed"]) == 1
    assert "root_lengths.csv is open in another program" in got["failed"][0]
    assert thread.job.rows is not None and len(thread.job.rows) == 3  # kept for Retry


def test_retry_rewrites_kept_rows_without_measuring_again(tmp_path, qapp, monkeypatch):
    images = _skeletons(tmp_path / "skeletons")
    _lock(monkeypatch)
    first = RootLengthCalculatorThread(images, str(tmp_path / "skeletons"))
    _run(first, qapp)
    monkeypatch.undo()  # the user closed Excel

    measured = []
    monkeypatch.setattr(measure_run, "run_metric_pool", lambda *a, **k: measured.append(1) or [])
    got = _run(MetricCalculatorThread(first.job), qapp)
    assert measured == []
    assert got["finished"] == [first.job.csv_path]
    assert os.path.isfile(first.job.csv_path)


def test_cancel_emits_cancelled_and_writes_no_csv(tmp_path, qapp):
    images = _skeletons(tmp_path / "skeletons", n=6)
    thread = RootLengthCalculatorThread(images, str(tmp_path / "skeletons"))
    # Cancel from the worker thread on the first progress tick (the Cancel button).
    thread.progress.connect(lambda _pct: thread.cancel(), Qt.ConnectionType.DirectConnection)
    got = _run(thread, qapp)
    assert got["finished"] == [] and got["failed"] == []
    assert got["cancelled"] == [True]
    assert thread.job.rows is None
    assert not os.path.exists(os.path.join(str(tmp_path / "skeletons"), "root_lengths.csv"))


def test_generic_thread_reports_its_spec(tmp_path):
    thread = MetricCalculatorThread(measure_run.MeasureRun(LENGTH, {}, str(tmp_path)))
    assert thread.spec is LENGTH

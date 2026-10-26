"""W13: a "saved" toast names the file and its folder; the full path goes to the status line.

After Measure the toast read "Saved <full CSV path>": a path is one unbreakable word, so
the toast elided it in the middle over two lines ("Saved C:\\Users\\…\\skeletons\\root_le…")
and the file name itself was cut. The toast now says which file was saved in which
project folder ("Saved root_lengths.csv in the skeletons folder"); the full path is
posted to the status line, whose tooltip shows all of it. The generation toasts
("Saved to <folder path>") follow the same rule.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from app.feedback import RecordingFeedback
from app.handlers import generate_skeleton_handler as gsh
from app.handlers import mask_generation_handler as mgh
from app.handlers.deps import folder_label
from app.handlers.job_registry import JobRegistry
from app.handlers.skeleton_handler import SkeletonHandler
from app.inference.batch_engine import BatchReport


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _IdleGeneration:
    def is_busy(self) -> bool:
        return False


def _project(root, n=3):
    (root / "skeletons").mkdir(parents=True)
    for i in range(n):
        img = np.zeros((64, 80), np.uint8)
        cv2.line(img, (0, i), (79, 40 + i), 255, 2)
        name = f"x_T{i + 1}_L2_2024.01.02_01020{i}_001"
        cv2.imwrite(str(root / f"{name}.jpg"), img)
        cv2.imwrite(str(root / "skeletons" / f"{name}_fake.png"), img)
    return root


def _measure_length(root, qapp):
    mw = SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=str(root), images={}),
        feedback=RecordingFeedback(),
        jobs=JobRegistry(),
    )
    handler = SkeletonHandler(mw, generation=_IdleGeneration())
    handler.calculate_root_length()
    deadline = time.monotonic() + 30
    while handler.is_busy() or mw.jobs.is_busy():
        assert time.monotonic() < deadline, "measure did not finish"
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    return mw.feedback


def _calls(feedback, name):
    return [call.args for call in feedback.calls if call.name == name]


def test_the_measure_toast_names_the_csv_and_its_folder_not_the_full_path(tmp_path, qapp):
    root = _project(tmp_path / "my project")
    csv_path = str(root / "skeletons" / "root_lengths.csv")

    feedback = _measure_length(root, qapp)

    assert os.path.isfile(csv_path)
    (message, kind), *_ = _calls(feedback, "notify")
    assert kind == "success"
    assert message == "Saved root_lengths.csv in the skeletons folder"
    assert str(root) not in message  # no path: nothing for the toast to elide mid-word


def test_the_full_csv_path_goes_to_the_status_line(tmp_path, qapp):
    root = _project(tmp_path / "my project")
    csv_path = str(root / "skeletons" / "root_lengths.csv")

    feedback = _measure_length(root, qapp)

    statuses = [message for message, *_ in _calls(feedback, "status")]
    assert f"Saved {csv_path}" in statuses
    (timeout,) = [args[1] for args in _calls(feedback, "status") if args[0].endswith(csv_path)]
    assert timeout >= 5000  # long enough to hover the status line for its tooltip


def test_a_measure_with_unreadable_images_still_posts_the_full_path(tmp_path, qapp):
    root = _project(tmp_path / "proj")
    # Its image is in the project, so the corrupt skeleton is measured and gets an Error row.
    (root / "x_T9_L2_2024.01.02_010209_001.jpg").write_bytes(b"jpg")
    (root / "skeletons" / "x_T9_L2_2024.01.02_010209_001_fake.png").write_bytes(b"not a png")

    feedback = _measure_length(root, qapp)

    (message, kind), *_ = _calls(feedback, "notify")
    assert kind == "warn" and "root_lengths.csv" in message and str(root) not in message
    assert f"Saved {root / 'skeletons' / 'root_lengths.csv'}" in [
        m for m, *_ in _calls(feedback, "status")
    ]


def test_folder_label_is_relative_to_the_project_when_inside_it(tmp_path):
    root = str(tmp_path / "proj")
    assert folder_label(os.path.join(root, "skeletons"), root) == "skeletons"
    assert folder_label(os.path.join(root, "skeletons") + os.sep, root) == "skeletons"
    legacy = os.path.join(root, "output", "mask")
    assert folder_label(legacy, root) == os.path.join("output", "mask")
    # The project folder itself, a folder outside it, or no project: the folder's own name.
    assert folder_label(root, root) == "proj"
    elsewhere = str(tmp_path / "elsewhere" / "out")
    assert folder_label(elsewhere, root) == "out"
    assert folder_label(elsewhere) == "out"


def _report(written=3, skipped=0):
    report = BatchReport(total=written)
    report.written = [f"{i}.jpg" for i in range(written)]
    report.skipped = [f"s{i}" for i in range(skipped)]
    return report


def test_the_mask_generation_toast_names_the_folder_and_the_status_line_the_path(tmp_path):
    root = tmp_path / "proj"
    out = str(root / "mask")
    window = SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=str(root), images={}),
        on_outputs_changed=lambda: None,
    )
    feedback = RecordingFeedback()
    handler = mgh.MaskGenerationHandler(window, feedback=feedback, jobs=JobRegistry())

    handler._on_finished(SimpleNamespace(report=_report(3, skipped=2)), out, [])

    (message, kind), *_ = _calls(feedback, "notify")
    assert kind == "success"
    assert message == "Generated 3 masks, kept 2 existing predictions. Saved to the mask folder"
    assert str(root) not in message
    assert f"Saved to {out}" in [m for m, *_ in _calls(feedback, "status")]


def test_the_skeleton_generation_toast_names_the_folder_and_the_status_line_the_path(tmp_path):
    root = tmp_path / "proj"
    out = str(root / "skeletons")
    loaded = []
    window = SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=str(root), images={}),
        load_results=loaded.append,
    )
    feedback = RecordingFeedback()
    handler = gsh.GenerateSkeletonHandler(window, feedback=feedback, jobs=JobRegistry())

    handler._on_finished(SimpleNamespace(report=_report(3)), out)

    (message, kind), *_ = _calls(feedback, "notify")
    assert kind == "success"
    assert message == "Skeletons saved to the skeletons folder"
    assert str(root) not in message
    assert f"Skeletons saved to {out}" in [m for m, *_ in _calls(feedback, "status")]
    assert loaded == [out]

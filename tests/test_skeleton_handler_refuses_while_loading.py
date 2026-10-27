"""Measure refuses at once while a folder loads (R7, O15).

The Measure buttons stay clickable while a folder loads (the loading card sits below the
top bar). The refusal used to come only when the job started: after the folders of the
project being left were listed on the GUI thread, or after a warning about that project
("No skeleton images found", "No images loaded" during the first load). Measure now
refuses before anything else, as Generate does; the check in ``_start`` stays for Retry
and "Measure again".
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.feedback import RecordingFeedback
from app.handlers import skeleton_handler as sh
from app.handlers.deps import LOADING_REFUSED
from app.handlers.job_registry import JobRegistry
from app.handlers.skeleton_handler import SkeletonHandler

LOADING = (LOADING_REFUSED, "warn")
ENTRY_POINTS = ["calculate_root_length", "calculate_root_area"]


class _IdleGeneration:
    def is_busy(self) -> bool:
        return False


def _project(folder, *, outputs: bool, older: bool = False):
    """Two photos; with ``outputs`` their skeletons and masks, with ``older`` also masks in
    the older ``output/mask`` folder (Measure Area then warns that it measures that one)."""
    img = np.zeros((48, 64), np.uint8)
    cv2.line(img, (0, 0), (63, 47), 255, 2)
    subs = [("skeletons", "_fake.png"), ("mask", ".png")] if outputs else []
    if older:
        subs.append((os.path.join("output", "mask"), ".png"))
    for stem in ("a", "b"):
        cv2.imwrite(str(folder / f"{stem}.jpg"), img)
        for sub, suffix in subs:
            os.makedirs(folder / sub, exist_ok=True)
            cv2.imwrite(str(folder / sub / f"{stem}{suffix}"), img)


def _handler(image_manager, *, loading: bool):
    window = SimpleNamespace(
        image_manager=image_manager,
        feedback=RecordingFeedback(),
        jobs=JobRegistry(),
        _loading_project=loading,
    )
    return SkeletonHandler(window, generation=_IdleGeneration()), window


def _notes(window):
    return [call.args for call in window.feedback.calls if call.name == "notify"]


@pytest.mark.parametrize("call", ENTRY_POINTS)
def test_nothing_of_the_open_project_is_read_while_a_folder_loads(
    tmp_path, qapp, monkeypatch, call
):
    _project(tmp_path, outputs=True, older=True)

    def fail(*_args, **_kwargs):
        pytest.fail("the project was resolved or listed while a folder loads")

    monkeypatch.setattr(sh, "project_layout_of", fail)
    monkeypatch.setattr(sh, "scan_png_files", fail)
    handler, window = _handler(
        SimpleNamespace(original_folder=str(tmp_path), images={}), loading=True
    )
    getattr(handler, call)()
    assert _notes(window) == [LOADING]
    assert "task_started" not in window.feedback.names()
    assert handler.calculator_thread is None and handler.area_calculator_thread is None
    assert not window.jobs.is_busy()


@pytest.mark.parametrize("call", ENTRY_POINTS)
@pytest.mark.parametrize("left", ["with_outputs", "without_outputs", "first_load"])
def test_the_only_message_while_a_folder_loads_is_the_refusal(tmp_path, qapp, call, left):
    """No message about the project being left: not its missing outputs, not its older
    mask folder, not 'No images loaded' while the first folder loads."""
    _project(tmp_path, outputs=left == "with_outputs", older=left == "with_outputs")
    folder = None if left == "first_load" else str(tmp_path)
    handler, window = _handler(SimpleNamespace(original_folder=folder, images={}), loading=True)
    getattr(handler, call)()
    assert _notes(window) == [LOADING]
    assert "task_started" not in window.feedback.names()


@pytest.mark.parametrize("call", ENTRY_POINTS)
def test_measure_runs_as_before_when_no_folder_loads(tmp_path, qapp, call):
    _project(tmp_path, outputs=True)
    handler, window = _handler(
        SimpleNamespace(original_folder=str(tmp_path), images={}), loading=False
    )
    getattr(handler, call)()
    assert window.feedback.names()[0] == "task_started"
    end = time.monotonic() + 20
    while handler.is_busy() or window.jobs.is_busy():
        assert time.monotonic() < end, "measure did not finish"
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert LOADING not in _notes(window)
    csv = "root_lengths.csv" if call == "calculate_root_length" else "root_areas.csv"
    folder = "skeletons" if call == "calculate_root_length" else "mask"
    assert (tmp_path / folder / csv).is_file()

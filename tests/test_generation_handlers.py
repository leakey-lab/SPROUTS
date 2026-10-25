"""Mask / skeleton generation handlers (offscreen Qt, CPU stub models, no checkpoints).

RB-01 / PI-04 / FR-04 / UXS-01: existing predictions -> one prompt (WG-T7: Keep / Replace
    predictions, gt/ is never touched); Keep is the default; Cancel starts nothing (so
    generate_all chains nothing).
PG-04 / PI-11: importing the handlers or building them loads neither torch nor a model.
PI-12 / UXS-02 / AR-07: progress through main_window.feedback, no modal success box.
PI-08 / UI-20: Cancel stops the thread; RB-07: refuses politely while another job runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from app import config
from app.gui.feedback import RecordingFeedback
from app.handlers import generate_skeleton_handler as gsh
from app.handlers import mask_generation_handler as mgh
from app.inference import runtime
from app.inference.mask_origin import OverwritePolicy
from tests.qt_teardown import flush_deleted


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubMaskModel(torch.nn.Module):
    def __init__(self, on_forward=None):
        super().__init__()
        torch.manual_seed(0)
        self.conv = torch.nn.Conv2d(3, 1, 1)
        self.on_forward = on_forward
        self.items = 0

    def forward(self, x):
        self.items += x.shape[0]
        if self.on_forward:
            self.on_forward()
        return torch.sigmoid(self.conv(x))


class _StubSkeletonModel:
    def run_batch(self, x):
        return torch.tanh(x)


class _Jobs:
    def __init__(self, accept=True):
        self.accept = accept
        self.started = []

    def try_start(self, name, thread):
        if not self.accept:
            return False
        self.started.append(name)
        thread.start()
        return True

    def running_name(self):
        return "Measuring root length"


@pytest.fixture
def project(tmp_path, qapp, monkeypatch):
    """A folder of 3 images, CPU runtime, stub models and a fake main window."""
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"w")
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(weights))
    stub = _StubMaskModel().eval()
    monkeypatch.setattr(runtime, "get_mask_model", lambda device=None: stub)
    monkeypatch.setattr(runtime, "get_skeleton_model", lambda device=None: _StubSkeletonModel())
    monkeypatch.setattr(config, "get_skeleton_batch_size", lambda refresh=False: 2)
    rng = np.random.default_rng(0)
    folder = tmp_path / "imgs"
    folder.mkdir()
    images = {}
    for stem in ("a", "b", "c"):
        path = folder / f"{stem}.jpg"
        Image.fromarray(rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)).save(path)
        images[f"{stem}.jpg"] = str(path)
    window = QWidget()
    window.image_manager = SimpleNamespace(images=images, original_folder=str(folder))
    window.feedback = RecordingFeedback()
    window.loaded = []
    window.outputs_changed = []
    window.load_results = window.loaded.append
    window.on_outputs_changed = lambda: window.outputs_changed.append(True)
    dialogs = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _n=name, **k: dialogs.append((_n, a)))
        )
    monkeypatch.setattr(mgh, "show_failure_report", lambda *a, **k: dialogs.append(("report", a)))
    # never block on the real modal prompt: tests that expect it inject confirm_overwrite
    monkeypatch.setattr(
        mgh, "ask_overwrite_policy", lambda *a, **k: pytest.fail("unexpected overwrite prompt")
    )
    monkeypatch.setattr(gsh, "show_failure_report", lambda *a, **k: dialogs.append(("report", a)))
    yield SimpleNamespace(folder=folder, window=window, stub=stub, dialogs=dialogs)
    runtime.get_runtime.cache_clear()
    window.deleteLater()
    flush_deleted()


def _wait(thread, qapp, timeout=30.0):
    assert thread is not None
    deadline = time.monotonic() + timeout
    while (thread.isRunning() or not thread.isFinished()) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    for _ in range(20):  # deliver the queued finished/failed/cancelled signals
        qapp.processEvents()
        time.sleep(0.005)
    assert thread.isFinished()


def _save_traced(path):
    Image.new("RGBA", (64, 48), (255, 255, 255, 255)).save(path)


def test_handler_modules_do_not_import_torch():
    code = (
        "import sys, app.handlers.mask_generation_handler, "
        "app.handlers.generate_skeleton_handler; print('torch' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert out.stdout.strip() == "False"


def test_constructing_the_handler_loads_no_model(project, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "get_mask_model", lambda device=None: calls.append(1))
    mgh.MaskGenerationHandler(project.window)
    assert calls == []


def test_existing_masks_keep_is_the_default_choice(project, qapp, monkeypatch):
    monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")  # (O4) no padding rows in stub.items
    mask_dir = project.folder / "mask"
    mask_dir.mkdir()
    _save_traced(mask_dir / "a.png")
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(mask_dir / "b.png")
    before = {n: (mask_dir / n).read_bytes() for n in ("a.png", "b.png")}
    seen = []

    def confirm(existing, total):
        seen.append((existing, total))
        return OverwritePolicy.KEEP_EXISTING

    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=confirm)
    assert handler.generate_masks() is True
    _wait(handler.generation_thread, qapp)

    assert seen == [(2, 3)]  # two of the three images already have a prediction
    assert {n: (mask_dir / n).read_bytes() for n in ("a.png", "b.png")} == before
    assert (mask_dir / "c.png").exists()
    assert project.stub.items == 1
    assert project.window.outputs_changed == [True]


def test_replace_all_replaces_every_prediction(project, qapp):
    mask_dir = project.folder / "mask"
    mask_dir.mkdir()
    _save_traced(mask_dir / "a.png")  # a prediction like any other
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda e, t: OverwritePolicy.OVERWRITE_ALL
    )
    handler.generate_masks()
    _wait(handler.generation_thread, qapp)
    with Image.open(mask_dir / "a.png") as im:
        assert im.mode == "L"


def test_cancel_in_the_prompt_starts_nothing(project, qapp):
    (project.folder / "mask").mkdir()
    _save_traced(project.folder / "mask" / "a.png")
    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=lambda e, t: None)
    assert handler.generate_masks() is False
    assert handler.generation_thread is None
    assert project.stub.items == 0
    assert "task_started" not in project.window.feedback.names()


def test_no_existing_masks_means_no_prompt(project, qapp):
    def confirm(existing, total):
        raise AssertionError("no prompt expected")

    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=confirm)
    assert handler.generate_masks() is True
    _wait(handler.generation_thread, qapp)
    assert sorted(os.listdir(project.folder / "mask")) == [
        "_sprouts_run.json",
        "_sprouts_runs.jsonl",
        "a.png",
        "b.png",
        "c.png",
    ]


def test_progress_goes_through_feedback_without_modal_success(project, qapp):
    handler = mgh.MaskGenerationHandler(project.window)
    handler.generate_masks()
    _wait(handler.generation_thread, qapp)
    fb = project.window.feedback
    labels = [c.args[0] for c in fb.calls if c.name == "task_started"]
    assert labels == ["Loading mask model", "Generating masks"]
    assert all(c.kwargs.get("cancelable") for c in fb.calls if c.name == "task_started")
    assert "task_progress" in fb.names() and "task_finished" in fb.names()
    assert "success" in [
        c.args[1] if len(c.args) > 1 else c.kwargs.get("kind")
        for c in fb.calls
        if c.name == "notify"
    ]
    assert project.dialogs == []
    assert not fb.busy


def test_model_failure_is_reported_once(project, qapp, monkeypatch):
    def unavailable(device=None):
        raise runtime.ModelUnavailableError("Mask model weights not found: x.pth")

    monkeypatch.setattr(runtime, "get_mask_model", unavailable)
    handler = mgh.MaskGenerationHandler(project.window)
    handler.generate_masks()
    _wait(handler.generation_thread, qapp)
    fb = project.window.feedback
    assert "task_failed" in fb.names()
    assert [d[0] for d in project.dialogs] == ["critical"]


def test_missing_weights_are_refused_before_starting(project, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(tmp_path / "none.pth"))
    handler = mgh.MaskGenerationHandler(project.window)
    assert handler.generate_masks() is False
    assert handler.generation_thread is None
    assert [d[0] for d in project.dialogs] == ["warning"]


def test_busy_job_registry_refuses_politely(project, qapp):
    project.window.jobs = _Jobs(accept=False)
    handler = mgh.MaskGenerationHandler(project.window)
    assert handler.generate_masks() is False
    assert handler.generation_thread is None
    notes = [c for c in project.window.feedback.calls if c.name == "notify"]
    assert notes and "Measuring root length" in notes[-1].args[0]
    assert project.dialogs == []


def test_jobs_registry_runs_the_thread(project, qapp):
    project.window.jobs = _Jobs(accept=True)
    handler = mgh.MaskGenerationHandler(project.window)
    assert handler.generate_masks() is True
    _wait(handler.generation_thread, qapp)
    assert project.window.jobs.started == ["Generating masks"]


def test_on_success_runs_after_the_job_registry_is_free(project, qapp):
    """generate_all chains skeletons via on_success: the mask job must be cleared first,
    otherwise main_window.jobs refuses the skeleton job."""
    from app.handlers.job_registry import JobRegistry

    registry = JobRegistry()
    project.window.jobs = registry
    busy_at_callback = []
    handler = mgh.MaskGenerationHandler(project.window)
    assert handler.generate_masks(on_success=lambda: busy_at_callback.append(registry.is_busy()))
    _wait(handler.generation_thread, qapp)
    assert busy_at_callback == [False]


def test_on_success_is_not_called_after_cancel_or_failure(project, qapp, monkeypatch):
    monkeypatch.setattr(config, "MASK_BATCH_SIZE", 1)
    calls = []
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda e, t: OverwritePolicy.OVERWRITE_ALL
    )
    project.stub.on_forward = lambda: handler.generation_thread.cancel()
    handler.generate_masks(on_success=lambda: calls.append("chained"))
    _wait(handler.generation_thread, qapp)

    def unavailable(device=None):
        raise runtime.ModelUnavailableError("missing")

    monkeypatch.setattr(runtime, "get_mask_model", unavailable)
    handler.generate_masks(on_success=lambda: calls.append("chained"))
    _wait(handler.generation_thread, qapp)
    assert calls == []


def test_cancel_stops_the_run_and_keeps_finished_masks(project, qapp, monkeypatch):
    monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")  # (O4) MASK_BATCH_SIZE sizes auto runs
    monkeypatch.setattr(config, "MASK_BATCH_SIZE", 1)
    handler = mgh.MaskGenerationHandler(project.window)
    project.stub.on_forward = lambda: handler.generation_thread.cancel()
    finished = []
    handler.generate_masks()
    handler.generation_thread.finished.connect(lambda *a: finished.append(a))
    _wait(handler.generation_thread, qapp)
    assert finished == []  # generate_all chains on finished: a cancelled run must not chain
    written = [n for n in os.listdir(project.folder / "mask") if n.endswith(".png")]
    assert 1 <= len(written) < 3
    fb = project.window.feedback
    finished_msgs = [c.args[0] for c in fb.calls if c.name == "task_finished"]
    assert finished_msgs[-1].startswith("Mask generation cancelled after")
    assert not fb.busy


def test_skeleton_handler_loads_results_and_reports(project, qapp):
    handler = gsh.GenerateSkeletonHandler(project.window)
    assert handler.generate_skeleton() is True
    _wait(handler._thread, qapp)
    out = os.path.normpath(os.path.join(str(project.folder), "skeletons"))
    assert project.window.loaded == [out]
    assert sorted(n for n in os.listdir(out) if n.endswith(".png")) == [
        "a_fake.png",
        "b_fake.png",
        "c_fake.png",
    ]
    fb = project.window.feedback
    assert [c.args[0] for c in fb.calls if c.name == "task_started"] == ["Generating skeletons"]
    assert project.dialogs == []


def test_skeleton_failures_get_one_report(project, qapp):
    (project.folder / "zz_bad.jpg").write_bytes(b"not an image")
    handler = gsh.GenerateSkeletonHandler(project.window)
    handler.generate_skeleton()
    _wait(handler._thread, qapp)
    reports = [d for d in project.dialogs if d[0] == "report"]
    assert len(reports) == 1 and "zz_bad.jpg" in str(reports[0][1])
    assert project.window.loaded  # results still load


def test_overwrite_prompt_defaults_to_keep(qapp):
    clicked = []

    def press_default():
        box = QApplication.activeModalWidget()
        clicked.append(box.defaultButton().text())
        box.defaultButton().click()

    QTimer.singleShot(50, press_default)
    policy = mgh.ask_overwrite_policy(None, 2, 5)
    assert policy is OverwritePolicy.KEEP_EXISTING
    assert "Keep predictions" in clicked[0]


def test_overwrite_prompt_escape_is_cancel(qapp):
    def press_escape():
        box = QApplication.activeModalWidget()
        box.escapeButton().click()

    QTimer.singleShot(50, press_escape)
    assert mgh.ask_overwrite_policy(None, 1, 5) is None

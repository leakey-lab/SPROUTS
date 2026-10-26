"""wf2 fixer #1: cancel/close robustness, CPU fallback and project/checkpoint lookup.

R1  -- a cancel that lands during the last batch must not start the chained job,
       and nothing may start once the window is closing.
R2  -- CPU batches are small so Cancel is honoured within a batch or two; closing
       while a job outlives the 5 s wait hides the window and keeps waiting.
R3  -- CUDA_VISIBLE_DEVICES="" (CUDA "available" with 0 devices) falls back to CPU.
CQ-04 -- one HandlerDependencies mixin supplies ``feedback`` / ``jobs``.
CQ-06 -- ImageManager.project_layout() is the one project-folder lookup.
CQ-12 -- SPROUTS_CHECKPOINTS_DIR relocates the model weights.
"""

from __future__ import annotations

import importlib
import os
import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

from app import config
from app.gui.main_window import MainWindow  # before any QApplication (QtWebEngine)
from app.handlers.job_registry import JobRegistry
from app.inference import runtime
from app.workers import GuardedThread


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spin(qapp, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _LastBatchCancelled(GuardedThread):
    """Cancel pressed during the final batch: the engine reports completion anyway."""

    def __init__(self):
        super().__init__()
        self.completed = False

    def work(self):
        self.cancel()
        self.completed = True  # done == total, so the run "completed"


# --------------------------------------------------------------------------- R1
def _last_batch_cancel(thread):
    """Stand-in inference: Cancel arrives while the only batch runs; all images done."""

    def run(*args, **kwargs):
        thread.cancel()
        from app.inference.batch_engine import BatchReport

        return BatchReport(total=8, written=[f"{i}.png" for i in range(8)])

    return run


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_cancel_in_the_last_batch_is_a_cancel_and_chains_nothing(qapp, monkeypatch, kind):
    from app.handlers import generate_skeleton_handler as gsh
    from app.handlers import mask_generation_handler as mgh
    from app.handlers.generation_base import GenerationHandlerBase
    from app.inference import mask_inference, skeleton_inference

    if kind == "mask":
        thread = mgh.MaskGenerationThread("in", "out")
        monkeypatch.setattr(mask_inference, "generate_masks", _last_batch_cancel(thread))
    else:
        thread = gsh.SkeletonGeneratorThread("in", "out")
        monkeypatch.setattr(
            skeleton_inference, "run_skeleton_generation", _last_batch_cancel(thread)
        )
    window = SimpleNamespace(jobs=JobRegistry(), feedback=None)
    handler = GenerationHandlerBase(window)
    calls, cancelled = [], []
    thread.cancelled.connect(lambda: cancelled.append(True))
    assert handler._start(thread, on_success=lambda: calls.append("chained"))
    assert _spin(qapp, lambda: not window.jobs.is_busy())
    _spin(qapp, lambda: False, timeout=0.2)
    assert not thread.completed and cancelled == [True] and calls == []


def test_job_registry_refuses_new_jobs_once_shut_down(qapp):
    registry = JobRegistry()
    registry.shutdown()
    thread = _LastBatchCancelled()
    assert registry.is_closing()
    assert registry.try_start("Generating skeletons", thread) is False
    assert not thread.isRunning()


# --------------------------------------------------------------------------- R3
def test_cuda_available_with_zero_devices_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    runtime.get_runtime.cache_clear()
    try:
        assert runtime.get_runtime().device.type == "cpu"
    finally:
        runtime.get_runtime.cache_clear()


def test_auto_skeleton_batch_with_zero_devices_never_queries_vram(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)

    def boom():
        raise AssertionError("mem_get_info must not be called without a device")

    monkeypatch.setattr(torch.cuda, "mem_get_info", boom)
    assert config._auto_skeleton_batch() == 64


# --------------------------------------------------------------------------- R2
def test_cpu_batches_are_small_enough_to_cancel_quickly(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert config.MASK_BATCH_SIZE_CPU <= 4
    assert config.SKELETON_BATCH_SIZE_CPU <= 8
    from app.inference import skeleton_inference

    assert skeleton_inference.default_skeleton_batch_size("cpu") == config.SKELETON_BATCH_SIZE_CPU


class _StubMask(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, 1, 1)
        self.sizes = []

    def forward(self, x):
        self.sizes.append(x.shape[0])
        return torch.sigmoid(self.conv(x))


def test_mask_generation_uses_the_cpu_batch_on_cpu(tmp_path, monkeypatch):
    from app.inference import mask_inference

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    runtime.get_runtime.cache_clear()
    src = tmp_path / "imgs"
    src.mkdir()
    for i in range(10):
        Image.fromarray(np.full((48, 64, 3), 20 * i, np.uint8)).save(src / f"T1_L{i}.png")
    model = _StubMask()
    try:
        report = mask_inference.generate_masks(str(src), str(tmp_path / "mask"), model=model)
    finally:
        runtime.get_runtime.cache_clear()
    assert report.batch_size == config.MASK_BATCH_SIZE_CPU
    assert max(model.sizes) <= config.MASK_BATCH_SIZE_CPU


class _SlowJobs:
    """A job that ignores the first stop_all timeout, then stops."""

    def __init__(self):
        self.calls = []
        self.closing = False

    def is_busy(self):
        return True

    def running_name(self):
        return "Generating masks"

    def shutdown(self):
        self.closing = True

    def stop_all(self, timeout_ms=5000):
        self.calls.append(timeout_ms)
        return len(self.calls) > 1


def test_close_hides_the_window_and_keeps_waiting_for_a_slow_job(qapp, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    statuses, hidden = [], []
    fake = SimpleNamespace(
        jobs=_SlowJobs(),
        feedback=SimpleNamespace(status=lambda msg, timeout_ms=0: statuses.append(msg)),
        hide=lambda: hidden.append(True),
    )
    assert MainWindow._stop_jobs_for_close(fake) is True
    assert fake.jobs.closing, "the registry must refuse new jobs before stopping"
    assert len(fake.jobs.calls) == 2 and hidden == [True]
    assert any("Stopping" in s for s in statuses)


# --------------------------------------------------------------------------- CQ-04
def test_handlers_share_one_dependencies_mixin():
    from app.feedback import NullFeedback
    from app.handlers.deps import HandlerDependencies
    from app.handlers.generation_base import GenerationHandlerBase
    from app.handlers.skeleton_handler import SkeletonHandler

    assert issubclass(GenerationHandlerBase, HandlerDependencies)
    assert issubclass(SkeletonHandler, HandlerDependencies)
    bare = HandlerDependencies(SimpleNamespace())
    assert isinstance(bare.feedback, NullFeedback) and bare.feedback is bare.feedback
    assert isinstance(bare.jobs, JobRegistry) and bare.jobs is bare.jobs
    injected = JobRegistry()
    window = SimpleNamespace(feedback="fb", jobs=injected)
    assert HandlerDependencies(window).feedback == "fb"
    assert HandlerDependencies(window).jobs is injected
    assert HandlerDependencies(window, jobs=JobRegistry()).jobs is not injected


# --------------------------------------------------------------------------- CQ-06
def test_image_manager_project_layout(tmp_path):
    from app.gui.image_manager import ImageManager

    manager = ImageManager()
    assert manager.project_layout() is None
    manager.images = {"a": str(tmp_path / "sub" / "a.png")}
    assert manager.project_layout().root == str(tmp_path / "sub")
    manager.original_folder = str(tmp_path)
    assert manager.project_layout().root == str(tmp_path)


def test_generation_base_project_folder_uses_project_layout(tmp_path):
    from app.gui.image_manager import ImageManager
    from app.handlers.generation_base import GenerationHandlerBase

    manager = ImageManager()
    manager.images = {"a": str(tmp_path / "a.png")}
    handler = GenerationHandlerBase(SimpleNamespace(image_manager=manager))
    assert handler.project_folder() == str(tmp_path)


# --------------------------------------------------------------------------- CQ-12
def test_checkpoints_dir_can_be_overridden(tmp_path, monkeypatch):
    from app import models

    monkeypatch.setenv("SPROUTS_CHECKPOINTS_DIR", str(tmp_path))
    try:
        cfg = importlib.reload(config)
        assert cfg.CHECKPOINTS_DIR == str(tmp_path)
        assert cfg.MASK_WEIGHTS_PATH == os.path.join(
            str(tmp_path), "mask_weights", "best_mask_model_V5.pth"
        )
        assert models.MODELS["skeleton"].path() == os.path.normpath(
            os.path.join(str(tmp_path), "skeletonizer", "latest_net_G.pth")
        )
        assert models.MODELS["mask"].relpath.startswith("checkpoints/")
    finally:
        monkeypatch.delenv("SPROUTS_CHECKPOINTS_DIR")
        importlib.reload(config)

"""R6: a generation run in which every input failed ends as a failure, not a success.

Repro [oom-always] of the review: out of memory even on single images wrote no mask,
yet the run ended with task_finished "Generated 0 masks" and Generate chained the
skeleton pass. Such a run now ends with task_failed; the one non-modal failure report
still lists every input (UXS-02: no modal box); nothing chains; skeleton results are
not loaded. A partial failure and a run with nothing to do keep their behaviour.
Offscreen Qt, CPU stub models, no checkpoints (fixtures as in test_generation_handlers).
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from app import config
from app.gui.feedback import RecordingFeedback
from app.handlers import generate_skeleton_handler as gsh
from app.handlers import mask_generation_handler as mgh
from app.handlers.generation_base import show_failure_report
from app.inference import runtime
from app.inference.mask_origin import OverwritePolicy
from tests.qt_teardown import flush_deleted


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubMaskModel(torch.nn.Module):
    """1x1-conv mask model; with ``oom`` set, every forward runs out of GPU memory."""

    def __init__(self):
        super().__init__()
        torch.manual_seed(0)
        self.conv = torch.nn.Conv2d(3, 1, 1)
        self.oom = False

    def forward(self, x):
        if self.oom:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        return torch.sigmoid(self.conv(x))


class _StubSkeletonModel:
    def run_batch(self, x):
        return torch.tanh(x)


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
    for stem in ("a", "b", "c"):
        image = Image.fromarray(rng.integers(0, 255, (48, 64, 3), dtype=np.uint8))
        image.save(folder / f"{stem}.jpg")
    window = QWidget()
    window.image_manager = SimpleNamespace(images={}, original_folder=str(folder))
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
    report_kwargs = []  # keyword arguments of each failure report (nothing_written)

    def report(*a, **k):
        dialogs.append(("report", a))
        report_kwargs.append(k)

    monkeypatch.setattr(mgh, "show_failure_report", report)
    monkeypatch.setattr(gsh, "show_failure_report", report)
    monkeypatch.setattr(
        mgh, "ask_overwrite_policy", lambda *a, **k: pytest.fail("unexpected overwrite prompt")
    )
    yield SimpleNamespace(
        folder=folder, window=window, stub=stub, dialogs=dialogs, report_kwargs=report_kwargs
    )
    runtime.get_runtime.cache_clear()
    window.deleteLater()
    flush_deleted()


def _wait(thread, qapp, timeout=30.0):
    """Wait for ``thread``, then deliver its queued signals and any chained callback."""
    assert thread is not None
    deadline = time.monotonic() + timeout
    while (thread.isRunning() or not thread.isFinished()) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    for _ in range(40):
        qapp.processEvents()
        time.sleep(0.005)
    assert thread.isFinished()


def _messages(feedback, name):
    return [c.args[0] for c in feedback.calls if c.name == name]


def _toast_kinds(feedback):
    return [c.args[1] for c in feedback.calls if c.name == "notify"]


def _unreadable(folder, *names):
    for name in names:
        (folder / name).write_bytes(b"not an image")


# --------------------------------------------------------------------------- every input failed
def test_mask_run_where_every_image_failed_is_a_failure(project, qapp, monkeypatch):
    monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")  # (O4) halving on out of memory
    project.stub.oom = True  # [oom-always]: even a single image runs out of memory
    chained = []
    handler = mgh.MaskGenerationHandler(project.window)
    assert handler.generate_masks(on_success=lambda: chained.append(True)) is True
    thread = handler.generation_thread
    _wait(thread, qapp)

    assert thread.report.written == [] and len(thread.report.failed) == 3
    fb = project.window.feedback
    assert "task_finished" not in fb.names()
    assert _messages(fb, "task_failed") == [
        "Mask generation failed: none of 3 images could be processed"
    ]
    assert "success" not in _toast_kinds(fb)
    assert not fb.busy
    # UXS-02: the one non-modal report lists every input with its reason; no modal box
    assert [kind for kind, _ in project.dialogs] == ["report"]
    _parent, title, _out, failed = project.dialogs[0][1]
    assert title == "Mask generation"
    assert sorted(name for name, _ in failed) == ["a.jpg", "b.jpg", "c.jpg"]
    assert all("out of memory" in reason for _, reason in failed)
    assert project.report_kwargs == [{"nothing_written": True}]  # no "Other outputs" line
    # nothing chains: Generate must not start the skeleton pass
    assert thread.completed is False
    assert chained == []
    assert project.window.outputs_changed == [True]


def test_skeleton_run_where_every_image_failed_is_a_failure(project, qapp):
    _unreadable(project.folder, "a.jpg", "b.jpg", "c.jpg")
    handler = gsh.GenerateSkeletonHandler(project.window)
    assert handler.generate_skeleton() is True
    thread = handler._thread
    _wait(thread, qapp)

    assert thread.report.written == [] and len(thread.report.failed) == 3
    fb = project.window.feedback
    assert "task_finished" not in fb.names()
    assert _messages(fb, "task_failed") == [
        "Skeleton generation failed: none of 3 images could be processed"
    ]
    assert "success" not in _toast_kinds(fb)
    assert [kind for kind, _ in project.dialogs] == ["report"]
    _parent, title, _out, failed = project.dialogs[0][1]
    assert title == "Skeleton generation"
    assert sorted(name for name, _ in failed) == ["a.jpg", "b.jpg", "c.jpg"]
    assert project.report_kwargs == [{"nothing_written": True}]
    assert thread.completed is False
    assert project.window.loaded == []  # load_results would toast "Results loaded"


def test_the_report_of_a_run_that_wrote_nothing_claims_no_other_outputs(qapp, tmp_path):
    parent = QWidget()
    failed = [("a.jpg", "UnidentifiedImageError: x"), ("b.jpg", "ItemOutOfMemory: y")]
    box = show_failure_report(
        parent, "Mask generation", str(tmp_path), failed, nothing_written=True
    )
    try:
        assert not box.isModal()
        assert "Other outputs" not in box.informativeText()
        assert "No output was written" in box.informativeText()
        assert str(tmp_path) in box.informativeText()
        assert box.detailedText().splitlines() == [f"{name}: {reason}" for name, reason in failed]
    finally:
        box.close()
        parent.deleteLater()
        flush_deleted()


# --------------------------------------------------------------------------- unchanged behaviour
def test_mask_partial_failure_still_completes_and_chains(project, qapp):
    _unreadable(project.folder, "zz_bad.jpg")
    chained = []
    handler = mgh.MaskGenerationHandler(project.window)
    handler.generate_masks(on_success=lambda: chained.append(True))
    thread = handler.generation_thread
    _wait(thread, qapp)

    fb = project.window.feedback
    assert "task_failed" not in fb.names()
    assert _messages(fb, "task_finished") == ["Generated 3 masks"]
    assert ("1 images could not be processed", "warn") in [
        c.args for c in fb.calls if c.name == "notify"
    ]
    assert [kind for kind, _ in project.dialogs] == ["report"]
    assert project.report_kwargs == [{}]  # other outputs were written
    assert thread.completed is True
    assert chained == [True]
    assert project.window.outputs_changed == [True]


def test_skeleton_partial_failure_still_completes_and_loads_results(project, qapp):
    _unreadable(project.folder, "zz_bad.jpg")
    handler = gsh.GenerateSkeletonHandler(project.window)
    handler.generate_skeleton()
    thread = handler._thread
    _wait(thread, qapp)

    fb = project.window.feedback
    assert "task_failed" not in fb.names()
    assert _messages(fb, "task_finished") == ["Generated 3 skeletons"]
    assert [kind for kind, _ in project.dialogs] == ["report"]
    assert project.report_kwargs == [{}]
    assert thread.completed is True
    assert project.window.loaded == [os.path.normpath(str(project.folder / "skeletons"))]


def test_a_run_that_kept_masks_is_not_a_run_where_every_input_failed(project, qapp):
    # Keep-existing: a and b keep their masks, and the only image attempted (c) fails.
    # The kept inputs did not fail, so this is a partial failure: Generate still chains
    # the skeleton pass (it reads the images, not the masks).
    mask_dir = project.folder / "mask"
    mask_dir.mkdir()
    for stem in ("a", "b"):
        Image.fromarray(np.zeros((48, 64), np.uint8)).save(mask_dir / f"{stem}.png")
    _unreadable(project.folder, "c.jpg")
    chained = []
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda e, t: OverwritePolicy.KEEP_EXISTING
    )
    handler.generate_masks(on_success=lambda: chained.append(True))
    thread = handler.generation_thread
    _wait(thread, qapp)

    assert thread.report.written == [] and thread.report.failed_names == ["c.jpg"]
    fb = project.window.feedback
    assert "task_failed" not in fb.names()
    assert _messages(fb, "task_finished") == ["Generated 0 masks, kept 2 existing predictions"]
    assert ("1 images could not be processed", "warn") in [
        c.args for c in fb.calls if c.name == "notify"
    ]
    assert [kind for kind, _ in project.dialogs] == ["report"]
    assert project.report_kwargs == [{}]  # the kept masks are the other outputs
    assert thread.completed is True
    assert chained == [True]


def test_a_run_with_nothing_to_do_still_completes_and_chains(project, qapp):
    mask_dir = project.folder / "mask"
    mask_dir.mkdir()
    for stem in ("a", "b", "c"):
        Image.fromarray(np.zeros((48, 64), np.uint8)).save(mask_dir / f"{stem}.png")
    chained = []
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda e, t: OverwritePolicy.KEEP_EXISTING
    )
    handler.generate_masks(on_success=lambda: chained.append(True))
    thread = handler.generation_thread
    _wait(thread, qapp)

    fb = project.window.feedback
    assert "task_failed" not in fb.names()
    assert _messages(fb, "task_finished") == ["Generated 0 masks, kept 3 existing predictions"]
    assert "success" in _toast_kinds(fb)
    assert project.dialogs == []
    assert thread.completed is True
    assert chained == [True]

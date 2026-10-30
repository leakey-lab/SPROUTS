"""Set-up shared by the generation handler tests: offscreen Qt, CPU stub models, no checkpoints.

- ``wait_for_thread`` waits for a worker thread, then delivers the signals it queued and the
  callbacks they chain, with no fixed sleep.
- ``generation_project`` is the body of the handler tests' ``project`` fixture: a folder of
  3 images, the CPU runtime, stub models and a fake main window.

A handler test module keeps its own ``project`` fixture, with
``yield from generation_project(tmp_path, monkeypatch)`` as its body: the ``project``
fixture of ``conftest.py`` is the Correct page's.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from app import config
from app.feedback import RecordingFeedback
from app.handlers import generation_base
from app.handlers import mask_generation_handler as mgh
from app.inference import runtime
from tests.qt_teardown import flush_deleted


def wait_for_thread(thread, app, timeout: float = 30.0, drain: int = 40) -> None:
    """Wait for ``thread`` to finish, then deliver its queued signals and any chained
    callback: ``drain`` rounds of the posted events, without sleeping.

    ``isFinished()`` is True already while QThread emits its own ``finished`` (the job
    registry's release and ``on_success`` hang on it); ``wait()`` returns after that
    emission, so everything the thread queued is posted before the drain.
    """
    assert thread is not None
    deadline = time.monotonic() + timeout
    while (thread.isRunning() or not thread.isFinished()) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    thread.wait(max(1, int((deadline - time.monotonic()) * 1000)))
    for _ in range(drain):
        QCoreApplication.sendPostedEvents()
        app.processEvents()
    assert thread.isFinished()


class StubMaskModel(torch.nn.Module):
    """1x1-conv mask model: ``items`` counts the rows it ran, ``on_forward()`` runs in each
    forward, and with ``oom`` set every forward runs out of GPU memory."""

    def __init__(self, on_forward=None):
        super().__init__()
        torch.manual_seed(0)
        self.conv = torch.nn.Conv2d(3, 1, 1)
        self.on_forward = on_forward
        self.oom = False
        self.items = 0

    def forward(self, x):
        if self.oom:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        self.items += x.shape[0]
        if self.on_forward:
            self.on_forward()
        return torch.sigmoid(self.conv(x))


class StubSkeletonModel:
    def run_batch(self, x):
        return torch.tanh(x)


def generation_project(tmp_path, monkeypatch, *, list_images: bool = True):
    """Generator for a fixture (it needs a QApplication): yield the project of the handler
    tests and clean up after the test.

    The project is a namespace: ``folder`` (``tmp_path/imgs``, photos a, b and c), ``window``
    (a QWidget with the image manager, feedback and result hooks the handlers use),
    ``stub`` (the mask model), ``dialogs`` (every message box and failure report, as
    ``(kind, args)``) and ``report_kwargs`` (the keyword arguments of each failure report).
    With ``list_images`` False the image manager lists no image.
    """
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"w")
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(weights))
    stub = StubMaskModel().eval()
    monkeypatch.setattr(runtime, "get_mask_model", lambda device=None: stub)
    monkeypatch.setattr(runtime, "get_skeleton_model", lambda device=None: StubSkeletonModel())
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
    window.image_manager = SimpleNamespace(
        images=images if list_images else {}, original_folder=str(folder)
    )
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

    monkeypatch.setattr(generation_base, "show_failure_report", report)  # both handlers' reports
    # never block on the real modal prompt: tests that expect it inject confirm_overwrite
    monkeypatch.setattr(
        mgh, "ask_overwrite_policy", lambda *a, **k: pytest.fail("unexpected overwrite prompt")
    )
    yield SimpleNamespace(
        folder=folder, window=window, stub=stub, dialogs=dialogs, report_kwargs=report_kwargs
    )
    runtime.get_runtime.cache_clear()
    window.deleteLater()
    flush_deleted()

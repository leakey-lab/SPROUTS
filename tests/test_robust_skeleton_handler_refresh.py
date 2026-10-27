"""R7: a skeleton run that is cancelled or fails still refreshes what the window shows.

``GenerateSkeletonHandler`` told the window about new skeletons only on success
(``load_results`` -> ``on_outputs_changed``). A cancelled run keeps the skeletons it
finished (PI-08) and a run that raises after writing some keeps them too (R7), but the
Library's skeleton badges, the display cache and the metrics bar went on describing
the folder as it was before the run until the project was reloaded. The mask handler
refreshes after every outcome; the skeleton handler now does the same.
"""

from __future__ import annotations

import os

import torch

from app.handlers import generate_skeleton_handler as gsh
from app.inference import runtime
from tests.test_generation_handlers import _wait, project  # noqa: F401


class _Model:
    """Stub skeleton model: ``on_batch(n)`` runs before batch ``n`` (1-based)."""

    def __init__(self, on_batch):
        self.on_batch = on_batch
        self.batches = 0

    def run_batch(self, x):
        self.batches += 1
        self.on_batch(self.batches)
        return torch.tanh(x)


def _skeletons(folder):
    out = folder / "skeletons"
    return sorted(n for n in os.listdir(out) if n.endswith("_fake.png")) if out.is_dir() else []


def test_a_cancelled_skeleton_run_refreshes_the_window(project, qapp, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")  # (O4) the fixture sizes auto batches
    handler = gsh.GenerateSkeletonHandler(project.window)

    def cancel_after_the_first_batch(n):
        if n == 1:
            handler._thread.cancel()

    model = _Model(cancel_after_the_first_batch)
    monkeypatch.setattr(runtime, "get_skeleton_model", lambda device=None: model)
    assert handler.generate_skeleton() is True
    _wait(handler._thread, qapp)

    assert 1 <= len(_skeletons(project.folder)) < 3  # the first batch was kept
    fb = project.window.feedback
    cancelled = [c.args[0] for c in fb.calls if c.name == "task_finished"][-1]
    assert cancelled.startswith("Skeleton generation cancelled after")
    toasts = [c.args for c in fb.calls if c.name == "notify"]
    assert toasts[-1] == (f"{cancelled}; finished skeletons were kept", "info")
    assert project.window.outputs_changed == [True]  # badges / display follow the disk
    assert project.window.loaded == []  # no "Results loaded" for a cancelled run


def test_a_failed_skeleton_run_refreshes_the_window(project, qapp, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")  # (O4) the fixture sizes auto batches

    def fail_on_the_second_batch(n):
        if n == 2:
            raise RuntimeError("CUDA error: unspecified launch failure (simulated)")

    model = _Model(fail_on_the_second_batch)
    monkeypatch.setattr(runtime, "get_skeleton_model", lambda device=None: model)
    handler = gsh.GenerateSkeletonHandler(project.window)
    assert handler.generate_skeleton() is True
    _wait(handler._thread, qapp)

    assert len(_skeletons(project.folder)) == 2  # the first batch of two is on disk
    assert [kind for kind, _args in project.dialogs] == ["critical"]
    assert project.window.outputs_changed == [True]
    assert project.window.loaded == []

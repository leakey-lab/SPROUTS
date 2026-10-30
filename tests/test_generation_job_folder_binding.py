"""A job belongs to the folder it started in (R7, O15).

Generate's chained skeleton pass runs on the mask pass's folder, even when another
folder was opened while the mask pass ran; no job starts while a folder loads.
Offscreen Qt, CPU stub models (the ``project`` fixture of test_generation_handlers).
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.gui.main_window import MainWindow
from app.handlers import generate_skeleton_handler as gsh
from app.handlers import mask_generation_handler as mgh
from app.handlers.job_registry import JobRegistry
from app.handlers.skeleton_handler import SkeletonHandler
from app.inference.measure_run import LENGTH, MeasureRun
from tests.test_generation_handlers import _wait, project  # noqa: F401

LOADING = ("A folder is loading. Try again when it has loaded.", "warn")


def _folder_b(tmp_path):
    """A second project of 2 images (``x``, ``y``)."""
    folder = tmp_path / "other"
    folder.mkdir()
    rng = np.random.default_rng(1)
    images = {}
    for stem in ("x", "y"):
        path = folder / f"{stem}.jpg"
        Image.fromarray(rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)).save(path)
        images[f"{stem}.jpg"] = str(path)
    return folder, images


def _pngs(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".png"))


def _wait_for(predicate, app, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return predicate()


def test_the_skeleton_pass_writes_into_the_mask_pass_folder(project, qapp, tmp_path):  # noqa: F811
    window = project.window
    folder_a = project.folder
    folder_b, images_b = _folder_b(tmp_path)
    window.jobs = JobRegistry()
    window.mask_generation_handler = mgh.MaskGenerationHandler(window)
    generation = gsh.GenerateSkeletonHandler(window)
    window.skeleton_handler = SkeletonHandler(window, generation=generation)

    def open_folder_b():  # the user opens another folder while the mask pass runs
        project.stub.on_forward = None
        window.image_manager = SimpleNamespace(images=images_b, original_folder=str(folder_b))

    project.stub.on_forward = open_folder_b

    assert MainWindow.generate_all(window) is True
    _wait(window.mask_generation_handler.generation_thread, qapp)
    assert _wait_for(lambda: generation._thread is not None, qapp)
    _wait(generation._thread, qapp)

    assert _pngs(folder_a / "skeletons") == ["a_fake.png", "b_fake.png", "c_fake.png"]
    assert not (folder_b / "skeletons").exists()


def test_generate_skeleton_takes_the_folder_it_is_given(project, qapp, tmp_path):  # noqa: F811
    folder_b, _images_b = _folder_b(tmp_path)
    handler = gsh.GenerateSkeletonHandler(project.window)  # the window shows folder A
    assert handler.generate_skeleton(input_dir=str(folder_b)) is True
    _wait(handler._thread, qapp)
    assert _pngs(folder_b / "skeletons") == ["x_fake.png", "y_fake.png"]
    assert not (project.folder / "skeletons").exists()


@pytest.mark.parametrize("start", ["masks", "skeletons", "measure_length"])
def test_no_job_starts_while_a_folder_loads(project, qapp, start, monkeypatch):  # noqa: F811
    window = project.window
    window._loading_project = True
    asked = []
    if start == "masks":
        (project.folder / "mask").mkdir()
        Image.new("RGBA", (64, 48), (255, 255, 255, 255)).save(project.folder / "mask" / "a.png")
        handler = mgh.MaskGenerationHandler(
            window, confirm_overwrite=lambda e, t: asked.append(e) or None
        )
        assert handler.generate_masks() is False
        assert handler.generation_thread is None
    elif start == "skeletons":
        handler = gsh.GenerateSkeletonHandler(window)
        assert handler.generate_skeleton() is False
        assert handler._thread is None
    else:
        out = project.folder / "skeletons"
        out.mkdir()
        handler = SkeletonHandler(window)
        job = MeasureRun(LENGTH, {"a": str(project.folder / "a.jpg")}, str(out))
        assert handler._start(job) is False
        assert handler.calculator_thread is None
    assert asked == []
    assert "task_started" not in window.feedback.names()
    notes = [c.args for c in window.feedback.calls if c.name == "notify"]
    assert notes[-1] == LOADING

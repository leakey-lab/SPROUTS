"""Generate writes predictions only (WG-T7, O9).

Every human correction lives in ``gt/`` (``gt/mask/<stem>.png``, ``gt/skeletons/<stem>.png``),
so ``mask/`` and ``skeletons/`` hold the model's output and nothing else. The overwrite
prompt says so: "Keep predictions" / "Replace all predictions". A Generate run never reads or
writes ``gt/``; a file in ``mask/`` is a prediction like any other, whatever its PNG type.
"""

from __future__ import annotations

import hashlib
import json
import os

import cv2
import numpy as np
import pytest
import torch
from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.gui import canvas_gl, image_manager  # noqa: E402
from app.gui.correct.layer import MASK  # noqa: E402
from app.handlers import generate_skeleton_handler as gsh  # noqa: E402
from app.handlers import mask_generation_handler as mgh  # noqa: E402
from app.handlers.mask_generation_handler import ask_overwrite_policy  # noqa: E402
from app.inference import runtime  # noqa: E402
from app.inference.mask_origin import OverwritePolicy, png_colour_type  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.test_correct_editor import plane, write_image, write_mask  # noqa: E402
from tests.test_correct_main_window import (  # noqa: E402
    STEMS,
    Project,
    app,  # noqa: F401  (fixture)
    in_correct,
    make_window,  # noqa: F401  (fixture)
    settle,
)
from tests.test_generation_handlers import _wait, project, qapp  # noqa: E402,F401  (fixtures)

KEEP_TEXT = "Keep predictions (predict missing only)"
REPLACE_TEXT = "Replace all predictions"


# ================================================================================= helpers
def tree_sha(folder: str) -> dict[str, str]:
    """Every file under ``folder``: relative path -> SHA-256 of its bytes."""
    found = {}
    for root, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as fh:
                found[os.path.relpath(path, folder)] = hashlib.sha256(fh.read()).hexdigest()
    return found


def file_sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_manifest(layout: ProjectLayout) -> dict:
    with open(os.path.join(layout.mask_dir, "_sprouts_run.json"), encoding="utf-8") as fh:
        return json.load(fh)


def capture_prompt(call, *, press: QMessageBox.ButtonRole | None = None):
    """Run ``call`` (which opens the overwrite prompt) and return what the box showed.

    The box is answered with Cancel, or with the button of role ``press``; the result holds
    ``title``, ``text``, ``info``, ``buttons``, ``default`` and the call's return value.
    """
    shown: dict = {}

    def answer() -> None:
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(10, answer)
            return
        shown.update(
            title=box.windowTitle(),
            text=box.text(),
            info=box.informativeText(),
            buttons=[b.text() for b in box.buttons()],
            default=box.defaultButton().text(),
            roles=[box.buttonRole(b) for b in box.buttons()],
        )
        if press is None:
            box.escapeButton().click()
        else:
            [button] = [b for b in box.buttons() if box.buttonRole(b) == press]
            button.click()

    QTimer.singleShot(0, answer)
    shown["result"] = call()
    return shown


def run(handler, qapp) -> None:  # noqa: F811
    """Generate masks through ``handler`` and wait for the worker."""
    assert handler.generate_masks() is True
    _wait(handler.generation_thread, qapp)


@pytest.fixture
def real_prompt(monkeypatch):
    """The real overwrite prompt (the shared ``project`` fixture replaces it by a failure)."""
    monkeypatch.setattr(mgh, "ask_overwrite_policy", ask_overwrite_policy)


@pytest.fixture
def gt_project(project):  # noqa: F811
    """``project`` (images a, b, c; stub model) with GT of image a: a mask and a skeleton."""
    layout = ProjectLayout(str(project.folder))
    block = np.zeros((48, 64), bool)
    block[10:20, 5:30] = True
    write_mask_file(layout.gt_mask_path("a"), block)
    skeleton = layout.gt_skeleton_path("a")
    os.makedirs(os.path.dirname(skeleton), exist_ok=True)
    Image.fromarray(np.full((48, 64), 255, np.uint8)).save(skeleton)
    project.layout = layout
    return project


# ============================================================================== the prompt
def test_the_prompt_says_keep_or_replace_predictions(qapp):  # noqa: F811
    shown = capture_prompt(lambda: ask_overwrite_policy(None, 3, 10))

    assert shown["title"] == "Predictions already exist"
    assert shown["text"] == "3 of 10 images already have a predicted mask."
    assert shown["info"] == (
        "Keep them and predict only the missing ones, or replace every prediction with a new "
        "one. Masks you saved in Correct are GT: they live in gt/ and are never touched."
    )
    # The box lays the buttons out by role and platform, so the texts are pinned with roles.
    roles = dict(zip(shown["buttons"], shown["roles"], strict=True))
    assert roles == {
        KEEP_TEXT: QMessageBox.ButtonRole.AcceptRole,
        REPLACE_TEXT: QMessageBox.ButtonRole.DestructiveRole,
        "Cancel": QMessageBox.ButtonRole.RejectRole,
    }
    assert shown["default"] == KEEP_TEXT
    assert shown["result"] is None  # the box was cancelled


def test_the_prompt_counts_with_thousands_separators(qapp):  # noqa: F811
    shown = capture_prompt(lambda: ask_overwrite_policy(None, 1234, 46000))

    assert shown["text"] == "1,234 of 46,000 images already have a predicted mask."


@pytest.mark.parametrize(
    ("role", "policy"),
    [
        (QMessageBox.ButtonRole.AcceptRole, OverwritePolicy.KEEP_EXISTING),
        (QMessageBox.ButtonRole.DestructiveRole, OverwritePolicy.OVERWRITE_ALL),
        (QMessageBox.ButtonRole.RejectRole, None),
    ],
)
def test_the_prompt_buttons_answer_keep_replace_and_cancel(qapp, role, policy):  # noqa: F811
    shown = capture_prompt(lambda: ask_overwrite_policy(None, 2, 5), press=role)

    assert shown["result"] is policy


# ================================================================== the run never touches gt
def test_replace_all_predictions_never_touches_gt(gt_project, qapp, monkeypatch):  # noqa: F811
    layout = gt_project.layout
    write_mask(layout.mask_path("a"), np.ones((48, 64), np.uint8))  # an old prediction
    old_prediction = file_sha(layout.mask_path("a"))
    gt_before = tree_sha(layout.gt_dir)
    assert gt_before  # the fixture wrote a GT mask and a GT skeleton
    for name in ("scan_gt_masks", "scan_gt_skeletons", "scan_gt_stems", "find_gt_mask"):
        monkeypatch.setattr(
            ProjectLayout, name, lambda *a, _n=name, **k: pytest.fail(f"Generate read gt/ ({_n})")
        )
    handler = mgh.MaskGenerationHandler(
        gt_project.window, confirm_overwrite=lambda n, t: OverwritePolicy.OVERWRITE_ALL
    )

    run(handler, qapp)

    assert tree_sha(layout.gt_dir) == gt_before
    assert file_sha(layout.mask_path("a")) != old_prediction  # replaced by the new prediction
    assert png_colour_type(layout.mask_path("a")) == 0
    assert run_manifest(layout)["parameters"]["overwrite_policy"] == "overwrite_all"


def test_a_skeleton_run_never_touches_gt(gt_project, qapp):  # noqa: F811
    layout = gt_project.layout
    gt_before = tree_sha(layout.gt_dir)
    assert any(name.startswith(os.path.join("skeletons", "a")) for name in gt_before)
    handler = gsh.GenerateSkeletonHandler(gt_project.window)

    assert handler.generate_skeleton() is True
    _wait(handler._thread, qapp)

    assert tree_sha(layout.gt_dir) == gt_before
    assert sorted(n for n in os.listdir(layout.skeletons_dir) if n.endswith(".png")) == [
        "a_fake.png",
        "b_fake.png",
        "c_fake.png",
    ]  # the run rewrote the generated skeletons, as always


def test_keep_predictions_generates_only_the_missing_ones(project, qapp):  # noqa: F811
    layout = ProjectLayout(str(project.folder))
    write_mask(layout.mask_path("a"), np.ones((48, 64), np.uint8))
    write_mask(layout.mask_path("b"), np.zeros((48, 64), np.uint8))
    kept = {stem: file_sha(layout.mask_path(stem)) for stem in ("a", "b")}
    asked = []

    def confirm(existing_count, total):
        asked.append((existing_count, total))
        return OverwritePolicy.KEEP_EXISTING

    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=confirm)
    run(handler, qapp)

    report = handler.generation_thread.report
    assert asked == [(2, 3)]
    assert sorted(report.skipped) == ["a", "b"] and report.written == ["c.jpg"]
    assert {stem: file_sha(layout.mask_path(stem)) for stem in ("a", "b")} == kept
    assert os.path.isfile(layout.mask_path("c"))
    assert run_manifest(layout)["parameters"]["overwrite_policy"] == "keep_existing"


def test_an_rgba_file_in_mask_is_a_prediction_like_any_other(project, qapp, real_prompt):  # noqa: F811
    # Old data is out of scope: mask/ has no hand-corrected category.
    layout = ProjectLayout(str(project.folder))
    on = np.zeros((48, 64), bool)
    on[5:15, 5:15] = True
    write_mask_file(layout.mask_path("a"), on)  # RGBA, white on transparent
    write_mask(layout.mask_path("b"), on)
    assert png_colour_type(layout.mask_path("a")) == 6
    texts = []

    def replace_all(existing_count, total):
        shown = capture_prompt(
            lambda: ask_overwrite_policy(None, existing_count, total),
            press=QMessageBox.ButtonRole.DestructiveRole,
        )
        texts.append(shown["text"])
        return shown["result"]

    handler = mgh.MaskGenerationHandler(project.window, confirm_overwrite=replace_all)
    run(handler, qapp)

    assert texts == ["2 of 3 images already have a predicted mask."]
    assert png_colour_type(layout.mask_path("a")) == 0
    assert run_manifest(layout)["parameters"]["overwrite_policy"] == "overwrite_all"


def test_the_handler_asks_with_the_count_of_existing_predictions(project, qapp):  # noqa: F811
    layout = ProjectLayout(str(project.folder))
    write_mask_file(layout.mask_path("a"), np.ones((48, 64), bool))  # RGBA: still a prediction
    write_mask(layout.mask_path("b"), np.zeros((48, 64), np.uint8))
    asked = []
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda n, t: asked.append((n, t))
    )

    assert handler.generate_masks() is False  # the stub answers Cancel (None): nothing starts

    assert asked == [(2, 3)] and handler.generation_thread is None


# ================================================================= an open GT mask stays put
class _PatternModel(torch.nn.Module):
    """A mask model that predicts one fixed box (rows 200-259, columns 300-419)."""

    def forward(self, x):
        out = torch.zeros(x.shape[0], 1, x.shape[2], x.shape[3])
        out[:, :, 200:260, 300:420] = 1.0
        return out


def test_replace_predictions_leaves_an_open_gt_mask_as_it_is(
    make_window,  # noqa: F811
    tmp_path,
    monkeypatch,
    qapp,  # noqa: F811
):  # Review Focus 5
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"w")
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(weights))
    monkeypatch.setattr(runtime, "get_mask_model", lambda device=None: _PatternModel().eval())
    # The model works at 640 x 480, so the photos are that size: its masks fit them.
    w, h = 640, 480
    site = Project(str(tmp_path / "project"))
    for i in range(len(STEMS)):
        write_image(site.image(i), w=w, h=h, value=60 + 20 * i)
    old = np.zeros((h, w), np.uint8)
    old[30:70, 40:110] = 1
    write_mask(site.mask(0), old)
    gt = np.zeros((h, w), np.uint8)
    gt[90:120, 20:60] = 1
    write_mask_file(site.gt_mask(0), gt > 0)
    gt_before = tree_sha(os.path.join(site.folder, "gt"))
    window, _project = in_correct(make_window, tmp_path, 0, site)
    editor = window.correct_editor
    assert (plane(editor) == gt).all() and editor.layers[MASK].status().label == "GT"

    handler = mgh.MaskGenerationHandler(
        window, confirm_overwrite=lambda n, t: OverwritePolicy.OVERWRITE_ALL
    )
    run(handler, qapp)
    window.on_outputs_changed()
    settle()

    new_prediction = (cv2.imread(site.mask(0), cv2.IMREAD_GRAYSCALE) > 0).astype(np.uint8)
    assert new_prediction.any() and not (new_prediction == old).all()  # Generate rewrote it
    assert (plane(editor) == gt).all() and editor.layers[MASK].status().label == "GT"
    assert not editor.has_unsaved_changes()
    assert tree_sha(os.path.join(site.folder, "gt")) == gt_before
    editor.load_prediction()
    assert (plane(editor) == new_prediction).all()

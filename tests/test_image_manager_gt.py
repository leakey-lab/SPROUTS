"""WG-T1: ``ImageManager`` knows which images have GT (mask, skeleton, class labels)."""

from __future__ import annotations

import os
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication

from app.gui import image_manager as im
from app.project_layout import ProjectLayout
from tests.window_support import write_photos

pytestmark = pytest.mark.usefixtures("no_temp_sweep")

STEMS = ["a", "b", "c", "d"]


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x")


@pytest.fixture
def gt_project(tmp_path):
    """Images a..d; gt/mask/a.png, gt/skeletons/b.png, gt/c.png; mask/d.png (prediction)."""
    write_photos(tmp_path, STEMS, size=(8, 6), suffix=".png")
    layout = ProjectLayout(str(tmp_path))
    _touch(layout.gt_mask_path("a"))
    _touch(layout.gt_skeleton_path("b"))
    _touch(layout.gt_path("c"))
    _touch(layout.mask_path("d"))
    return layout


def _load(layout: ProjectLayout) -> im.ImageManager:
    manager = im.ImageManager(None)
    manager.load_images(layout.root)
    end = time.monotonic() + 10
    while time.monotonic() < end and not manager.images:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        time.sleep(0.005)
    assert manager.images
    return manager


def test_the_loader_scans_gt_after_the_predictions(gt_project, app):
    manager = _load(gt_project)
    assert manager.gt_mask_stems == {"a"}
    assert manager.gt_skeleton_stems == {"b"}
    assert manager.gt_label_stems == {"c"}
    assert manager.has_gt_mask("a") and not manager.has_gt_mask("b")
    assert manager.has_gt_skeleton("b") and not manager.has_gt_skeleton("a")
    assert manager.has_gt_labels("c") and not manager.has_gt_labels("a")
    assert manager.mask_state("a") == "gt"
    assert manager.mask_state("d") == "prediction"
    assert manager.mask_state("b") == "none"
    assert manager.skeleton_state("a") == "none"
    assert manager.skeleton_state("b") == "gt"


def test_a_gt_file_named_in_another_case_belongs_to_the_librarys_stem(tmp_path, app):
    write_photos(tmp_path, ["Plot"], size=(8, 6), color=(1, 2, 3), suffix=".png")
    layout = ProjectLayout(str(tmp_path))
    _touch(layout.gt_mask_path("plot"))
    manager = _load(layout)
    assert manager.gt_mask_stems == {"Plot"} and manager.has_gt_mask("Plot")


def test_set_gt_and_refresh_outputs_follow_the_files(gt_project, app):
    manager = _load(gt_project)

    manager.set_gt("d", "mask", True)
    assert manager.has_gt_mask("d")
    manager.set_gt(gt_project.gt_mask_path("d"), "mask", False)  # a path names the stem too
    assert not manager.has_gt_mask("d")
    manager.set_gt("d", "skeleton", True)
    manager.set_gt("d", "labels", True)
    assert manager.has_gt_skeleton("d") and manager.has_gt_labels("d")
    assert manager.skeleton_state("d") == "gt"
    with pytest.raises(ValueError):
        manager.set_gt("d", "classes", True)

    os.remove(gt_project.gt_mask_path("a"))
    _touch(gt_project.gt_skeleton_path("c"))
    manager.refresh_outputs()

    assert not manager.has_gt_mask("a")
    assert manager.gt_skeleton_stems == {"b", "c"}  # d's set_gt was a guess; the files rule
    assert manager.gt_label_stems == {"c"}
    assert manager.mask_state("a") == "none"


def test_a_new_load_forgets_the_gt_of_the_old_one(gt_project, tmp_path_factory, app):
    manager = _load(gt_project)
    other = tmp_path_factory.mktemp("other")
    write_photos(other, ["z"], size=(8, 6), color=(1, 2, 3), suffix=".png")
    manager.load_images(str(other))
    end = time.monotonic() + 10
    while time.monotonic() < end and "z" not in manager.images:
        QApplication.processEvents()
        time.sleep(0.005)
    assert manager.gt_mask_stems == set() == manager.gt_skeleton_stems == manager.gt_label_stems

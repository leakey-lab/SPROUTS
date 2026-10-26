"""GT files whose stem differs from the image's only in case are that image's GT (O7), and
the export never writes into a protected folder whatever the case (final review C2 / D3, D5).

Image ``IMG.png`` with ``gt/skeletons/img.png``, ``gt/mask/img.png`` and ``gt/img.png`` (an
image renamed in case only, or folders merged from another machine), on a case-sensitive
folder: the skeleton layer opens ``img.png`` and saves onto it (never a second
``IMG.png``), as the mask layer does; the window keeps the Library's GT-skeleton mark of the
open image; the dataset export and its dialog's count find the class labels. On Windows the
folder is made case-sensitive with ``fsutil`` (skipped where that is not possible; the
ubuntu CI is case-sensitive).

The export refuses an output folder inside ``mask/``, ``skeletons/`` or ``gt/`` spelled in
another case on every platform: Linux on a case-insensitive volume (exFAT, NTFS, CIFS,
ext4 casefold) is simulated on this disk with ``io_utils``' platform switched to linux and
``realpath`` keeping the spelling, as on Linux.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import app.io_utils as io_utils  # noqa: E402
from app import project_layout  # noqa: E402
from app.gt import Tracing, default_registry, write_tracing  # noqa: E402
from app.gt.export import ExportError, ExportOptions, export_dataset  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.export_dataset_dialog import count_traced  # noqa: E402
from app.gui.image_manager import ImageManager  # noqa: E402
from app.gui.stage_status import _with_gt  # noqa: E402
from app.gui.window import editors  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    H,
    W,
    box_plane,
    draw_line,
    editor_factory,
    install_guards,
    paint,
    qapp,
    raster,
    write_image,
    write_mask,
    write_skeleton,
)


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


def _case_sensitive(folder: str) -> bool:
    probe = os.path.join(folder, "Probe")
    with open(probe, "wb"):
        pass
    try:
        return not os.path.exists(os.path.join(folder, "probe"))
    finally:
        os.remove(probe)


def case_sensitive_folder(path: str) -> str:
    """``path`` created on a case-sensitive file system (folders made in it inherit that
    on NTFS), else the test is skipped."""
    os.makedirs(path)
    if sys.platform == "win32":
        subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", path, "enable"],
            capture_output=True,
            check=False,
        )
    if not _case_sensitive(path):
        pytest.skip("needs a case-sensitive folder (fsutil on Windows; the ubuntu CI)")
    return path


@pytest.fixture
def project(tmp_path):
    """``IMG.png`` with its predictions and GT files named ``img.png``: ``(folder, image)``."""
    folder = case_sensitive_folder(str(tmp_path / "project"))
    layout = ProjectLayout(folder)
    image = write_image(os.path.join(folder, "IMG.png"))
    write_skeleton(os.path.join(layout.skeletons_dir, "IMG_fake.png"), row=110)
    write_mask(os.path.join(layout.mask_dir, "IMG.png"), box_plane())
    write_skeleton(os.path.join(layout.gt_skeletons_dir, "img.png"), row=30)
    on = np.zeros((H, W), bool)
    on[10:40, 10:60] = True
    write_mask_file(os.path.join(layout.gt_mask_dir, "img.png"), on, durable=False)
    labels = Tracing(np.ones((H, W), np.uint8))
    write_tracing(os.path.join(layout.gt_dir, "img.png"), labels, default_registry())
    return folder, image


def png_names(folder: str) -> list[str]:
    return sorted(name for name in os.listdir(folder) if name.endswith(".png"))


def test_correct_opens_and_saves_the_case_variant_gt(make_editor, project):
    folder, image = project
    layout = ProjectLayout(folder)
    editor = make_editor(image, folder)
    skeleton, mask = editor.layers[SKELETON], editor.layers[MASK]

    assert skeleton.document.source == "gt" and raster(editor)[30].any()
    assert os.path.basename(skeleton.status().tooltip) == "img.png"
    assert mask.status().tooltip == "gt/mask/img.png"  # the mask finds it already

    draw_line(editor)
    paint(editor, (120, 20), (150, 40))
    assert editor.save()
    assert png_names(layout.gt_skeletons_dir) == ["img.png"]  # saved onto it, no IMG.png
    assert png_names(layout.gt_mask_dir) == ["img.png"]
    assert skeleton.status().state == mask.status().state == "clean"


def library(folder: str) -> ImageManager:
    """The Library of ``folder`` as a finished scan leaves it (its GT marks from disk)."""
    manager = ImageManager()
    manager.original_folder = folder
    layout = ProjectLayout(folder)
    manager.on_loading_finished(layout.scan_images(), {}, layout.scan_masks(), False)
    return manager


def test_a_save_onto_the_case_variant_reports_the_librarys_stem(make_editor, project):
    folder, image = project
    manager = library(folder)
    counts = _with_gt(manager.skeleton_stems, manager.gt_skeleton_stems)
    editor = make_editor(image, folder)
    saved, rows, refreshed = [], [], []
    editor.skeleton_saved.connect(saved.append)
    draw_line(editor)
    assert editor.save() and os.path.basename(saved[0]) == "img.png"
    window = SimpleNamespace(
        image_manager=manager,
        library_index=SimpleNamespace(refresh_status=rows.append),
        metrics=SimpleNamespace(refresh_if_current=refreshed.append),
        correct_editor=editor,
    )

    editors.on_skeleton_saved(window, saved[0])

    assert manager.gt_skeleton_stems == {"IMG"}  # no stray "img"
    assert _with_gt(manager.skeleton_stems, manager.gt_skeleton_stems) == counts
    assert rows == refreshed == ["IMG"]


def test_the_skeleton_gt_is_looked_up_once_per_open(make_editor, monkeypatch, tmp_path):
    folder = str(tmp_path / "project")
    layout = ProjectLayout(folder)
    image = write_image(os.path.join(folder, "a.png"))
    write_skeleton(os.path.join(layout.skeletons_dir, "a_fake.png"))  # a prediction, no GT
    os.makedirs(layout.gt_skeletons_dir)
    # Linux: a lookup that misses the exact name lists the folder for a case variant.
    monkeypatch.setattr(project_layout, "_ignores_case", lambda _directory: False)
    gt_dir = os.path.normcase(layout.gt_skeletons_dir)
    scans = []
    real_scandir = os.scandir

    def scandir(path="."):
        if os.path.normcase(os.fspath(path)) == gt_dir:
            scans.append(path)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    editor = make_editor()

    editor.load_image(image, folder)

    assert editor.layers[SKELETON].document.source == "prediction"
    assert len(scans) == 1
    draw_line(editor)
    assert editor.save() and os.path.isfile(layout.gt_skeleton_path(image))
    assert len(scans) == 1  # the save writes where the open looked


def test_the_window_keeps_the_gt_skeleton_mark_of_the_open_image(project):
    folder, image = project
    manager = ImageManager()
    manager.original_folder = folder
    layout = ProjectLayout(folder)
    manager.on_loading_finished(layout.scan_images(), {}, layout.scan_masks(), False)
    assert manager.has_gt_skeleton("IMG")
    window = SimpleNamespace(
        image_manager=manager,
        library_index=SimpleNamespace(refresh_status=lambda stem: None),
        correct_editor=SimpleNamespace(current_image_path=image),
    )

    editors.sync_open_correction(window)

    assert manager.has_gt_skeleton("IMG")


def test_the_export_and_its_count_find_the_case_variant_labels(project, monkeypatch, tmp_path):
    folder, _image = project
    images = ProjectLayout(folder).scan_images()
    monkeypatch.setattr(os.path, "normcase", posixpath.normcase)  # as on Linux
    traced = count_traced(images)
    monkeypatch.undo()
    assert traced == 1

    out = str(tmp_path / "dataset")
    options = ExportOptions(out, only_complete=False)
    result = export_dataset(folder, images, default_registry(), options)
    assert (result.exported, result.errors) == (1, [])
    assert os.listdir(os.path.join(out, "masks")) == ["IMG.png"]


@pytest.mark.parametrize("protected", ["MASK", "Skeletons", "GT", "Gt/Mask"])
def test_the_export_refuses_a_protected_folder_in_any_case(protected, monkeypatch, tmp_path):
    folder = str(tmp_path / "project")
    layout = ProjectLayout(folder)
    image = write_image(os.path.join(folder, "a.png"))
    write_mask(layout.mask_path(image), box_plane())
    write_tracing(layout.gt_path(image), Tracing(np.ones((H, W), np.uint8)), default_registry())
    for name in ("skeletons", os.path.join("gt", "mask")):
        os.makedirs(os.path.join(folder, name), exist_ok=True)
    # Linux on a case-insensitive volume: paths compared as written, realpath keeps them.
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    monkeypatch.setattr(os.path, "realpath", os.path.abspath)
    out = os.path.join(folder, *protected.split("/"), "export")

    with pytest.raises(ExportError, match="must not be inside"):
        export_dataset(
            folder,
            layout.scan_images(),
            default_registry(),
            ExportOptions(out, only_complete=False),
        )

    assert not os.path.exists(out)

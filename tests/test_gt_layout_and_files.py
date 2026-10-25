"""WG-T1: the GT paths and scans of ``ProjectLayout`` and the Qt-free ``app.gt.files``.

GT lives under ``gt/``: ``gt/mask/<stem>.png`` and ``gt/skeletons/<stem>.png`` next to the
class labels ``gt/<stem>.png`` and ``gt/classes.json``. Predictions in ``mask/`` and
``skeletons/`` are never touched by anything here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app import project_layout
from app.gt.files import (
    SOURCE_GT,
    SOURCE_NONE,
    SOURCE_PREDICTION,
    GtFiles,
    GtRemoveError,
    gt_files,
    image_size,
    png_size,
    remove_gt,
)
from app.gt.registry import registry_path
from app.project_layout import GtStems, ProjectLayout

ROOT = Path(__file__).resolve().parents[1]


def _touch(path, data: bytes = b"x") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _write_png(path, shape: tuple[int, int]) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros(shape, np.uint8)).save(path)
    return str(path)


def _write_jpeg(path, shape: tuple[int, int]) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((*shape, 3), np.uint8)).save(path)
    return str(path)


def test_gt_paths_mirror_the_prediction_folders(tmp_path):
    root = str(tmp_path)
    layout = ProjectLayout(root)
    assert layout.gt_mask_dir == os.path.join(root, "gt", "mask")
    assert layout.gt_skeletons_dir == os.path.join(root, "gt", "skeletons")
    assert layout.gt_mask_path("T001_L044") == os.path.join(root, "gt", "mask", "T001_L044.png")
    assert layout.gt_skeleton_path("T001_L044.jpg") == os.path.join(
        root, "gt", "skeletons", "T001_L044.png"
    )
    assert layout.gt_path("T001_L044") == os.path.join(root, "gt", "T001_L044.png")
    assert layout.output_dirs[-2:] == (layout.gt_mask_dir, layout.gt_skeletons_dir)
    assert len(layout.output_dirs) == 9
    assert os.listdir(tmp_path) == []  # describing the folders creates none


def test_scan_gt_stems_reads_the_three_kinds_and_ignores_the_rest(tmp_path):
    _touch(tmp_path / "gt" / "a.png")
    _touch(tmp_path / "gt" / "mask" / "b.png")
    _touch(tmp_path / "gt" / "skeletons" / "c.png")
    _touch(tmp_path / "gt" / "classes.json")
    _touch(tmp_path / "gt" / "mask" / "notes.txt")
    stems = ProjectLayout(str(tmp_path)).scan_gt_stems()
    assert (stems.labels, stems.mask, stems.skeleton) == (
        frozenset({"a"}),
        frozenset({"b"}),
        frozenset({"c"}),
    )
    nowhere = ProjectLayout(str(tmp_path / "nowhere")).scan_gt_stems()
    assert nowhere == GtStems(frozenset(), frozenset(), frozenset())


def test_the_label_scan_lists_files_only(tmp_path):
    _touch(tmp_path / "gt" / "a.png")
    (tmp_path / "gt" / "folder.png").mkdir()
    layout = ProjectLayout(str(tmp_path))
    assert layout.scan_gt_labels() == {"a": layout.gt_path("a")}


def test_scans_key_by_stem_key(tmp_path):
    # Two files that differ in case only are one stem: one winner, as for the masks.
    _touch(tmp_path / "gt" / "mask" / "B.png")
    _touch(tmp_path / "gt" / "mask" / "b.png")
    _touch(tmp_path / "gt" / "skeletons" / "C.png")
    layout = ProjectLayout(str(tmp_path))
    winners = layout.scan_gt_masks()
    assert len(winners) == 1 and next(iter(winners)).lower() == "b"
    assert list(layout.scan_gt_skeletons()) == ["C"]
    assert layout.scan_gt_stems().mask == frozenset(winners)


def test_find_gt_compares_stems_without_case(tmp_path):
    mask = _touch(tmp_path / "gt" / "mask" / "A.png")
    labels = _touch(tmp_path / "gt" / "a.png")
    layout = ProjectLayout(str(tmp_path))
    assert layout.find_gt_mask("A") == mask
    assert layout.find_gt_labels("a.jpg") == labels
    assert layout.find_gt_skeleton("a") is None
    assert layout.find_gt_mask("missing") is None


def test_gt_files_and_remove_gt_touch_only_that_image(tmp_path):
    root = str(tmp_path)
    layout = ProjectLayout(root)
    _touch(layout.gt_path("a"))
    _touch(layout.gt_mask_path("a"))
    _touch(layout.gt_skeleton_path("a"))
    _touch(layout.gt_mask_path("b"))
    _touch(registry_path(root))
    _touch(layout.mask_path("a"))
    _touch(layout.skeleton_path("a"))

    assert gt_files(layout, "a") == GtFiles(
        layout.gt_mask_path("a"), layout.gt_skeleton_path("a"), layout.gt_path("a")
    )
    assert gt_files(layout, "b") == GtFiles(layout.gt_mask_path("b"), None, None)

    removed = remove_gt(layout, "a")

    assert set(removed) == {
        layout.gt_path("a"),
        layout.gt_mask_path("a"),
        layout.gt_skeleton_path("a"),
    }
    assert os.path.isfile(layout.gt_mask_path("b"))
    assert os.path.isfile(layout.mask_path("a")) and os.path.isfile(layout.skeleton_path("a"))
    assert os.path.isfile(registry_path(root))
    assert os.path.isdir(layout.gt_mask_dir) and os.path.isdir(layout.gt_skeletons_dir)
    assert remove_gt(layout, "a") == ()


def test_an_upper_case_extension_is_found_where_the_scan_lists_it(tmp_path, monkeypatch):
    # Where the file system keeps case, "a.PNG" is listed by the scans (extension folded):
    # the finders must see it too, or remove_gt would leave it behind. (On a file system
    # that ignores case the exact name finds it; the paths then differ in letters only.)
    monkeypatch.setattr(project_layout, "_ignores_case", lambda directory: False)
    layout = ProjectLayout(str(tmp_path))
    mask = _touch(Path(layout.gt_mask_dir) / "a.PNG")
    skeleton = _touch(Path(layout.gt_skeletons_dir) / "B.PNG")
    labels = _touch(Path(layout.gt_dir) / "c.Png")

    def same(found, expected):
        return found is not None and os.path.samefile(found, expected)

    assert same(next(iter(layout.scan_gt_masks().values())), mask)
    assert same(layout.find_gt_mask("a"), mask)
    assert same(layout.find_gt_skeleton("b"), skeleton)  # the stem's case differs too
    assert same(layout.find_gt_labels("c"), labels)

    removed = remove_gt(layout, "a")

    assert len(removed) == 1 and not os.path.exists(mask)
    assert layout.find_gt_mask("a") is None


def test_remove_gt_tries_every_file_then_raises_one_error(tmp_path, monkeypatch):
    layout = ProjectLayout(str(tmp_path))
    mask = _touch(layout.gt_mask_path("a"))
    skeleton = _touch(layout.gt_skeleton_path("a"))
    labels = _touch(layout.gt_path("a"))
    real_remove = os.remove

    def remove(path):
        if path == skeleton:
            raise PermissionError("locked")
        real_remove(path)

    monkeypatch.setattr(os, "remove", remove)

    with pytest.raises(GtRemoveError) as raised:
        remove_gt(layout, "a")

    assert isinstance(raised.value, OSError)
    assert set(raised.value.removed) == {mask, labels}
    assert list(raised.value.failed) == [skeleton]
    assert isinstance(raised.value.failed[skeleton], PermissionError)
    assert os.path.isfile(skeleton) and not os.path.exists(mask) and not os.path.exists(labels)


def test_the_source_words(tmp_path):
    assert (SOURCE_GT, SOURCE_PREDICTION, SOURCE_NONE) == ("gt", "prediction", "none")


def test_png_size_and_image_size_read_headers_only(tmp_path):
    assert png_size(_write_png(tmp_path / "s.png", (256, 341))) == (341, 256)
    assert image_size(_write_jpeg(tmp_path / "p.jpg", (480, 640))) == (640, 480)
    assert image_size(str(tmp_path / "s.png")) == (341, 256)
    assert png_size(str(tmp_path / "missing.png")) is None
    assert image_size(str(tmp_path / "missing.png")) is None
    junk = _touch(tmp_path / "x.png", b"no")
    assert png_size(junk) is None and image_size(junk) is None
    assert png_size(str(tmp_path / "p.jpg")) is None  # a JPEG is not a PNG


def test_png_size_does_not_decode_the_pixels(tmp_path):
    # A header followed by garbage still has a size: only the first 33 bytes are read.
    good = Path(_write_png(tmp_path / "s.png", (7, 9))).read_bytes()
    cut = _touch(tmp_path / "cut.png", good[:33])
    assert png_size(cut) == (9, 7)


def test_files_is_qt_free():
    probe = (
        "import sys, app.gt.files; "
        "print(sorted(m for m in sys.modules "
        "if m in ('torch', 'PyQt6') or m.startswith('PyQt6.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize("module", ["app.project_layout"])
def test_the_layout_still_loads_no_heavy_modules(module):
    probe = (
        "import importlib, sys; importlib.import_module(sys.argv[1]); "
        "print(sorted(m for m in sys.modules if m in ('cv2', 'PyQt6', 'torch')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, module], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"

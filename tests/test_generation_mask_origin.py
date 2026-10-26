"""The pre-Generate scan of ``mask/`` and the overwrite policy (RB-01 / PI-04 / FR-04).

Qt-free. ``mask/`` holds predictions only (WG-T7): the scan counts the stems that have a
mask and does not look at what kind of file each is.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

from app.inference.mask_origin import ExistingMasks, OverwritePolicy, scan_existing_masks
from app.project_layout import ProjectLayout


def _save(path: str, mode: str) -> str:
    size = (8, 6)
    if mode == "L":
        Image.fromarray(np.full((6, 8), 255, np.uint8), mode="L").save(path)
    elif mode == "RGBA":
        Image.new("RGBA", size, (255, 255, 255, 255)).save(path)
    elif mode == "RGB":
        Image.new("RGB", size, (255, 255, 255)).save(path)
    else:
        raise ValueError(mode)
    return path


def _project(tmp_path, masks: dict[str, str]) -> ProjectLayout:
    for stem in ("a", "b", "c", "d"):
        Image.new("RGB", (8, 6)).save(tmp_path / f"{stem}.jpg")
    os.makedirs(tmp_path / "mask")
    for stem, mode in masks.items():
        _save(str(tmp_path / "mask" / f"{stem}.png"), mode)
    return ProjectLayout(str(tmp_path))


def test_scan_existing_masks_counts_every_mask_whatever_its_kind(tmp_path):
    layout = _project(tmp_path, {"a": "RGBA", "b": "L", "c": "RGB"})
    found = scan_existing_masks(layout)
    assert found.stems == frozenset({"a", "b", "c"})
    assert found.count == 3 and bool(found)


def test_scan_existing_masks_limits_to_given_stems(tmp_path):
    layout = _project(tmp_path, {"a": "RGBA", "b": "L", "zz_orphan": "L"})
    found = scan_existing_masks(layout, stems={"a", "b", "c", "d"})
    assert found.stems == frozenset({"a", "b"})


def test_scan_existing_masks_without_mask_folder_is_empty(tmp_path):
    found = scan_existing_masks(ProjectLayout(str(tmp_path)))
    assert found == ExistingMasks.empty()
    assert not found and found.count == 0


def test_the_mask_origin_classifier_is_gone():
    from app.inference import mask_origin

    for name in ("MaskOrigin", "classify_mask", "png_colour_type"):
        assert not hasattr(mask_origin, name), name
    assert not hasattr(ExistingMasks(), "manual")


def test_overwrite_policy_skip_sets(tmp_path):
    found = scan_existing_masks(_project(tmp_path, {"a": "RGBA", "b": "L"}))
    assert OverwritePolicy.KEEP_EXISTING.skip_stems(found) == frozenset({"a", "b"})
    assert OverwritePolicy.OVERWRITE_ALL.skip_stems(found) == frozenset()

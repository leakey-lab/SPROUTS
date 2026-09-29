"""Mask origin classification + overwrite policy (RB-01 / PI-04 / FR-04 / UXS-01).

Qt-free: hand-traced masks are what ``MaskTracingInterface.save_mask`` writes
(ARGB32 -> PNG colour type 6); ML masks are 8-bit greyscale (colour type 0).
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from app.inference.mask_origin import (
    ExistingMasks,
    MaskOrigin,
    OverwritePolicy,
    classify_mask,
    png_colour_type,
    scan_existing_masks,
)
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


def test_png_colour_type_reads_the_ihdr_byte(tmp_path):
    assert png_colour_type(_save(str(tmp_path / "l.png"), "L")) == 0
    assert png_colour_type(_save(str(tmp_path / "rgba.png"), "RGBA")) == 6
    assert png_colour_type(_save(str(tmp_path / "rgb.png"), "RGB")) == 2


def test_png_colour_type_is_none_for_non_png_or_missing(tmp_path):
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not a png at all, just text padding.....")
    short = tmp_path / "short.png"
    short.write_bytes(b"\x89PNG")
    assert png_colour_type(str(junk)) is None
    assert png_colour_type(str(short)) is None
    assert png_colour_type(str(tmp_path / "missing.png")) is None


def test_classify_mask_by_origin(tmp_path):
    assert classify_mask(_save(str(tmp_path / "a.png"), "L")) is MaskOrigin.MODEL
    assert classify_mask(_save(str(tmp_path / "b.png"), "RGBA")) is MaskOrigin.MANUAL
    assert classify_mask(_save(str(tmp_path / "c.png"), "RGB")) is MaskOrigin.UNKNOWN
    jpg = tmp_path / "d.jpg"
    Image.new("RGB", (8, 6)).save(jpg)
    assert classify_mask(str(jpg)) is MaskOrigin.UNKNOWN


def test_qt_saved_mask_is_classified_manual(tmp_path):
    """The exact encoder the Correct Mask editor uses (QImage ARGB32 -> PNG)."""
    qtgui = pytest.importorskip("PyQt6.QtGui")
    out = np.zeros((6, 8, 4), np.uint8)
    out[2:4, 2:5] = (255, 255, 255, 255)
    image = qtgui.QImage(out.data, 8, 6, 8 * 4, qtgui.QImage.Format.Format_ARGB32)
    path = str(tmp_path / "traced.png")
    assert image.save(path, "PNG", 100)
    assert classify_mask(path) is MaskOrigin.MANUAL


def _project(tmp_path, masks: dict[str, str]) -> ProjectLayout:
    for stem in ("a", "b", "c", "d"):
        Image.new("RGB", (8, 6)).save(tmp_path / f"{stem}.jpg")
    os.makedirs(tmp_path / "mask")
    for stem, mode in masks.items():
        _save(str(tmp_path / "mask" / f"{stem}.png"), mode)
    return ProjectLayout(str(tmp_path))


def test_scan_existing_masks_splits_manual_and_model(tmp_path):
    layout = _project(tmp_path, {"a": "RGBA", "b": "L", "c": "RGB"})
    found = scan_existing_masks(layout)
    assert found.manual == frozenset({"a"})
    assert found.model == frozenset({"b"})
    assert found.unknown == frozenset({"c"})
    assert found.stems == frozenset({"a", "b", "c"})
    assert found.count == 3 and bool(found)


def test_scan_existing_masks_limits_to_given_stems(tmp_path):
    layout = _project(tmp_path, {"a": "RGBA", "b": "L", "zz_orphan": "L"})
    found = scan_existing_masks(layout, stems={"a", "b", "c", "d"})
    assert found.stems == frozenset({"a", "b"})


def test_scan_existing_masks_without_mask_folder_is_empty(tmp_path):
    found = scan_existing_masks(ProjectLayout(str(tmp_path)))
    assert found == ExistingMasks.empty()
    assert not found


def test_overwrite_policy_skip_sets(tmp_path):
    found = scan_existing_masks(_project(tmp_path, {"a": "RGBA", "b": "L"}))
    assert OverwritePolicy.KEEP_EXISTING.skip_stems(found) == frozenset({"a", "b"})
    assert OverwritePolicy.OVERWRITE_ALL.skip_stems(found) == frozenset()

"""The dataset export skips an image Pillow refuses as too large, and carries on.

Pillow refuses to open an image over twice ``PIL.Image.MAX_IMAGE_PIXELS`` (about 179 Mpx
by default) with ``DecompressionBombError``, which is neither ``OSError`` nor
``ValueError``: the export's size check (R7) opens every exported source image, so one
such image aborted the whole export and left a half-written output folder. It is now that
image's error (``errors.txt``) and the other images are exported, as for any unreadable
image. A tracing Pillow refuses (the GT file of such an image) is handled the same way.

The tests lower the limit instead of writing a 179 Mpx image.
"""

from __future__ import annotations

import csv

import numpy as np
import pytest
from PIL import Image

from app.gt.export import ExportOptions, export_dataset
from app.gt.store import Tracing, gt_path, write_tracing
from tests.test_gt_export import _STEMS, _build_project

# 20 x 20 px (the project's images and tracings) pass; 40 x 30 px is over twice the limit.
_LIMIT = 500
_BIG = (40, 30)


@pytest.fixture
def project(tmp_path):
    return _build_project(tmp_path)


def _export(project, tmp_path):
    project_dir, images, _write_registry, registry = project
    out_dir = tmp_path / "out"
    options = ExportOptions(out_dir=str(out_dir), only_complete=False)
    return out_dir, export_dataset(project_dir, images, registry, options)


def _assert_only_error(out_dir, result, stem, ext=".png"):
    assert [s for s, _ in result.errors] == [stem]
    assert "exceeds limit" in result.errors[0][1]
    assert result.exported == len(_STEMS) - 1
    assert result.train + result.val == len(_STEMS) - 1
    assert not (out_dir / "images" / f"{stem}{ext}").exists()
    assert not (out_dir / "masks" / f"{stem}.png").exists()
    with open(out_dir / "manifest.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert stem not in {row["stem"] for row in rows} and len(rows) == len(_STEMS) - 1
    assert (out_dir / "errors.txt").read_text(encoding="utf-8").startswith(f"{stem}\t")
    assert (out_dir / "manifest.csv.provenance.json").exists()


def test_a_source_image_pillow_refuses_is_that_images_error(project, tmp_path, monkeypatch):
    _project_dir, images, _write_registry, _registry = project
    big = _STEMS[2]
    # Pillow refuses the image; its 20 x 20 tracing (read first) is read as usual.
    Image.new("RGB", _BIG, color=(10, 20, 30)).save(images[big])
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", _LIMIT)

    out_dir, result = _export(project, tmp_path)

    _assert_only_error(out_dir, result, big)


def test_a_tracing_pillow_refuses_is_that_images_error(project, tmp_path, monkeypatch):
    _project_dir, images, write_registry, _registry = project
    big = _STEMS[4]
    width, height = _BIG
    Image.new("RGB", _BIG, color=(10, 20, 30)).save(images[big])
    ids = np.zeros((height, width), np.uint8)
    write_tracing(gt_path(images[big]), Tracing(ids=ids, reviewed=frozenset()), write_registry)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", _LIMIT)

    out_dir, result = _export(project, tmp_path)

    _assert_only_error(out_dir, result, big)


def test_the_other_images_export_as_without_it(project, tmp_path, monkeypatch):
    """The images that pass are exported exactly as with Pillow's default limit."""
    _project_dir, images, _write_registry, _registry = project
    reference_dir, reference = _export(project, tmp_path / "reference")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", _LIMIT)
    Image.new("RGB", _BIG, color=(10, 20, 30)).save(images[_STEMS[2]])

    out_dir, result = _export(project, tmp_path)

    assert reference.errors == [] and [s for s, _ in result.errors] == [_STEMS[2]]
    for stem in _STEMS:
        if stem == _STEMS[2]:
            continue
        for sub in ("images", "masks"):
            assert (out_dir / sub / f"{stem}.png").read_bytes() == (
                reference_dir / sub / f"{stem}.png"
            ).read_bytes()

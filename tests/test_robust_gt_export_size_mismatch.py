"""R7: the dataset export never pairs an image with a tracing of another size.

Trace GT treats a ``gt/<stem>.png`` whose size differs from its image as unreadable
(spec 2026-09-22 section 3: danger notice, empty canvas; the image was replaced or
resized after tracing). The export copied such a pair as-is: ``images/<stem>`` at the
new size next to ``masks/<stem>.png`` at the old one, which a training pipeline pairs
by name and then misaligns or crashes on. The stem is now an export error, like an
unreadable GT file, and nothing of it is written.
"""

from __future__ import annotations

import csv

from PIL import Image

from app.gt.export import ExportOptions, export_dataset
from tests.test_gt_export import _STEMS, _build_project


def test_a_tracing_of_another_size_is_an_export_error(tmp_path):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    resized = _STEMS[1]
    Image.new("RGB", (40, 30), color=(10, 20, 30)).save(images[resized])  # GT is 20 x 20
    out_dir = tmp_path / "out"

    result = export_dataset(
        project_dir, images, registry, ExportOptions(out_dir=str(out_dir), only_complete=False)
    )

    assert [stem for stem, _ in result.errors] == [resized]
    message = result.errors[0][1]
    assert "20x20" in message and "40x30" in message
    assert result.exported == len(_STEMS) - 1
    assert not (out_dir / "images" / f"{resized}.png").exists()
    assert not (out_dir / "masks" / f"{resized}.png").exists()
    with open(out_dir / "manifest.csv", encoding="utf-8", newline="") as fh:
        assert resized not in {row["stem"] for row in csv.DictReader(fh)}
    assert resized in (out_dir / "errors.txt").read_text(encoding="utf-8")


def test_every_exported_image_matches_its_mask(tmp_path):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    out_dir = tmp_path / "out"

    result = export_dataset(
        project_dir, images, registry, ExportOptions(out_dir=str(out_dir), only_complete=False)
    )

    assert result.errors == [] and result.exported == len(_STEMS)
    for stem in _STEMS:
        with (
            Image.open(out_dir / "images" / f"{stem}.png") as im,
            Image.open(out_dir / "masks" / f"{stem}.png") as mask,
        ):
            assert im.size == mask.size

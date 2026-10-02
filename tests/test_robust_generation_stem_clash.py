"""R7: two image files with one stem get one output, from the image the Library shows.

``mask/<stem>.png`` and ``skeletons/<stem>_fake.png`` are named by stem. A folder with
``a.jpg`` and ``a.png`` had both processed: two saves raced for one output file on the
writer threads, so the surviving mask came from either image (29 / 1 over 30 runs), and
the run reported two written inputs for one file. Generation now takes one image per
stem, the Library's pick (``ProjectLayout.scan_images``: ``.png`` before ``.jpg`` before
``.jpeg``); folders without such a clash get exactly the same inputs as before.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
import torch
from PIL import Image

from app.inference import mask_inference as mi
from app.inference import skeleton_inference as si
from app.project_layout import ProjectLayout
from tests.test_generation_inference import cpu_runtime  # noqa: F401 - fixture


class _Brightness(torch.nn.Module):
    """Mask model stand-in: foreground where the input is bright."""

    def forward(self, x):
        return torch.sigmoid(x.mean(1, keepdim=True) * 50)


class _Skeleton:
    def run_batch(self, x):
        return torch.tanh(x * 5)


def _folder(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    Image.new("RGB", (64, 48), (0, 0, 0)).save(src / "a.jpg")  # dark: an empty mask
    Image.new("RGB", (64, 48), (255, 255, 255)).save(src / "a.png")  # bright: a full mask
    for k in range(6):
        Image.new("RGB", (64, 48), (40 * k, 0, 0)).save(src / f"b{k}.jpg")
    (src / "folder.jpg").mkdir()  # a folder named like an image: the Library ignores it
    return str(src)


def _mean(path):
    with Image.open(path) as im:
        return float(np.asarray(im).mean())


def test_the_library_shows_one_image_per_stem(tmp_path):
    src = _folder(tmp_path)
    images = ProjectLayout(src).scan_images()
    assert os.path.basename(images["a"]) == "a.png"
    assert "folder" not in images and len(images) == 7


@pytest.mark.parametrize("run", range(5))
def test_mask_generation_takes_the_librarys_image_for_a_shared_stem(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    run,
):
    src = _folder(tmp_path)
    out = str(tmp_path / "mask")

    report = mi.generate_masks(src, out, model=_Brightness(), batch_size=8)

    assert _mean(os.path.join(out, "a.png")) == 255  # from a.png, every time
    assert sorted(report.written) == ["a.png", *(f"b{k}.jpg" for k in range(6))]
    assert report.failed == [] and report.total == 7
    assert len(report.written) == len([n for n in os.listdir(out) if n.endswith(".png")])


@pytest.mark.parametrize("run", range(5))
def test_skeleton_generation_takes_the_librarys_image_for_a_shared_stem(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    run,
):
    src = _folder(tmp_path)
    out = str(tmp_path / "skeletons")

    report = si.run_skeleton_generation(src, out, model=_Skeleton(), batch_size=8)

    assert _mean(os.path.join(out, "a_fake.png")) > 250  # from a.png, every time
    assert sorted(report.written) == ["a.png", *(f"b{k}.jpg" for k in range(6))]
    assert report.failed == [] and report.total == 7


def test_skeleton_generation_still_reads_its_other_formats(tmp_path):
    # Skeleton generation has always read .bmp / .tif / .ppm too (the Library does not):
    # kept, so a folder of TIFFs still gets its skeletons. A stem shared with a .png
    # still goes to the .png.
    src = tmp_path / "imgs"
    src.mkdir()
    for name in ("t.tif", "u.bmp", "v.ppm", "w.TIFF", "x.png", "x.tif"):
        Image.new("RGB", (8, 6)).save(src / name)
    names = [os.path.basename(p) for p in si.make_dataset(str(src))]
    assert names == ["t.tif", "u.bmp", "v.ppm", "w.TIFF", "x.png"]


def test_inputs_without_a_shared_stem_are_unchanged(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    names = ["B.JPG", "a.jpeg", "c.png", "d.jpg", "e.PNG", "notes.txt"]
    for name in names:
        (src / name).write_bytes(b"x")
    legacy_masks = sorted(
        n for n in os.listdir(src) if n.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    legacy_skeletons = [
        os.path.join(str(src), n) for n in sorted(os.listdir(src)) if si.is_image_file(n)
    ]

    all_names, todo, skipped = mi.list_inputs(str(src))
    assert all_names == legacy_masks
    assert todo == [os.path.join(str(src), n) for n in legacy_masks] and skipped == []
    assert si.make_dataset(str(src)) == legacy_skeletons

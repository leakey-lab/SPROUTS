"""Export dataset dialog: how many images have a GT tracing (``count_traced``).

The dialog says it up front, before anything is chosen. Counting ran four ``os.path``
calls per image on the GUI thread: at 46k images the dialog showed 0.65 s late (and the
count came from one ``gt/`` listing per folder already). It now slices each path instead,
and still lists each ``gt/`` folder once; the count is the one the old code gave, pinned
here on awkward paths and random subsets of them.
"""

from __future__ import annotations

import os
import random
import time

import pytest

from app.gt import gt_dir, gt_path
from app.gui.export_dataset_dialog import count_traced


def _reference_count(images: dict[str, str]) -> int:
    """``count_traced`` as the export dialog first shipped it (8ae250e)."""
    listed: dict[str, set[str]] = {}
    traced = 0
    for path in images.values():
        folder = gt_dir(os.path.dirname(path))
        names = listed.get(folder)
        if names is None:
            try:
                names = {os.path.normcase(name) for name in os.listdir(folder)}
            except OSError:
                names = set()
            listed[folder] = names
        if os.path.normcase(os.path.basename(gt_path(path))) in names:
            traced += 1
    return traced


def _touch(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


@pytest.fixture
def awkward(tmp_path, monkeypatch):
    """Images (stem -> path, the files need not exist) next to gt/ folders of every kind."""
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for name in (
        "IMG_1.png",
        "img_2.PNG",  # another case than its image: counts where names ignore case
        ".hidden.png",
        "multi.dot.name.png",
        "noext.png",
        "..dots.png",
        ".png",  # the gt name of a path that ends in a separator
        "stray.png",
        "notes.txt",
    ):
        _touch(a / "gt" / name)
    (a / "gt" / "folder.png").mkdir()  # a folder named like a tracing
    b.mkdir()  # no gt/ at all
    _touch(c / "gt")  # gt is a file: it cannot be listed
    _touch(tmp_path / "gt" / "rel.png")
    monkeypatch.chdir(tmp_path)  # "rel.jpg" is a bare name, relative to here

    sep, alt = os.sep, "/"
    images = {
        "IMG_1": str(a / "IMG_1.jpg"),
        "IMG_1_alt": f"{a}{alt}IMG_1.jpg",  # the same folder, spelled with "/"
        "IMG_2": str(a / "IMG_2.JPG"),
        "hidden": str(a / ".hidden.jpg"),
        "multi": str(a / "multi.dot.name.tif"),
        "noext": str(a / "noext"),
        "dots": str(a / "..dots"),
        "folder": str(a / "folder.jpg"),
        "untraced": str(a / "untraced.jpg"),
        "trailing": f"{a}{sep}",
        "b": str(b / "IMG_1.jpg"),
        "c": str(c / "IMG_1.jpg"),
        "rel": "rel.jpg",
        "missing_folder": str(tmp_path / "nowhere" / "IMG_1.jpg"),
    }
    return images


def test_the_count_is_the_one_the_old_code_gave(awkward):
    expected = _reference_count(awkward)
    assert expected >= 8  # the fixture really has traced images of every kind

    assert count_traced(awkward) == expected
    assert count_traced({}) == 0
    rng = random.Random(0)
    items = list(awkward.items())
    for _ in range(200):
        subset = dict(rng.sample(items, rng.randint(1, len(items))))
        assert count_traced(subset) == _reference_count(subset), subset


def test_46k_images_are_counted_before_the_dialog_is_due(tmp_path):
    """At 46k images (100 traced) the old count took 0.57-0.75 s here and the new one about
    0.07 s, on a loaded machine; the bound is generous."""
    for i in range(0, 46_000, 460):
        _touch(tmp_path / "gt" / f"24EFCAM1_T001_L{i:06d}_2024.07.18_111618_001_SMM.png")
    images = {
        f"24EFCAM1_T001_L{i:06d}_2024.07.18_111618_001_SMM": str(
            tmp_path / f"24EFCAM1_T001_L{i:06d}_2024.07.18_111618_001_SMM.jpg"
        )
        for i in range(46_000)
    }
    runs = []
    for _ in range(3):
        start = time.perf_counter()
        traced = count_traced(images)
        runs.append((time.perf_counter() - start) * 1000)
    assert traced == 100
    assert min(runs) < 250, runs

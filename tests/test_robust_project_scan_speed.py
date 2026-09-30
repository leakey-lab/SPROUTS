"""R6: the ranked folder scan (images, masks) is leaner and returns the same mapping.

``ProjectLayout.scan_images`` / ``scan_masks`` list a folder once and keep one file
per stem by extension rank. Of 85 ms for a 46k-file folder, 30 ms was the listing and
the rest per-file Python (``os.path.splitext``, a generator, tuples). A single loop
takes 59 ms. They run while a project loads, before a mask run (on the GUI thread)
and after every generation run (``ImageManager.refresh_outputs``, GUI thread). The
reference is the pre-R6 scan, verbatim, over names that stress ``splitext``'s rules.
"""

from __future__ import annotations

import os

import pytest

from app import project_layout
from app.config import IMAGE_EXTENSIONS
from app.project_layout import ProjectLayout


def _pre_r6_scan(directory, key_of):
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                key = key_of(entry.name)
                if key is not None and entry.is_file():
                    yield key, entry.path
    except (FileNotFoundError, NotADirectoryError):
        return


def _pre_r6_scan_ranked(directory, extensions):
    """``project_layout._scan_ranked`` before R6, verbatim."""
    rank = {ext.lower(): i for i, ext in enumerate(extensions)}

    def ranked_stem(name):
        stem, ext = os.path.splitext(name)
        r = rank.get(ext.lower())
        return None if r is None else (stem, r)

    best = {}
    for (stem, r), path in _pre_r6_scan(directory, ranked_stem):
        if stem not in best or r < best[stem][0]:
            best[stem] = (r, path)
    return {stem: path for stem, (_, path) in best.items()}


NAMES = [
    ".png", "..png", "...jpg", ".a.png", "..a.jpg", "a.png", "a.JPG", "a.jpeg", "b.PnG",
    "c.jpg.png", "d.", "e", ".f", "g.tif", "h.png.tmp", ".x.png.1a2b3c4d.tmp", "i.jpeg",
    "i.jpg", "j .png", "k..png", "Données_根.png", "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM.jpg",
    "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM.PNG", "root_areas.csv", "_sprouts_run.json",
]  # fmt: skip


@pytest.fixture
def folder(tmp_path):
    for name in NAMES:
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "a_folder.png").mkdir()  # a folder is never a file of the project
    return str(tmp_path)


@pytest.mark.parametrize(
    "extensions",
    [IMAGE_EXTENSIONS, (".png",), (".jpg", ".png"), (".png", ".jpg", ".jpeg", ".tif", ".tiff")],
)
def test_the_ranked_scan_is_unchanged(folder, extensions):
    assert project_layout._scan_ranked(folder, extensions) == _pre_r6_scan_ranked(
        folder, extensions
    )


def test_the_layout_scans_are_unchanged(folder):
    layout = ProjectLayout(folder)
    assert layout.scan_images() == _pre_r6_scan_ranked(layout.root, IMAGE_EXTENSIONS)
    assert layout.scan_masks(folder) == _pre_r6_scan_ranked(folder, (".png", ".jpg", ".jpeg"))


def test_a_missing_folder_or_a_file_scans_empty(tmp_path):
    assert project_layout._scan_ranked(str(tmp_path / "missing"), IMAGE_EXTENSIONS) == {}
    (tmp_path / "file.png").write_bytes(b"")
    assert project_layout._scan_ranked(str(tmp_path / "file.png"), IMAGE_EXTENSIONS) == {}

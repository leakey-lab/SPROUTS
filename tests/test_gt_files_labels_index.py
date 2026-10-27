"""``LabelsIndex``: which images have class labels, from one listing of each ``gt/``.

The dataset export checked every image's ``gt/<stem>.png`` with two or three file checks
(46k images, few labelled: about 2-3 s of the export); the export dialog counted the same
names again with its own listing. Both now go through ``LabelsIndex``: the export passes
over an image without a file check when the listing of its ``gt/`` surely holds no such
name, and otherwise looks it up exactly as before. Pinned here: the export finds the file
the old lookup found (copied verbatim) for every image of awkward folders, on disks that
ignore case and on case-sensitive ones, and the listing never decides alone where a file
system may match names unlike ``lower()``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata

import pytest

from app.gt.export import ExportOptions, _labels_file, export_dataset
from app.gt.files import LabelsIndex
from app.gt.store import gt_path
from app.naming import stem_key
from app.project_layout import ProjectLayout
from tests.test_gt_export import _build_project


def _old_labels_file(image_path: str, listed: dict) -> str:
    """``app.gt.export._labels_file`` before 2026-10-26, verbatim (then ``os.path.exists``)."""
    exact = gt_path(image_path)
    if os.path.exists(exact):
        return exact
    folder = os.path.dirname(image_path)
    found = listed.get(folder)
    if found is None:
        labels = ProjectLayout(folder).scan_gt_labels()
        found = listed[folder] = {stem_key(stem): path for stem, path in labels.items()}
    stem = os.path.splitext(os.path.basename(image_path))[0]
    return found.get(stem_key(stem), exact)


def _old_find(image_path: str, listed: dict) -> str | None:
    found = _old_labels_file(image_path, listed)
    return found if os.path.exists(found) else None


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


def _make_case_sensitive(path: str) -> bool:
    os.makedirs(path)
    if sys.platform == "win32":
        subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", path, "enable"],
            capture_output=True,
            check=False,
        )
    probe = os.path.join(path, "Probe")
    _touch(probe)
    try:
        return not os.path.exists(os.path.join(path, "probe"))
    finally:
        os.remove(probe)


def _awkward(root: str) -> dict[str, str]:
    """Image paths by a test name (the images need not exist) next to ``gt/`` folders of
    every kind."""
    a, b, c, u = (os.path.join(root, name) for name in ("a", "b", "c", "u"))
    for name in (
        "IMG_1.png",
        "img_2.PNG",  # the extension and the stem in another case
        "Mixed_3.png",
        ".hidden.png",
        "multi.dot.name.png",
        "noext.png",
        "..dots.png",
        ".png",  # the labels name of a path that ends in a separator
        "x.png.png",  # of x.png.jpg
        "TILDE~1.png",
        "stray.png",
        "notes.txt",
    ):
        _touch(os.path.join(a, "gt", name))
    os.makedirs(os.path.join(a, "gt", "folder.png"))  # a folder named like labels
    os.makedirs(os.path.join(a, "gt", "CASEDIR.png"))  # ... in another case
    os.makedirs(b)  # no gt/ at all
    _touch(os.path.join(c, "gt"))  # gt is a file
    nfc = unicodedata.normalize("NFC", "plötz")
    for name in (nfc + ".png", unicodedata.normalize("NFD", "möhre") + ".png", "ASCII.png"):
        _touch(os.path.join(u, "gt", name))  # names outside ASCII: the folder is not sure
    _touch(os.path.join(root, "gt", "rel.png"))
    return {
        "img_1": os.path.join(a, "IMG_1.jpg"),
        "img_1_alt": f"{a}/IMG_1.jpg",  # the same folder, spelled with "/"
        "img_2": os.path.join(a, "IMG_2.JPG"),
        "mixed_3": os.path.join(a, "MIXED_3.tif"),
        "hidden": os.path.join(a, ".hidden.jpg"),
        "multi": os.path.join(a, "multi.dot.name.tif"),
        "noext": os.path.join(a, "noext"),
        "dots": os.path.join(a, "..dots"),
        "x.png": os.path.join(a, "x.png.jpg"),
        "tilde": os.path.join(a, "tilde~1.jpg"),
        "folder": os.path.join(a, "folder.jpg"),
        "casedir": os.path.join(a, "casedir.jpg"),
        "untraced": os.path.join(a, "untraced.jpg"),
        "trailing": a + os.sep,
        "no_gt": os.path.join(b, "IMG_1.jpg"),
        "gt_file": os.path.join(c, "IMG_1.jpg"),
        "nfc": os.path.join(u, nfc + ".jpg"),
        "nfd_labels": os.path.join(u, unicodedata.normalize("NFC", "möhre") + ".jpg"),
        "ascii_in_u": os.path.join(u, "ascii.jpg"),
        "untraced_in_u": os.path.join(u, "untraced.jpg"),
        "rel": "rel.jpg",  # a bare name, relative to the working folder
        "nowhere": os.path.join(root, "nowhere", "IMG_1.jpg"),
    }


@pytest.fixture(params=["as_the_disk_does", "case_sensitive"])
def awkward(request, tmp_path, monkeypatch):
    root = str(tmp_path / "root")
    if request.param == "case_sensitive":
        if not _make_case_sensitive(root):
            pytest.skip("needs a case-sensitive folder (fsutil on Windows; the ubuntu CI)")
    else:
        os.makedirs(root)
    paths = _awkward(root)
    monkeypatch.chdir(root)
    return paths


def test_the_export_finds_the_file_the_old_lookup_found(awkward):
    index, listed, old_listed = LabelsIndex(), {}, {}
    found = 0
    for name, path in awkward.items():
        expected = _old_find(path, old_listed)
        assert _labels_file(path, index, listed) == expected, name
        if index.lacks_labels(path):
            assert expected is None, name
        found += expected is not None
    assert found >= 8  # the fixture has labels of every kind


def test_a_listing_that_cannot_tell_never_decides_alone(awkward):
    index = LabelsIndex()
    assert index.lacks_labels(awkward["untraced"])  # an ASCII folder: no file check
    assert not index.lacks_labels(awkward["tilde"])  # "~": maybe a Windows short name
    assert not index.lacks_labels(awkward["untraced_in_u"])  # names outside ASCII in its gt/
    assert not index.lacks_labels(awkward["nfd_labels"])  # a name outside ASCII
    assert not index.lacks_labels(awkward["img_2"])  # listed in another case


def test_no_gt_folder_and_a_gt_file_have_no_labels(tmp_path):
    index = LabelsIndex()
    (tmp_path / "b").mkdir()
    _touch(str(tmp_path / "c" / "gt"))
    assert index.lacks_labels(str(tmp_path / "b" / "IMG.jpg"))
    assert index.lacks_labels(str(tmp_path / "c" / "IMG.jpg"))
    assert index.lacks_labels(str(tmp_path / "nowhere" / "IMG.jpg"))


def test_a_gt_folder_that_cannot_be_listed_is_checked_file_by_file(tmp_path, monkeypatch):
    _touch(str(tmp_path / "gt" / "IMG.png"))
    real_listdir = os.listdir

    def listdir(path="."):
        if os.path.basename(os.fspath(path)) == "gt":
            raise PermissionError(13, "Permission denied", path)
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", listdir)
    index = LabelsIndex()
    image = str(tmp_path / "IMG.jpg")
    assert not index.lacks_labels(image) and not index.lacks_labels(str(tmp_path / "x.jpg"))
    assert _labels_file(image, index, {}) == gt_path(image)  # found by its file check
    assert index.count([image]) == 0  # the dialog's count, as before: nothing listed


def test_the_export_checks_no_file_of_an_image_without_labels(tmp_path, monkeypatch):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    for i in range(200):
        images[f"untraced_{i:03d}"] = os.path.join(project_dir, f"untraced_{i:03d}.jpg")
    untraced = {os.path.normcase(gt_path(p)) for s, p in images.items() if "untraced" in s}
    checked = []
    real_exists = os.path.exists

    def exists(path):
        checked.append(os.path.normcase(os.fspath(path)))
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", exists)
    result = export_dataset(
        project_dir, images, registry, ExportOptions(str(tmp_path / "out"), only_complete=False)
    )

    assert result.exported == 6 and result.errors == []
    assert not untraced.intersection(checked)

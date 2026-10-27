"""The input keys of a folder's files come from one key per folder (R5 records, L1a).

``<csv>.inputs.json`` names each measured file by :func:`input_key` (its path relative to
the project, ``/`` separators), and the Measure status count, :meth:`MeasureRun.unchanged`
and the metrics bar compare keys with it. ``input_key`` costs one relpath per call (~10-30
us on Windows), so the folder listings and the Measure run take a folder's key once and
add each file name: these tests pin that every such key is byte-identical to
``input_key`` of the file's path, on real folders and on path strings of every shape.
"""

from __future__ import annotations

import os
import sys

import pytest

from app.inference import measure_inputs
from app.inference.measure_inputs import (
    file_key,
    input_key,
    key_prefix,
    keys_and_folders,
    scan_stamps,
)

WINDOWS = sys.platform == "win32"

NAMES = [
    "a.png",
    "B_fake.PNG",
    "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM_fake.png",
    "ünïcode ß.png",
    "with space.png",
    "MiXeD.Png",
    "x",
    "a.b.c.png",
    "..png",
    "_fake.png",
]
#: names Windows path rules rewrite (abspath drops a trailing dot or space): keyed alone
ODD_NAMES = ["trail.png.", "trail.png ", ".", "..", ""]


def _write(folder, names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"x")


@pytest.fixture
def project(tmp_path):
    """A project with files in the root, mask/, mask/sub/, a sibling and the parent."""
    root = tmp_path / "proj"
    for folder in (root, root / "mask", root / "mask" / "sub", tmp_path / "other", tmp_path):
        _write(str(folder), NAMES)
    return str(root)


def _folders(root):
    parent = os.path.dirname(root)
    return [
        root,
        os.path.join(root, "mask"),
        os.path.join(root, "mask", "sub"),
        os.path.join(parent, "other"),
        parent,  # above the project: keyed per file
        root + os.sep,
        os.path.join(root, "mask") + os.sep,
        os.path.join(root, "mask", "..", "mask"),
        os.path.join(root.upper() if WINDOWS else root, "mask"),
    ]


def _roots(root):
    roots = [root, root + os.sep, os.path.join(root, ".")]
    if WINDOWS:
        roots += [root.replace("\\", "/"), root.upper(), root.lower()]
    return roots


@pytest.mark.parametrize("suffix", ["", ".png", "_fake.png"])
def test_scan_stamps_keys_are_the_input_keys_of_the_paths(project, suffix):
    for root in _roots(project):
        for folder in _folders(project):
            stamps = scan_stamps(folder, suffix, root)
            assert stamps, (folder, suffix)
            for key, (path, _size, _mtime) in stamps.items():
                name = path[path.rfind(os.sep) + 1 :]
                assert path == os.path.join(os.path.abspath(folder), name)
                assert key == input_key(path, root), (root, folder, name)


def test_scan_stamps_of_a_missing_folder_is_empty(tmp_path):
    assert scan_stamps(str(tmp_path / "gone"), ".png", str(tmp_path)) == {}


class _Entry:
    def __init__(self, folder, name):
        self.name, self.path = name, os.path.join(folder, name)

    def is_file(self):
        return True

    def stat(self):
        return os.stat_result((0, 0, 0, 0, 0, 0, 1, 0, 1, 0))


class _Scan:
    def __init__(self, folder, names):
        self.entries = [_Entry(folder, n) for n in names]

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False


def test_names_ending_in_a_dot_or_a_space_are_keyed_as_input_key_keys_them(tmp_path, monkeypatch):
    """Only another system (or a ``\\\\?\\`` path) makes such names; Windows drops the dot or
    space in input_key, so they keep that key (two names can share it, as before)."""
    root = str(tmp_path)
    folder = os.path.join(root, "mask")
    names = ["a.png", "a.png.", "a.png ", "b. png"]
    monkeypatch.setattr(measure_inputs.os, "scandir", lambda f: _Scan(f, names))
    stamps = scan_stamps(folder, "", root)
    assert set(stamps) == {input_key(os.path.join(folder, n), root) for n in names}


def _path_shapes(root):
    """(file path, project root) strings of many shapes (not touched on disk)."""
    parent = os.path.dirname(root)
    folders = [
        root,
        os.path.join(root, "mask"),
        os.path.join(root, "gt", "skeletons"),
        os.path.join(root, "mask", "..", "skeletons"),
        os.path.join(root, ".", "mask"),
        os.path.join(parent, "sibling"),
        parent,
        os.path.dirname(parent),
        os.path.join(root, "mask."),
        os.path.join(root, "mask "),
        "mask",
        os.path.join("rel", "sub"),
        "",
    ]
    if WINDOWS:
        drive = os.path.splitdrive(root)[0]
        other = "Q:" if drive.upper() != "Q:" else "R:"
        folders += [
            root.replace("\\", "/") + "/mask",
            root.upper() + "\\MASK",
            other + "\\",
            other + "\\data\\mask",
            drive + "\\",
            "\\\\server\\share",
            "\\\\server\\share\\proj\\mask",
            "\\\\?\\" + os.path.join(root, "mask"),
            drive + "mask",  # drive-relative
            "\\mask",  # rooted, no drive
        ]
    else:
        folders += ["/", "//", "//net/x", "/proj/mask", os.path.join(root, "back\\slash")]
    seps = [os.sep] + (["/"] if WINDOWS else [])
    # a name like the project's folder: in the folder above, it is the project's key
    own = os.path.basename(root)
    extra = [own, own.upper()] + (["back\\slash.png"] if not WINDOWS else [])
    for folder in folders:
        for name in NAMES + ODD_NAMES + extra:
            for sep in seps:
                yield (folder + sep + name if folder else name), root


def _all_roots(tmp_path):
    root = str(tmp_path / "proj")
    roots = _roots(root) + ["proj", os.path.join("a", "proj")]
    if WINDOWS:
        roots += [
            "\\\\server\\share\\proj",
            "\\\\server\\share",
            os.path.splitdrive(root)[0] + "\\",
        ]
    else:
        roots += ["/"]
    return root, roots


def test_file_key_is_input_key_for_paths_of_every_shape(tmp_path):
    base, roots = _all_roots(tmp_path)
    checked = 0
    for root in roots:
        prefixes: dict[str, str | None] = {}
        for path, _ in _path_shapes(base):
            assert file_key(path, root, prefixes) == input_key(path, root), (path, root)
            checked += 1
    assert checked > 1000


def test_keys_and_folders_are_the_keys_and_folders_of_each_path(tmp_path):
    base, roots = _all_roots(tmp_path)
    paths = [path for path, _ in _path_shapes(base) if os.path.split(path)[1]]
    for root in roots:
        keys, folders = keys_and_folders(paths, root)
        assert keys == {path: input_key(path, root) for path in paths}, root
        assert folders == {os.path.dirname(os.path.abspath(path)) for path in paths}, root


def test_key_prefix_of_the_project_folder_and_of_folders_above_it(tmp_path):
    root = str(tmp_path / "proj")
    assert key_prefix(root, root) == ""
    assert key_prefix(os.path.join(root, "mask"), root) == "mask/"
    assert key_prefix(os.path.join(root, "gt", "mask"), root) == "gt/mask/"
    assert key_prefix(str(tmp_path), root) is None  # above the project: keyed per file
    assert key_prefix(str(tmp_path / "other"), root) == "../other/"


@pytest.mark.skipif(not WINDOWS, reason="drives are a Windows notion")
def test_a_drive_root_on_another_drive_keeps_its_one_separator(tmp_path):
    root = str(tmp_path / "proj")
    other = "Q:" if os.path.splitdrive(root)[0].upper() != "Q:" else "R:"
    assert key_prefix(other + "\\", root) == other + "/"
    assert file_key(other + "\\a.png", root, {}) == input_key(other + "\\a.png", root)
    assert file_key(other + "\\a.png", root, {}) == other + "/a.png"


def test_measure_run_records_the_input_keys_of_its_files(tmp_path):
    """The record's keys (MeasureRun._remember) are input_key of each measured file."""
    import numpy as np

    from app.inference.measure_run import AREA, MeasureRun
    from app.io_utils import imwrite_unicode

    root = tmp_path / "proj"
    images = {}
    for i, stem in enumerate(["a", "B b", "ünï"]):
        img = np.zeros((16, 16), np.uint8)
        img[i : i + 4, :] = 255
        path = root / "mask" / f"{stem}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        imwrite_unicode(str(path), img)
        imwrite_unicode(str(root / f"{stem}.jpg"), img)
        images[stem] = str(path)
    run = MeasureRun(AREA, images, str(root / "mask"), project_root=str(root))
    run.compute()
    assert set(run.fingerprints) == {input_key(p, str(root)) for p in images.values()}
    assert set(run.fingerprints) == {"mask/a.png", "mask/B b.png", "mask/ünï.png"}
    run.write()
    assert run.unchanged() is True


def test_measured_files_are_not_kept_in_the_session_hash_cache(tmp_path):
    """Only the CSV's hash is kept: the readers hash a file only when its size or time
    differ from the record's, so a measured file's stamp is never looked up."""
    import cv2
    import numpy as np

    from app.inference.measure_run import AREA, MeasureRun

    root = tmp_path / "proj"
    (root / "mask").mkdir(parents=True)
    images = {}
    for stem in ("a", "b"):
        cv2.imwrite(str(root / "mask" / f"{stem}.png"), np.zeros((8, 8), np.uint8))
        images[stem] = str(root / "mask" / f"{stem}.png")
    run = MeasureRun(AREA, images, str(root / "mask"), project_root=str(root))
    run.compute()
    csv_path = run.write()
    cached = {key[0] for key in measure_inputs._sha256_cache}
    assert os.path.abspath(csv_path) in cached
    assert not cached & {os.path.abspath(p) for p in images.values()}

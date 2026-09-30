"""R1: the dataset export writes its text files through the shared atomic writer.

``app/gt/export.py`` wrote ``splits/train.txt``, ``splits/val.txt``, ``classes.json``,
``manifest.csv`` and ``errors.txt`` with plain ``open(..., "w")``: a failure (disk full)
or a killed process part-way left a truncated file that looked complete, e.g. a
``manifest.csv`` listing only some of the exported images. They now go through
``app.io_utils`` (temp file + swap), so each file is complete or absent, and a file
held open by another program raises ``FileLockedError``. The bytes are unchanged:
the reference files below are written with the pre-R1 code, verbatim.
"""

from __future__ import annotations

import csv
import errno
import json
import os
import re

import pytest

from app import io_utils
from app.gt import export as gt_export
from app.gt.export import ExportOptions, export_dataset
from app.gt.store import gt_path
from app.io_utils import FileLockedError
from tests.test_gt_export import _STEMS, _build_project

_TEXT_FILES = ("splits/train.txt", "splits/val.txt", "classes.json", "manifest.csv", "errors.txt")


def _export_with_an_error(tmp_path, out_name="out"):
    """Export six stems, one of them with a corrupt GT file (so errors.txt exists)."""
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    with open(gt_path(images[_STEMS[2]]), "wb") as fh:
        fh.write(b"not a png\r\nsecond line")  # the message must stay one line
    out_dir = tmp_path / out_name
    result = export_dataset(
        project_dir, images, registry, ExportOptions(out_dir=str(out_dir), only_complete=False)
    )
    return out_dir, result, registry


def _read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


# --------------------------------------------------------------- pre-R1 writers, verbatim
def _pre_r1_write_split_file(path, stems):
    with open(path, "w", encoding="utf-8") as fh:
        for stem in stems:
            fh.write(stem + "\n")


def _pre_r1_write_files(ref_dir, out_dir, result, registry):
    """What the pre-R1 export wrote for the same content, with its own open() calls."""
    os.makedirs(os.path.join(ref_dir, "splits"))
    for split in ("train", "val"):
        stems = _read_lines(os.path.join(out_dir, "splits", f"{split}.txt"))
        _pre_r1_write_split_file(os.path.join(ref_dir, "splits", f"{split}.txt"), stems)

    with open(os.path.join(ref_dir, "classes.json"), "w", encoding="utf-8") as fh:
        json.dump(gt_export._classes_json(registry), fh, indent=2)

    with open(os.path.join(out_dir, "manifest.csv"), encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    with open(os.path.join(ref_dir, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=gt_export._MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with open(os.path.join(ref_dir, "errors.txt"), "w", encoding="utf-8") as fh:
        for stem, message in result.errors:
            fh.write(stem + "\t" + " ".join(message.split()) + "\n")


def test_the_exported_text_files_keep_their_bytes(tmp_path):
    out_dir, result, registry = _export_with_an_error(tmp_path)
    assert result.errors and result.train and result.val  # every file has content
    ref_dir = tmp_path / "ref"
    _pre_r1_write_files(str(ref_dir), str(out_dir), result, registry)

    for rel in _TEXT_FILES:
        assert (out_dir / rel).read_bytes() == (ref_dir / rel).read_bytes(), rel


def test_the_text_files_are_swapped_in_through_the_shared_writer(tmp_path, monkeypatch):
    swapped = []
    real_replace = os.replace

    def spy(src, dst):
        swapped.append((os.path.basename(src), os.path.basename(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", spy)
    out_dir, _result, _registry = _export_with_an_error(tmp_path)

    targets = [dst for _src, dst in swapped]
    for rel in _TEXT_FILES:
        assert os.path.basename(rel) in targets, rel
    for src, dst in swapped:  # ".<name>.<8 hex>.tmp": the name the stale-file sweep knows
        assert re.fullmatch(rf"\.{re.escape(dst)}\.[0-9a-f]{{8}}\.tmp", src), src
    leftovers = [n for _root, _dirs, files in os.walk(out_dir) for n in files if n.endswith(".tmp")]
    assert leftovers == []


def test_a_manifest_write_that_fails_part_way_leaves_no_truncated_manifest(tmp_path, monkeypatch):
    real_writerow = csv.DictWriter.writerow
    written = []

    def writerow(self, row):
        if len(written) == 3:  # the header and two images are out; the disk fills up
            raise OSError(errno.ENOSPC, "No space left on device (simulated)")
        written.append(row)
        return real_writerow(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", writerow)
    with pytest.raises(OSError, match="No space left"):
        _export_with_an_error(tmp_path)

    out_dir = tmp_path / "out"
    assert not (out_dir / "manifest.csv").exists()  # complete or absent, never truncated
    assert [n for n in os.listdir(out_dir) if n.endswith(".tmp")] == []


@pytest.mark.parametrize("rel", ["splits/train.txt", "classes.json", "manifest.csv"])
def test_a_text_file_held_open_by_another_program_raises_file_locked_error(
    tmp_path, monkeypatch, rel
):
    real_replace = os.replace

    def locked(src, dst):
        if os.path.basename(dst) == os.path.basename(rel):
            exc = PermissionError(errno.EACCES, "The process cannot access the file", dst)
            exc.winerror = 32  # ERROR_SHARING_VIOLATION
            raise exc
        real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", locked)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: None)
    with pytest.raises(FileLockedError, match="open in another program"):
        _export_with_an_error(tmp_path)
    assert not (tmp_path / "out" / rel).exists()

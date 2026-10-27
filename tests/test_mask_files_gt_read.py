"""Correct reads a GT mask as ``MaskStore.read`` reads a prediction (S01-07).

``mask_files._read_gt`` decodes ``gt/mask/<stem>.png`` through ``app.masks.read_mask_file``:
the exact fast path for plain grey and grey RGBA PNGs (every mask the editor saves), else the
reference decode. The reference here is ``_read_gt`` as it was, verbatim:
``imread_unicode(IMREAD_GRAYSCALE)``, ``None`` -> "unreadable", another size -> "size",
else the 0/1 plane of ``grey > MASK_THRESHOLD``. The corpora are the mask-store fast-path
tests' files (grey, RGBA, broken PNGs, legacy JPEG).
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

from app.gui.correct import mask_files
from app.io_utils import imread_unicode
from app.masks import MASK_THRESHOLD, read_mask_file, store, write_mask_file
from tests import test_mask_editor_store as editor_cases
from tests import test_mask_store_grey_fast_path as grey_cases
from tests import test_mask_store_rgba_fast_path as rgba_cases

CORPUS = {
    **{f"editor_{name}": case[0] for name, case in editor_cases.CASES.items()},
    **{f"grey_{name}": case[0] for name, case in grey_cases.ALL.items()},
    **{f"rgba_{name}": case[0] for name, case in rgba_cases.CASES.items()},
}


def _old_read_gt(path: str, size: tuple[int, int]) -> tuple[np.ndarray | None, str]:
    """``_read_gt`` before the shared reader, verbatim (without its log lines)."""
    grey = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if grey is None:
        return None, "unreadable"
    width, height = size
    if grey.shape[:2] != (height, width):
        return None, "size"
    return mask_files.zero_one_plane(grey > MASK_THRESHOLD), ""


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return (
        a.dtype == b.dtype
        and a.shape == b.shape
        and a.flags.writeable == b.flags.writeable
        and np.array_equal(a, b)
    )


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a_gt_mask_reads_as_it_read_before(tmp_path, name):
    path = str(tmp_path / "a.png")
    with open(path, "wb") as fh:
        fh.write(CORPUS[name])
    grey = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    fits = (61, 47) if grey is None else (grey.shape[1], grey.shape[0])

    for size in (fits, (fits[0] + 1, fits[1])):
        plane, why = mask_files._read_gt(path, size)
        old_plane, old_why = _old_read_gt(path, size)
        assert why == old_why
        assert _same(plane, old_plane)


def test_an_editor_gt_mask_takes_the_fast_path(tmp_path, monkeypatch):
    on = np.zeros((48, 64), bool)
    on[5:20, 3:60] = True
    path = str(tmp_path / "gt" / "mask" / "a.png")
    write_mask_file(path, on)
    calls = []
    real_imdecode, real_reference = cv2.imdecode, store._decode_reference
    monkeypatch.setattr(cv2, "imdecode", lambda *a: calls.append("cv2") or real_imdecode(*a))
    monkeypatch.setattr(
        store, "_decode_reference", lambda d: calls.append("ref") or real_reference(d)
    )

    plane, why = mask_files._read_gt(path, (64, 48))

    assert (why, calls) == ("", [])  # no OpenCV decode: the exact fast path read it
    assert plane.dtype == np.uint8 and np.array_equal(plane, on)
    assert np.array_equal(read_mask_file(path), on)


def test_a_missing_or_folder_gt_mask_is_unreadable_and_logged(tmp_path, caplog):
    folder = tmp_path / "gt" / "mask" / "b.png"
    folder.mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="app.gui.correct.mask_files"):
        for path in (str(tmp_path / "gt" / "mask" / "a.png"), str(folder)):
            assert mask_files._read_gt(path, (64, 48)) == (None, "unreadable")
            assert read_mask_file(path) is None
    assert [r.getMessage() for r in caplog.records] == [
        f"Failed to load the GT mask {tmp_path / 'gt' / 'mask' / 'a.png'}",
        f"Failed to load the GT mask {folder}",
    ]


def test_a_gt_mask_of_another_size_is_named_in_the_log(tmp_path, caplog):
    path = str(tmp_path / "a.png")
    write_mask_file(path, np.ones((60, 80), bool))
    with caplog.at_level(logging.WARNING, logger="app.gui.correct.mask_files"):
        assert mask_files._read_gt(path, (200, 150)) == (None, "size")
    assert [r.getMessage() for r in caplog.records] == [
        f"Mask of {path} is 80x60, image is 200x150; ignoring it"
    ]

"""One ``file_stamp`` and one ``same_file`` in ``app.io_utils`` (F5, X8)."""

from __future__ import annotations

import os

from app import io_utils
from app.io_utils import file_stamp, same_file


def test_the_helpers_are_exported():
    assert {"file_stamp", "same_file"} <= set(io_utils.__all__)


def test_file_stamp_of_a_file_is_path_mtime_and_size(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"12345")
    st = os.stat(path)
    assert file_stamp(str(path)) == (str(path), st.st_mtime_ns, st.st_size)


def test_file_stamp_of_a_missing_path_or_a_folder_is_unreadable(tmp_path):
    missing = str(tmp_path / "missing.png")
    folder = tmp_path / "named_like_a_file.png"
    folder.mkdir()
    assert file_stamp(missing) == (missing, None)
    assert file_stamp(str(folder)) == (str(folder), None)


def test_file_stamp_changes_when_the_file_is_rewritten(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"one")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = file_stamp(str(path))
    path.write_bytes(b"three")
    os.utime(path, ns=(2_000_000_000, 2_000_000_000))
    assert file_stamp(str(path)) != before


def test_same_file_needs_two_names():
    assert not same_file("", "")
    assert not same_file("a.png", "")
    assert not same_file("", "a.png")


def test_same_file_compares_two_spellings_of_one_path(tmp_path):
    path = tmp_path / "a.png"
    other = tmp_path.parent / tmp_path.name / ".." / tmp_path.name / "a.png"
    assert same_file(str(path), str(other))
    assert not same_file(str(path), str(tmp_path / "b.png"))

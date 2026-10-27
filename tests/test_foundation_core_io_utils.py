"""Foundation-core: atomic writes, locked-file errors and Unicode-safe image I/O."""

from __future__ import annotations

import csv
import errno
import os
import stat
import sys

import cv2
import numpy as np
import pytest

from app import io_utils
from app.io_utils import (
    FileLockedError,
    atomic_open,
    atomic_write_bytes,
    atomic_write_text,
    imread_unicode,
    imwrite_unicode,
)

UNICODE_DIRNAME = "Donn\u00e9es_\u6839\u7cfb"


def _leftovers(directory) -> list[str]:
    return sorted(os.listdir(directory))


def _sharing_violation(path, winerror: int = 32) -> PermissionError:
    exc = PermissionError(errno.EACCES, "The process cannot access the file", str(path))
    exc.winerror = winerror
    return exc


# ------------------------------------------------------------------ atomic writes


def test_atomic_write_bytes_creates_and_replaces(tmp_path):
    target = tmp_path / "a.bin"
    atomic_write_bytes(target, b"first")
    atomic_write_bytes(str(target), bytearray(b"second"))
    assert target.read_bytes() == b"second"
    assert _leftovers(tmp_path) == ["a.bin"]


def test_atomic_write_text_honours_encoding_and_newline(tmp_path):
    target = tmp_path / "t.csv"
    atomic_write_text(target, "a\nb\n", newline="")
    assert target.read_bytes() == b"a\nb\n"
    atomic_write_text(target, "\u00b5m\r\n", encoding="utf-8", newline="")
    assert target.read_bytes() == "\u00b5m\r\n".encode()


def test_atomic_open_streams_csv_identical_to_plain_open(tmp_path):
    rows = [{"Image": "x", "Area (mm\u00b2)": 1.25, "Error": ""}]
    headers = ["Image", "Area (mm\u00b2)", "Error"]

    def write(fh):
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    with open(tmp_path / "plain.csv", "w", newline="", encoding="utf-8") as fh:
        write(fh)
    with atomic_open(tmp_path / "atomic.csv", "w", newline="", encoding="utf-8") as fh:
        write(fh)
    assert (tmp_path / "atomic.csv").read_bytes() == (tmp_path / "plain.csv").read_bytes()


def test_failure_inside_atomic_open_keeps_old_file_and_no_temp(tmp_path):
    target = tmp_path / "keep.csv"
    target.write_bytes(b"old complete file")
    with pytest.raises(RuntimeError), atomic_open(target, "w") as fh:
        fh.write("partial")
        raise RuntimeError("killed mid-write")
    assert target.read_bytes() == b"old complete file"
    assert _leftovers(tmp_path) == ["keep.csv"]


def test_failed_replace_keeps_old_file_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "m.png"
    target.write_bytes(b"old")

    def broken_replace(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(io_utils.os, "replace", broken_replace)
    with pytest.raises(OSError) as info:
        atomic_write_bytes(target, b"new")
    assert not isinstance(info.value, FileLockedError)
    assert target.read_bytes() == b"old"
    assert _leftovers(tmp_path) == ["m.png"]


def test_temp_file_name_is_invisible_to_image_scanners(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", spy)
    atomic_write_bytes(tmp_path / "x.png", b"png")
    (name,) = seen
    assert name.endswith(".tmp")
    assert not name.lower().endswith((".png", ".jpg", ".jpeg", ".csv", ".json"))


def test_missing_directory_raises_without_side_effects(tmp_path):
    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(tmp_path / "nope" / "x.bin", b"x")
    assert _leftovers(tmp_path) == []


def test_invalid_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError), atomic_open(tmp_path / "x", "a"):
        pass


# ------------------------------------------------------------------ locked files


def test_sharing_violation_retries_then_raises_file_locked(tmp_path, monkeypatch):
    target = tmp_path / "root_lengths.csv"
    target.write_text("old\n", encoding="utf-8")
    calls = []

    def locked_replace(src, dst):
        calls.append(dst)
        raise _sharing_violation(dst)

    monkeypatch.setattr(io_utils.os, "replace", locked_replace)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: None)
    with pytest.raises(FileLockedError) as info:
        atomic_write_text(target, "new\n")
    err = info.value
    assert isinstance(err, PermissionError)
    assert str(err) == (
        "root_lengths.csv is open in another program (for example Excel). Close it and try again."
    )
    assert err.filename == str(target)
    assert len(calls) == len(io_utils.REPLACE_RETRY_DELAYS) + 1
    assert target.read_text(encoding="utf-8") == "old\n"
    assert _leftovers(tmp_path) == ["root_lengths.csv"]


def test_transient_sharing_violation_is_retried(tmp_path, monkeypatch):
    target = tmp_path / "x.png"
    real_replace = os.replace
    attempts = []

    def flaky_replace(src, dst):
        attempts.append(dst)
        if len(attempts) == 1:
            raise _sharing_violation(dst, winerror=5)
        real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", flaky_replace)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: None)
    atomic_write_bytes(target, b"ok")
    assert target.read_bytes() == b"ok"
    assert len(attempts) == 2


def test_plain_permission_error_is_not_reported_as_locked(tmp_path, monkeypatch):
    def denied(src, dst):
        raise PermissionError(errno.EACCES, "Permission denied", dst)  # no winerror

    monkeypatch.setattr(io_utils.os, "replace", denied)
    with pytest.raises(PermissionError) as info:
        atomic_write_bytes(tmp_path / "x.bin", b"x")
    assert not isinstance(info.value, FileLockedError)


def test_file_locked_error_survives_pickling():
    import pickle

    err = pickle.loads(pickle.dumps(FileLockedError("D:/data/root_areas.csv")))
    assert isinstance(err, FileLockedError)
    assert "root_areas.csv is open in another program" in str(err)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode lock")
def test_real_excel_style_lock_raises_file_locked(tmp_path):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    target = tmp_path / "root_lengths.csv"
    target.write_text("old\n", encoding="utf-8")
    generic_read, file_share_read, open_existing = 0x80000000, 0x1, 3
    handle = k32.CreateFileW(
        str(target), generic_read, file_share_read, None, open_existing, 0, None
    )
    try:
        with pytest.raises(FileLockedError):
            atomic_write_text(target, "new\n")
    finally:
        k32.CloseHandle(handle)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert _leftovers(tmp_path) == ["root_lengths.csv"]


# ------------------------------------------------------------------ image I/O


def _sample_image() -> np.ndarray:
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    cv2.line(img, (2, 2), (60, 40), (255, 255, 255), 1)
    img[5:9, 5:9] = (10, 200, 30)
    return img


def test_imwrite_then_imread_round_trips_on_unicode_path(tmp_path):
    folder = tmp_path / UNICODE_DIRNAME
    folder.mkdir()
    path = folder / "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM_fake.png"
    img = _sample_image()
    imwrite_unicode(path, img)
    back = imread_unicode(path)
    assert back is not None and np.array_equal(back, img)
    gray = imread_unicode(str(path), cv2.IMREAD_GRAYSCALE)
    assert gray.shape == img.shape[:2]


def test_imread_unicode_matches_cv2_imread_on_ascii_path(tmp_path):
    path = tmp_path / "plain.png"
    cv2.imwrite(str(path), _sample_image())
    for flags in (cv2.IMREAD_COLOR, cv2.IMREAD_GRAYSCALE, cv2.IMREAD_UNCHANGED):
        assert np.array_equal(imread_unicode(path, flags), cv2.imread(str(path), flags))


def test_imread_unicode_returns_none_for_missing_empty_or_corrupt(tmp_path):
    assert imread_unicode(tmp_path / "missing.png") is None
    (tmp_path / "empty.png").write_bytes(b"")
    assert imread_unicode(tmp_path / "empty.png") is None
    (tmp_path / "corrupt.png").write_bytes(b"not a png at all")
    assert imread_unicode(tmp_path / "corrupt.png") is None
    assert imread_unicode(tmp_path) is None  # a directory


def test_imwrite_unicode_raises_on_encode_failure_and_leaves_no_file(tmp_path):
    with pytest.raises(ValueError):
        imwrite_unicode(tmp_path / "bad.unknownext", _sample_image())
    assert _leftovers(tmp_path) == []


def test_imwrite_unicode_passes_encoder_params(tmp_path):
    img = _sample_image()
    imwrite_unicode(tmp_path / "fast.png", img, [cv2.IMWRITE_PNG_COMPRESSION, 0])
    imwrite_unicode(tmp_path / "small.png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    assert (tmp_path / "fast.png").stat().st_size > (tmp_path / "small.png").stat().st_size
    assert np.array_equal(imread_unicode(tmp_path / "fast.png"), img)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows read-only attribute")
def test_read_only_target_is_not_reported_as_locked(tmp_path, monkeypatch):
    import stat

    target = tmp_path / "ro.csv"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: pytest.fail("must not retry"))
    try:
        with pytest.raises(PermissionError) as info:
            atomic_write_text(target, "new\n")
        assert not isinstance(info.value, FileLockedError)
        assert target.read_text(encoding="utf-8") == "old\n"
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    assert _leftovers(tmp_path) == ["ro.csv"]


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_durable_writes_fsync_and_bulk_writes_can_skip_it(tmp_path, monkeypatch, platform):
    synced, folders = [], []
    monkeypatch.setattr(io_utils.os, "fsync", lambda fd: synced.append(fd))
    monkeypatch.setattr(io_utils, "_sync_folder", folders.append)
    monkeypatch.setattr(io_utils, "_current_platform", lambda: platform)
    synced_folders = [] if platform == "win32" else [os.path.abspath(tmp_path)]
    atomic_write_bytes(tmp_path / "hand_made.png", b"x")
    assert len(synced) == 1
    assert folders == synced_folders  # off Windows the rename is made durable too
    atomic_write_bytes(tmp_path / "generated.png", b"y", durable=False)
    atomic_write_text(tmp_path / "t.txt", "z", durable=False)
    with atomic_open(tmp_path / "s.csv", "w", durable=False) as fh:
        fh.write("a")
    imwrite_unicode(tmp_path / "g.png", _sample_image(), durable=False)
    assert len(synced) == 1
    assert folders == synced_folders
    assert (tmp_path / "generated.png").read_bytes() == b"y"
    assert sorted(os.listdir(tmp_path)) == [
        "g.png",
        "generated.png",
        "hand_made.png",
        "s.csv",
        "t.txt",
    ]


def test_the_folder_is_synced_after_the_swap_and_never_fails_a_save(tmp_path, monkeypatch):
    events = []
    real_replace = os.replace
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    monkeypatch.setattr(io_utils.os, "fsync", lambda fd: events.append("fsync"))
    monkeypatch.setattr(
        io_utils.os, "replace", lambda a, b: events.append("replace") or real_replace(a, b)
    )
    monkeypatch.setattr(io_utils, "_sync_folder", lambda folder: events.append(folder))
    atomic_write_bytes(tmp_path / "a.png", b"old")
    assert events == ["fsync", "replace", os.path.abspath(tmp_path)]

    def refuse(*_args):
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.undo()
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    monkeypatch.setattr(io_utils.os, "open", refuse)  # the folder cannot be opened
    atomic_write_bytes(tmp_path / "a.png", b"new")
    assert (tmp_path / "a.png").read_bytes() == b"new"
    assert _leftovers(tmp_path) == ["a.png"]


def test_a_folder_sync_never_raises(tmp_path):
    io_utils._sync_folder(str(tmp_path))  # on Windows a folder cannot be opened: no error
    io_utils._sync_folder(str(tmp_path / "missing"))


@pytest.mark.skipif(sys.platform == "win32", reason="the folder fsync of Linux and macOS")
def test_posix_saves_fsync_the_file_then_swap_then_fsync_the_folder(tmp_path, monkeypatch):
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        events.append("folder" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", lambda a, b: events.append("replace") or real_replace(a, b))
    atomic_write_bytes(tmp_path / "a.png", b"x")
    assert events == ["file", "replace", "folder"]

    def refuse_folders(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "Invalid argument")  # a file system without it
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", refuse_folders)
    atomic_write_bytes(tmp_path / "a.png", b"new")
    assert (tmp_path / "a.png").read_bytes() == b"new"
    assert _leftovers(tmp_path) == ["a.png"]
    events.clear()
    monkeypatch.setattr(os, "fsync", fsync)
    atomic_write_bytes(tmp_path / "b.png", b"y", durable=False)
    assert events == ["replace"]  # a bulk write syncs nothing


def test_temp_name_fits_255_utf8_bytes_on_posix(tmp_path):
    """ext4 and APFS cap a name at 255 UTF-8 bytes, not characters: 80 CJK characters plus
    ".png" is 244 bytes, its temp name 14 characters more (O21)."""
    target = tmp_path / ("\u6839" * 80 + ".png")
    name = os.path.basename(io_utils._temp_path(str(target), "linux"))
    assert len(name.encode("utf-8")) <= 255
    assert io_utils._is_temp_name(name)
    assert name.startswith(".\u6839")  # the name is cut from its end, not emptied


def test_temp_name_keeps_all_characters_on_windows(tmp_path):
    """NTFS counts UTF-16 units, so the whole 84-character name stays."""
    target = tmp_path / ("\u6839" * 80 + ".png")
    name = os.path.basename(io_utils._temp_path(str(target), "win32"))
    assert name.startswith("." + "\u6839" * 80 + ".png.")


def test_a_cjk_name_near_the_byte_limit_is_written_atomically(tmp_path):
    target = tmp_path / ("\u6839" * 80 + ".png")
    atomic_write_bytes(target, b"data")
    assert target.read_bytes() == b"data"
    assert _leftovers(tmp_path) == [target.name]


def test_temp_name_of_a_non_utf8_file_name_fits_255_bytes_on_posix(tmp_path):
    """os.listdir returns an undecodable name with surrogate escapes; counting its bytes
    must not raise, or no atomic write to such a file would work."""
    target = tmp_path / ("a\udcff" * 120 + ".png")
    name = os.path.basename(io_utils._temp_path(str(target), "linux"))
    assert len(os.fsencode(name)) <= 255
    assert io_utils._is_temp_name(name)


def test_a_surrogate_escaped_name_is_written_atomically(tmp_path):
    target = tmp_path / "a\udcff.png"
    try:
        atomic_write_bytes(target, b"data")
    except (OSError, UnicodeError):
        pytest.skip("this file system cannot hold a non-UTF-8 name")
    assert target.read_bytes() == b"data"
    assert _leftovers(tmp_path) == [target.name]

"""X1: the atomic writer's temp file never makes a write fail that the target allows.

``app.io_utils.atomic_open`` writes ``.<target name>.<8 hex>.tmp`` next to the target
and swaps it in: 14 characters longer than the target. A target path of 246-259
characters is fine on Windows without long-path support (MAX_PATH is 260 with the
NUL), but its temp path was not, so that mask / skeleton / CSV / GT save failed
("No such file or directory"). A target name of 242-255 characters failed the same
way on every system (a file name is at most 255 characters): "[Errno 22] Invalid
argument". The target name inside the temp name is now cut short when needed, and
the stale-temp sweep still recognises such a temp file.
"""

from __future__ import annotations

import os
import time

import pytest

from app import io_utils

_MAX_PATH = 259  # Windows MAX_PATH less the terminating NUL
_NAME = "24EFCAM1_T001_L011_2024.07.18_111618_001_SMM_fake.png"


def _padded_folder(base: str, length: int) -> str:
    """A folder path (not created) whose absolute form is exactly ``length`` characters."""
    base = os.path.abspath(base)
    need = length - len(base) - 1
    assert need > 0
    parts, left = [], need
    while left > 0:
        chunk = min(left, 40)
        if left - chunk == 1:  # never leave room for a lone separator
            chunk -= 1
        parts.append("d" * chunk)
        left -= chunk + 1
    folder = os.path.join(base, *parts)
    assert len(folder) == length, (len(folder), length)
    return folder


def test_a_file_name_near_the_255_character_limit_is_written_atomically(tmp_path):
    target = tmp_path / ("a" * 246 + ".png")  # 250 characters: a valid file name
    with open(target, "wb") as fh:  # a plain write works...
        fh.write(b"plain")

    io_utils.atomic_write_bytes(target, b"atomic")  # ...and so must the atomic one

    assert target.read_bytes() == b"atomic"
    assert os.listdir(tmp_path) == [target.name]  # no temp file left behind


@pytest.mark.parametrize("length", [200, 240, 245, 246, 250, 255, 259])
def test_the_temp_path_fits_wherever_the_target_path_fits(tmp_path, length):
    folder = _padded_folder(str(tmp_path), length - len(_NAME) - 1)
    target = os.path.join(folder, _NAME)
    assert len(target) == length

    temp = io_utils._temp_path(target)

    assert len(os.path.abspath(temp)) <= _MAX_PATH
    assert os.path.dirname(temp) == folder  # a sibling: os.replace stays on one volume
    name = os.path.basename(temp)
    assert name.startswith(".") and name.endswith(".tmp")  # hidden from every scanner
    assert io_utils._is_temp_name(name)  # the stale-temp sweep still finds it


def test_a_short_path_keeps_the_whole_target_name_in_the_temp_name(tmp_path):
    target = os.path.join(str(tmp_path), _NAME)

    name = os.path.basename(io_utils._temp_path(target))

    head, token, suffix = name.rsplit(".", 2)
    assert head == "." + _NAME and suffix == "tmp" and len(token) == 8


def test_a_path_longer_than_max_path_keeps_the_whole_target_name(tmp_path):
    # Such a target exists only where long paths are enabled: nothing to shorten for.
    folder = _padded_folder(str(tmp_path), 300)
    name = os.path.basename(io_utils._temp_path(os.path.join(folder, _NAME)))
    assert name.startswith("." + _NAME + ".")


def test_a_stale_shortened_temp_file_is_swept(tmp_path):
    target = tmp_path / ("b" * 246 + ".png")
    temp = io_utils._temp_path(str(target))
    with open(temp, "wb") as fh:
        fh.write(b"left by a killed save")
    old = time.time() - 2 * 3600
    os.utime(temp, (old, old))

    removed = io_utils.sweep_stale_temp_files(tmp_path)

    assert removed == [temp] and not os.path.exists(temp)


def test_a_near_max_path_target_is_written_atomically(tmp_path):
    folder = _padded_folder(str(tmp_path), _MAX_PATH - len(_NAME) - 1)
    try:
        os.makedirs(folder)
    except OSError as exc:  # pragma: no cover - the folder itself is within MAX_PATH
        pytest.skip(f"cannot create the test folder: {exc}")
    target = os.path.join(folder, _NAME)
    assert len(target) == _MAX_PATH

    io_utils.atomic_write_bytes(target, b"png bytes")

    with open(target, "rb") as fh:
        assert fh.read() == b"png bytes"
    assert os.listdir(folder) == [_NAME]

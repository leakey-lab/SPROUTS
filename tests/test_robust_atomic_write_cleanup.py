"""R7: a failed atomic write always removes its temp file and raises its own error.

``app.io_utils.atomic_open`` writes to ``.<name>.<hex>.tmp`` and, when anything fails,
closes that file, deletes it and re-raises. On a full disk the close itself fails
(it flushes the buffered rest of the data, ENOSPC again): that exception escaped from
the cleanup, so the temp file (a partial CSV or PNG) stayed in the folder, taking
space on the full disk until the next stale-file sweep, and it replaced the error
that stopped the write. The close in the cleanup can no longer interrupt it.
"""

from __future__ import annotations

import errno
import os

import pytest

from app import io_utils
from app.io_utils import atomic_open, atomic_write_bytes


class _CloseFails:
    """A file whose first ``close()`` closes it, then raises like a flush on a full disk."""

    def __init__(self, fh):
        self._fh = fh
        self._failed = False

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def close(self):
        self._fh.close()
        if not self._failed:
            self._failed = True
            raise OSError(errno.ENOSPC, "No space left on device (simulated)")


@pytest.fixture
def close_fails(monkeypatch):
    real_open = open
    monkeypatch.setattr(
        io_utils, "open", lambda *a, **k: _CloseFails(real_open(*a, **k)), raising=False
    )


def test_an_error_in_the_write_is_raised_and_the_temp_file_removed(tmp_path, close_fails):
    target = tmp_path / "root_lengths.csv"
    target.write_bytes(b"previous complete file")

    with pytest.raises(ValueError, match="bad row"), atomic_open(target, "w") as fh:
        fh.write("Image,Length (mm)\n")
        raise ValueError("bad row")

    assert os.listdir(tmp_path) == ["root_lengths.csv"]  # no temp file left behind
    assert target.read_bytes() == b"previous complete file"


def test_a_failing_flush_leaves_no_temp_file(tmp_path, monkeypatch, close_fails):
    def flush_fails(fh):
        raise OSError(errno.ENOSPC, "No space left on device (flush, simulated)")

    monkeypatch.setattr(_CloseFails, "flush", flush_fails, raising=False)

    with pytest.raises(OSError, match="flush, simulated"):
        atomic_write_bytes(tmp_path / "mask.png", b"\x89PNG" + b"\0" * 64)

    assert os.listdir(tmp_path) == []

"""R6: the pre-Generate mask-origin scan reads PNG headers faster, with the same answers.

Before a mask run, ``scan_existing_masks`` reads the 26-byte PNG header of every mask
(hand-corrected RGBA vs model greyscale) on the GUI thread: about 1.6 s for 46k masks
(warm cache). ``open(path, "rb")`` built a buffered reader that fetched 8 KB per file;
``os.open`` + ``os.read`` fetches the 26 bytes and costs about 60 % of it (1.0 s for
46k). The reference is the pre-R6 reader, verbatim, on every kind of file it can meet.
While the scan runs, the handler shows a wait cursor.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from app.inference import mask_origin
from app.inference.mask_origin import png_colour_type

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _pre_r6_png_colour_type(path):
    """``mask_origin.png_colour_type`` before R6, verbatim."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(26)
    except OSError:
        return None
    if len(head) <= 25 or not head.startswith(_PNG_SIGNATURE):
        return None
    if head[12:16] != b"IHDR":
        return None
    return head[25]


def _image(path, mode):
    if mode == "I;16":
        Image.fromarray(np.arange(48, dtype=np.uint16).reshape(6, 8)).save(path)
    else:
        Image.new(mode, (8, 6)).save(path)
    return path


FILES = {
    "grey.png": lambda p: _image(p, "L"),
    "grey_alpha.png": lambda p: _image(p, "LA"),
    "rgb.png": lambda p: _image(p, "RGB"),
    "rgba.png": lambda p: _image(p, "RGBA"),
    "palette.png": lambda p: _image(p, "P"),
    "grey16.png": lambda p: _image(p, "I;16"),
    "photo.jpg": lambda p: _image(p, "RGB"),
    "empty.png": lambda p: open(p, "wb").close(),
    "signature_only.png": lambda p: open(p, "wb").write(_PNG_SIGNATURE),
    "25_bytes.png": lambda p: open(p, "wb").write(_PNG_SIGNATURE + b"\0\0\0\rIHDR" + b"\0" * 9),
    "26_bytes.png": lambda p: open(p, "wb").write(_PNG_SIGNATURE + b"\0\0\0\rIHDR" + b"\0" * 10),
    "no_ihdr.png": lambda p: open(p, "wb").write(_PNG_SIGNATURE + b"\0\0\0\rIDAT" + b"\6" * 20),
    "text.png": lambda p: open(p, "wb").write(b"not a png at all, just text padding....."),
    "Données_根.png": lambda p: _image(p, "RGBA"),
}


@pytest.mark.parametrize("name", sorted(FILES))
def test_png_colour_type_is_unchanged(tmp_path, name):
    path = str(tmp_path / name)
    FILES[name](path)
    assert png_colour_type(path) == _pre_r6_png_colour_type(path)


def test_missing_files_and_folders_have_no_colour_type(tmp_path):
    assert png_colour_type(str(tmp_path / "missing.png")) is None
    folder = tmp_path / "a_folder.png"
    folder.mkdir()
    assert png_colour_type(str(folder)) is None


def test_a_short_read_is_completed(tmp_path, monkeypatch):
    # A network share may return fewer bytes than asked: the header is read to the end.
    path = _image(str(tmp_path / "rgba.png"), "RGBA")
    real_read = os.read
    monkeypatch.setattr(mask_origin.os, "read", lambda fd, n: real_read(fd, min(n, 7)))
    assert png_colour_type(path) == 6


def test_the_file_is_closed_even_when_the_read_fails(tmp_path, monkeypatch):
    path = _image(str(tmp_path / "rgba.png"), "RGBA")
    closed = []
    real_close = os.close

    def failing_read(fd, n):
        raise OSError("device not ready (simulated)")

    def spy_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(mask_origin.os, "read", failing_read)
    monkeypatch.setattr(mask_origin.os, "close", spy_close)
    assert png_colour_type(path) is None
    assert len(closed) == 1

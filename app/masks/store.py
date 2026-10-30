"""
Qt-free reading of mask files, and writing of the masks the Correct Mask editor saves.

The prediction of image ``<project>/<stem>.<ext>`` is ``<project>/mask/<stem>.png``
(legacy ``.jpg`` / ``.jpeg`` / upper-case variants are still found); :class:`MaskStore`
reads it and never writes or removes it. A mask the editor saves is GT, written by
:func:`write_mask_file` to ``gt/mask/<stem>.png``. On disk it is a white-on-transparent
RGBA PNG: RGBA 255 where the mask is set, 0 elsewhere. Every reader (grayscale +
threshold, QPixmap) sees exactly the pixels the editor wrote before; the files are only
far smaller, because they are now zlib-compressed (the old ``QImage.save(..., 100)`` meant
compression level 0: 1.2 MiB per 640x480 mask instead of a few KiB).

Writes are atomic (temp file + ``os.replace``, see :mod:`app.io_utils`): a
failed save never leaves a truncated mask behind and keeps the previous file.

Reads (:func:`read_mask_file`, of a prediction and of a GT mask alike) are a grayscale
decode thresholded at :data:`MASK_THRESHOLD`, of the bytes read once. A plain 8-bit grey
PNG (a model mask) and a plain 8-bit RGBA PNG whose R == G == B (an editor mask) take an
exact faster path: see :func:`_decode_plain_png`.
"""

from __future__ import annotations

import os
import zlib
from dataclasses import dataclass

import cv2
import numpy as np

from app.io_utils import atomic_write_bytes, imdecode_bytes
from app.project_layout import ProjectLayout

# Grayscale value above which a stored mask pixel counts as foreground: the one threshold
# of Correct, Measure and the GT readers (owner decision O10).
MASK_THRESHOLD = 127


class MaskFileError(OSError):
    """A saved mask exists but cannot be decoded."""


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Chunks that never change the decoded pixels. Any other chunk (tRNS, gAMA, sRGB, iCCP,
# cHRM, sBIT, eXIf - OpenCV rotates by its orientation - ...) sends the file to the
# reference decode.
_PLAIN_CHUNKS = frozenset({b"IHDR", b"IDAT", b"IEND", b"pHYs", b"tEXt", b"zTXt", b"iTXt", b"tIME"})
# PNG colour type -> (Pillow mode, bytes per pixel) of the files the fast path reads.
_FAST_KINDS = {0: ("L", 1), 6: ("RGBA", 4)}
# Bounds of the fast path, far inside what libpng, OpenCV and Pillow accept. OpenCV
# refuses some chunks over 8,000,000 bytes, and libpng limits chunk lengths too.
_MAX_SIDE = 1 << 16
_MAX_PIXELS = 1 << 26
_MAX_CHUNK = 1 << 22
# The rows decoded past the image hold at least this many random pixels (see below).
_SPARE_PIXELS = 16
# What the last image row holds before the decode: a ramp for grey, and for RGBA a
# pixel whose R != G, which no row the fast path keeps can equal.
_LAST_ROW_GREY = bytes(range(1, 256)) + b"\x00"
_LAST_ROW_RGBA = b"\x01\x02\x03\x04"


def _plain_png(data: bytes) -> tuple[int, int, int, bytes] | None:
    """``(width, height, colour type, zlib stream)`` of a PNG the fast path may read.

    The file must be laid out the way libpng (the reference decode) reads it without an
    error: one IHDR of 13 bytes, first (8-bit grey or RGBA, compression 0, filter 0, not
    interlaced, sides of 1 to ``_MAX_SIDE``), only :data:`_PLAIN_CHUNKS` of at most
    ``_MAX_CHUNK`` bytes, every CRC right, then IEND. The zlib stream is the data of the
    first run of IDAT chunks: libpng takes the image from that run alone. Else None.
    """
    if len(data) < 45 or data[:16] != _PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    depth, colour, compression, filtering, interlace = data[24:29]
    if (depth, compression, filtering, interlace) != (8, 0, 0, 0) or colour not in _FAST_KINDS:
        return None
    if not (0 < width <= _MAX_SIDE and 0 < height <= _MAX_SIDE and width * height <= _MAX_PIXELS):
        return None
    view = memoryview(data)
    run: list[memoryview] = []
    in_run = None  # None before the first IDAT, True inside the first run, False after it
    pos = 8
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if kind not in _PLAIN_CHUNKS or length > _MAX_CHUNK or end + 4 > len(data):
            return None
        if kind == b"IHDR" and pos != 8:
            return None
        if zlib.crc32(view[pos + 4 : end]) != int.from_bytes(data[end : end + 4], "big"):
            return None
        if kind == b"IDAT":
            if in_run is not False:
                run.append(view[pos + 8 : end])
                in_run = True
        elif in_run:
            in_run = False
        if kind == b"IEND":
            return (width, height, colour, b"".join(run)) if run else None
        pos = end + 4
    return None


def _decode_plain_png(data: bytes) -> np.ndarray | None:
    """The grey plane of a plain 8-bit grey PNG, or of a plain 8-bit RGBA PNG whose
    R == G == B everywhere, decoded by Pillow; None for anything else.

    Exact: for such a file the reference decode (``IMREAD_GRAYSCALE``) gives the same
    plane. A grey PNG is not transformed at all. For RGBA, libpng strips the alpha channel
    (it does not composite it) and converts RGB to grey, which returns the shared value
    itself when R == G == B, whatever the alpha. Pillow inflates with zlib-ng, about twice
    as fast as OpenCV's zlib, so the fast path is faster although it checks every channel.

    Pillow's decoder stops as soon as the image is full, or when the zlib stream ends,
    without telling which, and does not mind a stream that ends early. libpng refuses a
    stream that ends before the image or that does not end in the IDAT run. So the image
    is decoded with spare rows of random bytes after it, its last row holding a pattern:
    the file is kept only when the stream ended (zlib checked its checksum) right after
    the last row, which then changed while the spare rows did not.
    Anything else (another PNG kind or chunk, a colour pixel, a decode error) returns
    None and the reference decode decides, errors included.
    """
    png = _plain_png(data)
    if png is None:
        return None
    width, height, colour, stream = png
    mode, size = _FAST_KINDS[colour]
    row = width * size
    # libpng inflates with the window the zlib header declares, Pillow always with 32 KiB.
    # They agree on every stream when the window covers the whole image or is 32 KiB.
    if stream and 1 << ((stream[0] >> 4) + 8) < min(1 << 15, height * (row + 1)):
        return None
    # Random, so that no file can decode into the spare rows exactly what they hold.
    spare_rows = -(-_SPARE_PIXELS // width)
    noise = np.frombuffer(os.urandom(2 * spare_rows * width), np.uint8).reshape(-1, 2)
    if mode == "L":
        last = (_LAST_ROW_GREY * (width // 256 + 1))[:width]
        spare = noise[:, 0].tobytes()
    else:
        last = _LAST_ROW_RGBA * width
        spare = noise[:, [0, 0, 0, 1]].tobytes()  # random grey with a random alpha
    try:
        from PIL import Image

        # Not filled: rows are written in order, so once the last row changed, every row
        # above it holds decoded pixels.
        img = Image.new(mode, (width, height + spare_rows), None)
        img.paste(Image.frombytes(mode, (width, 1 + spare_rows), last + spare), (0, height - 1))
        decoder = Image._getdecoder(mode, "zip", mode)
        decoder.setimage(img.im, (0, 0, width, height + spare_rows))
        try:
            status, error = decoder.decode(stream)
        finally:
            decoder.cleanup()
        if status != -1 or error != 0:
            return None  # the stream is broken, or it needs more data than the run holds
        tail = img.crop((0, height - 1, width, height + spare_rows)).tobytes()
        if tail[:row] == last or tail[row:] != spare:
            return None  # the stream ended before the last row, or ran past it
        if mode == "L":
            plane = img.tobytes()
        else:
            # The raw packer copies one band straight out of the RGBA pixels, without
            # the one-band image that getchannel() makes first.
            plane = img.tobytes("raw", "R")
            if plane != img.tobytes("raw", "G") or plane != img.tobytes("raw", "B"):
                return None
        return np.frombuffer(plane, np.uint8, count=width * height).reshape(height, width)
    except Exception:  # noqa: BLE001 - Pillow refused it: the reference decode decides
        return None


def _decode_reference(data: bytes) -> np.ndarray | None:
    """The reference decode of a file's bytes: what ``imread_unicode(path,
    cv2.IMREAD_GRAYSCALE)`` returns for the file, without reading it a second time."""
    return imdecode_bytes(data, cv2.IMREAD_GRAYSCALE)


def _read_bytes(path: str) -> bytes | None:
    """The file's bytes, read as :func:`app.io_utils.imread_unicode` reads a file; None
    when it cannot be read (gone, locked), where that returns None too."""
    try:
        return np.fromfile(path, np.uint8).tobytes()
    except OSError, ValueError:
        return None


def read_mask_file(path: str) -> np.ndarray | None:
    """The mask file at ``path`` as an ``(h, w)`` bool array: its grey decode above
    :data:`MASK_THRESHOLD`, of the bytes read once (exact fast path first, else the
    reference decode). ``None`` when it cannot be read or decoded."""
    data = _read_bytes(path)
    if data is None:
        return None
    gray = _decode_plain_png(data)
    if gray is None:
        gray = _decode_reference(data)
    return None if gray is None else gray > MASK_THRESHOLD


def encode_mask_png(on: np.ndarray) -> bytes:
    """``(h, w)`` bool mask -> white-on-transparent RGBA PNG bytes."""
    rgba = np.zeros((*on.shape, 4), np.uint8)
    rgba[on] = 255
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise ValueError("could not encode the mask as PNG")
    return buf.tobytes()


def write_mask_file(path: str, on: np.ndarray, *, durable: bool = True) -> None:
    """Atomically write ``on`` as a white-on-transparent PNG at ``path`` (folder created).

    Raises ``OSError`` (e.g. :class:`app.io_utils.FileLockedError`) when it cannot be
    written; an existing file at ``path`` is then left unchanged.
    """
    data = encode_mask_png(np.asarray(on, bool))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    atomic_write_bytes(path, data, durable=durable)


@dataclass(frozen=True)
class MaskStore:
    """Reads the predicted masks (``mask/``) of one project folder; never writes them."""

    layout: ProjectLayout

    @classmethod
    def for_image(cls, image_path: str | os.PathLike[str]) -> MaskStore:
        """Store of the folder that contains ``image_path``."""
        return cls(ProjectLayout.for_image(image_path))

    @property
    def directory(self) -> str:
        return self.layout.mask_dir

    def path_for(self, stem: str) -> str:
        """``mask/<stem>.png``: the PNG name of ``stem``'s prediction."""
        return self.layout.mask_path(stem)

    def find(self, stem: str) -> str | None:
        """The existing mask file of ``stem`` (``.png`` first), else ``None``."""
        return self.layout.find_mask(stem)

    def read(self, stem: str, *, path: str | None = None) -> np.ndarray | None:
        """The saved mask as an ``(h, w)`` bool array; ``None`` when there is none.

        ``path``: the file :meth:`find` already found, read without a second lookup (None
        when it is gone since). Raises :class:`MaskFileError` when a file exists but cannot
        be decoded.
        """
        found = path or self.find(stem)
        if found is None:
            return None
        on = read_mask_file(found)  # None also when the file is gone or locked
        if on is None:
            if path is not None and not os.path.isfile(path):
                return None  # gone since the lookup, as a second lookup would have said
            raise MaskFileError(f"cannot decode {found}")
        return on

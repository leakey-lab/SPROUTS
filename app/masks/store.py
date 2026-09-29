"""
Qt-free storage of the masks the Correct Mask editor saves.

A mask of image ``<project>/<stem>.<ext>`` lives at ``<project>/mask/<stem>.png``
(legacy ``.jpg`` / ``.jpeg`` / upper-case variants are still found and
removed). On disk it is a white-on-transparent RGBA PNG: RGBA 255 where the
mask is set, 0 elsewhere. Every reader (grayscale + threshold, QPixmap) sees
exactly the pixels the editor wrote before; the files are only far smaller,
because they are now zlib-compressed (the old ``QImage.save(..., 100)`` meant
compression level 0: 1.2 MiB per 640x480 mask instead of a few KiB).

Writes are atomic (temp file + ``os.replace``, see :mod:`app.io_utils`): a
failed save never leaves a truncated mask behind and keeps the previous file.

Reads are a grayscale decode thresholded at :data:`MASK_THRESHOLD`. A file shaped like
the ones :func:`write_mask_file` makes (a plain 8-bit RGBA PNG whose R == G == B) takes
an exact faster path: see :func:`_decode_grey_rgba`.
"""

from __future__ import annotations

import io
import os
import zlib
from dataclasses import dataclass

import cv2
import numpy as np

from app.io_utils import atomic_write_bytes, imread_unicode
from app.project_layout import ProjectLayout

# Grayscale value above which a stored mask pixel counts as foreground.
MASK_THRESHOLD = 200


class MaskFileError(OSError):
    """A saved mask exists but cannot be decoded."""


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Chunks that never change the decoded pixels. Any other chunk (gAMA, sRGB, iCCP, sBIT,
# eXIf - OpenCV rotates by its orientation - ...) sends the file to the reference decode.
_PLAIN_CHUNKS = frozenset({b"IHDR", b"IDAT", b"IEND", b"pHYs", b"tEXt", b"zTXt", b"iTXt", b"tIME"})


def _is_plain_rgba8_png(data: bytes) -> bool:
    """True for a PNG like the ones :func:`write_mask_file` writes: 8-bit RGBA, not
    interlaced, only :data:`_PLAIN_CHUNKS`, every chunk CRC right, IEND present."""
    if data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR" or len(data) < 33:
        return False
    if (data[24], data[25], data[28]) != (8, 6, 0):  # bit depth, colour type, interlace
        return False
    pos = 8
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        end = pos + 8 + length
        kind = data[pos + 4 : pos + 8]
        if kind not in _PLAIN_CHUNKS or end + 4 > len(data):
            return False
        if zlib.crc32(data[pos + 4 : end]) != int.from_bytes(data[end : end + 4], "big"):
            return False
        if kind == b"IEND":
            return True
        pos = end + 4
    return False


def _decode_grey_rgba(data: bytes) -> np.ndarray | None:
    """The grey plane of a plain 8-bit RGBA PNG whose R == G == B everywhere, else None.

    Exact: for such a file the reference decode (``IMREAD_GRAYSCALE``: libpng drops alpha
    and converts RGB to grey, which returns the shared value itself when R == G == B)
    gives the same plane. Pillow inflates with zlib-ng and splits the channels in C, about
    40 % faster than the reference decode of an editor-saved mask (640 and 2048 px).
    Anything else (another PNG kind, another chunk, a colour pixel, a decode error)
    returns None and the reference decode decides, errors included.
    """
    if not _is_plain_rgba8_png(data):
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            max_pixels = Image.MAX_IMAGE_PIXELS
            if img.mode != "RGBA" or (max_pixels and img.width * img.height > max_pixels):
                return None
            img.load()
            red = img.getchannel(0).tobytes()
            if red != img.getchannel(1).tobytes() or red != img.getchannel(2).tobytes():
                return None
            return np.frombuffer(red, np.uint8).reshape(img.height, img.width)
    except Exception:  # noqa: BLE001 - Pillow refused it: the reference decode decides
        return None


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


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
    """Reads, writes and removes the saved masks of one project folder."""

    layout: ProjectLayout

    @classmethod
    def for_image(cls, image_path: str | os.PathLike[str]) -> MaskStore:
        """Store of the folder that contains ``image_path``."""
        return cls(ProjectLayout.for_image(image_path))

    @property
    def directory(self) -> str:
        return self.layout.mask_dir

    def path_for(self, stem: str) -> str:
        """Where :meth:`write` saves the mask of ``stem`` (``mask/<stem>.png``)."""
        return self.layout.mask_path(stem)

    def find(self, stem: str) -> str | None:
        """The existing mask file of ``stem`` (``.png`` first), else ``None``."""
        return self.layout.find_mask(stem)

    def read(self, stem: str) -> np.ndarray | None:
        """The saved mask as an ``(h, w)`` bool array; ``None`` when there is none.

        Raises :class:`MaskFileError` when a file exists but cannot be decoded.
        """
        path = self.find(stem)
        if path is None:
            return None
        data = _read_bytes(path)
        gray = None if data is None else _decode_grey_rgba(data)
        if gray is None:  # the reference decode
            gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise MaskFileError(f"cannot decode {path}")
        return gray > MASK_THRESHOLD

    def write(self, stem: str, on: np.ndarray, *, durable: bool = True) -> str:
        """Save ``on`` atomically as ``mask/<stem>.png``; returns the path.

        Raises ``OSError`` (including :class:`app.io_utils.FileLockedError`) when
        the folder or file cannot be written; the previous file is then unchanged.
        """
        path = self.path_for(stem)
        write_mask_file(path, on, durable=durable)
        return path

    def remove(self, stem: str) -> bool:
        """Delete every saved variant of ``stem``'s mask; True if one was removed."""
        removed = False
        for path in self.layout.mask_candidates(stem):
            if os.path.isfile(path):
                os.remove(path)
                removed = True
        return removed

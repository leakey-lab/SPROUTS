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
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np

from app.io_utils import atomic_write_bytes, imread_unicode
from app.project_layout import ProjectLayout

# Grayscale value above which a stored mask pixel counts as foreground.
MASK_THRESHOLD = 200


class MaskFileError(OSError):
    """A saved mask exists but cannot be decoded."""


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

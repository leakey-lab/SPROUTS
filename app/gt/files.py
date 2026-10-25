"""The GT files of one image: where they are, how to remove them, how big they are.

Qt-free. A human-saved edit is GT under ``gt/``: ``gt/mask/<stem>.png``,
``gt/skeletons/<stem>.png`` and the class labels ``gt/<stem>.png``. Predictions in
``mask/`` and ``skeletons/`` are never touched here.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from app.project_layout import ProjectLayout

__all__ = [
    "SOURCE_GT",
    "SOURCE_NONE",
    "SOURCE_PREDICTION",
    "GtFiles",
    "GtRemoveError",
    "gt_files",
    "image_size",
    "png_size",
    "remove_gt",
]

#: Where the mask or skeleton shown for an image comes from.
SOURCE_GT = "gt"
SOURCE_PREDICTION = "prediction"
SOURCE_NONE = "none"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_END = 33  # signature (8) + chunk length, type, 13 bytes of data, CRC (25)


@dataclass(frozen=True)
class GtFiles:
    """The existing GT files of one image (None: that kind has none)."""

    mask: str | None
    skeleton: str | None
    labels: str | None


def gt_files(layout: ProjectLayout, stem: str) -> GtFiles:
    """The GT files ``stem`` has (``stem`` compared without case, O7)."""
    return GtFiles(
        mask=layout.find_gt_mask(stem),
        skeleton=layout.find_gt_skeleton(stem),
        labels=layout.find_gt_labels(stem),
    )


class GtRemoveError(OSError):
    """:func:`remove_gt` could not delete every file: ``removed`` went, ``failed`` maps each
    path that stayed to its ``OSError`` (a locked file on Windows, a read-only folder)."""

    def __init__(self, removed: tuple[str, ...], failed: dict[str, OSError]):
        self.removed = removed
        self.failed = failed
        names = ", ".join(f"{path} ({exc})" for path, exc in failed.items())
        super().__init__(f"Could not remove {names}")


def remove_gt(layout: ProjectLayout, stem: str) -> tuple[str, ...]:
    """Delete the GT files of ``stem`` and return the paths removed (0 to 3).

    Only that image's mask, skeleton and labels: never ``classes.json``, another image's
    GT, the ``gt/`` folders or anything in ``mask/`` and ``skeletons/``. A file that is
    already gone is skipped. Every file is tried; when any could not be deleted, one
    :class:`GtRemoveError` is raised after the rest, carrying what went and what stayed.
    """
    found = gt_files(layout, stem)
    removed: list[str] = []
    failed: dict[str, OSError] = {}
    for path in (found.mask, found.skeleton, found.labels):
        if path is None:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed[path] = exc
            continue
        removed.append(path)
    if failed:
        raise GtRemoveError(tuple(removed), failed)
    return tuple(removed)


def png_size(path: str) -> tuple[int, int] | None:
    """``(width, height)`` of the PNG at ``path`` from its header, else None.

    Reads the first 33 bytes (the signature and the IHDR chunk), never the pixels. None
    for a missing, unreadable or non-PNG file.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(_IHDR_END)
    except OSError:
        return None
    if len(head) < _IHDR_END or not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def image_size(path: str) -> tuple[int, int] | None:
    """``(width, height)`` of a PNG or JPEG from its header, else None (nothing decoded)."""
    size = png_size(path)
    if size is not None:
        return size
    from PIL import Image  # lazy: the header read above covers every GT file

    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None

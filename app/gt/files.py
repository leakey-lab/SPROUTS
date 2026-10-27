"""The GT files of one image: where they are, how to remove them, how big they are.

Qt-free. A human-saved edit is GT under ``gt/``: ``gt/mask/<stem>.png``,
``gt/skeletons/<stem>.png`` and the class labels ``gt/<stem>.png``. Predictions in
``mask/`` and ``skeletons/`` are never touched here. :class:`LabelsIndex` tells which of many
images have class labels from one listing of each ``gt/``.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from app.config import GT_DIRNAME
from app.project_layout import ProjectLayout

__all__ = [
    "SOURCE_GT",
    "SOURCE_NONE",
    "SOURCE_PREDICTION",
    "GtFiles",
    "GtRemoveError",
    "LabelsIndex",
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


def gt_files(layout: ProjectLayout, image: str) -> GtFiles:
    """The GT files of ``image``, its path or file name (its stem compared without case, O7).

    Pass the path, not a stem: the image extension is stripped here, so the stem ``X.png`` of
    ``X.png.jpg`` would become ``X``, another image's (final review D1). A bare stem works
    only when it does not end in an image extension.
    """
    return GtFiles(
        mask=layout.find_gt_mask(image),
        skeleton=layout.find_gt_skeleton(image),
        labels=layout.find_gt_labels(image),
    )


class GtRemoveError(OSError):
    """:func:`remove_gt` could not delete every file: ``removed`` went, ``failed`` maps each
    path that stayed to its ``OSError`` (a locked file on Windows, a read-only folder)."""

    def __init__(self, removed: tuple[str, ...], failed: dict[str, OSError]):
        self.removed = removed
        self.failed = failed
        names = ", ".join(f"{path} ({exc})" for path, exc in failed.items())
        super().__init__(f"Could not remove {names}")


def remove_gt(layout: ProjectLayout, image: str) -> tuple[str, ...]:
    """Delete the GT files of ``image`` (as :func:`gt_files`: its path or file name) and
    return the paths removed (0 to 3).

    Only that image's mask, skeleton and labels: never ``classes.json``, another image's
    GT, the ``gt/`` folders or anything in ``mask/`` and ``skeletons/``. A file that is
    already gone is skipped. Every file is tried; when any could not be deleted, one
    :class:`GtRemoveError` is raised after the rest, carrying what went and what stayed.
    """
    found = gt_files(layout, image)
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


def _list_gt(folder: str) -> tuple[set[str], bool]:
    """The names in ``folder``'s ``gt/`` in lower case, and whether a name missing from them
    is surely no file there: yes when ``gt/`` is absent or every name is ASCII; no when it
    cannot be listed (a file check may still find a file)."""
    try:
        names = os.listdir(os.path.join(folder, GT_DIRNAME))
    except (FileNotFoundError, NotADirectoryError):
        return set(), True
    except OSError:
        return set(), False
    return {name.lower() for name in names}, "".join(names).isascii()  # every name ASCII


class LabelsIndex:
    """Which images have class labels ``gt/<stem>.png``, the name compared without case (O7).

    One listing of each image folder's ``gt/``, then per image only string slicing: four
    ``os.path`` calls per image took 0.65 s at 46k images for the export dialog's count (on
    the GUI thread), and the export checked the file of every image twice.
    """

    _SEP, _ALT = os.sep, os.altsep or os.sep

    def __init__(self) -> None:
        # The folder as spelled, with its separator -> the names of its gt/ and whether they
        # are sure (_list_gt).
        self._names: dict[str, set[str]] = {}
        self._sure: dict[str, bool] = {}

    def _list(self, folder_key: str, path: str) -> set[str]:
        """List the ``gt/`` of image ``path``'s folder once; its names."""
        names, sure = _list_gt(os.path.dirname(path))
        self._names[folder_key], self._sure[folder_key] = names, sure
        return names

    def count(self, paths: Iterable[str]) -> int:
        """How many of the image ``paths`` have a ``gt/<stem>.png`` in any case (none in a
        ``gt/`` that cannot be listed)."""
        sep, alt, listed = self._SEP, self._ALT, self._names
        traced = 0
        for path in paths:  # lacks_labels' lookup inlined: the dialog counts on the GUI thread
            cut = max(path.rfind(sep), path.rfind(alt))
            if cut < 0:
                folder_key, name = os.path.split(path)
            else:
                folder_key, name = path[: cut + 1], path[cut + 1 :]
            names = listed.get(folder_key)
            if names is None:
                names = self._list(folder_key, path)
            if names and (os.path.splitext(name)[0] + ".png").lower() in names:
                traced += 1
        return traced

    def lacks_labels(self, path: str) -> bool:
        """True when image ``path`` surely has no ``gt/<stem>.png`` in any case: its ``gt/``
        holds no such name. False when it may have one, also where the names cannot tell: a
        ``gt/`` that cannot be listed, or names a file system may match unlike ``lower()``
        (outside ASCII: Unicode case folding and normalization; ``~``: Windows short names).
        """
        cut = max(path.rfind(self._SEP), path.rfind(self._ALT))
        if cut < 0:  # a bare (or drive-relative) name: the general path functions
            folder_key, name = os.path.split(path)
        else:  # the folder as spelled, with its separator; the file name after it
            folder_key, name = path[: cut + 1], path[cut + 1 :]
        names = self._names.get(folder_key)
        if names is None:
            names = self._list(folder_key, path)
        labels = os.path.splitext(name)[0] + ".png"  # gt_path's name: the stem + ".png"
        return (
            self._sure[folder_key]
            and labels.isascii()
            and "~" not in labels
            and labels.lower() not in names
        )


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

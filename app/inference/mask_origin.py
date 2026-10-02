"""Who made a mask on disk: the model or a person (RB-01 / PI-04 / FR-04 / UXS-01).

Generated masks and hand-corrected masks share one file name (``mask/<stem>.png``),
so a blind re-run of Generate would overwrite manual work. The two kinds differ in
their PNG header: the Correct Mask editor saves ARGB32 (white on transparent ->
PNG colour type 6) while the mask model writes 8-bit greyscale (colour type 0).
Reading the 26-byte header is enough to tell them apart; no pixel is decoded (a
buffered ``open`` read 8 KB per file, R6).

Opening a file costs far more than reading its header: of about 1.4 s for 46k masks
(warm cache, NTFS), the opens took 0.85 s, the reads 0.3 s and the folder listing
0.1 s. ``os.open`` and ``os.read`` release the GIL, so a large folder reads its
headers on a few threads (about 0.6 s for 46k). The scan still returns only once
every header is read.

Qt-free and torch-free, so the pre-flight scan is cheap and unit-testable.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.project_layout import ProjectLayout

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_COLOUR_TYPE_OFFSET = 25  # 8 signature + 4 length + 4 "IHDR" + 4 w + 4 h + 1 depth
_HEADER_BYTES = _IHDR_COLOUR_TYPE_OFFSET + 1
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0)  # O_BINARY: Windows only

# Masks per thread-pool task; a folder of one chunk or less is read on the calling
# thread (a pool would cost more than it saves). Six threads were the fastest for
# 46k masks; more contend for the GIL and the file system.
_SCAN_CHUNK = 1024
_SCAN_WORKERS = 6

# PNG colour types with an alpha channel: what the mask editor writes.
_MANUAL_COLOUR_TYPES = frozenset({4, 6})  # grey+alpha, RGBA
_MODEL_COLOUR_TYPES = frozenset({0})  # 8-bit greyscale ("L"): the generator's output


class MaskOrigin(enum.Enum):
    """Where a mask file came from, judged by its PNG header."""

    MANUAL = "manual"  # saved by the Correct Mask editor (alpha channel)
    MODEL = "model"  # written by the mask generator (greyscale)
    UNKNOWN = "unknown"  # anything else (JPEG, RGB PNG, unreadable)


def _read_head(path: str) -> bytes:
    """The first ``_HEADER_BYTES`` of ``path`` (fewer at end of file); raises OSError.

    A raw ``os.read``: the header without a buffered reader's 8 KB read (R6).
    """
    fd = os.open(path, _OPEN_FLAGS)
    try:
        head = b""
        while len(head) < _HEADER_BYTES:
            chunk = os.read(fd, _HEADER_BYTES - len(head))  # a share may return less
            if not chunk:
                break
            head += chunk
        return head
    finally:
        os.close(fd)


def png_colour_type(path: str) -> int | None:
    """The IHDR colour-type byte of a PNG file, or None if it is not a readable PNG."""
    try:
        head = _read_head(path)
    except OSError:
        return None
    if len(head) <= _IHDR_COLOUR_TYPE_OFFSET or not head.startswith(_PNG_SIGNATURE):
        return None
    if head[12:16] != b"IHDR":
        return None
    return head[_IHDR_COLOUR_TYPE_OFFSET]


def classify_mask(path: str) -> MaskOrigin:
    """Classify one mask file as manual, model or unknown (reads 26 bytes)."""
    colour_type = png_colour_type(path)
    if colour_type in _MANUAL_COLOUR_TYPES:
        return MaskOrigin.MANUAL
    if colour_type in _MODEL_COLOUR_TYPES:
        return MaskOrigin.MODEL
    return MaskOrigin.UNKNOWN


@dataclass(frozen=True)
class ExistingMasks:
    """Stems that already have a mask, split by origin."""

    manual: frozenset[str] = field(default_factory=frozenset)
    model: frozenset[str] = field(default_factory=frozenset)
    unknown: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> ExistingMasks:
        return cls()

    @property
    def stems(self) -> frozenset[str]:
        """Every stem with a mask on disk."""
        return self.manual | self.model | self.unknown

    @property
    def count(self) -> int:
        return len(self.manual) + len(self.model) + len(self.unknown)

    def __bool__(self) -> bool:
        return self.count > 0


def _classify_chunk(paths: list[str]) -> list[MaskOrigin]:
    return [classify_mask(path) for path in paths]


def _classify_all(paths: list[str]) -> list[MaskOrigin]:
    """``classify_mask`` of every path, in order; many paths are read on a thread pool."""
    if len(paths) <= _SCAN_CHUNK:
        return _classify_chunk(paths)
    chunks = [paths[i : i + _SCAN_CHUNK] for i in range(0, len(paths), _SCAN_CHUNK)]
    workers = min(_SCAN_WORKERS, len(chunks))
    with ThreadPoolExecutor(workers, thread_name_prefix="mask-origin") as pool:
        return [origin for part in pool.map(_classify_chunk, chunks) for origin in part]


def scan_existing_masks(layout: ProjectLayout, stems: Iterable[str] | None = None) -> ExistingMasks:
    """Scan ``layout.mask_dir`` once and classify every mask by origin.

    ``stems`` limits the result to masks of those images (orphan masks of images
    that are no longer in the folder are irrelevant to a generation run).
    """
    found = layout.scan_masks()  # {} when mask/ does not exist
    if stems is not None:
        wanted = set(stems)
        found = {stem: path for stem, path in found.items() if stem in wanted}
    groups: dict[MaskOrigin, set[str]] = {origin: set() for origin in MaskOrigin}
    for stem, origin in zip(found, _classify_all(list(found.values())), strict=True):
        groups[origin].add(stem)
    return ExistingMasks(
        manual=frozenset(groups[MaskOrigin.MANUAL]),
        model=frozenset(groups[MaskOrigin.MODEL]),
        unknown=frozenset(groups[MaskOrigin.UNKNOWN]),
    )


class OverwritePolicy(enum.Enum):
    """What a generation run does with masks that already exist."""

    KEEP_EXISTING = "keep_existing"  # generate missing masks only (the safe default)
    OVERWRITE_ALL = "overwrite_all"  # regenerate every mask, including hand-corrected ones

    def skip_stems(self, existing: ExistingMasks) -> frozenset[str]:
        """Stems the generator must not write under this policy."""
        if self is OverwritePolicy.KEEP_EXISTING:
            return existing.stems
        return frozenset()


__all__ = [
    "ExistingMasks",
    "MaskOrigin",
    "OverwritePolicy",
    "classify_mask",
    "png_colour_type",
    "scan_existing_masks",
]

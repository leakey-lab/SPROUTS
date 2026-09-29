"""
Image-name conventions: one parser for the library tree, the metric CSVs, the
dashboards and the GT export split.

Minirhizotron cameras name each capture
``<field>[camN]_T<tube>_L<position>_<YYYY.MM.DD>_<HHMMSS>_<anything>``, e.g.
``24EFCAM1_T001_L045_2024.07.18_111618_001_SMM``. Tube and position are read
only from stand-alone tokens (delimited by the start/end of the name or by a
character that is not a letter or digit), so a project prefix such as
``PLOT12_`` or ``EXPT2_`` no longer masquerades as the tube (audit FR-15). A
name without such a token gets ``None`` instead of a guess. Date and time use
the same patterns as before.

Qt-free and dependency-free; regexes are compiled once at import. Canonical
names take a single-regex fast path (~2 us per name, 46k names in < 0.1 s).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

__all__ = ["ImageName", "image_stem", "parse_image_name"]

# General parser: each part searched on its own, for any name.
_TUBE = re.compile(r"(?<![A-Za-z0-9])T(\d+)(?![A-Za-z0-9])")
_POSITION = re.compile(r"(?<![A-Za-z0-9])L(\d+)(?![A-Za-z0-9])")
_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}")
_TIME = re.compile(r"_(\d{6})(?:_|$)")
_CAMERA_SUFFIX = re.compile(r"cam\d+$", re.IGNORECASE)

# Fast path for the canonical camera name. It only matches names for which the
# general parser gives the same result: the field is alphanumeric (so it holds
# no earlier token or date) and is not itself a T/L token.
_CANONICAL = re.compile(
    r"(?![TL]\d+_)(?P<field>[A-Za-z0-9]*?)(?:(?i:cam)\d+)?"
    r"_T(?P<tube>\d+)_L(?P<position>\d+)"
    r"_(?P<date>\d{4}\.\d{2}\.\d{2})_(?P<time>\d{6})(?:_|$)"
)

# Extensions image_stem() strips. Anything else after the last dot (e.g.
# ".18_111618_001_SMM" in a bare stem ending in a dotted date) is part of the name.
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".ppm"})
_SEPARATORS = tuple({"/", os.sep, os.altsep or "/"})


@dataclass(frozen=True)
class ImageName:
    """Parsed parts of an image name; a part that is not present is ``None``.

    ``date`` is ``"YYYY.MM.DD"`` and ``time`` is ``"HH:MM:SS"``, exactly as the
    metric CSVs store them. ``field`` is the text before the first underscore
    without a trailing camera tag (``24EFCAM1`` -> ``24EF``).
    """

    stem: str
    field: str | None = None
    tube: int | None = None
    position: int | None = None
    date: str | None = None
    time: str | None = None

    @property
    def is_complete(self) -> bool:
        """True when tube, position and date are all known (a plottable capture)."""
        return self.tube is not None and self.position is not None and self.date is not None


def image_stem(path_or_name: str | os.PathLike[str]) -> str:
    """File name without its image extension (``D:/x/a.jpg`` -> ``a``).

    Only image extensions are stripped, so a bare stem whose capture date has
    dots (``..._2024.07.18_111618_001_SMM``) comes back unchanged.
    """
    name = os.fspath(path_or_name)
    if any(sep in name for sep in _SEPARATORS):
        name = os.path.basename(os.path.normpath(name))
    dot = name.rfind(".")
    if dot > 0 and name[dot:].lower() in _IMAGE_EXTENSIONS and name[:dot].lstrip("."):
        return name[:dot]
    return name


def _format_time(hhmmss: str) -> str:
    return f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}"


def _parse_tokens(stem: str) -> ImageName:
    """General parser: search each part on its own (any name)."""
    tube = _TUBE.search(stem)
    position = _POSITION.search(stem)
    date = _DATE.search(stem)
    time = _TIME.search(stem)
    return ImageName(
        stem=stem,
        field=_CAMERA_SUFFIX.sub("", stem.split("_", 1)[0]) or None,
        tube=int(tube.group(1)) if tube else None,
        position=int(position.group(1)) if position else None,
        date=date.group(0) if date else None,
        time=_format_time(time.group(1)) if time else None,
    )


def parse_image_name(name: str | os.PathLike[str]) -> ImageName:
    """Parse an image stem, file name or path into an :class:`ImageName`."""
    stem = image_stem(name) if name else ""
    match = _CANONICAL.match(stem)
    if match is None:
        return _parse_tokens(stem)
    field, tube, position, date, hhmmss = match.groups()
    return ImageName(stem, field or None, int(tube), int(position), date, _format_time(hhmmss))

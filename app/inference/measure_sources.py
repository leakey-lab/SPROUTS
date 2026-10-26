"""Which file Measure reads for each image: the GT file when it is valid, else the prediction.

Owner decision O1: both metrics read GT first. Length reads ``gt/skeletons/<stem>.png`` when
it is valid, else the prediction ``skeletons/<stem>_fake.png``; area reads
``gt/mask/<stem>.png`` when it decodes, else ``mask/<stem>.png``. An image that has only a
valid GT file gets a row too. The CSV column ``Source`` says which (:data:`GT` /
:data:`PREDICTION`).

Validity is generic; the reason a GT file was not used is one of :data:`SIZE`,
:data:`UNEDITED`, :data:`UNREADABLE`:

- A GT mask is used when it decodes (grayscale), whatever its pixels: a mask equal to its
  prediction measures the same pixels, and an emptied mask is an edit (area 0, O9), so
  ``Source`` stays stable across a Generate run.
- A GT skeleton is used when it decodes, is the photo's size (Correct saves at the photo's
  resolution; a photo whose size cannot be read makes it :data:`UNREADABLE`) and, when the
  image has a prediction, is not that prediction as Correct opens it
  (:func:`app.editing.thinning.is_generated_as_opened`): the editor's path measures a median
  0.8 % longer than the prediction's on the same lines (L1), so an unedited copy would
  change a value nobody edited. An emptied skeleton is an edit (length 0, O9), whether or
  not the image has a prediction.
- An invalid GT file with no prediction to fall back to gets no row; it is recorded in the
  :class:`GtReport` like every other GT file not used.

One image is decided by :func:`decide_length_source` / :func:`decide_area_source`, pure
functions of its paths that read the GT file once: the bytes that were validated are the
bytes hashed, and they are handed on (:attr:`SourceDecision.data`) so the measurement need
not read the file again. At the photo's resolution the skeleton check costs tens of ms per
image (it thins the prediction), so :func:`resolve_length_sources` /
:func:`resolve_area_sources` take a ``mapper``: ``mapper(decide, *columns)`` is called once
with one column per argument of ``decide`` (one entry per image, every image) and returns
the decisions in that order. The default, the built-in ``map``, decides serially; an
executor's ``map`` decides in parallel.

The measuring mapper (how Measure uses it, :class:`app.inference.measure_run.MeasureRun`):
the mapper runs one task per image on the Measure pool's worker threads; the task calls
``decide(*args)``, measures the file the decision names (a used GT file from
``decision.data``, the bytes it validated, never read twice), and returns the decision with
``data`` dropped (``dataclasses.replace(decision, data=None)``). So the decisions and the
measurements happen off the GUI thread, in one pass, and at most one image's bytes per
worker are held at a time, never every image's.

The inputs are ``stem -> path`` scans already split by image (one file per image:
:func:`app.inference.input_split.split_inputs`); two files of one image raise
``ValueError``. Stems are compared without case (:func:`app.naming.stem_key`): a row is
named by its prediction's stem, else by its GT file's. Qt-free: numpy and cv2, no PyQt6, no
torch. Nothing is written.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple

import cv2
import numpy as np

from app.editing.thinning import is_generated_as_opened
from app.gt.files import SOURCE_GT, SOURCE_PREDICTION, image_size
from app.inference.measure_inputs import Fingerprint, input_key
from app.naming import stem_key
from app.project_layout import ProjectLayout

__all__ = [
    "GT",
    "PREDICTION",
    "SIZE",
    "UNEDITED",
    "UNREADABLE",
    "GtReport",
    "MeasureSource",
    "SourceDecision",
    "decide_area_source",
    "decide_length_source",
    "images_of",
    "resolve_area_sources",
    "resolve_length_sources",
]

#: ``Source`` values: the row was measured from the GT file, or from the prediction.
GT = SOURCE_GT
PREDICTION = SOURCE_PREDICTION

#: Why a GT file was not used: not the photo's size, equal to the prediction as Correct
#: opens it, or it (or its photo's size) could not be read.
SIZE = "size"
UNEDITED = "unedited"
UNREADABLE = "unreadable"

#: ``mapper(fn, *columns)``: the built-in ``map`` or an executor's ``map`` (order kept).
Mapper = Callable[..., Iterable["SourceDecision"]]


@dataclass(frozen=True)
class MeasureSource:
    """The file Measure reads for one image (a CSV row).

    ``path`` is the file measured and ``source`` where it comes from (:data:`GT` /
    :data:`PREDICTION`). ``gt_path`` is the image's GT file when it has one, used or not;
    ``reason`` is set only when that GT file was not used (:data:`SIZE`, :data:`UNEDITED`,
    :data:`UNREADABLE`).
    """

    stem: str
    path: str
    source: str
    gt_path: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SourceDecision:
    """One image decided: ``source`` is its row (``None``: no row, an invalid GT file and no
    prediction); ``reason`` why its GT file was not used (``""`` when used or none).

    ``fingerprint`` is ``(size, mtime_ns, sha256)`` of the GT file's bytes as read, used or
    not (the record of a Measure run lists both, so a GT file that changes outdates the
    value); ``None`` when the image has no GT file or it could not be opened. ``data`` is
    those bytes when the GT file is used (to measure them without a second read), else
    ``None``.
    """

    stem: str
    source: MeasureSource | None
    reason: str = ""
    fingerprint: Fingerprint | None = None
    data: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class GtReport:
    """The GT files of one metric: ``folder`` (``"gt/skeletons"`` / ``"gt/mask"``),
    ``files`` (stem -> SHA-256 of each GT file used; a read-only mapping, so a JSON writer
    takes ``dict(report.files)``) and ``skipped`` (``(stem, reason)`` of each GT file not
    used, by stem)."""

    folder: str
    files: Mapping[str, str]
    skipped: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))

    @property
    def n_used(self) -> int:
        """How many GT files are measured."""
        return len(self.files)


# ------------------------------------------------------------------ one image
def decide_area_source(stem: str, prediction: str | None, gt: str | None) -> SourceDecision:
    """The mask the area of image ``stem`` is measured from: the GT mask at ``gt`` when it
    decodes, else the prediction at ``prediction`` (either may be ``None``)."""
    if gt is None:
        return _without_gt(stem, prediction)
    read = _read(gt)
    reason = "" if read is not None and _decode_gray(read[0]) is not None else UNREADABLE
    return _decided(stem, prediction, gt, reason, read)


def decide_length_source(
    stem: str, prediction: str | None, gt: str | None, photo: str | None
) -> SourceDecision:
    """The skeleton the length of image ``stem`` is measured from: the GT skeleton at ``gt``
    when it is valid against the photo at ``photo``, else the prediction at ``prediction``
    (any may be ``None``)."""
    if gt is None:
        return _without_gt(stem, prediction)
    read = _read(gt)
    gray = None if read is None else _decode_gray(read[0])
    return _decided(stem, prediction, gt, _skeleton_reason(gray, prediction, photo), read)


def _skeleton_reason(gray: np.ndarray | None, prediction: str | None, photo: str | None) -> str:
    """Why the GT skeleton ``gray`` (``None``: it does not decode) is not measured, ``""``
    when it is."""
    if gray is None:
        return UNREADABLE
    photo_size = image_size(photo) if photo is not None else None
    if photo_size is None:
        return UNREADABLE
    if (gray.shape[1], gray.shape[0]) != photo_size:
        return SIZE
    # With no prediction there is no value an unedited copy could change: an emptied GT
    # skeleton then says "no roots" (O9), as an emptied GT mask does.
    if prediction is not None and is_generated_as_opened(gray, prediction, photo_size):
        return UNEDITED
    return ""


def _read(path: str) -> tuple[bytes, int, int] | None:
    """``(bytes, size, mtime_ns)`` of the file at ``path``, read once; ``None`` when it
    cannot be read."""
    try:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            return fh.read(), st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _decode_gray(data: bytes) -> np.ndarray | None:
    """``data`` decoded in grayscale (the decoder of :func:`app.io_utils.imread_unicode`),
    ``None`` when it does not decode."""
    if not data:
        return None
    try:
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    except cv2.error:
        return None


def _without_gt(stem: str, prediction: str | None) -> SourceDecision:
    source = None if prediction is None else MeasureSource(stem, prediction, PREDICTION)
    return SourceDecision(stem, source)


def _decided(
    stem: str,
    prediction: str | None,
    gt: str,
    reason: str,
    read: tuple[bytes, int, int] | None,
) -> SourceDecision:
    fingerprint = None
    if read is not None:
        data, size, mtime_ns = read
        fingerprint = (size, mtime_ns, hashlib.sha256(data).hexdigest())
    if reason or read is None:
        reason = reason or UNREADABLE
        source = None
        if prediction is not None:
            source = MeasureSource(stem, prediction, PREDICTION, gt, reason)
        return SourceDecision(stem, source, reason, fingerprint)
    return SourceDecision(stem, MeasureSource(stem, gt, GT, gt), "", fingerprint, data)


# ------------------------------------------------------------------ every image
class _Image(NamedTuple):
    """One image's files: the row's ``stem``, its GT file's ``gt_stem`` and the paths."""

    stem: str
    gt_stem: str | None
    prediction: str | None
    gt: str | None


def resolve_area_sources(
    layout: ProjectLayout,
    predictions: Mapping[str, str],
    gt_files: Mapping[str, str],
    *,
    mapper: Mapper = map,
) -> tuple[dict[str, MeasureSource], GtReport]:
    """:func:`decide_area_source` for every image of ``predictions`` (``mask/``) and
    ``gt_files`` (``gt/mask/``), both ``stem -> path``, through ``mapper``; returns
    ``(stem -> source, report)``."""
    found = _pair(predictions, gt_files)
    columns = ([i.stem for i in found], [i.prediction for i in found], [i.gt for i in found])
    decided = mapper(decide_area_source, *columns) if found else ()
    return _collect(layout, layout.gt_mask_dir, found, decided)


def resolve_length_sources(
    layout: ProjectLayout,
    images: Mapping[str, str],
    predictions: Mapping[str, str],
    gt_files: Mapping[str, str],
    *,
    mapper: Mapper = map,
) -> tuple[dict[str, MeasureSource], GtReport]:
    """:func:`decide_length_source` for every image of ``predictions`` (``skeletons/``) and
    ``gt_files`` (``gt/skeletons/``), both ``stem -> path``, through ``mapper``; ``images``
    (stem -> photo) serves the size check. Returns ``(stem -> source, report)``."""
    photo_of = _photo_finder(images)
    found = _pair(predictions, gt_files)
    columns = (
        [i.stem for i in found],
        [i.prediction for i in found],
        [i.gt for i in found],
        [photo_of(i.gt_stem) for i in found],
    )
    decided = mapper(decide_length_source, *columns) if found else ()
    return _collect(layout, layout.gt_skeletons_dir, found, decided)


def images_of(predictions: Mapping[str, str], gt_files: Mapping[str, str]) -> dict[str, str]:
    """``stem -> path`` of every image of ``predictions`` and ``gt_files`` (the names the
    rows get): its prediction, else its GT file. Nothing is read; two files of one image
    raise ``ValueError`` (pass ``split_inputs`` kept scans)."""
    return {
        i.stem: i.prediction if i.prediction is not None else i.gt
        for i in _pair(predictions, gt_files)
    }


def _photo_finder(images: Mapping[str, str]) -> Callable[[str | None], str | None]:
    """The photo of a stem: the image of that exact stem, else the first image of its key
    (as :func:`app.inference.input_split.split_inputs` pairs them)."""
    by_key: dict[str, str] = {}
    for stem, path in images.items():
        by_key.setdefault(stem_key(stem), path)

    def photo_of(stem: str | None) -> str | None:
        if stem is None:
            return None
        exact = images.get(stem)
        return exact if exact is not None else by_key.get(stem_key(stem))

    return photo_of


def _by_key(files: Mapping[str, str]) -> dict[str, str]:
    """``stem_key -> stem`` of ``files``; two stems of one key raise ``ValueError``."""
    found: dict[str, str] = {}
    for stem in files:
        held = found.setdefault(stem_key(stem), stem)
        if held != stem:
            raise ValueError(
                f"Two files of one image, {held!r} and {stem!r}: pass the scan through "
                "split_inputs first"
            )
    return found


def pairs(
    predictions: Mapping[str, str], gt_files: Mapping[str, str]
) -> list[tuple[str | None, str | None]]:
    """``(prediction path, GT path)`` of every image of ``predictions`` and ``gt_files``
    (paired by stem key; ``None`` for a side the image lacks). Nothing is read."""
    return [(i.prediction, i.gt) for i in _pair(predictions, gt_files)]


def _pair(predictions: Mapping[str, str], gt_files: Mapping[str, str]) -> list[_Image]:
    """Every image of ``predictions`` and ``gt_files``, by stem key."""
    predicted, traced = _by_key(predictions), _by_key(gt_files)
    found: list[_Image] = []
    for key in sorted(predicted.keys() | traced.keys()):
        prediction_stem, gt_stem = predicted.get(key), traced.get(key)
        found.append(
            _Image(
                stem=prediction_stem if prediction_stem is not None else gt_stem,
                gt_stem=gt_stem,
                prediction=None if prediction_stem is None else predictions[prediction_stem],
                gt=None if gt_stem is None else gt_files[gt_stem],
            )
        )
    return found


def _collect(
    layout: ProjectLayout,
    gt_dir: str,
    found: list[_Image],
    decided: Iterable[SourceDecision],
) -> tuple[dict[str, MeasureSource], GtReport]:
    """The rows and the report of ``found``; ``decided`` holds one decision per image, in
    ``found``'s order."""
    sources: dict[str, MeasureSource] = {}
    files: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for image, decision in zip(found, decided, strict=True):
        if decision.source is not None:
            sources[image.stem] = decision.source
        if decision.reason:
            skipped.append((image.stem, decision.reason))
        elif image.gt is not None and decision.fingerprint is not None:
            files[image.stem] = decision.fingerprint[2]
    return sources, GtReport(input_key(gt_dir, layout.root), files, tuple(skipped))

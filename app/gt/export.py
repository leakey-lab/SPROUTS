"""
Dataset export for ground-truth (GT) tracings (spec §4 / §5.4).

Turns the per-image ``gt/<stem>.png`` tracings into a flat training-ready
dataset: copied source images, remapped-id class masks, a train/val split, a
``classes.json`` documenting the id remap, a ``manifest.csv`` and its
provenance record ``manifest.csv.provenance.json`` (export options including
the split seed, counts, the source class registry; ``app.provenance``). No Qt
dependency — the GUI progress dialog (``app/gui/export_dataset_dialog.py``)
drives this from a ``QThread`` via ``progress_cb``.

Independent of every ML model in the app: this module only reads ``gt/`` and
the source images, and never touches ``mask/`` or ``skeletons/``
(docs/superpowers/specs/2026-09-22-multiclass-labels-design.md, hard rule 1).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np
from PIL import Image

from app.config import GT_BACKGROUND_ID, GT_DIRNAME, GT_IGNORE_ID
from app.gt.files import LabelsIndex
from app.gt.registry import Registry
from app.gt.store import (
    GtFileError,
    align_to_registry,
    gt_path,
    histogram,
    is_complete,
    read_tracing,
)
from app.io_utils import atomic_open, atomic_write_text, path_key
from app.naming import parse_image_name, stem_key
from app.project_layout import ProjectLayout
from app.provenance import build_record, portable_path, write_sidecar

logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "manifest.csv"
# The columns every manifest has, in order; ``px_background``, ``px_<key>`` for each labelled
# class in registry order and ``px_ignore`` follow (``_manifest_fields``).
_MANIFEST_FIELDS = [
    "stem",
    "source_image",
    "split",
    "tube",
    "position",
    "date",
    "classes_present",
    "exif_orientation",
]
# The EXIF tag that says how a viewer should rotate the stored pixels (never applied here).
_EXIF_ORIENTATION_TAG = 0x0112
_ERRORS_FILENAME = "errors.txt"
_PROVENANCE_KIND = "gt_export"
# Project folders the export must never write into (the ML pipeline's outputs
# and the GT tracings themselves). Only their names are used here: nothing in
# them is read.
_PROTECTED_DIRS = ("mask", "skeletons", GT_DIRNAME)
# Pillow refuses to open an image over twice ``Image.MAX_IMAGE_PIXELS`` (about 179 Mpx)
# with this error, which is neither OSError nor ValueError: one such source image is that
# stem's export error, like any unreadable file, and never ends the whole export (a tracing
# Pillow refuses is a ``GtFileError`` of ``read_tracing``).
_TOO_LARGE = Image.DecompressionBombError


class ExportError(ValueError):
    """Raised for an invalid export request (e.g. a non-empty output directory)."""


@dataclass(frozen=True)
class ExportOptions:
    """What to export and how to split it.

    ``out_dir`` must be empty or absent; a relative or drive-relative one (``D:out``) is
    resolved once, when the export starts. ``val_fraction`` (0-1) is the target
    share of exported images in the validation split; ``group_by_tube`` keeps
    every image of one tube in the same split; ``only_complete`` exports only
    images whose every active class was reviewed; ``seed`` makes the split
    reproducible.
    """

    out_dir: str
    val_fraction: float = 0.2
    group_by_tube: bool = True
    only_complete: bool = True
    seed: int = 0


@dataclass
class ExportResult:
    """Counts from one export; ``errors`` lists ``(stem, message)`` for skipped stems."""

    out_dir: str
    exported: int
    train: int
    val: int
    skipped_incomplete: int
    errors: list[tuple[str, str]] = field(default_factory=list)


def remap_ids(ids: np.ndarray, registry: Registry) -> np.ndarray:
    """Remap registry ids to contiguous export ids.

    Background stays 0; active non-Ignore classes become 1..N in registry
    (display) order; Ignore, retired classes and any id unknown to the
    registry all become ``GT_IGNORE_ID`` (255).
    """
    lut = np.full(256, GT_IGNORE_ID, dtype=np.uint8)
    lut[GT_BACKGROUND_ID] = GT_BACKGROUND_ID
    for new_id, c in enumerate(registry.labelled(), start=1):
        lut[c.id] = new_id
    if ids.dtype == np.uint8 and ids.ndim == 2 and ids.size:
        # The same table lookup, about 10x faster (cv2.LUT keeps the shape of 2-D arrays only).
        return cv2.LUT(np.ascontiguousarray(ids), lut)
    return lut[ids]


def assign_splits(
    stems: list[str], val_fraction: float, group_by_tube: bool, seed: int
) -> dict[str, str]:
    """Deterministically assign each stem to "train" or "val".

    Groups stems by tube number (the ``_T<n>_`` token, ``app.naming``) when
    ``group_by_tube`` is true and the tube parses, else by stem, so a tube
    never spans both splits. Groups are sorted (for a stable starting order)
    then shuffled with ``random.Random(seed)``; groups are added to val,
    skipping the last group so train never empties, until val holds
    ``val_fraction`` of the stems.
    """
    groups: dict[str, list[str]] = {}
    for stem in stems:
        tube = parse_image_name(stem).tube if group_by_tube else None
        key = f"tube:{tube}" if tube is not None else f"stem:{stem}"
        groups.setdefault(key, []).append(stem)
    order = sorted(groups)
    random.Random(seed).shuffle(order)
    val_keys: list[str] = []
    if val_fraction > 0 and len(order) >= 2:
        target = val_fraction * len(stems)
        count = 0
        for key in order[:-1]:  # the last group always stays in train
            if count >= target:
                break
            val_keys.append(key)
            count += len(groups[key])
    return {s: ("val" if key in val_keys else "train") for key in order for s in groups[key]}


def _manifest_fields(registry: Registry) -> list[str]:
    """The manifest header: the fixed columns, then one pixel count per exported id."""
    return [
        *_MANIFEST_FIELDS,
        "px_background",
        *(f"px_{c.key}" for c in registry.labelled()),
        "px_ignore",
    ]


def _pixel_counts(remapped: np.ndarray, registry: Registry) -> dict[str, int]:
    """Pixels per exported id (``remap_ids`` output): background, each labelled class
    in registry order, Ignore (retired and unknown classes included)."""
    counts = histogram(remapped)
    row = {"px_background": int(counts[GT_BACKGROUND_ID])}
    for new_id, c in enumerate(registry.labelled(), start=1):
        row[f"px_{c.key}"] = int(counts[new_id])
    row["px_ignore"] = int(counts[GT_IGNORE_ID])
    return row


def _checked_orientation(image_path: str, shape: tuple[int, ...]) -> int | str:
    """The image's EXIF orientation tag (1-8), or ``""`` when it has none or it is unreadable,
    once the image is the size of its tracing (one open of the image).

    Raises ``ValueError`` when a tracing is not the size of its source image: Correct treats
    such a file as unusable (the image was replaced or resized after tracing); exported, the
    pair would be misaligned. A missing or unidentifiable image raises ``OSError``, one Pillow
    refuses as too large ``DecompressionBombError``. The tag is only recorded: the pixels are
    always the stored grid (W2-T10, ``tests/test_exif_policy.py``).
    """
    with Image.open(image_path) as image:
        width, height = image.size
        if tuple(shape) != (height, width):
            gt_h, gt_w = shape[:2]
            raise ValueError(f"GT is {gt_w}x{gt_h} but the image is {width}x{height}")
        try:
            value = image.getexif().get(_EXIF_ORIENTATION_TAG)
        except Exception as exc:  # a metadata column must never fail an export
            logger.debug("No EXIF orientation for %s: %s", image_path, exc)
            return ""
    if isinstance(value, int) and 1 <= value <= 8:
        return value
    return ""


def _classes_json(registry: Registry) -> dict:
    classes = [{"id": GT_BACKGROUND_ID, "key": "background", "name": "Background"}]
    for new_id, c in enumerate(registry.labelled(), start=1):
        classes.append({"id": new_id, "key": c.key, "name": c.name, "color": c.color})
    return {"version": 1, "ignore_index": GT_IGNORE_ID, "classes": classes}


def _check_out_dir(project_dir: str, out_dir: str) -> None:
    """Refuse an ``out_dir`` inside (or equal to) one of the project's data folders.

    The paths are compared without case on every platform (``path_key``'s darwin key: NFC,
    case-folded): Linux on a case-insensitive volume (exFAT, NTFS, CIFS, ext4 casefold) keeps
    a path's spelling, so ``<project>/MASK/export`` is inside ``mask/`` there (final review
    D5). A folder named like a protected one in another case is refused on any disk.
    """
    out = path_key(os.path.realpath(out_dir), platform="darwin")
    for name in _PROTECTED_DIRS:
        protected = os.path.realpath(os.path.join(project_dir, name))
        protected = path_key(protected, platform="darwin")
        try:
            inside = os.path.commonpath([out, protected]) == protected
        except ValueError:  # different drives (Windows): cannot be inside
            inside = False
        if inside:
            raise ExportError(
                f"Output folder must not be inside the project's {name}/ folder: {out_dir}"
            )


def _labels_file(
    image_path: str, index: LabelsIndex, listed: dict[str, dict[str, str]]
) -> str | None:
    """The class labels of ``image_path``: ``gt/<stem>.png``, else a ``.png`` of ``gt/`` whose
    stem differs only in case (O7), as Correct and the Library find it; else None.

    ``index`` answers most images without a file check (the names of each folder's ``gt/``);
    ``listed`` keeps one ranked scan of ``gt/`` per image folder, made on the folder's first
    image that may have labels but has no exact file."""
    if index.lacks_labels(image_path):
        return None
    exact = gt_path(image_path)
    if os.path.exists(exact):
        return exact
    folder = os.path.dirname(image_path)
    found = listed.get(folder)
    if found is None:
        labels = ProjectLayout(folder).scan_gt_labels()
        found = listed[folder] = {stem_key(stem): path for stem, path in labels.items()}
    stem = os.path.splitext(os.path.basename(image_path))[0]  # gt_path's stem
    return found.get(stem_key(stem))


def _export_stem(
    stem: str,
    image_path: str,
    ids: np.ndarray,
    registry: Registry,
    masks_dir: str,
    images_dir: str,
) -> dict:
    """Write the mask and the image copy of one stem; its manifest row (without the split).

    An image that is missing, unreadable, too large or not the size of ``ids`` raises
    (``OSError``, ``DecompressionBombError``, ``ValueError``) before anything is written; a
    failed write raises ``OSError`` and leaves what was written for the caller to remove.
    """
    orientation = _checked_orientation(image_path, ids.shape)
    remapped = remap_ids(ids, registry)
    Image.fromarray(remapped, mode="L").save(os.path.join(masks_dir, f"{stem}.png"))
    ext = os.path.splitext(image_path)[1]
    shutil.copy2(image_path, os.path.join(images_dir, f"{stem}{ext}"))
    counts = _pixel_counts(remapped, registry)
    parsed = parse_image_name(stem)
    return {
        "stem": stem,
        "source_image": image_path,
        "tube": _or_blank(parsed.tube),
        "position": _or_blank(parsed.position),
        "date": _or_blank(parsed.date),
        # One export id per labelled class: the class is present when its id has pixels.
        "classes_present": ";".join(c.key for c in registry.labelled() if counts[f"px_{c.key}"]),
        "exif_orientation": orientation,
        **counts,
    }


def _write_split_file(path: str, stems: list[str]) -> None:
    # Text mode as before (CRLF on Windows), so the bytes are unchanged (R1).
    atomic_write_text(path, "".join(stem + "\n" for stem in stems))


def _write_provenance(
    manifest_path: str,
    project_dir: str,
    n_images: int,
    registry: Registry,
    options: ExportOptions,
    result: ExportResult,
) -> None:
    """Record how the dataset was made next to its manifest (best effort).

    Holds everything needed to audit or re-derive the split: the options (seed
    included, ``out_dir`` left out), the counts, and the registry with its
    pre-remap ids. Paths are portable (no user or drive names). ``models`` stays
    empty: ground truth never comes from a model. A write failure is logged and
    never fails an export that is otherwise complete.
    """
    record = build_record(
        _PROVENANCE_KIND,
        inputs={
            "project": portable_path(project_dir),
            "images": n_images,
            "registry": registry.to_dict(),
        },
        parameters={k: v for k, v in asdict(options).items() if k != "out_dir"},
        outputs={
            "exported": result.exported,
            "train": result.train,
            "val": result.val,
            "skipped_incomplete": result.skipped_incomplete,
            "errors": len(result.errors),
        },
    )
    try:
        write_sidecar(manifest_path, record)
    except OSError as exc:
        logger.warning("Could not write the provenance record for %s: %s", manifest_path, exc)


def export_dataset(
    project_dir: str,
    images: dict[str, str],
    registry: Registry,
    options: ExportOptions,
    progress_cb: Callable[[int], None] | None = None,
) -> ExportResult:
    """Export a training dataset from the GT tracings under ``project_dir``.

    ``images`` is ``image_manager.images`` (stem -> image path). Stems
    without a GT file are silently skipped. A GT file that fails to read
    (``GtFileError``), a missing/unreadable source image, a tracing or image
    Pillow refuses as too large (over about 179 Mpx), a tracing whose size
    is not the image's (Correct does not show it either), or a failure while
    writing that stem's mask/copy (``OSError``/``ValueError``) is recorded in
    ``errors`` and the export continues with the remaining stems (spec §5.4);
    any partial mask/image already written for that stem is removed so it is
    never half-exported or counted in the splits/manifest. When there are
    errors, ``<out_dir>/errors.txt`` lists them, one ``stem<TAB>message`` per
    line. The split files, ``classes.json``, ``manifest.csv`` and ``errors.txt``
    are written atomically (``app.io_utils``): complete or absent, and a file
    held open by another program raises ``FileLockedError`` (R1). ``mask/``,
    ``skeletons/`` and the GT files themselves are never modified: an
    ``out_dir`` inside ``mask/``, ``skeletons/`` or ``gt/`` raises ``ExportError``.
    The folder is resolved to an absolute path once, at
    the start (``D:out`` is the current folder of drive D then), and every file, check,
    message and the result use it. ``progress_cb`` gets the percent done, once per new
    value: 0 at the first image, 100 at the end.
    """
    out_dir = os.path.abspath(options.out_dir)
    _check_out_dir(project_dir, out_dir)
    if os.path.exists(out_dir):
        if not os.path.isdir(out_dir):
            raise ExportError(f"Output path exists and is not a directory: {out_dir}")
        if os.listdir(out_dir):
            raise ExportError(f"Output directory is not empty: {out_dir}")

    images_dir = os.path.join(out_dir, "images")
    masks_dir = os.path.join(out_dir, "masks")
    splits_dir = os.path.join(out_dir, "splits")
    for d in (out_dir, images_dir, masks_dir, splits_dir):
        os.makedirs(d, exist_ok=True)

    errors: list[tuple[str, str]] = []
    skipped_incomplete = 0
    rows: dict[str, dict] = {}  # exported stem -> its manifest row, in export order
    index = LabelsIndex()  # the names of each image folder's gt/
    listed: dict[str, dict[str, str]] = {}  # image folder -> its gt/ labels by stem key

    stems = sorted(images)
    n = len(stems)
    progress = _Progress(progress_cb, n)
    for i, stem in enumerate(stems):
        image_path = images[stem]
        gt_file = _labels_file(image_path, index, listed)
        if gt_file is not None:  # read_tracing gives None if the file went in the meantime
            try:
                tracing = read_tracing(gt_file)
            except GtFileError as exc:
                logger.warning("Skipping unreadable GT file for %s: %s", stem, exc)
                errors.append((stem, str(exc)))
                tracing = None

            if tracing is not None:
                try:
                    aligned, warnings, _changed = align_to_registry(tracing, registry)
                    for warning in warnings:
                        logger.info("GT alignment warning for %s: %s", stem, warning)

                    if options.only_complete and not is_complete(aligned, registry):
                        skipped_incomplete += 1
                    else:
                        rows[stem] = _export_stem(
                            stem, image_path, aligned.ids, registry, masks_dir, images_dir
                        )
                except (OSError, ValueError, _TOO_LARGE) as exc:
                    # A missing/unreadable/too large source image, or a write
                    # failure (disk full, bad permissions, malformed array), must
                    # not abort the whole export (spec §5.4: one bad file adds an
                    # errors entry and the export continues). Clean up any
                    # partial output so this stem is never half-exported.
                    logger.warning("Skipping %s due to export error: %s", stem, exc)
                    errors.append((stem, str(exc)))
                    ext = os.path.splitext(image_path)[1]
                    _remove_if_exists(os.path.join(masks_dir, f"{stem}.png"))
                    _remove_if_exists(os.path.join(images_dir, f"{stem}{ext}"))

        progress(i)

    exported_stems = list(rows)
    splits = assign_splits(
        exported_stems, options.val_fraction, options.group_by_tube, options.seed
    )
    train_stems = sorted(s for s in exported_stems if splits.get(s) == "train")
    val_stems = sorted(s for s in exported_stems if splits.get(s) == "val")

    # (R1) Every file below is written to a temp file and swapped in (app.io_utils):
    # a failure or a killed export leaves it complete or absent, never truncated.
    _write_split_file(os.path.join(splits_dir, "train.txt"), train_stems)
    _write_split_file(os.path.join(splits_dir, "val.txt"), val_stems)

    atomic_write_text(
        os.path.join(out_dir, "classes.json"), json.dumps(_classes_json(registry), indent=2)
    )

    manifest_path = os.path.join(out_dir, _MANIFEST_FILENAME)
    with atomic_open(manifest_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_manifest_fields(registry))
        writer.writeheader()
        for stem in exported_stems:
            writer.writerow({**rows[stem], "split": splits.get(stem, "train")})

    if errors:
        # One line per error, whatever the message contains.
        atomic_write_text(
            os.path.join(out_dir, _ERRORS_FILENAME),
            "".join(stem + "\t" + " ".join(message.split()) + "\n" for stem, message in errors),
        )

    result = ExportResult(
        out_dir=out_dir,
        exported=len(exported_stems),
        train=len(train_stems),
        val=len(val_stems),
        skipped_incomplete=skipped_incomplete,
        errors=errors,
    )
    _write_provenance(manifest_path, project_dir, n, registry, options, result)
    progress(n)
    return result


def _or_blank(value):
    return "" if value is None else value


def _remove_if_exists(path: str) -> None:
    """Best-effort delete of a partially written output file for a failed stem."""
    try:
        os.remove(path)
    except OSError:
        pass


class _Progress:
    """Reports the percent done of ``n`` stems to ``progress_cb``, once per new value: one
    queued signal per image (46k) took about a tenth of a large export's time."""

    def __init__(self, progress_cb: Callable[[int], None] | None, n: int) -> None:
        self._progress_cb, self._n, self._last = progress_cb, n, None

    def __call__(self, i: int) -> None:
        if self._progress_cb is None:
            return
        percent = int(100 * i / self._n) if self._n else 100
        if percent != self._last:
            self._last = percent
            self._progress_cb(percent)

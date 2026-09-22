"""
Dataset export for ground-truth (GT) tracings (spec §4 / §5.4).

Turns the per-image ``gt/<stem>.png`` tracings into a flat training-ready
dataset: copied source images, remapped-id class masks, a train/val split, a
``classes.json`` documenting the id remap, and a ``manifest.csv``. No Qt
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
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from app.config import GT_BACKGROUND_ID, GT_IGNORE_ID
from app.gt.registry import Registry
from app.gt.store import (
    GtFileError,
    align_to_registry,
    gt_path,
    is_complete,
    present_keys,
    read_tracing,
)
from app.inference.metrics import parse_image_name

logger = logging.getLogger(__name__)

_MANIFEST_FIELDS = ["stem", "source_image", "split", "tube", "position", "date", "classes_present"]


class ExportError(ValueError):
    """Raised for an invalid export request (e.g. a non-empty output directory)."""


@dataclass(frozen=True)
class ExportOptions:
    out_dir: str
    val_fraction: float = 0.2
    group_by_tube: bool = True
    only_complete: bool = True
    seed: int = 0


@dataclass
class ExportResult:
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
    return lut[ids].astype(np.uint8)


def assign_splits(
    stems: list[str], val_fraction: float, group_by_tube: bool, seed: int
) -> dict[str, str]:
    """Deterministically assign each stem to "train" or "val".

    Groups stems by tube number (from ``parse_image_name``) when
    ``group_by_tube`` is true and the tube parses, else by stem, so a tube
    never spans both splits. Groups are sorted (for a stable starting order)
    then shuffled with ``random.Random(seed)``; groups are added to val,
    skipping the last group so train never empties, until val holds
    ``val_fraction`` of the stems.
    """
    groups: dict[str, list[str]] = {}
    for stem in stems:
        tube = parse_image_name(stem).get("tube_number") if group_by_tube else None
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


def _classes_json(registry: Registry) -> dict:
    classes = [{"id": GT_BACKGROUND_ID, "key": "background", "name": "Background"}]
    for new_id, c in enumerate(registry.labelled(), start=1):
        classes.append({"id": new_id, "key": c.key, "name": c.name, "color": c.color})
    return {"version": 1, "ignore_index": GT_IGNORE_ID, "classes": classes}


def _write_split_file(path: str, stems: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for stem in stems:
            fh.write(stem + "\n")


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
    (``GtFileError``), a missing/unreadable source image, or a failure while
    writing that stem's mask/copy (``OSError``/``ValueError``) is recorded in
    ``errors`` and the export continues with the remaining stems (spec §5.4);
    any partial mask/image already written for that stem is removed so it is
    never half-exported or counted in the splits/manifest. ``mask/``,
    ``skeletons/`` and the GT files themselves are never modified.
    """
    out_dir = options.out_dir
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
    exported_stems: list[str] = []
    manifest_rows: dict[str, dict] = {}

    stems = sorted(images)
    n = len(stems)
    for i, stem in enumerate(stems):
        image_path = images[stem]
        gt_file = gt_path(image_path)
        if os.path.exists(gt_file):
            try:
                tracing = read_tracing(gt_file)
            except GtFileError as exc:
                logger.warning("Skipping unreadable GT file for %s: %s", stem, exc)
                errors.append((stem, str(exc)))
                _emit_progress(progress_cb, i, n)
                continue

            if tracing is not None:
                try:
                    aligned, warnings, _changed = align_to_registry(tracing, registry)
                    for warning in warnings:
                        logger.info("GT alignment warning for %s: %s", stem, warning)

                    if options.only_complete and not is_complete(aligned, registry):
                        skipped_incomplete += 1
                    else:
                        remapped = remap_ids(aligned.ids, registry)
                        mask_out_path = os.path.join(masks_dir, f"{stem}.png")
                        Image.fromarray(remapped, mode="L").save(mask_out_path)

                        ext = os.path.splitext(image_path)[1]
                        image_out_path = os.path.join(images_dir, f"{stem}{ext}")
                        shutil.copy2(image_path, image_out_path)

                        parsed = parse_image_name(stem)
                        present = present_keys(aligned, registry)
                        classes_present = ";".join(
                            c.key for c in registry.labelled() if c.key in present
                        )
                        manifest_rows[stem] = {
                            "stem": stem,
                            "source_image": image_path,
                            "tube": _or_blank(parsed.get("tube_number")),
                            "position": _or_blank(parsed.get("length_position")),
                            "date": _or_blank(parsed.get("date")),
                            "classes_present": classes_present,
                        }
                        exported_stems.append(stem)
                except (OSError, ValueError) as exc:
                    # A missing/unreadable source image, or a write failure
                    # (disk full, bad permissions, malformed array), must not
                    # abort the whole export (spec §5.4: one bad file adds an
                    # errors entry and the export continues). Clean up any
                    # partial output so this stem is never half-exported.
                    logger.warning("Skipping %s due to export error: %s", stem, exc)
                    errors.append((stem, str(exc)))
                    ext = os.path.splitext(image_path)[1]
                    _remove_if_exists(os.path.join(masks_dir, f"{stem}.png"))
                    _remove_if_exists(os.path.join(images_dir, f"{stem}{ext}"))
                    manifest_rows.pop(stem, None)
                    if stem in exported_stems:
                        exported_stems.remove(stem)

        _emit_progress(progress_cb, i, n)

    splits = assign_splits(
        exported_stems, options.val_fraction, options.group_by_tube, options.seed
    )
    train_stems = sorted(s for s in exported_stems if splits.get(s) == "train")
    val_stems = sorted(s for s in exported_stems if splits.get(s) == "val")

    _write_split_file(os.path.join(splits_dir, "train.txt"), train_stems)
    _write_split_file(os.path.join(splits_dir, "val.txt"), val_stems)

    with open(os.path.join(out_dir, "classes.json"), "w", encoding="utf-8") as fh:
        json.dump(_classes_json(registry), fh, indent=2)

    with open(os.path.join(out_dir, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MANIFEST_FIELDS)
        writer.writeheader()
        for stem in exported_stems:
            row = dict(manifest_rows[stem])
            row["split"] = splits.get(stem, "train")
            writer.writerow(row)

    _emit_progress(progress_cb, n, n)

    return ExportResult(
        out_dir=out_dir,
        exported=len(exported_stems),
        train=len(train_stems),
        val=len(val_stems),
        skipped_incomplete=skipped_incomplete,
        errors=errors,
    )


def _or_blank(value):
    return "" if value is None else value


def _remove_if_exists(path: str) -> None:
    """Best-effort delete of a partially written output file for a failed stem."""
    try:
        os.remove(path)
    except OSError:
        pass


def _emit_progress(progress_cb: Callable[[int], None] | None, i: int, n: int) -> None:
    if progress_cb is None:
        return
    progress_cb(int(100 * i / n) if n else 100)

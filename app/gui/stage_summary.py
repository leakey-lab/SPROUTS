"""What the Generate and Measure stages would work on, in one status-line phrase (W12).

The two stages showed the Library canvas and their buttons, and nothing said what a run
would do. The status line's idle text now says it while one of them is on screen:

* Generate: ``40 images · 28 without a mask · 20 without a skeleton``;
* Measure: ``20 skeletons: measured, current · 12 masks: 3 changed since measured``; a
  CSV measured with another field of view or length method than the project's now says
  ``measured with another field of view or length method: measure again`` (O3).
  With the record of the files a Measure run measured (``<csv>.inputs.json``, R5),
  "changed" are the inputs whose content differs from the recorded one, and inputs added
  and removed since are named (``12 masks: 3 changed, 2 new, 1 removed since measured``);
  without one, "changed" counts the inputs written after the metric's CSV. With GT files
  (O1, Measure reads them first) each metric counts its images and says how many have one:
  ``20 skeletons (2 GT): measured, current``.

Qt-free. Counting what changed since a CSV reads one folder (:func:`input_freshness`), so
it runs off the GUI thread; the counts of the other phrases come from the library state.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Collection, Container, Iterable
from dataclasses import dataclass

from app.config import SOURCE_COLUMN
from app.inference.input_split import group_by_stem, png_stem, split_inputs
from app.inference.measure_inputs import (
    InputsRecord,
    Stamp,
    diff_inputs,
    input_key,
    scan_stamps,
)
from app.naming import stem_key
from app.project_layout import SKELETON_SUFFIX, skeleton_stem

__all__ = ["Freshness", "generate_text", "input_freshness", "measure_text", "missing"]

#: Suffixes listed by the name rule of Measure's own listing (masks and GT files: the
#: extension in any case; skeletons: the exact suffix; hidden names too), so the count is of
#: the files Measure reads.
_NAME_RULES = {SKELETON_SUFFIX: skeleton_stem, ".png": png_stem}


@dataclass(frozen=True)
class Freshness:
    """A metric's inputs against its CSV: ``inputs`` files to measure, ``changed`` of them
    since measured (``measured`` False: no CSV yet).

    ``by_content``: compared with the record of the measured inputs: ``changed`` differ in
    content, ``new`` were not measured, ``removed`` were measured and are gone. Otherwise
    ``changed`` were written after the CSV (file time) and ``new`` / ``removed`` are 0.

    ``without_image``: files left out of ``inputs`` because their image is no longer in the
    project (Measure skips them, O37); ``duplicates``: files left out because another file of
    the same image is measured (a case-sensitive disk).

    ``gt``: how many of the ``inputs`` images have a GT file (O1: Measure reads it first).

    ``method_note``: the CSV was measured with another field of view than the project's, or
    (length) another length method (:func:`app.calibration.method_note`, O3); ``""``: not.
    """

    inputs: int
    measured: bool
    changed: int = 0
    new: int = 0
    removed: int = 0
    by_content: bool = False
    without_image: int = 0
    duplicates: int = 0
    gt: int = 0
    method_note: str = ""


def missing(images: Iterable[str], present: Container[str]) -> int:
    """How many of ``images`` are not in ``present``."""
    return sum(1 for name in images if name not in present)


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def generate_text(images: Collection[str], masks: Container[str], skeletons: Container[str]) -> str:
    """``40 images · 28 without a mask · 20 without a skeleton`` (what Generate would add)."""
    no_mask, no_skeleton = missing(images, masks), missing(images, skeletons)
    parts = [_count(len(images), "image")]
    if not no_mask and not no_skeleton:
        parts.append("every image has a mask and a skeleton")
    else:
        parts.append(f"{no_mask:,} without a mask")
        parts.append(f"{no_skeleton:,} without a skeleton")
    return " · ".join(parts)


def _metric_text(noun: str, freshness: Freshness | None, inputs: int, gt: int) -> str:
    if freshness is not None:
        inputs = freshness.inputs
    skipped = ""
    if freshness is not None and freshness.without_image:
        skipped = f" ({freshness.without_image:,} without an image: skipped)"
    if freshness is not None and freshness.duplicates:
        n = freshness.duplicates
        skipped += f" ({n:,} duplicate{'' if n == 1 else 's'} not counted)"
    if not inputs:
        return f"no {noun}s to measure{skipped}"
    head = _count(inputs, noun) + (f" ({gt:,} GT)" if gt else "")
    if freshness is None:
        return head
    if not freshness.measured:
        return f"{head}: not measured yet{skipped}"
    if freshness.method_note:
        return (
            f"{head}: measured with another field of view or length method: measure again{skipped}"
        )
    parts = [
        f"{count:,} {what}"
        for count, what in (
            (freshness.changed, "changed"),
            (freshness.new, "new"),
            (freshness.removed, "removed"),
        )
        if count
    ]
    if not parts:
        return f"{head}: measured, current{skipped}"
    return f"{head}: {', '.join(parts)} since measured{skipped}"


def measure_text(
    skeletons: int,
    masks: int,
    length: Freshness | None = None,
    area: Freshness | None = None,
    *,
    gt_skeletons: int = 0,
    gt_masks: int = 0,
) -> str:
    """``20 skeletons (2 GT): measured, current · 12 masks: 3 changed since measured``.

    ``skeletons`` / ``masks`` (images with a skeleton / a mask) and ``gt_skeletons`` /
    ``gt_masks`` (of them, with a GT file) are the counts known without reading a folder;
    ``length`` / ``area`` (from :func:`input_freshness`) replace them once known.
    """
    return " · ".join(
        (
            _metric_text("skeleton", length, skeletons, gt_skeletons),
            _metric_text("mask", area, masks, gt_masks),
        )
    )


def _only_with_image(
    files: Iterable[tuple[str, str]], images: Collection[str]
) -> tuple[set[str], int, int]:
    """``(paths Measure would read, files without an image, duplicates)`` of ``files``
    (``(stem, path)`` pairs): what :func:`~app.inference.input_split.split_inputs` sorts."""
    listed, shadowed = group_by_stem(files)
    split = split_inputs(listed, images, shadowed)
    return set(split.kept.values()), len(split.without_image), len(split.duplicates)


def _stem_of(path: str, cut: int) -> str:
    """The file name of ``path`` (a :func:`scan_stamps` path: ``os.sep`` separators) without
    its last ``cut`` characters (the suffix)."""
    name = path[path.rfind(os.sep) + 1 :]  # os.path.basename, ~20x faster on Windows
    return name[: len(name) - cut]


def _listed(
    folder: str | None, suffix: str, root: str, images: Collection[str] | None
) -> tuple[dict[str, Stamp], int, int]:
    """``(input key -> stamp, without an image, duplicates)`` of the ``*suffix`` files of
    ``folder`` Measure would read (with ``images``: those of an image in the project)."""
    if not folder:
        return {}, 0, 0
    cut = len(suffix)
    stamps = scan_stamps(folder, suffix, root, stem_of=_NAME_RULES.get(suffix))
    if images is None:
        return stamps, 0, 0
    files = ((_stem_of(path, cut), path) for path, _size, _mtime in stamps.values())
    keep, without_image, duplicates = _only_with_image(files, images)
    return {key: st for key, st in stamps.items() if st[0] in keep}, without_image, duplicates


def _has_source_column(csv_path: str) -> bool:
    """True when the CSV's header names ``Source`` (written since O1); also when it cannot
    be read (nothing is claimed then)."""
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh), [])
    except (OSError, UnicodeDecodeError, csv.Error):
        return True
    return SOURCE_COLUMN in header


def _removed(
    record: InputsRecord,
    current: Collection[str],
    images: Collection[str],
    folder: str | None,
    gt_folder: str | None,
    root: str,
) -> int:
    """Recorded files of ``folder`` and ``gt_folder`` (``None``: no GT files) that are gone
    (all recorded files when one of them is the project folder). A GT file counts only when
    its image is no longer an input: an image measured from its GT file and now from its
    prediction is new, and counted once."""
    keys = [input_key(path, root) for path in (folder, gt_folder) if path]
    prefixes = tuple(key + "/" for key in keys if key != ".")
    gt_prefix = None if gt_folder is None else input_key(gt_folder, root) + "/"
    removed = 0
    for key in record.inputs:
        if key in current or (len(prefixes) == len(keys) and not key.startswith(prefixes)):
            continue
        if gt_prefix is not None and key.startswith(gt_prefix):
            name = key[len(gt_prefix) :]
        else:
            name = None
        if name is not None and "/" not in name and stem_key(name[: -len(".png")]) in images:
            continue
        removed += 1
    return removed


def _gt_first_freshness(
    folder: str | None,
    suffix: str,
    csv_path: str | None,
    record: InputsRecord | None,
    root: str,
    images: Collection[str] | None,
    gt_folder: str | None,
) -> Freshness:
    """:func:`input_freshness` with the GT files of ``gt_folder`` (``None``: no GT files):
    one input per image (its prediction or its GT file), judged as the metrics bar judges it
    (``MeasurementIndex.outdated``).

    Against ``record`` (by content): an image's GT file is always compared (one saved after
    the CSV is new), its prediction only when the image has no GT file or the record lists
    the prediction (it was measured: the GT file was not used); see :func:`_removed` for the
    files that are gone. Without a record (by file time): an image's GT file alone when it
    has one, else its prediction, is changed when written after the CSV; an image with a GT
    file is changed whatever the times when the CSV has no ``Source`` column (written before
    O1: Measure would now read that file)."""
    predicted, p_without, p_duplicates = _listed(folder, suffix, root, images)
    traced, g_without, g_duplicates = _listed(gt_folder, ".png", root, images)
    gt_of = {stem_key(_stem_of(stamp[0], len(".png"))): key for key, stamp in traced.items()}
    measured = record.inputs if record is not None else {}
    current: dict[str, Stamp] = {}  # the files compared with the record
    by_time: list[tuple[Stamp, bool]] = []  # per image: (the file compared by time, a GT file)
    inputs: set[str] = set()  # the images, by stem key
    for key, stamp in predicted.items():
        image = stem_key(_stem_of(stamp[0], len(suffix)))
        inputs.add(image)
        gt_key = gt_of.get(image)
        if gt_key is None:
            current[key] = stamp
            by_time.append((stamp, False))
            continue
        current[gt_key] = traced[gt_key]
        by_time.append((traced[gt_key], True))
        if key in measured:  # measured from the prediction: the GT file was not used
            current[key] = stamp
    for image, gt_key in gt_of.items():
        if image not in inputs:
            inputs.add(image)
            current[gt_key] = traced[gt_key]
            by_time.append((traced[gt_key], True))
    common = dict(
        inputs=len(by_time),
        without_image=p_without + g_without,
        duplicates=p_duplicates + g_duplicates,
        gt=len(traced),
    )
    if record is not None:
        diff = diff_inputs(record, current)
        return Freshness(
            measured=True,
            changed=diff.changed,
            new=diff.new,
            removed=_removed(record, current, inputs, folder, gt_folder, root),
            by_content=True,
            **common,
        )
    csv_mtime = _csv_mtime(csv_path)
    changed = 0
    if csv_mtime is not None:
        before_o1 = bool(traced) and not _has_source_column(csv_path)  # only GT files ask
        changed = sum(1 for st, is_gt in by_time if st[2] > csv_mtime or (is_gt and before_o1))
    return Freshness(measured=csv_mtime is not None, changed=changed, **common)


def _csv_mtime(csv_path: str | None) -> int | None:
    """``st_mtime_ns`` of the CSV, ``None`` without one."""
    if not csv_path:
        return None
    try:
        return os.stat(csv_path).st_mtime_ns
    except OSError:
        return None


def input_freshness(
    folder: str | None,
    suffix: str,
    csv_path: str | None,
    *,
    record: InputsRecord | None = None,
    project_root: str | None = None,
    images: Collection[str] | None = None,
    gt_folder: str | None = None,
) -> Freshness:
    """The ``*suffix`` files of ``folder`` against ``csv_path`` (one ``os.scandir``).

    The temp file of a write in progress (``.<name>.<token>.tmp``) is not an input.
    ``*.png`` files and generated skeletons are listed as Measure lists them, hidden names
    too (:func:`~app.inference.input_split.png_stem`: the extension in any case;
    :func:`~app.project_layout.skeleton_stem`: the exact suffix); other suffixes match
    without case and skip hidden names. With ``record`` (the CSV's
    :func:`~app.inference.measure_inputs.load_inputs_record`; keys relative to
    ``project_root``, default the parent of ``folder`` as in Measure), an input
    is changed when its content differs from the recorded one (hashed only when its size
    or time differ), and new and removed inputs are counted. Without one, an input is
    changed when it was written after the CSV (mtime). With ``images`` (the stems of the
    project's images), only the files Measure would read are inputs: those whose image is
    gone are counted in ``without_image`` (O37), and a second file of an image in
    ``duplicates``. Never raises: an unreadable folder has no inputs.

    ``gt_folder`` (``gt/skeletons/`` or ``gt/mask/``, its ``*.png``): Measure reads an
    image's GT file first (O1). ``inputs`` then counts images, ``gt`` those with a GT file;
    an image is changed when the file it was measured from changed (its GT file, or its
    prediction when the record lists that), new when its GT file is not in the record.
    """
    if gt_folder is None and not folder:  # nothing to list: the CSV alone
        return Freshness(0, measured=_csv_mtime(csv_path) is not None)
    root = project_root or os.path.dirname(os.path.abspath(folder or gt_folder))
    return _gt_first_freshness(folder, suffix, csv_path, record, root, images, gt_folder)

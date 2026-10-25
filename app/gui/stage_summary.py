"""What the Generate and Measure stages would work on, in one status-line phrase (W12).

The two stages showed the Library canvas and their buttons, and nothing said what a run
would do. The status line's idle text now says it while one of them is on screen:

* Generate: ``40 images · 28 without a mask · 20 without a skeleton``;
* Measure: ``20 skeletons: measured, current · 12 masks: 3 changed since measured``.
  With the record of the files a Measure run measured (``<csv>.inputs.json``, R5),
  "changed" are the inputs whose content differs from the recorded one, and inputs added
  and removed since are named (``12 masks: 3 changed, 2 new, 1 removed since measured``);
  without one, "changed" counts the inputs written after the metric's CSV.

Qt-free. Counting what changed since a CSV reads one folder (:func:`input_freshness`), so
it runs off the GUI thread; the counts of the other phrases come from the library state.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Container, Iterable
from dataclasses import dataclass

from app.inference.input_split import group_by_stem, split_inputs
from app.inference.measure_inputs import InputsRecord, diff_inputs, input_key, scan_stamps

__all__ = ["Freshness", "generate_text", "input_freshness", "measure_text", "missing"]


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
    """

    inputs: int
    measured: bool
    changed: int = 0
    new: int = 0
    removed: int = 0
    by_content: bool = False
    without_image: int = 0
    duplicates: int = 0


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


def _metric_text(noun: str, freshness: Freshness | None, inputs: int) -> str:
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
    head = _count(inputs, noun)
    if freshness is None:
        return head
    if not freshness.measured:
        return f"{head}: not measured yet{skipped}"
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
) -> str:
    """``20 skeletons: measured, current · 12 masks: 3 changed since measured``.

    ``skeletons`` / ``masks`` are the counts known without reading a folder; ``length`` /
    ``area`` (from :func:`input_freshness`) replace them once known.
    """
    return " · ".join(
        (_metric_text("skeleton", length, skeletons), _metric_text("mask", area, masks))
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
    """The file name of ``path`` without its last ``cut`` characters (the suffix)."""
    name = os.path.basename(path)
    return name[: len(name) - cut]


def input_freshness(
    folder: str | None,
    suffix: str,
    csv_path: str | None,
    *,
    record: InputsRecord | None = None,
    project_root: str | None = None,
    images: Collection[str] | None = None,
) -> Freshness:
    """The ``*suffix`` files of ``folder`` against ``csv_path`` (one ``os.scandir``).

    Hidden files (the ``.<name>.tmp`` of a write in progress) are not inputs. With
    ``record`` (the CSV's :func:`~app.inference.measure_inputs.load_inputs_record`; keys
    relative to ``project_root``, default the parent of ``folder`` as in Measure), an input
    is changed when its content differs from the recorded one (hashed only when its size
    or time differ), and new and removed inputs are counted. Without one, an input is
    changed when it was written after the CSV (mtime). With ``images`` (the stems of the
    project's images), only the files Measure would read are inputs: those whose image is
    gone are counted in ``without_image`` (O37), and a second file of an image in
    ``duplicates``. Never raises: an unreadable folder has no inputs.
    """
    cut = len(suffix)
    if record is not None and folder:
        root = project_root or os.path.dirname(os.path.abspath(folder))
        folder_key = input_key(folder, root)
        stamps = scan_stamps(folder, suffix, root)
        without_image = duplicates = 0
        if images is not None:
            files = ((_stem_of(path, cut), path) for path, _size, _mtime in stamps.values())
            keep, without_image, duplicates = _only_with_image(files, images)
            stamps = {key: stamp for key, stamp in stamps.items() if stamp[0] in keep}
        diff = diff_inputs(record, stamps, prefix=None if folder_key == "." else folder_key + "/")
        return Freshness(
            inputs=diff.inputs,
            measured=True,
            changed=diff.changed,
            new=diff.new,
            removed=diff.removed,
            by_content=True,
            without_image=without_image,
            duplicates=duplicates,
        )
    csv_mtime = None
    if csv_path:
        try:
            csv_mtime = os.stat(csv_path).st_mtime_ns
        except OSError:
            csv_mtime = None
    mtimes: dict[str, int] = {}  # path -> mtime_ns
    files: list[tuple[str, str]] = []  # (stem, path)
    if folder:
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith(".") or not name.lower().endswith(suffix):
                        continue
                    try:
                        if not entry.is_file():
                            continue
                        mtimes[entry.path] = entry.stat().st_mtime_ns
                    except OSError:
                        continue
                    files.append((name[: len(name) - cut], entry.path))
        except OSError:
            pass
    without_image = duplicates = 0
    if images is not None:
        keep, without_image, duplicates = _only_with_image(files, images)
        mtimes = {path: mtime for path, mtime in mtimes.items() if path in keep}
    changed = sum(1 for m in mtimes.values() if csv_mtime is not None and m > csv_mtime)
    return Freshness(
        inputs=len(mtimes),
        measured=csv_mtime is not None,
        changed=changed,
        without_image=without_image,
        duplicates=duplicates,
    )

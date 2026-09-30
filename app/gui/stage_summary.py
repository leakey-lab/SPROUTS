"""What the Generate and Measure stages would work on, in one status-line phrase (W12).

The two stages showed the Library canvas and their buttons, and nothing said what a run
would do. The status line's idle text now says it while one of them is on screen:

* Generate: ``40 images · 28 without a mask · 20 without a skeleton``;
* Measure: ``20 skeletons: measured, current · 12 masks: 3 changed since measured``,
  where "changed since measured" counts the inputs written after the metric's CSV.

Qt-free. Counting what changed since a CSV reads one folder (:func:`input_freshness`), so
it runs off the GUI thread; the counts of the other phrases come from the library state.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Container, Iterable
from dataclasses import dataclass

__all__ = ["Freshness", "generate_text", "input_freshness", "measure_text", "missing"]


@dataclass(frozen=True)
class Freshness:
    """A metric's inputs against its CSV: ``inputs`` files to measure, ``changed`` of them
    written after the CSV (``measured`` False: no CSV yet)."""

    inputs: int
    measured: bool
    changed: int = 0


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
    if not inputs:
        return f"no {noun}s to measure"
    head = _count(inputs, noun)
    if freshness is None:
        return head
    if not freshness.measured:
        return f"{head}: not measured yet"
    if not freshness.changed:
        return f"{head}: measured, current"
    return f"{head}: {freshness.changed:,} changed since measured"


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


def input_freshness(folder: str | None, suffix: str, csv_path: str | None) -> Freshness:
    """The ``*suffix`` files of ``folder`` against ``csv_path`` (one ``os.scandir``).

    Hidden files (the ``.<name>.tmp`` of a write in progress) are not inputs. An input is
    changed when it was written after the CSV (mtime). Never raises: an unreadable folder
    has no inputs.
    """
    csv_mtime = None
    if csv_path:
        try:
            csv_mtime = os.stat(csv_path).st_mtime_ns
        except OSError:
            csv_mtime = None
    inputs = changed = 0
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
                        mtime = entry.stat().st_mtime_ns
                    except OSError:
                        continue
                    inputs += 1
                    if csv_mtime is not None and mtime > csv_mtime:
                        changed += 1
        except OSError:
            pass
    return Freshness(inputs=inputs, measured=csv_mtime is not None, changed=changed)

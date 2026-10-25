"""Which input files Measure reads: those whose image is in the project (O37), stdlib only.

Light on purpose (no cv2 / numpy / torch): the status line's text module and the Measure run
both use it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from app.naming import stem_key

__all__ = ["InputSplit", "file_name", "group_by_stem", "split_by_image", "split_inputs"]


class InputSplit(NamedTuple):
    """Input files sorted by their image: ``kept`` (``name -> path``) are measured;
    ``without_image`` (their image is gone) and ``duplicates`` (another file of the same
    image is measured) are the sorted file names that are not."""

    kept: dict[str, str]
    without_image: list[str]
    duplicates: list[str]


def file_name(path: str) -> str:
    return os.path.basename(path)


def group_by_stem(
    files: Iterable[tuple[str, str]],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """``(stem -> path, shadowed)`` of ``files`` (``(stem, path)`` pairs).

    Of several files with one stem (``a.png`` and ``a.PNG`` on a case-sensitive disk) the
    lowest file name is listed (``a.PNG`` before ``a.png``); the others are ``shadowed``,
    for :func:`split_inputs`, so none of them vanishes unrecorded.
    """
    listed: dict[str, str] = {}
    shadowed: list[tuple[str, str]] = []
    for stem, path in files:
        held = listed.get(stem)
        if held is None:
            listed[stem] = path
        elif file_name(path) < file_name(held):
            listed[stem] = path
            shadowed.append((stem, held))
        else:
            shadowed.append((stem, path))
    return listed, shadowed


def split_inputs(
    inputs: Mapping[str, str],
    image_stems: Iterable[str],
    shadowed: Sequence[tuple[str, str]] = (),
) -> InputSplit:
    """Sort ``inputs`` (``name -> path``) by the images of ``image_stems`` (O37).

    Names are compared as images are, without case (:func:`app.naming.stem_key`). An input
    with no image is ``without_image``. On a case-sensitive disk several inputs can share one
    image's key (``A.png`` and ``a.png`` for image ``A``): one is kept, the one named exactly
    like the image, else the first by file name; the others are ``duplicates``. Nothing is
    read or removed: the skipped files stay where they are.

    ``shadowed`` are ``(name, path)`` of files whose name another entry of ``inputs`` has
    already (:func:`group_by_stem`): never kept, a duplicate when the name has an image, else
    without one.
    """
    image_of: dict[str, str] = {}
    for stem in image_stems:
        image_of.setdefault(stem_key(stem), stem)
    groups: dict[str, list[str]] = {}
    for name in inputs:
        groups.setdefault(stem_key(name), []).append(name)
    chosen: set[str] = set()
    for key, names in groups.items():
        if key in image_of:
            exact = image_of[key]
            chosen.add(exact if exact in names else min(names, key=lambda n: file_name(inputs[n])))
    kept: dict[str, str] = {}
    without_image: list[str] = []
    duplicates: list[str] = []
    for name, path in inputs.items():
        if name in chosen:
            kept[name] = path
        elif stem_key(name) in image_of:
            duplicates.append(file_name(path))
        else:
            without_image.append(file_name(path))
    for name, path in shadowed:
        (duplicates if stem_key(name) in image_of else without_image).append(file_name(path))
    return InputSplit(kept, sorted(without_image), sorted(duplicates))


def split_by_image(
    inputs: Mapping[str, str], image_stems: Iterable[str]
) -> tuple[dict[str, str], list[str]]:
    """:func:`split_inputs` as ``(kept, sorted file names of the inputs without an image)``;
    the duplicates are in neither."""
    split = split_inputs(inputs, image_stems)
    return split.kept, split.without_image

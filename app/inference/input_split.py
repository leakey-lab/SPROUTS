"""Which input files Measure reads: those whose image is in the project (O37), stdlib only.

Light on purpose (no cv2 / numpy / torch): the status line's text module and the Measure run
both use it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from app.naming import stem_key

__all__ = [
    "InputSplit",
    "file_name",
    "group_by_stem",
    "images_by_key",
    "png_stem",
    "split_by_image",
    "split_inputs",
]


class InputSplit(NamedTuple):
    """Input files sorted by their image: ``kept`` (``name -> path``) are measured;
    ``without_image`` (their image is gone) and ``duplicates`` (another file of the same
    image is measured) are the sorted file names that are not."""

    kept: dict[str, str]
    without_image: list[str]
    duplicates: list[str]


def file_name(path: str) -> str:
    return os.path.basename(path)


def png_stem(name: str) -> str | None:
    """The stem of a ``*.png`` file name (the extension in any case) as Measure lists masks
    and GT files; ``None`` for any other name and for a stem of dots only (``.png``,
    ``..png``). Hidden names are listed: Measure reads them."""
    dot = name.rfind(".")
    if dot <= 0 or name[dot:].lower() != ".png" or not name[:dot].lstrip("."):
        return None
    return name[:dot]


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


def images_by_key(image_stems: Iterable[str]) -> dict[str, str]:
    """``stem_key -> stem`` of the images of ``image_stems`` (the first stem of each key):
    built once for several :func:`split_inputs` calls on the same images (``by_key``)."""
    image_of: dict[str, str] = {}
    for stem in image_stems:
        image_of.setdefault(stem_key(stem), stem)
    return image_of


def split_inputs(
    inputs: Mapping[str, str],
    image_stems: Iterable[str],
    shadowed: Sequence[tuple[str, str]] = (),
    *,
    by_key: Mapping[str, str] | None = None,
) -> InputSplit:
    """Sort ``inputs`` (``name -> path``) by the images of ``image_stems`` (O37).

    Names are compared as images are, without case (:func:`app.naming.stem_key`). An input
    with no image is ``without_image``. On a case-sensitive disk several inputs can share one
    image's key (``A.png`` and ``a.png`` for image ``A``): one is kept, the one named exactly
    like the image, else the first by file name; the others are ``duplicates``. Nothing is
    read or removed: the skipped files stay where they are.

    ``shadowed`` are ``(name, path)`` of files whose name another entry of ``inputs`` has
    already (:func:`group_by_stem`): never kept, a duplicate when the name has an image, else
    without one. ``by_key``: :func:`images_by_key` of ``image_stems``, when the caller has it.
    """
    if not inputs and not shadowed:
        return InputSplit({}, [], [])
    image_of = images_by_key(image_stems) if by_key is None else by_key
    keys = list(map(stem_key, inputs))
    kept: dict[str, str] = {}
    without_image: list[str] = []
    duplicates: list[str] = []
    if len(set(keys)) == len(keys):  # one input per image key: the usual case
        for (name, path), key in zip(inputs.items(), keys, strict=True):
            if key in image_of:
                kept[name] = path
            else:
                without_image.append(file_name(path))
    else:
        groups: dict[str, list[str]] = {}
        for name, key in zip(inputs, keys, strict=True):
            groups.setdefault(key, []).append(name)
        chosen: set[str] = set()
        for key, names in groups.items():
            if key in image_of:
                exact = image_of[key]
                chosen.add(
                    exact if exact in names else min(names, key=lambda n: file_name(inputs[n]))
                )
        for (name, path), key in zip(inputs.items(), keys, strict=True):
            if name in chosen:
                kept[name] = path
            elif key in image_of:
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

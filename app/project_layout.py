"""
Project folder layout: the one place that turns an image folder into paths.

A SPROUTS project is a folder of images. Every stage writes next to them::

    <root>/<stem>.jpg                               source image (IMAGE_EXTENSIONS)
    <root>/mask/<stem>.png                          mask, generated or traced
    <root>/mask/root_areas.csv                      Measure (area)
    <root>/skeletons/<stem>_fake.png                generated skeleton
    <root>/skeletons/root_lengths.csv               Measure (length)
    <root>/skeleton_corrections/<stem>_skeleton.png corrected skeleton
    <root>/gt/<stem>.png                            ground-truth tracing

Older runs used ``output/mask`` and ``[output/]skeletonizer/test_latest/images``
(plus a ``test_latest`` folder found by a bounded walk); the ``resolve_*`` and
``find_*_csv`` methods keep today's search order for those.

Qt-free and side-effect free: nothing is created on disk, and each ``scan_*``
method is a single ``os.scandir`` of one folder.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TypeVar

from app.config import (
    GT_DIRNAME,
    IMAGE_EXTENSIONS,
    MASK_DIRNAME,
    ROOT_AREAS_CSV,
    ROOT_LENGTHS_CSV,
    SKELETON_CORRECTIONS_DIRNAME,
    SKELETONS_DIRNAME,
)
from app.naming import image_stem

logger = logging.getLogger(__name__)

__all__ = [
    "CORRECTED_SKELETON_SUFFIX",
    "MASK_EXTENSIONS",
    "SKELETON_SUFFIX",
    "ProjectLayout",
    "find_test_latest_dir",
]

#: File-name suffix of a generated skeleton (skeleton_inference output).
SKELETON_SUFFIX = "_fake.png"
#: File-name suffix of a skeleton saved by the skeleton-correction editor.
CORRECTED_SKELETON_SUFFIX = "_skeleton.png"
#: Mask file types the app reads, in lookup priority. Masks are written as .png.
MASK_EXTENSIONS = (".png", ".jpg", ".jpeg")

# Mask lookup order of the mask editor (find_mask_path); the case variants
# matter on case-sensitive file systems.
_MASK_LOOKUP = tuple(v for ext in MASK_EXTENSIONS for v in (ext, ext.upper()))

_K = TypeVar("_K")

_TEST_LATEST = "test_latest"
_LEGACY_MASK_DIR = ("output", MASK_DIRNAME)
_LEGACY_SKELETON_DIRS = (
    ("output", "skeletonizer", _TEST_LATEST, "images"),
    ("skeletonizer", _TEST_LATEST, "images"),
)


def _scan(directory: str, key_of: Callable[[str], _K | None]) -> Iterator[tuple[_K, str]]:
    """Yield ``(key, path)`` for files in ``directory`` whose name ``key_of`` accepts.

    One ``os.scandir``; a missing folder yields nothing.
    """
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                key = key_of(entry.name)
                if key is not None and entry.is_file():
                    yield key, entry.path
    except (FileNotFoundError, NotADirectoryError):
        return


def _suffix_stem(suffix: str) -> Callable[[str], str | None]:
    def stem_of(name: str) -> str | None:
        return name[: -len(suffix)] if name.endswith(suffix) else None

    return stem_of


def _scan_ranked(directory: str, extensions: tuple[str, ...]) -> dict[str, str]:
    """``stem -> path`` for files with ``extensions``; earlier extensions win a stem clash."""
    rank = {ext.lower(): i for i, ext in enumerate(extensions)}

    def ranked_stem(name: str) -> tuple[str, int] | None:
        stem, ext = os.path.splitext(name)
        r = rank.get(ext.lower())
        return None if r is None else (stem, r)

    best: dict[str, tuple[int, str]] = {}
    for (stem, r), path in _scan(directory, ranked_stem):
        if stem not in best or r < best[stem][0]:
            best[stem] = (r, path)
    return {stem: path for stem, (_, path) in best.items()}


@dataclass(frozen=True)
class ProjectLayout:
    """Paths of one project folder (``root`` holds the images).

    Methods that take a ``stem`` also accept an image path or file name.
    """

    root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", os.path.normpath(os.fspath(self.root)))

    @classmethod
    def for_image(cls, image_path: str | os.PathLike[str]) -> ProjectLayout:
        """Layout of the folder that contains ``image_path``."""
        return cls(os.path.dirname(os.fspath(image_path)))

    @staticmethod
    def stem(path_or_name: str | os.PathLike[str]) -> str:
        """File name without its image extension (see :func:`app.naming.image_stem`)."""
        return image_stem(path_or_name)

    def _path(self, *parts: str) -> str:
        return os.path.normpath(os.path.join(self.root, *parts))

    # ------------------------------------------------------------------ folders
    @property
    def mask_dir(self) -> str:
        return self._path(MASK_DIRNAME)

    @property
    def skeletons_dir(self) -> str:
        return self._path(SKELETONS_DIRNAME)

    @property
    def corrections_dir(self) -> str:
        return self._path(SKELETON_CORRECTIONS_DIRNAME)

    @property
    def gt_dir(self) -> str:
        return self._path(GT_DIRNAME)

    @property
    def mask_dir_candidates(self) -> tuple[str, str]:
        """Mask folders in Measure-area priority: legacy ``output/mask`` first, as today."""
        return self._path(*_LEGACY_MASK_DIR), self.mask_dir

    @property
    def skeleton_dir_candidates(self) -> tuple[str, ...]:
        """Skeleton folders in Measure-length priority (``skeletons/``, then legacy)."""
        return (self.skeletons_dir, *(self._path(*parts) for parts in _LEGACY_SKELETON_DIRS))

    # ------------------------------------------------------------------ images
    def scan_images(self) -> dict[str, str]:
        """``stem -> path`` of the source images in ``root`` (one scandir)."""
        return _scan_ranked(self.root, IMAGE_EXTENSIONS)

    # ------------------------------------------------------------------ masks
    def mask_path(self, stem: str) -> str:
        """Canonical save path ``mask/<stem>.png`` (what the mask editor writes)."""
        return self._path(MASK_DIRNAME, self.stem(stem) + ".png")

    def mask_candidates(self, stem: str) -> tuple[str, ...]:
        """Every file name a mask of ``stem`` may have, in lookup order (``.png`` first)."""
        stem = self.stem(stem)
        return tuple(self._path(MASK_DIRNAME, stem + ext) for ext in _MASK_LOOKUP)

    def find_mask(self, stem: str) -> str | None:
        """Existing mask file for ``stem`` (``.png`` first, then legacy types), else None."""
        return next((p for p in self.mask_candidates(stem) if os.path.isfile(p)), None)

    def scan_masks(
        self, directory: str | None = None, *, extensions: tuple[str, ...] = MASK_EXTENSIONS
    ) -> dict[str, str]:
        """``stem -> path`` of the masks in ``directory`` (default ``mask/``).

        A ``.png`` wins over a ``.jpg``/``.jpeg`` with the same stem. Pass
        ``extensions=(".png",)`` to keep only PNG masks (what Measure reads).
        """
        return _scan_ranked(directory or self.mask_dir, extensions)

    def scan_mask_stems(self) -> set[str]:
        """Stems that have a mask in ``mask/`` (one scandir)."""
        return set(self.scan_masks())

    def resolve_masks_dir(self) -> str | None:
        """First existing of :attr:`mask_dir_candidates` (the folder Measure reads)."""
        return next((d for d in self.mask_dir_candidates if os.path.isdir(d)), None)

    # ------------------------------------------------------------------ skeletons
    def skeleton_path(self, stem: str) -> str:
        """Generated skeleton ``skeletons/<stem>_fake.png``."""
        return self._path(SKELETONS_DIRNAME, self.stem(stem) + SKELETON_SUFFIX)

    def find_skeleton(self, stem: str) -> str | None:
        path = self.skeleton_path(stem)
        return path if os.path.isfile(path) else None

    def scan_skeletons(self, directory: str | None = None) -> dict[str, str]:
        """``stem -> path`` of ``*_fake.png`` in ``directory`` (default ``skeletons/``)."""
        return dict(_scan(directory or self.skeletons_dir, _suffix_stem(SKELETON_SUFFIX)))

    def scan_skeleton_stems(self) -> set[str]:
        return set(self.scan_skeletons())

    def resolve_skeletons_dir(self, search_test_latest: bool = True) -> str | None:
        """Folder Measure-length reads: ``skeletons/``, then the legacy layouts.

        With ``search_test_latest`` a bounded walk for a ``test_latest`` folder
        runs only when none of :attr:`skeleton_dir_candidates` exists.
        """
        for folder in self.skeleton_dir_candidates:
            if os.path.isdir(folder):
                return folder
        if search_test_latest:
            found = find_test_latest_dir(self.root)
            if found and os.path.isdir(os.path.join(found, "images")):
                return os.path.join(found, "images")
        return None

    # ------------------------------------------------------------------ corrections
    def corrected_skeleton_path(self, stem: str) -> str:
        """Skeleton-editor save path ``skeleton_corrections/<stem>_skeleton.png``."""
        return self._path(SKELETON_CORRECTIONS_DIRNAME, self.stem(stem) + CORRECTED_SKELETON_SUFFIX)

    def find_corrected_skeleton(self, stem: str) -> str | None:
        path = self.corrected_skeleton_path(stem)
        return path if os.path.isfile(path) else None

    def scan_corrected_skeletons(self) -> dict[str, str]:
        return dict(_scan(self.corrections_dir, _suffix_stem(CORRECTED_SKELETON_SUFFIX)))

    def scan_corrected_stems(self) -> set[str]:
        return set(self.scan_corrected_skeletons())

    # ------------------------------------------------------------------ GT
    def gt_path(self, stem: str) -> str:
        """Ground-truth tracing ``gt/<stem>.png`` (same as ``app.gt.store.gt_path``)."""
        return self._path(GT_DIRNAME, self.stem(stem) + ".png")

    # ------------------------------------------------------------------ metric CSVs
    def _lengths_csv_candidates(self, search_test_latest: bool) -> Iterator[str]:
        yield self._path(SKELETONS_DIRNAME, ROOT_LENGTHS_CSV)
        yield self._path(ROOT_LENGTHS_CSV)
        if search_test_latest:
            for parts in _LEGACY_SKELETON_DIRS:  # where Measure writes for legacy layouts
                yield self._path(*parts, ROOT_LENGTHS_CSV)
            found = find_test_latest_dir(self.root)
            if found:
                yield os.path.join(found, ROOT_LENGTHS_CSV)

    def find_lengths_csv(self, search_test_latest: bool = True) -> str | None:
        """Existing ``root_lengths.csv``: ``skeletons/``, the project folder, then legacy.

        The ``test_latest`` walk only runs when no primary location has the file.
        """
        return next(
            (p for p in self._lengths_csv_candidates(search_test_latest) if os.path.isfile(p)),
            None,
        )

    def find_areas_csv(self) -> str | None:
        """Existing ``root_areas.csv`` in :meth:`resolve_masks_dir`, else None."""
        masks_dir = self.resolve_masks_dir()
        if masks_dir is None:
            return None
        path = os.path.join(masks_dir, ROOT_AREAS_CSV)
        return path if os.path.isfile(path) else None


def find_test_latest_dir(start_path: str, max_depth: int = 3) -> str | None:
    """Find a legacy ``test_latest`` folder near ``start_path``.

    Searches down ``max_depth`` levels (likely names such as ``output`` first),
    then walks up ``max_depth`` parents looking at promising siblings. Moved
    unchanged from ``app.gui.visualization_manager`` so the writer and every
    reader share one implementation; unreadable folders are skipped.
    """
    start_path = os.path.abspath(start_path)
    priority = ("output", "results", "test", "latest", _TEST_LATEST)

    def search_directory(root_path: str, depth: int = 0) -> str | None:
        if depth > max_depth:
            return None
        if os.path.basename(root_path) == _TEST_LATEST:
            return root_path
        try:
            subdirs: list[str] = []
            with os.scandir(root_path) as entries:
                for entry in entries:
                    if entry.is_dir():
                        if entry.name.lower() in priority:
                            subdirs.insert(0, entry.path)
                        else:
                            subdirs.append(entry.path)
            for subdir in subdirs:
                result = search_directory(subdir, depth + 1)
                if result:
                    return result
        except OSError as exc:
            logger.debug("Skipping directory %s during search: %s", root_path, exc)
        return None

    def search_up(path: str, depth: int = 0) -> str | None:
        if depth > max_depth:
            return None
        try:
            if os.path.basename(path) == _TEST_LATEST:
                return path
            parent = os.path.dirname(path)
            if parent == path:
                return None
            with os.scandir(parent) as entries:
                for entry in entries:
                    if entry.is_dir() and entry.path != path:
                        if entry.name == _TEST_LATEST:
                            return entry.path
                        if entry.name.lower() in priority[:-1]:
                            sibling_result = search_directory(entry.path, 0)
                            if sibling_result:
                                return sibling_result
            return search_up(parent, depth + 1)
        except OSError as exc:
            logger.debug("Skipping parent %s during search: %s", path, exc)
        return None

    return search_directory(start_path) or search_up(start_path)

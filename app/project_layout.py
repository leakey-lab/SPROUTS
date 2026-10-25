"""
Project folder layout: the one place that turns an image folder into paths.

A SPROUTS project is a folder of images. Every stage writes next to them::

    <root>/<stem>.jpg                               source image (IMAGE_EXTENSIONS)
    <root>/mask/<stem>.png                          mask, generated or traced
    <root>/mask/root_areas.csv                      Measure (area)
    <root>/skeletons/<stem>_fake.png                generated skeleton
    <root>/skeletons/root_lengths.csv               Measure (length)
    <root>/gt/<stem>.png                            GT class labels
    <root>/gt/mask/<stem>.png                       GT mask (a saved edit)
    <root>/gt/skeletons/<stem>.png                  GT skeleton (a saved edit)

Older runs used ``output/mask`` and ``[output/]skeletonizer/test_latest/images``
(plus a ``test_latest`` folder found by a bounded walk); the ``resolve_*`` and
``find_*_csv`` methods keep today's search order for those.

Qt-free and side-effect free: nothing is created on disk, and each ``scan_*``
method is a single ``os.scandir`` of one folder.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

from app.config import (
    GT_CLASSES_FILENAME,
    GT_DIRNAME,
    IMAGE_EXTENSIONS,
    MASK_DIRNAME,
    ROOT_AREAS_CSV,
    ROOT_LENGTHS_CSV,
    SKELETONS_DIRNAME,
)
from app.naming import LEGACY_IMAGE_EXTENSIONS, image_stem, stem_key

logger = logging.getLogger(__name__)

__all__ = [
    "MASK_EXTENSIONS",
    "SKELETON_SUFFIX",
    "GtStems",
    "ImageScan",
    "ProjectLayout",
    "find_by_stem",
    "find_test_latest_dir",
    "key_to_stems",
]

#: File-name suffix of a generated skeleton (skeleton_inference output).
SKELETON_SUFFIX = "_fake.png"
#: Mask file types the app reads, in lookup priority: the image formats, as masks use
#: the same scan (one mask per stem without case). Masks are written as .png.
MASK_EXTENSIONS = IMAGE_EXTENSIONS

# Mask lookup order of the mask editor (find_mask_path); the case variants
# matter on case-sensitive file systems.
_MASK_LOOKUP = tuple(v for ext in MASK_EXTENSIONS for v in (ext, ext.upper()))

_K = TypeVar("_K")
_V = TypeVar("_V")

_TEST_LATEST = "test_latest"
_LEGACY_MASK_DIR = ("output", MASK_DIRNAME)
_LEGACY_SKELETON_DIRS = (
    ("output", "skeletonizer", _TEST_LATEST, "images"),
    ("skeletonizer", _TEST_LATEST, "images"),
)
# Before R8 the GT store and registry wrote "<path>.tmp" next to gt/<stem>.png and
# gt/classes.json; a killed save left that file in gt/ for good.
_LEGACY_GT_TEMP_SUFFIX = ".tmp"


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


@dataclass
class _Skips:
    """What a ranked scan did not list: ``(key, file name)`` of each file that lost its
    stem to another, and the names with a :data:`LEGACY_IMAGE_EXTENSIONS` suffix."""

    losers: list[tuple[str, str]] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def _scan_ranked(
    directory: str, extensions: tuple[str, ...], skips: _Skips | None = None
) -> dict[str, str]:
    """``stem -> path`` for files with ``extensions``, one file per stem without case.

    Stems that differ only in case are one stem (:func:`app.naming.stem_key`, O7): the
    lowest ``(extension rank, file name)`` wins (earlier ``extensions`` first, then the
    name that sorts first) and the result keeps the winner's real stem. With ``skips``
    the same listing also collects the losers and the unsupported image files.

    One ``os.scandir`` in a single loop (R6: 85 -> 59 ms for 46k files). The stem
    and extension follow ``os.path.splitext``: the last dot splits, unless only
    dots come before it (``.png`` and ``..png`` have no extension).
    """
    rank = {ext.lower(): i for i, ext in enumerate(extensions)}
    best: dict[str, tuple[int, str, str, str]] = {}  # key -> (rank, name, stem, path)
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                dot = name.rfind(".")
                if dot <= 0:
                    continue
                r = rank.get(name[dot:].lower())
                if r is None:
                    if (
                        skips is not None
                        and name[dot:].lower() in LEGACY_IMAGE_EXTENSIONS
                        and name[:dot].lstrip(".")
                        and entry.is_file()
                    ):
                        skips.unsupported.append(name)
                    continue
                if not name[:dot].lstrip(".") or not entry.is_file():
                    continue
                stem = name[:dot]
                key = stem_key(stem)
                held = best.get(key)
                if held is None or (r, name) < held[:2]:
                    best[key] = (r, name, stem, entry.path)
                    if held is not None and skips is not None:
                        skips.losers.append((key, held[1]))
                elif skips is not None:
                    skips.losers.append((key, name))
    except (FileNotFoundError, NotADirectoryError):
        return {}
    return {stem: path for _, _, stem, path in best.values()}


# Folders known to ignore case (one probe each): a lookup that misses there lists nothing.
_CASE_FOLDING_DIRS: dict[str, bool] = {}


def _ignores_case(directory: str) -> bool:
    """True when ``directory``'s file system ignores case (Windows, macOS by default).

    Probed once per folder: its own name with the case of every letter swapped names the
    same folder. A folder whose name has no letters counts as case-sensitive.
    """
    directory = os.path.normpath(directory)  # a trailing separator would hide its name
    known = _CASE_FOLDING_DIRS.get(directory)
    if known is not None:
        return known
    head, name = os.path.split(directory)
    swapped = os.path.join(head, name.swapcase())
    try:
        here = os.stat(directory)
    except OSError:
        return False  # missing: nothing to find, and nothing worth remembering
    try:
        folds = swapped != directory and os.path.samestat(here, os.stat(swapped))
    except OSError:
        folds = False
    _CASE_FOLDING_DIRS[directory] = folds
    return folds


def _find_without_case(
    directory: str, stem: str, endings: tuple[str, ...], *, fold_endings: bool
) -> str | None:
    """A file of ``directory`` named ``<stem'><ending>`` where ``stem'`` has ``stem``'s
    :func:`~app.naming.stem_key`; the lowest ``(ending rank, file name)`` wins, as in the
    ranked scan. ``fold_endings`` matches the endings without case too (extensions).

    One ``os.scandir``, skipped where the file system ignores case: there the exact-name
    lookup has already found any case variant.
    """
    if _ignores_case(directory):
        return None
    key = stem_key(stem)
    rank = {(e.lower() if fold_endings else e): i for i, e in enumerate(endings)}
    best: tuple[int, str, str] | None = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                for ending, r in rank.items():
                    tail = name[-len(ending) :]
                    if (tail.lower() if fold_endings else tail) != ending:
                        continue
                    if stem_key(name[: -len(ending)]) != key or not entry.is_file():
                        continue
                    if best is None or (r, name) < best[:2]:
                        best = (r, name, entry.path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return best[2] if best is not None else None


def find_by_stem(directory: str, stem: str, suffix: str) -> str | None:
    """The file ``<directory>/<stem><suffix>``, else one whose stem differs only in case
    (O7; the exact name wins); None when there is neither."""
    path = os.path.join(directory, stem + suffix)
    if os.path.isfile(path):
        return path
    return _find_without_case(directory, stem, (suffix,), fold_endings=False)


def key_to_stems(found: Mapping[str, _V], stems: Iterable[str]) -> dict[str, _V]:
    """``found`` with each key replaced by the stem of ``stems`` with the same
    :func:`~app.naming.stem_key` (``{"a": m}`` with stems ``["A"]`` -> ``{"A": m}``).

    Keys without such a stem are kept as they are. A mask or skeleton whose name differs
    from its image's only in case is then found under the image's stem (O7).
    """
    by_key = {stem_key(stem): stem for stem in stems}
    return {by_key.get(stem_key(key), key): value for key, value in found.items()}


@dataclass(frozen=True)
class ImageScan:
    """One listing of an image folder: what the Library shows, and what it does not.

    ``images`` is ``stem -> path`` (as :meth:`ProjectLayout.scan_images`); ``hidden``
    holds ``(file not listed, file listed instead)`` for each file whose stem another
    file took (sorted); ``unsupported`` the file names with a
    :data:`~app.naming.LEGACY_IMAGE_EXTENSIONS` suffix, in any case (sorted).
    """

    images: dict[str, str]
    hidden: tuple[tuple[str, str], ...] = ()
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True)
class GtStems:
    """The stems with GT, one set per kind (:meth:`ProjectLayout.scan_gt_stems`).

    Each set holds the stems as the scans list them, the winners' real stems (not their
    :func:`~app.naming.stem_key`): one per name without case (O7).
    """

    mask: frozenset[str]
    skeleton: frozenset[str]
    labels: frozenset[str]


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
    def gt_dir(self) -> str:
        return self._path(GT_DIRNAME)

    @property
    def gt_mask_dir(self) -> str:
        """``gt/mask/``: the GT of ``mask/``, same ``<stem>.png`` names."""
        return self._path(GT_DIRNAME, MASK_DIRNAME)

    @property
    def gt_skeletons_dir(self) -> str:
        """``gt/skeletons/``: the GT of ``skeletons/``, ``<stem>.png`` (no ``_fake``)."""
        return self._path(GT_DIRNAME, SKELETONS_DIRNAME)

    @property
    def mask_dir_candidates(self) -> tuple[str, str]:
        """Mask folders in Measure-area priority: legacy ``output/mask`` first, as today."""
        return self._path(*_LEGACY_MASK_DIR), self.mask_dir

    @property
    def skeleton_dir_candidates(self) -> tuple[str, ...]:
        """Skeleton folders in Measure-length priority (``skeletons/``, then legacy)."""
        return (self.skeletons_dir, *(self._path(*parts) for parts in _LEGACY_SKELETON_DIRS))

    @property
    def output_dirs(self) -> tuple[str, ...]:
        """Every folder the app writes into: where killed writes leave temp files (R8).

        The mask and skeleton candidates (generation writes there, and Measure writes
        its CSV and ``.provenance.json`` into the folder it measured), ``gt/``, ``gt/mask/``
        and ``gt/skeletons/``. Not the image folder (nothing writes there) and no legacy
        ``test_latest`` walk: nothing is read from disk.
        """
        return (
            *self.mask_dir_candidates,
            *self.skeleton_dir_candidates,
            self.gt_dir,
            self.gt_mask_dir,
            self.gt_skeletons_dir,
        )

    @staticmethod
    def is_legacy_gt_temp(name: str) -> bool:
        """True for ``classes.json.tmp`` / ``<stem>.png.tmp``: what a killed GT save left
        in :attr:`gt_dir` before R8, when the GT writers used ``<path>.tmp``."""
        if not name.endswith(_LEGACY_GT_TEMP_SUFFIX):
            return False
        target = name[: -len(_LEGACY_GT_TEMP_SUFFIX)]
        return target == GT_CLASSES_FILENAME or target.endswith(".png")

    # ------------------------------------------------------------------ images
    def scan_images(self) -> dict[str, str]:
        """``stem -> path`` of the source images in ``root`` (one scandir).

        PNG and JPEG only (``IMAGE_EXTENSIONS``, any letter case; O6). One file per
        stem without case (O7): when two share it (``a.png`` and ``A.jpg``), ``.png``
        wins over ``.jpg`` over ``.jpeg``, then the name that sorts first. That is the
        image the Library shows, so mask and skeleton generation read it too (R7).
        """
        return _scan_ranked(self.root, IMAGE_EXTENSIONS)

    def scan_image_folder(self) -> ImageScan:
        """:meth:`scan_images` plus the files it does not list (the same one scandir)."""
        skips = _Skips()
        images = _scan_ranked(self.root, IMAGE_EXTENSIONS, skips)
        winners = {stem_key(stem): os.path.basename(path) for stem, path in images.items()}
        return ImageScan(
            images=images,
            hidden=tuple(sorted((name, winners[key]) for key, name in skips.losers)),
            unsupported=tuple(sorted(skips.unsupported)),
        )

    # ------------------------------------------------------------------ masks
    def mask_path(self, stem: str) -> str:
        """Canonical save path ``mask/<stem>.png`` (what the mask editor writes)."""
        return self._path(MASK_DIRNAME, self.stem(stem) + ".png")

    def mask_candidates(self, stem: str) -> tuple[str, ...]:
        """Every file name a mask of ``stem`` may have, in lookup order (``.png`` first)."""
        stem = self.stem(stem)
        return tuple(self._path(MASK_DIRNAME, stem + ext) for ext in _MASK_LOOKUP)

    def find_mask(self, stem: str) -> str | None:
        """Existing mask file for ``stem`` (``.png`` first, then ``.jpg`` / ``.jpeg``), else
        None. A file named exactly ``<stem>.<ext>`` wins; else one whose stem differs only
        in case (O7), ranked as :meth:`scan_masks` ranks them."""
        found = next((p for p in self.mask_candidates(stem) if os.path.isfile(p)), None)
        if found is None:
            found = _find_without_case(
                self.mask_dir, self.stem(stem), MASK_EXTENSIONS, fold_endings=True
            )
        return found

    def scan_masks(
        self, directory: str | None = None, *, extensions: tuple[str, ...] = MASK_EXTENSIONS
    ) -> dict[str, str]:
        """``stem -> path`` of the masks in ``directory`` (default ``mask/``).

        The same scan as the images: one mask per stem without case, a ``.png`` wins
        over a ``.jpg``/``.jpeg``. Pass ``extensions=(".png",)`` to keep only PNG masks
        (what Measure reads).
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
        """Existing ``skeletons/<stem>_fake.png``, its stem compared without case (O7)."""
        return find_by_stem(self.skeletons_dir, self.stem(stem), SKELETON_SUFFIX)

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

    # ------------------------------------------------------------------ GT
    def gt_path(self, stem: str) -> str:
        """GT class labels ``gt/<stem>.png`` (same as ``app.gt.store.gt_path``)."""
        return self._path(GT_DIRNAME, self.stem(stem) + ".png")

    def gt_mask_path(self, stem: str) -> str:
        """GT mask ``gt/mask/<stem>.png`` (what a saved mask edit writes)."""
        return self._path(GT_DIRNAME, MASK_DIRNAME, self.stem(stem) + ".png")

    def gt_skeleton_path(self, stem: str) -> str:
        """GT skeleton ``gt/skeletons/<stem>.png`` (what a saved skeleton edit writes)."""
        return self._path(GT_DIRNAME, SKELETONS_DIRNAME, self.stem(stem) + ".png")

    def _find_gt_png(self, directory: str, stem: str) -> str | None:
        """``<directory>/<stem>.png`` when it exists, else a ``.png`` file (any case of the
        extension) whose stem differs only in case: what :func:`_scan_ranked` lists."""
        stem = self.stem(stem)
        path = os.path.join(directory, stem + ".png")
        if os.path.isfile(path):
            return path
        return _find_without_case(directory, stem, (".png",), fold_endings=True)

    def find_gt_mask(self, stem: str) -> str | None:
        """Existing ``gt/mask/<stem>.png``, found as :meth:`scan_gt_masks` lists it (O7)."""
        return self._find_gt_png(self.gt_mask_dir, stem)

    def find_gt_skeleton(self, stem: str) -> str | None:
        """Existing ``gt/skeletons/<stem>.png``, found as :meth:`scan_gt_skeletons` lists
        it (O7)."""
        return self._find_gt_png(self.gt_skeletons_dir, stem)

    def find_gt_labels(self, stem: str) -> str | None:
        """Existing ``gt/<stem>.png``, found as :meth:`scan_gt_labels` lists it (O7)."""
        return self._find_gt_png(self.gt_dir, stem)

    def scan_gt_masks(self) -> dict[str, str]:
        """``stem -> path`` of ``gt/mask/*.png``: one file per stem without case."""
        return _scan_ranked(self.gt_mask_dir, (".png",))

    def scan_gt_skeletons(self) -> dict[str, str]:
        """``stem -> path`` of ``gt/skeletons/*.png``: one file per stem without case."""
        return _scan_ranked(self.gt_skeletons_dir, (".png",))

    def scan_gt_labels(self) -> dict[str, str]:
        """``stem -> path`` of ``gt/*.png`` (files only: ``classes.json`` and the two
        folders under ``gt/`` are not labels)."""
        return _scan_ranked(self.gt_dir, (".png",))

    def scan_gt_stems(self) -> GtStems:
        """The stems with GT of each kind: three scans, one scandir each."""
        return GtStems(
            mask=frozenset(self.scan_gt_masks()),
            skeleton=frozenset(self.scan_gt_skeletons()),
            labels=frozenset(self.scan_gt_labels()),
        )

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

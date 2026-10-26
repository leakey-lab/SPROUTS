"""The class-label files of one image, as the class-label document reads them (Qt-free).

An image's class labels are ``gt/<stem>.png`` (found with its stem compared without case,
else where a save writes it) with the project's registry ``gt/classes.json``, through
:mod:`app.gt` only. :func:`read_classes` reads both on any thread and aligns the file's ids to
the registry, as :class:`~app.gui.correct.classes_document.ClassesDocument` opens them;
:func:`file_stamp` pins the files it read. It is to the class labels what
:mod:`app.gui.correct.mask_files` is to the mask.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass

from app import gt
from app.gt import Registry, Tracing
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

__all__ = ["ClassesRead", "file_stamp", "labels_files", "read_classes"]


@dataclass(frozen=True)
class ClassesRead:
    """What :func:`read_classes` read: ``stamps`` taken first, ``registry`` (the defaults
    when missing or invalid: ``registry_error`` says why), ``tracing`` aligned to it (None: no
    usable file), ``warnings`` (classes sent to Ignore), ``changed`` (an id was remapped),
    ``error`` (why the file was not loaded: "unreadable" / "size", else "")."""

    image_path: str
    size: tuple[int, int]
    stamps: tuple
    registry: Registry
    registry_error: str
    tracing: Tracing | None
    warnings: tuple[str, ...]
    changed: bool
    error: str

    @property
    def nbytes(self) -> int:
        """Bytes held (the read-ahead cache counts them)."""
        return 0 if self.tracing is None else int(self.tracing.ids.nbytes)


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a regular file, else ``(path, None)``."""
    try:
        st = os.stat(path)
    except OSError:
        return (path, None)
    return (path, st.st_mtime_ns, st.st_size) if stat.S_ISREG(st.st_mode) else (path, None)


def labels_files(image_path: str) -> tuple[ProjectLayout, str, str]:
    """``(layout, gt/<stem>.png as found or to write, gt/classes.json)`` of ``image_path``."""
    layout = ProjectLayout.for_image(image_path)
    labels = layout.find_gt_labels(image_path) or layout.gt_path(image_path)
    return layout, labels, gt.registry_path(layout.root)


def read_classes(image_path: str, size: tuple[int, int]) -> ClassesRead:
    """Read the registry and the labels of ``image_path`` for an image of ``size`` ``(w, h)``
    and align them (any thread: touches no document)."""
    layout, labels, registry_file = labels_files(image_path)
    stamps = (file_stamp(labels), file_stamp(registry_file))
    try:
        registry, registry_error = gt.load_registry(layout.root), ""
    except gt.RegistryError as exc:  # what is wrong, without the "Could not read" wrapper
        registry, registry_error = gt.default_registry(), str(exc.__cause__ or exc)
    common = (image_path, tuple(size), stamps, registry, registry_error)
    try:
        tracing = gt.read_tracing(labels)
    except gt.GtFileError as exc:
        logger.warning("Could not read the class labels %s: %s", labels, exc)
        return ClassesRead(*common, None, (), False, "unreadable")
    if tracing is None:
        return ClassesRead(*common, None, (), False, "")
    if tracing.ids.shape != (size[1], size[0]):
        logger.warning("Class labels %s are not %sx%s: not shown", labels, *size)
        return ClassesRead(*common, None, (), False, "size")
    tracing, warnings, changed = gt.align_to_registry(tracing, registry)
    return ClassesRead(*common, tracing, tuple(warnings), changed, "")

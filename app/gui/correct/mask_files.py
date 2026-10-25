"""The mask files of one image, as the Correct page's mask document reads them (Qt-free).

An image's mask is its GT mask ``gt/mask/<stem>.png`` when that file exists (``source``
"gt"), else the model's prediction ``mask/<stem>.png`` (legacy ``.jpg`` / ``.jpeg`` names
too; ``source`` "prediction"), else nothing (``source`` None). :func:`read_mask` reads the
chosen file and the prediction (kept for "Load prediction" and the save rule of
:class:`~app.gui.correct.mask_document.MaskDocument`) on any thread, as
:class:`MaskRead`; :func:`mask_stamps` pins the files it read. A file of another size than
the photo, or one that cannot be decoded, is not loaded (``not_loaded``).
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass

import cv2
import numpy as np

from app.io_utils import imread_unicode
from app.masks import MASK_THRESHOLD, MaskFileError, MaskStore
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_GT",
    "SOURCE_PREDICTION",
    "MaskRead",
    "file_stamp",
    "gt_mask_of",
    "mask_stamps",
    "read_mask",
    "zero_one_plane",
]

#: Where the open mask comes from (the values of ``app.gt.files``; the mask document never
#: imports ``app.gt``: each document reaches only its own files).
SOURCE_GT = "gt"
SOURCE_PREDICTION = "prediction"


@dataclass(frozen=True)
class MaskRead:
    """The mask of one image, read ahead on a worker thread (:func:`read_mask`).

    ``stamps`` are the mask files' stamps taken before the read (:func:`mask_stamps`: the
    GT mask, then the prediction's names); ``plane`` is the 0/1 plane of the chosen file
    at ``size`` (None: no usable file); ``not_loaded``: why the chosen file was not
    loaded, ``"size"`` (another size) or ``"unreadable"`` (undecodable), else ``""`` (the
    open warns about it). ``source``: ``"gt"``, ``"prediction"`` or None (no file);
    ``prediction_plane``: the prediction as opened (None: none usable); ``gt_path``: the
    GT file a save writes; ``prediction_path``: the prediction file found, else None.
    """

    image_path: str
    size: tuple[int, int]
    stamps: tuple
    plane: np.ndarray | None
    has_saved_file: bool
    not_loaded: str
    source: str | None = None
    prediction_plane: np.ndarray | None = None
    gt_path: str = ""
    prediction_path: str | None = None

    @property
    def nbytes(self) -> int:
        """Bytes held (the read-ahead cache counts them; one array held twice counts once)."""
        arrays = {id(a): a for a in (self.plane, self.prediction_plane) if a is not None}
        return sum(int(a.nbytes) for a in arrays.values())


def read_mask(image_path: str, size: tuple[int, int], *, gt_path: str | None = None) -> MaskRead:
    """Read the mask of ``image_path`` for an image of ``size`` ``(w, h)`` (any thread): the
    GT mask when it exists, else the prediction; the prediction is read either way.
    ``gt_path``: the GT mask path already looked up (:func:`gt_mask_of`), else looked up."""
    layout = ProjectLayout.for_image(image_path)
    gt_path = gt_path or gt_mask_of(layout, image_path)
    stamps = mask_stamps(layout, gt_path, image_path)
    has_gt = stamps[0][1:] != (None,)  # a GT mask exists: it is the one shown, or not shown
    store = MaskStore(layout)
    found, prediction, why = _read_prediction(store, image_path, size, quiet=has_gt)
    common = {"prediction_plane": prediction, "gt_path": gt_path, "prediction_path": found}
    if has_gt:
        plane, gt_why = _read_gt(gt_path, size)
        has_file = plane is not None
        return MaskRead(image_path, size, stamps, plane, has_file, gt_why, SOURCE_GT, **common)
    source = SOURCE_PREDICTION if found is not None else None
    has_file = prediction is not None
    return MaskRead(image_path, size, stamps, prediction, has_file, why, source, **common)


def gt_mask_of(layout: ProjectLayout, image_path: str) -> str:
    """The GT mask of ``image_path``: the existing file (its stem compared without case,
    O7), else ``gt/mask/<stem>.png``."""
    return layout.find_gt_mask(image_path) or layout.gt_mask_path(image_path)


def mask_stamps(layout: ProjectLayout, gt_path: str, image_path: str) -> tuple:
    """The GT mask's stamp, then those of every name the prediction may have, in lookup
    order (:func:`file_stamp` each)."""
    candidates = layout.mask_candidates(image_path)
    return (file_stamp(gt_path), *(file_stamp(path) for path in candidates))


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a regular file, else ``(path, None)``."""
    try:
        st = os.stat(path)
    except OSError:
        return (path, None)
    if not stat.S_ISREG(st.st_mode):
        return (path, None)
    return (path, st.st_mtime_ns, st.st_size)


def zero_one_plane(on: np.ndarray) -> np.ndarray:
    """The ``uint8`` 0/1 plane of the boolean mask ``on`` (a fresh array).

    A contiguous boolean array already holds bytes 0 and 1: its ``uint8`` view is the plane,
    with no second array to fill (1.7 ms at 2048 x 1536). Anything else is converted.
    """
    if on.dtype == np.bool_ and on.ndim == 2 and on.flags.c_contiguous and on.flags.writeable:
        return on.view(np.uint8)
    return np.asarray(on, dtype=bool).astype(np.uint8)


def _read_prediction(
    store: MaskStore, image_path: str, size: tuple[int, int], *, quiet: bool = False
) -> tuple[str | None, np.ndarray | None, str]:
    """``(path found, plane at size, why not loaded)`` of the prediction of ``image_path``.

    ``quiet``: a GT mask is shown instead, so a prediction of another size or one that
    cannot be read is logged at debug level only (it would be logged on every open)."""
    path = store.find(image_path)
    if path is None:
        return None, None, ""
    try:
        on = store.read(image_path)
    except MaskFileError as exc:
        level = logging.DEBUG if quiet else logging.WARNING
        logger.log(level, "Failed to load existing mask: %s", exc)
        return path, None, "unreadable"
    if on is None:  # gone since the lookup
        return None, None, ""
    if _wrong_size(on, size, image_path, level=logging.DEBUG if quiet else logging.WARNING):
        return path, None, "size"
    return path, zero_one_plane(on), ""


def _read_gt(path: str, size: tuple[int, int]) -> tuple[np.ndarray | None, str]:
    """``(plane at size, why not loaded)`` of the GT mask at ``path``: the grey decode
    thresholded above :data:`~app.masks.MASK_THRESHOLD`, as ``MaskStore.read`` reads."""
    grey = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if grey is None:
        logger.warning("Failed to load the GT mask %s", path)
        return None, "unreadable"
    if _wrong_size(grey, size, path):
        return None, "size"
    return zero_one_plane(grey > MASK_THRESHOLD), ""


def _wrong_size(
    on: np.ndarray, size: tuple[int, int], name: str, *, level: int = logging.WARNING
) -> bool:
    """True (and logged at ``level``) when ``on`` is not ``size`` ``(w, h)``."""
    width, height = size
    if on.shape[:2] == (height, width):
        return False
    logger.log(
        level,
        "Mask of %s is %sx%s, image is %sx%s; ignoring it",
        name,
        on.shape[1],
        on.shape[0],
        width,
        height,
    )
    return True

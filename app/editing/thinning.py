"""Skeleton thinning as Correct opens a skeleton file (Qt-free: numpy and cv2).

- :func:`thin_raster`: a grayscale skeleton raster resized to the image and thinned to 1 px,
  the skeleton Correct shows for a generated file or a saved correction.
- :func:`is_generated_as_opened`: whether a skeleton is the generated one as Correct opens
  it. Saving is not acceptance (O2): a skeleton Save writes a correction only when this is
  False.
- The lazy scikit-image import (:func:`prewarm_skeletonize`) and the 0/255 helpers the
  skeleton model shares (``app.gui.skeleton_graph_model`` imports them back).

scikit-image (about 0.2 s to import, most of it scipy) is imported on the first
skeletonize, or ahead of time by :func:`prewarm_skeletonize`, which ``MainWindow`` runs in
the thread pool once the window shows; importing this module stays cheap. No Qt, no torch:
``app.inference`` may import it.
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

from app.io_utils import imread_unicode

__all__ = ["is_generated_as_opened", "prewarm_skeletonize", "thin_raster"]

# scikit-image's skeletonize, imported on first use (see prewarm_skeletonize).
_skimage_skeletonize: Callable[[np.ndarray], np.ndarray] | None = None


def _skeletonize_fn() -> Callable[[np.ndarray], np.ndarray]:
    """scikit-image's ``skeletonize``, imported (once) on first use."""
    global _skimage_skeletonize
    if _skimage_skeletonize is None:
        from skimage.morphology import skeletonize

        _skimage_skeletonize = skeletonize
    return _skimage_skeletonize


def prewarm_skeletonize() -> None:
    """Import scikit-image's skeletonize now, so the first skeleton edit does not wait.

    Idempotent: later calls return at once. Safe from a worker thread (Python's import
    lock makes a skeletonize on another thread wait for the import to finish).
    """
    _skeletonize_fn()


def _ensure_uint8_binary(mask: np.ndarray) -> np.ndarray:
    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    if mask.ndim != 2:
        raise ValueError("Expected a 2D binary mask.")
    # Normalize to 0/255. uint8 scalars keep np.where from building an int64 temporary.
    return np.where(mask > 0, np.uint8(255), np.uint8(0))


def _skeletonize_uint8(mask: np.ndarray) -> np.ndarray:
    """Skeletonize a uint8 0/255 foreground mask to 1px (uint8 0/255)."""
    mask = _ensure_uint8_binary(mask)
    skel_bool = _skeletonize_fn()(mask > 0)
    return skel_bool.astype(np.uint8) * 255


def thin_raster(
    raster_gray: np.ndarray, target_size: tuple[int, int], threshold: str = "otsu"
) -> np.ndarray:
    """A grayscale skeleton raster at ``target_size``, 1 px thin (uint8 0/255); any thread.

    A raster of another size (the generated skeleton is ``SKELETON_OUTPUT_SIZE``) is
    upscaled bilinearly *before* thresholding, so thin lines come out as smooth,
    connected 1 px lines of the right length instead of a nearest-neighbour
    staircase. A raster already at ``target_size`` (a correction saved at image
    resolution) is only thresholded and thinned, so it reopens unchanged.
    """
    if raster_gray.ndim != 2:
        raise ValueError("Expected a grayscale image.")

    img = raster_gray
    w_t, h_t = target_size
    if (img.shape[1], img.shape[0]) != (w_t, h_t):
        img = cv2.resize(img, (w_t, h_t), interpolation=cv2.INTER_LINEAR)
    if threshold == "otsu":
        _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(img, 50, 255, cv2.THRESH_BINARY)

    # Ensure skeleton is foreground (white) by inverting if background dominates.
    if float(np.mean(binary)) > 127.0:
        binary = 255 - binary
    return _skeletonize_uint8(binary)


def is_generated_as_opened(
    mask: np.ndarray, generated_path: str | None, photo_size: tuple[int, int]
) -> bool:
    """True when ``mask`` sets exactly the pixels of the generated skeleton as Correct opens
    it: the file at ``generated_path`` read in grayscale and :func:`thin_raster`-ed to
    ``photo_size`` ``(w, h)``.

    No path, a missing file or an unreadable one opens as an empty skeleton, so an empty
    ``mask`` is then the generated one; an emptied skeleton over a non-empty generated one
    is not (a real correction). Only where ``mask`` is set counts, not its values.
    """
    gray = imread_unicode(generated_path, cv2.IMREAD_GRAYSCALE) if generated_path else None
    if gray is None:
        return not mask.any()
    opened = thin_raster(gray, photo_size)
    return opened.shape == mask.shape and np.array_equal(opened > 0, mask > 0)

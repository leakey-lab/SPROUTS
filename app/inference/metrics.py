"""
Shared root-metric primitives (length & area).

This module unifies logic that was previously duplicated, near-identically,
across ``root_length_inference_handler`` and ``root_area_inference_handler``
(filename parsing, the per-image worker, CSV writing, the QThread driver) and
fixes the calibration science:

* Area (``root_area_mm2``) is computed from the mask's *actual* resolution and
  the physical field of view, so a mask that is not 640x480 no longer reports an
  area wrong by the square of the resolution ratio (F-002).

* Length (``root_length_mm``) uses a true arc-length estimator: orthogonal pixel
  steps cost one pixel pitch, diagonal steps cost the Euclidean diagonal. The
  previous code summed the raw skeleton pixel *count*, which undercounts every
  diagonal step (treating sqrt(2) as 1) and biased reported length low by
  ~15-20% (F-003). The estimator also uses the per-axis pixel pitch, so the
  near-isotropic (<4%) x/y difference is handled exactly rather than averaged.
"""

from __future__ import annotations

import csv
import logging
import math
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np

from app.config import AREA_VALUE_COLUMN, CALIBRATION, LENGTH_VALUE_COLUMN
from app.io_utils import atomic_open, imread_unicode
from app.naming import parse_image_name as _parse_name

logger = logging.getLogger(__name__)

# --------------------------- image -> binary -------------------------------


def preprocess_image(image_path: str) -> np.ndarray:
    """Read an image and return its 1px skeleton (uint8 0/255)."""
    # Imported here: scikit-image (and scipy behind it) stays out of the app's start-up.
    from skimage.morphology import skeletonize

    img = imread_unicode(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(binary) > 127:
        binary = 255 - binary
    skeleton = skeletonize(binary / 255)
    return (skeleton * 255).astype(np.uint8)


def load_mask(image_path: str) -> np.ndarray:
    """Read a mask image and return a binary (0/255) array."""
    img = imread_unicode(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read mask: {image_path}")
    _, binary = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
    return binary


# ----------------------------- metrics -------------------------------------


def skeleton_arc_length_px(skeleton: np.ndarray) -> tuple[float, float, float]:
    """Return (orthogonal_x_steps, orthogonal_y_steps, diagonal_steps) counted as
    edges in the 8-connected pixel graph of the skeleton."""
    s = skeleton > 0
    orth_x = int(np.count_nonzero(s[:, :-1] & s[:, 1:]))  # horizontal neighbors
    orth_y = int(np.count_nonzero(s[:-1, :] & s[1:, :]))  # vertical neighbors
    diag = int(np.count_nonzero(s[:-1, :-1] & s[1:, 1:])) + int(
        np.count_nonzero(s[:-1, 1:] & s[1:, :-1])
    )
    return orth_x, orth_y, diag


def root_length_mm(skeleton: np.ndarray) -> float:
    """Root length in mm from a skeleton, using arc-length + per-axis calibration."""
    h, w = skeleton.shape[:2]
    mx, my = CALIBRATION.mm_per_px(w, h)
    orth_x, orth_y, diag = skeleton_arc_length_px(skeleton)
    diag_mm = math.hypot(mx, my)
    return orth_x * mx + orth_y * my + diag * diag_mm


def root_area_mm2(mask: np.ndarray) -> float:
    """Root area in mm^2 from a binary mask, calibrated to the mask's resolution."""
    h, w = mask.shape[:2]
    root_pixels = int(np.count_nonzero(mask == 255))
    return root_pixels * CALIBRATION.area_per_px_mm2(w, h)


# Backwards-compatible aliases (kept so existing imports keep working).
def calculate_root_length(skeleton: np.ndarray) -> float:
    return root_length_mm(skeleton)


def calculate_root_area(mask: np.ndarray) -> float:
    return root_area_mm2(mask)


# ----------------------------- filename ------------------------------------


def parse_image_name(name: str) -> dict:
    """Parse ``..._T{tube}_L{pos}_YYYY.MM.DD_HHMMSS_...`` into its fields.

    Delegates to :func:`app.naming.parse_image_name` (tube/position come only
    from stand-alone ``T#``/``L#`` tokens, FR-15) and keeps this module's dict
    shape; missing fields are ``None``.
    """
    parsed = _parse_name(name)
    return {
        "original_name": name,
        "tube_number": parsed.tube,
        "length_position": parsed.position,
        "date": parsed.date,
        "time": parsed.time,
    }


# ----------------------------- rows ----------------------------------------


def _metric_row(
    name: str,
    path: str,
    measure: Callable[[str], float],
    value_column: str,
    label: str,
) -> dict:
    """One CSV row: ``measure(path)`` rounded to 2 dp, or a 0 row with ``Error``."""
    try:
        value = measure(path)
        info = parse_image_name(name)
        return {
            "Image": info["original_name"],
            "Tube": info["tube_number"],
            "Position": info["length_position"],
            "Date": info["date"],
            "Time": info["time"],
            value_column: round(value, 2),
        }
    except Exception as e:  # per-image boundary: one bad file never aborts a run
        logger.error("%s metric failed for %r: %s", label, name, e)
        return {
            "Image": name,
            "Tube": None,
            "Position": None,
            "Date": None,
            "Time": None,
            value_column: 0,
            "Error": str(e),
        }


def length_row(name: str, path: str) -> dict:
    """The ``root_lengths.csv`` row for the skeleton image at ``path``."""
    return _metric_row(
        name, path, lambda p: root_length_mm(preprocess_image(p)), LENGTH_VALUE_COLUMN, "Length"
    )


def area_row(name: str, path: str) -> dict:
    """The ``root_areas.csv`` row for the mask image at ``path``."""
    return _metric_row(name, path, lambda p: root_area_mm2(load_mask(p)), AREA_VALUE_COLUMN, "Area")


# ----------------------------- CSV -----------------------------------------


def write_metric_csv(results: list[dict], filename: str, headers: list[str]) -> None:
    """Write ``results`` as a CSV with exactly ``headers`` (missing cells empty).

    The file is replaced atomically: a failure (disk full, the CSV held open by
    Excel -> :class:`app.io_utils.FileLockedError`) leaves the previous CSV intact
    and no temp file behind.
    """
    try:
        with atomic_open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in results:
                writer.writerow({h: row.get(h, "") for h in headers})
        logger.debug("Wrote %d rows to %s", len(results), filename)
    except OSError as exc:
        logger.error("Failed to write metric CSV %s: %s", filename, exc)
        raise


def _sort_key(row: dict):
    return (
        row.get("Tube") or float("inf"),
        row.get("Date") or "",
        row.get("Time") or "",
        row.get("Position") or float("inf"),
    )


def run_metric_pool(
    images: dict[str, str],
    worker: Callable[[str, str], dict],
    progress_cb: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[dict]:
    """Run ``worker(name, path)`` over ``images`` on a thread pool, return sorted rows.

    ``should_stop`` is polled after each finished image; once it returns True
    the pending images are cancelled and the rows so far are returned, so the
    caller must check its own cancel flag before using them.
    """
    total = len(images)
    if total == 0:
        return []
    # Rows are kept in input order before the (stable) sort, so rows with equal
    # sort keys (e.g. error rows) come out in a deterministic order.
    results: list[dict | None] = [None] * total
    # Cap at half of logical cores (min 2, max 8) so the Qt main thread
    # is not starved by workers saturating every CPU core.
    max_workers = min(max(2, (os.cpu_count() or 4) // 2), 8, total)
    last_pct = -1
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(worker, name, path): index
            for index, (name, path) in enumerate(images.items())
        }
        for i, fut in enumerate(as_completed(futures), start=1):
            results[futures[fut]] = fut.result()
            if should_stop is not None and should_stop():
                ex.shutdown(wait=True, cancel_futures=True)
                break
            if progress_cb is not None:
                pct = int(i / total * 100)
                if pct != last_pct:
                    last_pct = pct
                    progress_cb(pct)
    return sorted((row for row in results if row is not None), key=_sort_key)

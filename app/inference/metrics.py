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

from app import config
from app.config import AREA_VALUE_COLUMN, LENGTH_VALUE_COLUMN, Calibration
from app.io_utils import atomic_open, imread_unicode
from app.masks.store import MASK_THRESHOLD
from app.naming import parse_image_name as _parse_name

logger = logging.getLogger(__name__)

# The app's default field of view as this module bound it before O3; kept importable for code
# that reads ``metrics.CALIBRATION`` (a benchmark imports this module). The functions read
# ``config.CALIBRATION`` at call time instead.
CALIBRATION = config.CALIBRATION

# --------------------------- image -> binary -------------------------------


def _mostly_white(binary: np.ndarray) -> bool:
    """``np.mean(binary) > 127`` for a 0/255 image, in exact integers (R6).

    The mean is ``255 * white / size``: comparing ``255 * white`` with ``127 * size``
    decides the same, without summing every pixel as float64.
    """
    return cv2.countNonZero(binary) * 255 > 127 * binary.size


def _read_grey(path: str, data: bytes | None) -> np.ndarray | None:
    """The grey image at ``path``, or decoded from ``data`` (the file's bytes, already read).

    Both decode with the same ``cv2.imdecode`` as :func:`app.io_utils.imread_unicode`, so
    the pixels are identical; ``None`` when nothing can be decoded (empty ``data`` too).
    """
    if data is None:
        return imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if not data:
        return None
    try:
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    except cv2.error:
        return None


def preprocess_image(image_path: str, *, data: bytes | None = None) -> np.ndarray:
    """Read an image and return its 1px skeleton (uint8 0/255).

    ``data``: the file's bytes, already read (Measure reads each input once, R5).

    Otsu threshold, inverted when mostly white, then thinned (skimage's Zhang
    method). The result is the same, pixel for pixel, as the former
    ``skeletonize(binary / 255)``: nonzero is foreground either way, but the
    float64 copy and the int64 temporary are gone (R6, ~1.5 ms per 640 x 480).
    """
    # Imported here: scikit-image (and scipy behind it) stays out of the app's start-up.
    from skimage.morphology import skeletonize

    img = _read_grey(image_path, data)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {os.path.basename(image_path)}")
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if _mostly_white(binary):
        binary = 255 - binary
    skeleton = np.asarray(skeletonize(binary > 0), dtype=bool)
    return skeleton.view(np.uint8) * np.uint8(255)


def _load_mask_and_grey_flag(image_path: str, data: bytes | None) -> tuple[np.ndarray, bool]:
    """The binary (0/255) mask, and whether its grey decode holds a value other than 0 and
    255 (a mask with grey levels: ``> MASK_THRESHOLD`` decides which pixels are root)."""
    img = _read_grey(image_path, data)
    if img is None:
        raise FileNotFoundError(f"Could not read mask: {os.path.basename(image_path)}")
    _, binary = cv2.threshold(img, MASK_THRESHOLD, 255, cv2.THRESH_BINARY)
    non_binary = int(np.count_nonzero(img == 0)) + int(np.count_nonzero(img == 255)) != img.size
    return binary, non_binary


def load_mask(image_path: str, *, data: bytes | None = None) -> np.ndarray:
    """Read a mask image and return a binary (0/255) array (``data``: its bytes, read)."""
    return _load_mask_and_grey_flag(image_path, data)[0]


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


def root_length_mm(skeleton: np.ndarray, *, calibration: Calibration | None = None) -> float:
    """Root length in mm from a skeleton, using arc-length + per-axis calibration.

    ``calibration``: the field of view (a project's, O3); ``None`` is
    :data:`app.config.CALIBRATION`, read at call time."""
    h, w = skeleton.shape[:2]
    cal = config.CALIBRATION if calibration is None else calibration
    mx, my = cal.mm_per_px(w, h)
    orth_x, orth_y, diag = skeleton_arc_length_px(skeleton)
    diag_mm = math.hypot(mx, my)
    return orth_x * mx + orth_y * my + diag * diag_mm


def root_area_mm2(mask: np.ndarray, *, calibration: Calibration | None = None) -> float:
    """Root area in mm^2 from a binary mask, calibrated to the mask's resolution.

    ``calibration``: as :func:`root_length_mm`."""
    h, w = mask.shape[:2]
    root_pixels = int(np.count_nonzero(mask == 255))
    cal = config.CALIBRATION if calibration is None else calibration
    return root_pixels * cal.area_per_px_mm2(w, h)


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


def _error_text(exc: BaseException, path: str) -> str:
    """``str(exc)`` with ``path`` (as given, absolute, normalised, and as ``repr`` prints it,
    doubled backslashes) replaced by the file name: a shared CSV carries no folder (O38)."""
    text = str(exc)
    base = os.path.basename(path)
    forms = {path, os.path.abspath(path), os.path.normpath(path)}
    forms |= {repr(f)[1:-1] for f in forms}
    for form in sorted(forms, key=len, reverse=True):
        if form:
            text = text.replace(form, base)
    return text


def _size_text(image: np.ndarray) -> str:
    """``"WxH"`` of a decoded image: the private ``_size_px`` key of a row."""
    h, w = image.shape[:2]
    return f"{w}x{h}"


def _metric_row(
    name: str,
    path: str,
    measure: Callable[[str], tuple[float, str, dict]],
    value_column: str,
    label: str,
) -> dict:
    """One CSV row: ``measure(path)`` (the value and the decoded size) with the value
    rounded to 2 dp, or a 0 row with ``Error``. ``_size_px`` (and the further private keys
    ``measure`` returns, e.g. ``_non_binary``) are private to the row: the CSV writer writes
    the headers only, so they are never a column."""
    try:
        value, size, private = measure(path)
        info = parse_image_name(name)
        return {
            "Image": info["original_name"],
            "Tube": info["tube_number"],
            "Position": info["length_position"],
            "Date": info["date"],
            "Time": info["time"],
            value_column: round(value, 2),
            "_size_px": size,
            **private,
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
            "Error": _error_text(e, path),
        }


def length_row(
    name: str, path: str, *, data: bytes | None = None, calibration: Calibration | None = None
) -> dict:
    """The ``root_lengths.csv`` row for the skeleton image at ``path`` (``data``: its bytes,
    already read; the row is the same), measured with ``calibration`` (``None``: the app's)."""

    def measure(p: str) -> tuple[float, str, dict]:
        skeleton = preprocess_image(p, data=data)
        return root_length_mm(skeleton, calibration=calibration), _size_text(skeleton), {}

    return _metric_row(name, path, measure, LENGTH_VALUE_COLUMN, "Length")


def area_row(
    name: str, path: str, *, data: bytes | None = None, calibration: Calibration | None = None
) -> dict:
    """The ``root_areas.csv`` row for the mask image at ``path`` (``data``: its bytes,
    already read; the row is the same), measured with ``calibration`` (``None``: the app's).
    A mask with grey levels also gets the private key ``_non_binary: True`` (never a CSV
    column)."""

    def measure(p: str) -> tuple[float, str, dict]:
        mask, non_binary = _load_mask_and_grey_flag(p, data)
        area = root_area_mm2(mask, calibration=calibration)
        return area, _size_text(mask), {"_non_binary": True} if non_binary else {}

    return _metric_row(name, path, measure, AREA_VALUE_COLUMN, "Area")


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
        row.get("Image") or "",  # ties: the same order on every OS (scan order differs, WX-T9)
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
    caller must check its own cancel flag before using them. A ``progress_cb``
    that raises is logged and ignored: it never fails a run whose images were
    measured (R3).
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
                    try:
                        progress_cb(pct)
                    except Exception as exc:  # noqa: BLE001 - progress is advisory (R3)
                        logger.warning("Progress callback failed (ignored): %s", exc)
    return sorted((row for row in results if row is not None), key=_sort_key)

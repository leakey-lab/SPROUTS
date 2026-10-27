"""
Shared root-metric primitives (length & area).

This module unifies logic that was previously duplicated, near-identically,
across the former ``root_length_inference_handler`` and ``root_area_inference_handler``
modules (since removed): filename parsing, the per-image worker and CSV writing (the
QThread driver is now :mod:`app.inference.metric_thread`). It also fixes the
calibration science:

* Area (``root_area_mm2``) is computed from the mask's *actual* resolution and
  the physical field of view, so a mask that is not 640x480 no longer reports an
  area wrong by the square of the resolution ratio (F-002).

* Length (``root_length_mm``) uses the corner-count estimator of Vossepoel &
  Smeulders (1982): 0.980 per orthogonal step, 1.406 per diagonal step and -0.091 per
  change of direction, on the m-adjacent 8-connected skeleton (a junction's diagonal is
  not counted on top of its two orthogonal steps), with the per-axis pixel pitch (O12,
  audit X1). It is unbiased on average over all directions (-2.0 % to +1.0 % by direction;
  a perfectly horizontal or vertical line reads 2.0 % short). It replaces the Freeman
  weights (1, sqrt(2)), which read about 5 % long; before those, the raw pixel *count* read
  15-20 % short (F-003). The default pixels are square (18 x 13.5 mm at 640 x 480); a
  non-square project calibration (``sprouts_project.json``, O3) uses each axis's own pitch.
  For non-square pixels the step weights are applied per axis, an extension of the
  square-grid estimator: by direction -2.0 to +1.3 % at 18 x 13 mm, -2.9 to +3.3 % at a
  20 % difference.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import NamedTuple

import cv2
import numpy as np

from app import config
from app.config import AREA_VALUE_COLUMN, LENGTH_VALUE_COLUMN, Calibration
from app.io_utils import atomic_open, imdecode_bytes, imread_unicode
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
    return imdecode_bytes(data, cv2.IMREAD_GRAYSCALE)


def preprocess_image(image_path: str, *, data: bytes | None = None) -> np.ndarray:
    """Read an image and return its 1px skeleton (uint8 0/255).

    ``data``: the file's bytes, already read (Measure reads each input once, R5).

    Otsu threshold, inverted when mostly white, then thinned (skimage's Zhang
    method). The result is the same, pixel for pixel, as the former
    ``skeletonize(binary / 255)``: nonzero is foreground either way, but the
    float64 copy and the int64 temporary are gone (R6, ~1.5 ms per 640 x 480).
    An image without foreground comes back empty without thinning (the same pixels:
    thinning only removes foreground); most predictions of a project are empty.
    """
    # Imported here: scikit-image (and scipy behind it) stays out of the app's start-up.
    from skimage.morphology import skeletonize

    img = _read_grey(image_path, data)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {os.path.basename(image_path)}")
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if _mostly_white(binary):
        binary = 255 - binary
    if not cv2.countNonZero(binary):
        return np.zeros(binary.shape, np.uint8)
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


# Vossepoel & Smeulders (1982), CGIP 20:347-364, the "corner count estimator" recommended by
# Dorst & Smeulders (1987), CVGIP 40:311-333, eq. 24: L = 0.980 n_e + 1.406 n_o - 0.091 n_c.
VS_ORTHOGONAL = 0.980
VS_DIAGONAL = 1.406
VS_CORNER = 0.091

# code(p) = sum of 2**k over the set 8-neighbours of p: k = 0 E, 1 NE, 2 N, 3 NW, 4 W, 5 SW, 6 S, 7 SE
_NEIGHBOUR_CODE = np.array([[8, 4, 2], [16, 0, 1], [32, 64, 128]], np.float32)


def _corner_lut() -> np.ndarray:
    """1 for a neighbour code whose pixel is a corner: exactly two m-adjacent neighbours,
    not on opposite sides (the chain code changes value there)."""
    lut = np.zeros(256, np.uint8)
    for code in range(256):
        bits = [(code >> k) & 1 for k in range(8)]
        # a diagonal neighbour that shares a 4-neighbour in the skeleton is not m-adjacent
        for k in (1, 3, 5, 7):
            if bits[k] and (bits[k - 1] or bits[(k + 1) % 8]):
                bits[k] = 0
        steps = [k for k in range(8) if bits[k]]
        lut[code] = len(steps) == 2 and steps[1] - steps[0] != 4
    return lut


_CORNER_LUT = _corner_lut()


class SkeletonSteps(NamedTuple):
    horizontal: int  # 4-adjacent pairs along x
    vertical: int  # 4-adjacent pairs along y
    diagonal: int  # m-adjacent diagonal pairs (no shared 4-neighbour in the skeleton)
    corners: int  # pixels where the 8-connected step changes direction


def skeleton_arc_length_px(skeleton: np.ndarray) -> SkeletonSteps:
    """Steps and corners of a 1-px, 8-connected skeleton under m-adjacency."""
    s8 = (skeleton > 0).astype(np.uint8)
    x, y, w, h = cv2.boundingRect(s8)
    if w == 0:
        return SkeletonSteps(0, 0, 0, 0)
    s8 = cv2.copyMakeBorder(s8[y : y + h, x : x + w], 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    s = s8.astype(bool)
    horizontal = int(np.count_nonzero(s[:, :-1] & s[:, 1:]))
    vertical = int(np.count_nonzero(s[:-1, :] & s[1:, :]))
    tl, tr, bl, br = s[:-1, :-1], s[:-1, 1:], s[1:, :-1], s[1:, 1:]
    down_right = int(np.count_nonzero(tl & br & ~tr & ~bl))
    down_left = int(np.count_nonzero(tr & bl & ~tl & ~br))
    # exact in uint8: a code is at most 255
    code = cv2.filter2D(s8, -1, _NEIGHBOUR_CODE, borderType=cv2.BORDER_CONSTANT)
    corners = int(_CORNER_LUT[code[s]].sum())
    return SkeletonSteps(horizontal, vertical, down_right + down_left, corners)


def root_length_mm(skeleton: np.ndarray, *, calibration: Calibration | None = None) -> float:
    """Root length in mm: the corner-count estimator with the per-axis pixel pitch.

    ``calibration``: the field of view (a project's, O3); ``None`` is
    :data:`app.config.CALIBRATION`, read at call time."""
    h, w = skeleton.shape[:2]
    cal = config.CALIBRATION if calibration is None else calibration
    mx, my = cal.mm_per_px(w, h)
    st = skeleton_arc_length_px(skeleton)
    return (
        VS_ORTHOGONAL * (st.horizontal * mx + st.vertical * my)
        + VS_DIAGONAL / math.sqrt(2.0) * st.diagonal * math.hypot(mx, my)
        - VS_CORNER * st.corners * math.sqrt(mx * my)
    )


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


#: The largest file a run's ``memo`` keeps: the empty predictions a model writes are a few
#: hundred bytes, one file content per project.
_MEMO_MAX_BYTES = 1024


def _metric_row(
    name: str,
    path: str,
    measure: Callable[[str], tuple[float, str, dict]],
    value_column: str,
    label: str,
    *,
    data: bytes | None = None,
    memo: dict | None = None,
    log: Callable[..., None] | None = None,
) -> dict:
    """One CSV row: ``measure(path)`` (the value and the decoded size) with the value
    rounded to 2 dp, or a 0 row with ``Error``. ``_size_px`` (and the further private keys
    ``measure`` returns, e.g. ``_non_binary``) are private to the row: the CSV writer writes
    the headers only, so they are never a column.

    ``memo``: one dict per run (one metric, one field of view). A file of at most
    ``_MEMO_MAX_BYTES`` whose bytes ``data`` equal those of a file of the run that measured
    0 is not decoded again: the measurement depends on the bytes and the field of view only,
    and most predictions of a project are the one empty file. A failure is never kept, so
    each failed file names itself.

    ``log(msg, *args)`` logs a failure (``None``: ``logger.error``); a Measure run passes
    one that logs its first failures and counts the rest (C6-02)."""
    try:
        key = data if memo is not None and data and len(data) <= _MEMO_MAX_BYTES else None
        measured = None if key is None else memo.get(key)
        if measured is None:
            measured = measure(path)
            if key is not None and measured[0] == 0:
                memo[key] = measured
        value, size, private = measured
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
        (logger.error if log is None else log)("%s metric failed for %r: %s", label, name, e)
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
    name: str,
    path: str,
    *,
    data: bytes | None = None,
    calibration: Calibration | None = None,
    memo: dict | None = None,
    log: Callable[..., None] | None = None,
) -> dict:
    """The ``root_lengths.csv`` row for the skeleton image at ``path`` (``data``: its bytes,
    already read; the row is the same), measured with ``calibration`` (``None``: the app's).
    ``memo`` and ``log``: the run's (:func:`_metric_row`); the row is the same without them."""

    def measure(p: str) -> tuple[float, str, dict]:
        skeleton = preprocess_image(p, data=data)
        return root_length_mm(skeleton, calibration=calibration), _size_text(skeleton), {}

    return _metric_row(
        name, path, measure, LENGTH_VALUE_COLUMN, "Length", data=data, memo=memo, log=log
    )


def area_row(
    name: str,
    path: str,
    *,
    data: bytes | None = None,
    calibration: Calibration | None = None,
    memo: dict | None = None,
    log: Callable[..., None] | None = None,
) -> dict:
    """The ``root_areas.csv`` row for the mask image at ``path`` (``data``: its bytes,
    already read; the row is the same), measured with ``calibration`` (``None``: the app's).
    A mask with grey levels also gets the private key ``_non_binary: True`` (never a CSV
    column). ``memo`` and ``log``: as :func:`length_row`."""

    def measure(p: str) -> tuple[float, str, dict]:
        mask, non_binary = _load_mask_and_grey_flag(p, data)
        area = root_area_mm2(mask, calibration=calibration)
        return area, _size_text(mask), {"_non_binary": True} if non_binary else {}

    return _metric_row(
        name, path, measure, AREA_VALUE_COLUMN, "Area", data=data, memo=memo, log=log
    )


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


def _task_bounds(total: int, workers: int) -> list[tuple[int, int]]:
    """``(start, stop)`` of the pool's tasks over ``total`` images: runs of up to 32
    consecutive images, shorter towards the end so that no worker is left with a long last
    task. Each is under 1 % of the images (one image per task below 256 images), so the
    progress values are those of one task per image."""
    longest = max(1, min(32, total // 128))
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < total:
        size = min(longest, -(-(total - start) // (4 * workers)))
        bounds.append((start, start + size))
        start += size
    return bounds


#: How often :func:`run_metric_pool` asks ``should_stop`` while no task ends: a Cancel waits
#: about this, plus the image each task is measuring, never for a whole run of images.
_STOP_POLL_S = 0.02


def _next_finished(
    finished: queue.SimpleQueue, should_stop: Callable[[], bool] | None
) -> Future | None:
    """The next task of ``finished`` to end, or None when ``should_stop`` says so first."""
    if should_stop is None:
        return finished.get()
    while True:
        try:
            return finished.get(timeout=_STOP_POLL_S)
        except queue.Empty:
            if should_stop():
                return None


def run_metric_pool(
    images: dict[str, str],
    worker: Callable[[str, str], dict],
    progress_cb: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[dict]:
    """Run ``worker(name, path)`` over ``images`` on a thread pool, return sorted rows.

    ``should_stop`` is polled after each finished task (a run of images), and every
    :data:`_STOP_POLL_S` while none ends; once it returns True the pending images are
    cancelled and the rows so far are returned, so the caller must check its own cancel
    flag before using them. A ``worker`` that raises cancels the pending images too; its
    error is raised once the running ones have finished. A ``progress_cb`` that raises is
    logged and ignored: it never fails a run whose images were measured (R3).
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
    # One future per image cost ~30 us of GIL-held work per image on this thread.
    items = list(images.items())
    stopping = threading.Event()  # set: a task measures no further image

    def task(start: int, stop: int) -> tuple[int, list[dict]]:
        rows = []
        for name, path in items[start:stop]:
            if stopping.is_set():
                break
            rows.append(worker(name, path))
        return start, rows

    last_pct = -1
    done = 0
    finished: queue.SimpleQueue = queue.SimpleQueue()  # the tasks, as they end
    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [ex.submit(task, *bounds) for bounds in _task_bounds(total, max_workers)]
        for fut in futures:
            fut.add_done_callback(finished.put)
        for _ in futures:
            fut = _next_finished(finished, should_stop)
            if fut is None:  # stopped while every task was still measuring
                break
            start, rows = fut.result()
            results[start : start + len(rows)] = rows
            done += len(rows)
            if should_stop is not None and should_stop():
                break
            if progress_cb is not None:
                pct = int(done / total * 100)
                if pct != last_pct:
                    last_pct = pct
                    try:
                        progress_cb(pct)
                    except Exception as exc:  # noqa: BLE001 - progress is advisory (R3)
                        logger.warning("Progress callback failed (ignored): %s", exc)
    finally:  # stopped, or an image raised: the images not started are left out
        stopping.set()
        ex.shutdown(wait=True, cancel_futures=True)
        # Every done callback has run. A task left in the queue refers to it through its
        # callback: empty it so refcounting frees both (a cycle would survive a gc.freeze()).
        while not finished.empty():
            finished.get_nowait()
    return sorted((row for row in results if row is not None), key=_sort_key)

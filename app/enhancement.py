"""Display-only image enhancement for the editors (the "Enhance" popover), Qt-free.

* :class:`EnhanceSettings` - the one immutable description of an enhancement (method + slider
  values) and its validation (:meth:`EnhanceSettings.from_dict` never raises).
* :func:`deblock` - the "Smooth JPEG blocks" switch: an H.264-style weak filter over the 8 x 8
  block boundaries of a JPEG photo (:func:`is_jpeg_path` says which sources it is offered for).
* :func:`clahe`, :func:`contrast_stretch`, :func:`enhance` - the math. ``contrast_stretch`` is
  bit-exact with the former float64 ``np.percentile`` implementation but takes the percentiles
  from a 256-bin histogram and applies a ``cv2.LUT`` for uint8 images (~10x faster, audit PE-08);
  a flat channel (upper percentile == lower) is returned unchanged instead of turning black.
* :class:`SourceImageCache` - keeps the last decoded source image (read-only, decoded with
  :data:`DECODE_FLAGS`: EXIF orientation ignored, as Qt does) so the photo, the live
  previews and the photo pyramid share one decode.
* :class:`EnhanceDefaultsStore` - the user's saved defaults, stored per user in
  ``user_config_dir()/normalization_defaults.json`` (atomic write). The shipped
  ``config/normalization_defaults.json`` is read-only factory defaults and seeds the user file
  on first run (audit RB-13 / UI-19 / PE-14). The first load in a process sweeps that folder
  for the temp files of killed saves, on a daemon thread (R4).

Nothing here touches masks, skeletons or measurements: enhancement only changes what is shown.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from typing import Any, ClassVar

import cv2
import numpy as np

from app.io_utils import atomic_write_text, imread_unicode, sweep_stale_temp_files

logger = logging.getLogger(__name__)

__all__ = [
    "DECODE_FLAGS",
    "DEFAULT_SETTINGS",
    "DEFAULTS_FILE_NAME",
    "JPEG_EXTENSIONS",
    "METHODS",
    "METHOD_CLAHE",
    "METHOD_STRETCH",
    "EnhanceDefaultsStore",
    "EnhanceSettings",
    "SourceImageCache",
    "clahe",
    "contrast_stretch",
    "deblock",
    "enhance",
    "is_jpeg_path",
]

METHOD_CLAHE = "CLAHE"
METHOD_STRETCH = "Contrast Stretching"
METHODS = (METHOD_CLAHE, METHOD_STRETCH)
DEFAULTS_FILE_NAME = "normalization_defaults.json"
#: Source files the "Smooth JPEG blocks" switch is offered for (compared lower-cased).
JPEG_EXTENSIONS = (".jpg", ".jpeg")


@dataclass(frozen=True)
class EnhanceSettings:
    """Enhancement parameters in slider units.

    ``clip`` is the CLAHE clip limit in tenths (20 = 2.0), ``tile`` the CLAHE grid size,
    ``lower`` / ``upper`` the contrast-stretch percentiles, ``deblock`` the "Smooth JPEG
    blocks" switch (:func:`deblock`; independent of ``method``, off by default).
    """

    method: str = METHOD_CLAHE
    clip: int = 20
    tile: int = 8
    lower: int = 2
    upper: int = 98
    deblock: bool = False

    #: Inclusive slider range of every integer field (the controls use the same ranges).
    RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "clip": (1, 50),
        "tile": (2, 16),
        "lower": (0, 49),
        "upper": (51, 100),
    }
    #: Field <-> key of the JSON file (the historical key names, kept for compatibility).
    _KEYS: ClassVar[dict[str, str]] = {
        "clip": "clahe_clip",
        "tile": "clahe_tile",
        "lower": "contrast_lower",
        "upper": "contrast_upper",
        "method": "last_method",
        "deblock": "jpeg_deblock",
    }

    @property
    def clip_limit(self) -> float:
        """CLAHE clip limit as OpenCV expects it."""
        return self.clip / 10.0

    def to_dict(self) -> dict[str, Any]:
        """The JSON form (``clahe_clip``, ``clahe_tile``, ``contrast_*``, ``last_method``,
        ``jpeg_deblock``)."""
        return {key: getattr(self, field) for field, key in self._KEYS.items()}

    @classmethod
    def from_dict(
        cls, data: Any, base: EnhanceSettings | None = None
    ) -> tuple[EnhanceSettings, list[str]]:
        """Validated settings from untrusted JSON data, plus a list of the problems found.

        Missing keys keep ``base`` (the built-in defaults when None); unknown keys, wrong
        types and out-of-range numbers are reported and ignored or clamped. Never raises.
        """
        base = base or DEFAULT_SETTINGS
        problems: list[str] = []
        if not isinstance(data, dict):
            return base, [f"expected an object, got {type(data).__name__}"]
        values: dict[str, Any] = {}
        by_key = {key: field for field, key in cls._KEYS.items()}
        for key, raw in data.items():
            field = by_key.get(key)
            if field is None:
                problems.append(f"unknown key {key!r} ignored")
            elif field == "method":
                if raw in METHODS:
                    values["method"] = raw
                else:
                    problems.append(f"{key}={raw!r} is not one of {list(METHODS)}")
            elif field == "deblock":
                if type(raw) is bool:
                    values["deblock"] = raw
                else:
                    problems.append(f"{key}={raw!r} is not true or false")
            else:
                number = _as_int(raw)
                if number is None:
                    problems.append(f"{key}={raw!r} is not a number")
                    continue
                lo, hi = cls.RANGES[field]
                clamped = min(max(number, lo), hi)
                if clamped != raw or type(raw) is not int:
                    problems.append(f"{key}={raw!r} read as {clamped}")
                values[field] = clamped
        merged = {field: getattr(base, field) for field in cls._KEYS} | values
        return cls(**merged), problems


def _as_int(raw: Any) -> int | None:
    """``raw`` as a rounded int, or None if it is not a finite number (bools are rejected)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.strip())
        except ValueError:
            return None
    if isinstance(raw, (int, float)) and math.isfinite(raw):
        return int(round(raw))
    return None


DEFAULT_SETTINGS = EnhanceSettings()


# --------------------------------------------------------------------------- the math
def clahe(img: np.ndarray, clip_limit: float = 2.0, tile: int | tuple[int, int] = 8) -> np.ndarray:
    """CLAHE on the L channel of a BGR image (or directly on a grayscale one)."""
    grid = (tile, tile) if isinstance(tile, int) else tuple(tile)
    op = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=grid)
    if img.ndim == 3:
        lum, a, b = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2LAB))
        return cv2.cvtColor(cv2.merge((op.apply(lum), a, b)), cv2.COLOR_LAB2BGR)
    return op.apply(img)


def _order_stat(cdf: np.ndarray, k: int) -> int:
    """Value of the k-th (0-based) smallest sample, given the 256-bin cumulative histogram."""
    return int(np.searchsorted(cdf, k, side="right"))


def _percentile_u8(cdf: np.ndarray, count: int, q: float) -> np.float64:
    """``np.percentile(channel, q)`` (method 'linear') computed from the histogram CDF.

    Reproduces numpy's virtual index and its two-sided ``_lerp`` so the result is bit-exact.
    """
    virtual = (count - 1) * (np.float64(q) / 100)
    k = int(np.floor(virtual))
    gamma = virtual - k
    a = _order_stat(cdf, k)
    b = _order_stat(cdf, min(k + 1, count - 1))
    diff = np.float64(b - a)
    if gamma >= 0.5:
        return np.float64(b) - diff * (1 - gamma)
    return np.float64(a) + diff * gamma


def _stretch_channel_u8(channel: np.ndarray, lower_p: float, upper_p: float) -> np.ndarray:
    hist = cv2.calcHist([channel], [0], None, [256], [0, 256]).ravel().astype(np.int64)
    cdf = np.cumsum(hist)
    count = int(cdf[-1])
    if count == 0:
        return channel.copy()
    lower = _percentile_u8(cdf, count, lower_p)
    upper = _percentile_u8(cdf, count, upper_p)
    if upper <= lower:  # flat channel: nothing to stretch
        return channel.copy()
    levels = np.arange(256, dtype=np.uint8)
    lut = np.clip((levels - lower) * 255.0 / (upper - lower), 0, 255).astype(np.uint8)
    return cv2.LUT(channel, lut)


def _stretch_channel_float(channel: np.ndarray, lower_p: float, upper_p: float) -> np.ndarray:
    lower = np.percentile(channel, lower_p)
    upper = np.percentile(channel, upper_p)
    if upper <= lower:
        return np.clip(channel, 0, 255).astype(np.uint8)
    return np.clip((channel - lower) * 255.0 / (upper - lower), 0, 255).astype(np.uint8)


def contrast_stretch(img: np.ndarray, lower_p: float = 2, upper_p: float = 98) -> np.ndarray:
    """Per-channel percentile contrast stretch to 0..255 (uint8 result)."""
    stretch = _stretch_channel_u8 if img.dtype == np.uint8 else _stretch_channel_float
    if img.ndim == 3:
        return cv2.merge([stretch(ch, lower_p, upper_p) for ch in cv2.split(img)])
    return stretch(img, lower_p, upper_p)


def is_jpeg_path(path: str | os.PathLike[str] | None) -> bool:
    """True for a ``.jpg`` / ``.jpeg`` file name, in any case (None and "" are not)."""
    if not path:
        return False
    return os.path.splitext(os.fspath(path))[1].lower() in JPEG_EXTENSIONS


def deblock(img: np.ndarray, threshold: float = 14.0) -> np.ndarray:
    """Soften the block boundaries of a JPEG photo (display only; a new array).

    The weak filter of H.264: luma at every 8 px boundary, Cr and Cb at every 16 px boundary
    (4:2:0). A boundary step ``d = q0 - p0`` is spread over six pixels (taps ``d/8, d/4, 3d/8``
    on each side) only when ``|d| < threshold`` and both sides are flat (``|p1 - p0|`` and
    ``|q1 - q0|`` < ``threshold / 2``): a real edge, a larger step or a textured side, is left
    alone. A 2-D gray input is filtered as luma only; anything but uint8 gray or BGR comes back
    as a copy. Only pixels whose YCrCb value changed are written back, so the rest stay
    bit-identical (a whole-image YCrCb round trip is off by one on colours). The input is
    never modified (the source cache hands out read-only arrays).
    """
    if img.dtype != np.uint8 or not (img.ndim == 2 or (img.ndim == 3 and img.shape[2] == 3)):
        return img.copy()
    if img.ndim == 2:
        luma = img.astype(np.float32)
        _deblock_axis(luma, 8, threshold)  # vertical boundaries (columns)
        _deblock_axis(luma.T, 8, threshold)  # horizontal boundaries (rows), via a view
        return np.clip(luma + 0.5, 0, 255).astype(np.uint8)
    base = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    ycc = base.astype(np.float32)
    for c, block in ((0, 8), (1, 16), (2, 16)):
        ch = ycc[:, :, c]
        _deblock_axis(ch, block, threshold)
        _deblock_axis(ch.T, block, threshold)
    new = np.clip(ycc + 0.5, 0, 255).astype(np.uint8)
    changed = (new != base).any(axis=2)
    out = img.copy()
    if changed.any():
        out[changed] = cv2.cvtColor(new, cv2.COLOR_YCrCb2BGR)[changed]
    return out


def _deblock_axis(ch: np.ndarray, block: int, t: float) -> None:
    """Filter the boundaries between columns ``block`` apart of ``ch`` in place (float32)."""
    xs = np.arange(block, ch.shape[1] - 2, block)  # q0 columns
    if xs.size == 0:
        return
    p2, p1, p0 = ch[:, xs - 3], ch[:, xs - 2], ch[:, xs - 1]
    q0, q1, q2 = ch[:, xs], ch[:, xs + 1], ch[:, xs + 2]
    d = q0 - p0
    m = (np.abs(d) < t) & (np.abs(p1 - p0) < t / 2) & (np.abs(q1 - q0) < t / 2)
    d = np.where(m, d, np.float32(0))
    ch[:, xs - 3] = p2 + d / 8
    ch[:, xs - 2] = p1 + d / 4
    ch[:, xs - 1] = p0 + 3 * d / 8
    ch[:, xs] = q0 - 3 * d / 8
    ch[:, xs + 1] = q1 - d / 4
    ch[:, xs + 2] = q2 - d / 8


def enhance(img: np.ndarray, settings: EnhanceSettings, *, tone: bool = True) -> np.ndarray:
    """``img`` enhanced with ``settings`` (a new array; the input is never modified).

    The JPEG block smoothing runs first when ``settings.deblock`` is set (CLAHE would amplify
    the block steps); the CLAHE / stretch step runs only when ``tone`` is true.
    """
    out = deblock(img) if settings.deblock else img
    if not tone:
        return out if out is not img else img.copy()
    if settings.method == METHOD_STRETCH:
        return contrast_stretch(out, settings.lower, settings.upper)
    return clahe(out, settings.clip_limit, settings.tile)


# --------------------------------------------------------------------------- source cache
#: How every editor decodes its photo: 3-channel BGR in the file's *stored* geometry. Qt
#: ignores EXIF orientation (``QImageReader.autoTransform`` is off), and so do the canvases
#: and every saved mask; OpenCV would apply it, which transposes a rotated JPEG against them.
DECODE_FLAGS = cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION


class SourceImageCache:
    """The most recently decoded source image, re-read only when the file changes.

    One decode feeds the photo, the Enhance render and the photo pyramid, so the returned
    array is shared and marked read-only (``flags.writeable`` is False).
    """

    def __init__(self) -> None:
        self._key: tuple[str, int, int] | None = None
        self._image: np.ndarray | None = None

    def get(self, path: str | os.PathLike[str]) -> np.ndarray | None:
        """Decoded BGR image at ``path`` in Qt's geometry (None when it cannot be read)."""
        full = os.path.abspath(os.fspath(path))
        try:
            st = os.stat(full)
        except OSError:
            return None
        key = (full, st.st_mtime_ns, st.st_size)
        if key != self._key:
            self._image = imread_unicode(full, DECODE_FLAGS)
            if self._image is not None:
                self._image.flags.writeable = False
            self._key = key if self._image is not None else None
        return self._image

    def clear(self) -> None:
        """Forget the cached image (the next :meth:`get` decodes again)."""
        self._key = None
        self._image = None


# --------------------------------------------------------------------------- saved defaults
def _read_json(path: str) -> Any | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        # ValueError covers JSONDecodeError / bad UTF-8; RecursionError, JSON nested too deep (R4).
        logger.warning("Ignoring unreadable normalization defaults %s: %s", path, exc)
        return None


#: Settings folders already swept by this process (normcased absolute paths, R4).
_swept_settings_dirs: set[str] = set()
_sweep_lock = threading.Lock()


def _sweep_settings_dir_once(folder: str) -> threading.Thread | None:
    """Sweep ``folder`` for stale temp files on a daemon thread, once per process (R4).

    The same rule as a project's output folders (``app.io_utils.sweep_stale_temp_files``:
    only ``.<name>.<8 hex>.tmp`` files older than an hour). Returns the started thread,
    or None when this process already swept ``folder`` or no thread could start (then
    a later call tries again). Never raises.
    """
    key = os.path.normcase(os.path.abspath(folder))
    with _sweep_lock:
        if key in _swept_settings_dirs:
            return None
        _swept_settings_dirs.add(key)

    def run() -> None:
        try:
            removed = sweep_stale_temp_files(folder)
        except Exception:  # housekeeping: never let it disturb the editors
            logger.exception("Stale temp-file sweep failed in %s", folder)
            return
        if removed:
            logger.info("Removed %d stale temp file(s) in %s", len(removed), folder)

    thread = threading.Thread(target=run, name="settings-temp-sweep", daemon=True)
    try:
        thread.start()
    except RuntimeError as exc:  # "can't start new thread": skip it, a later load retries
        logger.warning("Could not start the stale temp-file sweep of %s: %s", folder, exc)
        with _sweep_lock:
            _swept_settings_dirs.discard(key)
        return None
    return thread


class EnhanceDefaultsStore:
    """Load and save the user's default enhancement settings.

    Load order: the user file, then the shipped factory file, then :data:`DEFAULT_SETTINGS`.
    On first run (no user file) the factory values seed the user file, so a preference
    previously saved into the shipped file carries over. The factory file is never written.
    The first :meth:`load` in a process also sweeps the user file's folder for the temp
    files of killed saves, on a daemon thread (``sweep_thread``; R4).
    """

    def __init__(self, user_path: str | None = None, factory_path: str | None = None) -> None:
        if user_path is None:
            from app.user_paths import user_config_dir

            user_path = os.path.join(user_config_dir(ensure=False), DEFAULTS_FILE_NAME)
        if factory_path is None:
            from resources.resource_utils import resource_path

            factory_path = resource_path("config", DEFAULTS_FILE_NAME)
        self.user_path = user_path
        self.factory_path = factory_path
        #: The stale temp-file sweep this store started (None: none, or already swept).
        self.sweep_thread: threading.Thread | None = None

    def _validated(self, data: Any, path: str) -> EnhanceSettings:
        settings, problems = EnhanceSettings.from_dict(data)
        if problems:
            logger.warning("Normalization defaults %s: %s", path, "; ".join(problems))
        return settings

    def load(self) -> EnhanceSettings:
        """The saved defaults (never raises)."""
        thread = _sweep_settings_dir_once(os.path.dirname(os.path.abspath(self.user_path)))
        if thread is not None:
            self.sweep_thread = thread
        user_exists = os.path.exists(self.user_path)
        if user_exists:
            data = _read_json(self.user_path)
            if data is not None:
                return self._validated(data, self.user_path)
        data = _read_json(self.factory_path)
        if data is None:
            return DEFAULT_SETTINGS
        settings = self._validated(data, self.factory_path)
        if not user_exists:
            self.save(settings)
        return settings

    def save(self, settings: EnhanceSettings) -> bool:
        """Write ``settings`` to the user file atomically; False (logged) on failure."""
        try:
            os.makedirs(os.path.dirname(self.user_path) or ".", exist_ok=True)
            atomic_write_text(self.user_path, json.dumps(settings.to_dict(), indent=2) + "\n")
        except OSError as exc:
            logger.warning("Could not save normalization defaults to %s: %s", self.user_path, exc)
            return False
        return True

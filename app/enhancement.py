"""Display-only image enhancement for the editors (the "Enhance" popover), Qt-free.

* :class:`EnhanceSettings` - the one immutable description of an enhancement (method + slider
  values) and its validation (:meth:`EnhanceSettings.from_dict` never raises).
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
  on first run (audit RB-13 / UI-19 / PE-14).

Nothing here touches masks, skeletons or measurements: enhancement only changes what is shown.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, ClassVar

import cv2
import numpy as np

from app.io_utils import atomic_write_text, imread_unicode

logger = logging.getLogger(__name__)

__all__ = [
    "DECODE_FLAGS",
    "DEFAULT_SETTINGS",
    "DEFAULTS_FILE_NAME",
    "METHODS",
    "METHOD_CLAHE",
    "METHOD_STRETCH",
    "EnhanceDefaultsStore",
    "EnhanceSettings",
    "SourceImageCache",
    "clahe",
    "contrast_stretch",
    "enhance",
]

METHOD_CLAHE = "CLAHE"
METHOD_STRETCH = "Contrast Stretching"
METHODS = (METHOD_CLAHE, METHOD_STRETCH)
DEFAULTS_FILE_NAME = "normalization_defaults.json"


@dataclass(frozen=True)
class EnhanceSettings:
    """Enhancement parameters in slider units.

    ``clip`` is the CLAHE clip limit in tenths (20 = 2.0), ``tile`` the CLAHE grid size,
    ``lower`` / ``upper`` the contrast-stretch percentiles.
    """

    method: str = METHOD_CLAHE
    clip: int = 20
    tile: int = 8
    lower: int = 2
    upper: int = 98

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
    }

    @property
    def clip_limit(self) -> float:
        """CLAHE clip limit as OpenCV expects it."""
        return self.clip / 10.0

    def to_dict(self) -> dict[str, Any]:
        """The JSON form (``clahe_clip``, ``clahe_tile``, ``contrast_*``, ``last_method``)."""
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


def enhance(img: np.ndarray, settings: EnhanceSettings) -> np.ndarray:
    """``img`` enhanced with ``settings`` (a new array; the input is never modified)."""
    if settings.method == METHOD_STRETCH:
        return contrast_stretch(img, settings.lower, settings.upper)
    return clahe(img, settings.clip_limit, settings.tile)


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


class EnhanceDefaultsStore:
    """Load and save the user's default enhancement settings.

    Load order: the user file, then the shipped factory file, then :data:`DEFAULT_SETTINGS`.
    On first run (no user file) the factory values seed the user file, so a preference
    previously saved into the shipped file carries over. The factory file is never written.
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

    def _validated(self, data: Any, path: str) -> EnhanceSettings:
        settings, problems = EnhanceSettings.from_dict(data)
        if problems:
            logger.warning("Normalization defaults %s: %s", path, "; ".join(problems))
        return settings

    def load(self) -> EnhanceSettings:
        """The saved defaults (never raises)."""
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

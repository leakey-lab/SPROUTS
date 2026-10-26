"""
Central configuration for Root-Mask-and-Skeletons.

Single source of truth for the values that were previously hardcoded and
duplicated across the inference handlers, visualization apps, and GUI
(calibration constants, network ports, model-checkpoint paths, batch sizes,
thresholds). Importing from here keeps the scientific calibration in exactly
one place — see CALIBRATION below, which the root-length/area metrics depend on.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from resources.resource_utils import project_root

logger = logging.getLogger(__name__)

# --- Repository / resource paths -------------------------------------------
# Resolve relative to this file so behavior does not depend on the current
# working directory (fixes F-005: relative CHECKPOINTS_DIR).
PROJECT_ROOT = str(project_root())
# SPROUTS_CHECKPOINTS_DIR relocates the model weights (e.g. for a pip-installed
# SPROUTS, whose PROJECT_ROOT is site-packages); same convention as app.user_paths.
CHECKPOINTS_DIR = os.environ.get("SPROUTS_CHECKPOINTS_DIR") or os.path.join(
    PROJECT_ROOT, "checkpoints"
)
MASK_WEIGHTS_PATH = os.path.join(CHECKPOINTS_DIR, "mask_weights", "best_mask_model_V5.pth")
SKELETONIZER_DIR = os.path.join(CHECKPOINTS_DIR, "skeletonizer")
SKELETON_WEIGHTS_PATH = os.path.join(SKELETONIZER_DIR, "latest_net_G.pth")  # "<EPOCH>_net_G.pth"


@dataclass(frozen=True)
class Calibration:
    """
    Physical calibration of the imaging system.

    The camera captures a fixed physical field of view (FOV). The pixel<->mm
    conversion is derived from the FOV and the *actual* image/mask resolution,
    NOT from any hardcoded pixel dimension — this is what makes the metrics
    resolution-independent (fixes F-002, where a non-640x480 mask silently
    reported an area wrong by the square of the resolution ratio).

    The default is the frame of the Bartz BTC-100X minirhizotron camera with its I-CAP
    capture software, 18 mm x 13.5 mm at 640 x 480 px (Rajurkar et al. 2022, Plant Methods
    18:39): square pixels, 0.028125 mm. A project can state its own field of view in
    ``sprouts_project.json`` (:mod:`app.calibration`, O3).
    """

    fov_width_mm: float = 18.0
    fov_height_mm: float = 13.5

    def mm_per_px(self, width_px: int, height_px: int) -> tuple[float, float]:
        """Return (x, y) mm-per-pixel for an image of the given resolution."""
        return self.fov_width_mm / float(width_px), self.fov_height_mm / float(height_px)

    def area_per_px_mm2(self, width_px: int, height_px: int) -> float:
        """Physical area represented by one pixel (mm^2) at the given resolution."""
        mx, my = self.mm_per_px(width_px, height_px)
        return mx * my

    def mean_mm_per_px(self, width_px: int, height_px: int) -> float:
        """Mean mm-per-pixel. The default pixels are square (0.028125 mm at 640 x 480); a
        project file may set another field of view."""
        mx, my = self.mm_per_px(width_px, height_px)
        return (mx + my) / 2.0


CALIBRATION = Calibration()

# --- Visualization servers --------------------------------------------------
DASH_HOST = "127.0.0.1"  # loopback only: the dashboards are never exposed to the network
DASH_LENGTH_PORT = 8050
DASH_AREA_PORT = 8051

# --- Inference / batching ---------------------------------------------------
# Per-sample VRAM budget for the skeletonizer at 256x256 RGB. Originally
# measured as ~5 GB / 64 = 80 MB/sample under fp16/bf16 autocast; the fp32 path
# measures ~53 MiB/sample (audit PI-07), so 80 MB is a conservative estimate.
# Used to auto-size the skeleton batch to the GPU instead of hardcoding 64
# (which left a 16 GB card at ~5 GB used).
_SKELETON_BYTES_PER_SAMPLE = 80 * 1024 * 1024
_SKELETON_BATCH_DEFAULT = 64

# CPU fallback batches. Cancel is checked between batches, so a CPU batch must
# take a second or two, not ~10 s (audit wf2 R2: 16 masks = 11 s, 64 skeletons
# = 9 s on CPU, longer than the 5 s the window waits on close). BatchNorm runs in
# eval mode, but on a GPU the batch size can still change outputs slightly (cuDNN
# picks kernels per batch shape; docs/KNOWN_ISSUES.md #7).
MASK_BATCH_SIZE_CPU = 4
SKELETON_BATCH_SIZE_CPU = 8


def _cuda_usable(torch_module) -> bool:
    """CUDA available with at least one device (``CUDA_VISIBLE_DEVICES=""`` has none)."""
    if not torch_module.cuda.is_available():
        return False
    device_count = getattr(torch_module.cuda, "device_count", None)  # absent in stand-ins
    return device_count is None or device_count() > 0


def _auto_skeleton_batch(default: int = _SKELETON_BATCH_DEFAULT) -> int:
    """Size the skeleton inference batch to the GPU's free VRAM.

    Returns ``default`` on CPU or if the query fails (the CPU run itself uses
    :data:`SKELETON_BATCH_SIZE_CPU`, see ``skeleton_inference``). On CUDA, targets 70% of
    *currently free* memory (leaving headroom for the driver, fragmentation, and
    pinned host transfers), rounds down to a multiple of 8, and never goes below
    ``default``. BatchNorm runs in eval() (running stats), but the cuDNN kernel depends on
    the batch shape, so the output can change slightly with the batch size
    (docs/KNOWN_ISSUES.md #7).

    Imports torch and may create the CUDA context, so it only runs on demand
    (see :func:`get_skeleton_batch_size`), never at import.
    """
    try:
        import torch

        if not _cuda_usable(torch):
            logger.info("CUDA unavailable; using default skeleton batch=%d", default)
            return default
        free, _ = torch.cuda.mem_get_info()  # bytes free on the active device
        est = int((free * 0.70) // _SKELETON_BYTES_PER_SAMPLE)
        result = max(default, (est // 8) * 8)
        logger.info("Auto skeleton batch: %.1f GB free VRAM → batch=%d", free / 1024**3, result)
        return result
    except Exception as exc:  # noqa: BLE001 -- batch sizing must never break a run
        logger.warning("CUDA mem query failed, using default batch=%d: %s", default, exc)
        return default


_skeleton_batch: int | None = None
_skeleton_batch_lock = threading.Lock()


def get_skeleton_batch_size(refresh: bool = False) -> int:
    """Return the auto-sized skeleton batch, computing it on first use.

    The value is cached for the process. Pass ``refresh=True`` at the start of
    an inference job to re-measure free VRAM (e.g. after the mask model has
    been loaded), which also updates ``SKELETON_BATCH_SIZE``.
    """
    global _skeleton_batch
    with _skeleton_batch_lock:
        if refresh or _skeleton_batch is None:
            _skeleton_batch = _auto_skeleton_batch()
        return _skeleton_batch


# Lazily computed module attributes (PEP 562). ``SKELETON_BATCH_SIZE`` used to be
# computed at import, which imported torch and created a CUDA context for every
# importer of this module (including the Qt-free GT core). It stays importable
# (``from app.config import SKELETON_BATCH_SIZE``) but is resolved on access.
_LAZY_ATTRIBUTES = {"SKELETON_BATCH_SIZE": get_skeleton_batch_size}


def __getattr__(name: str) -> int:
    try:
        factory = _LAZY_ATTRIBUTES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return factory()


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY_ATTRIBUTES])


MASK_BATCH_SIZE = 16  # GPU; on CPU min(MASK_BATCH_SIZE, MASK_BATCH_SIZE_CPU) is used
MASK_THRESHOLD = 0.5  # sigmoid threshold for binarizing the mask model output

# Size (width, height) of the skeletonizer's saved output: the CROP_SIZE (256)
# square network output stretched by 1/ASPECT_RATIO (4/3) horizontally in
# skeleton_inference.save_image. Corrected skeletons default to the same size.
SKELETON_OUTPUT_SIZE = (341, 256)

# --- Project folder layout --------------------------------------------------
# Folders created next to the images (see app/project_layout.py, the one place
# that turns these into paths).
MASK_DIRNAME = "mask"  # masks: generated and traced, <stem>.png
SKELETONS_DIRNAME = "skeletons"  # generated skeletons, <stem>_fake.png
SKELETON_CORRECTIONS_DIRNAME = "skeleton_corrections"  # retired (WG-T3): edits go to gt/skeletons/

# Image files the library lists and the mask generator reads (lower case;
# compare with ``name.lower()``).
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def is_image_file(name: str) -> bool:
    """True if ``name`` has one of :data:`IMAGE_EXTENSIONS` (case-insensitive)."""
    return name.lower().endswith(IMAGE_EXTENSIONS)


# --- Output filenames / CSV schema -----------------------------------------
ROOT_LENGTHS_CSV = "root_lengths.csv"
ROOT_AREAS_CSV = "root_areas.csv"
LENGTH_VALUE_COLUMN = "Length (mm)"
AREA_VALUE_COLUMN = "Area (mm²)"
#: Last column of both CSVs: the row was measured from the GT file (``gt``) or the
#: prediction (``prediction``); owner decision O1 (WG-T8). ``[5]`` and ``Error`` at ``[6]``
#: keep their places.
SOURCE_COLUMN = "Source"
LENGTH_CSV_HEADERS = [
    "Image",
    "Tube",
    "Position",
    "Date",
    "Time",
    LENGTH_VALUE_COLUMN,
    "Error",
    SOURCE_COLUMN,
]
AREA_CSV_HEADERS = [
    "Image",
    "Tube",
    "Position",
    "Date",
    "Time",
    AREA_VALUE_COLUMN,
    "Error",
    SOURCE_COLUMN,
]

# --- Ground-truth (GT) tracing ---------------------------------------------
# Hand-traced multi-class ground truth, independent of every ML model in the
# app (see docs/superpowers/specs/2026-09-22-multiclass-labels-design.md).
GT_DIRNAME = "gt"
GT_CLASSES_FILENAME = "classes.json"
GT_META_CHUNK = "sprouts_gt"
GT_BACKGROUND_ID = 0
GT_IGNORE_ID = 255
GT_UNKNOWN_COLOR = "#808080"
DEFAULT_GT_CLASSES = (
    {"id": 1, "key": "root", "name": "Root", "color": "#5fd6a0", "retired": False},
    {"id": 2, "key": "nodule", "name": "Nodule", "color": "#ff7eb6", "retired": False},
    {"id": 3, "key": "root_tip", "name": "Root tip", "color": "#ffd166", "retired": False},
    {"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98", "retired": False},
)

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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- Repository / resource paths -------------------------------------------
# Resolve relative to this file so behavior does not depend on the current
# working directory (fixes F-005: relative CHECKPOINTS_DIR).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKPOINTS_DIR = os.path.join(PROJECT_ROOT, "checkpoints")
MASK_WEIGHTS_PATH = os.path.join(CHECKPOINTS_DIR, "mask_weights", "best_mask_model_V5.pth")
SKELETONIZER_DIR = os.path.join(CHECKPOINTS_DIR, "skeletonizer")


@dataclass(frozen=True)
class Calibration:
    """
    Physical calibration of the imaging system.

    The camera captures a fixed physical field of view (FOV). The pixel<->mm
    conversion is derived from the FOV and the *actual* image/mask resolution,
    NOT from any hardcoded pixel dimension — this is what makes the metrics
    resolution-independent (fixes F-002, where a non-640x480 mask silently
    reported an area wrong by the square of the resolution ratio).
    """

    fov_width_mm: float = 18.0
    fov_height_mm: float = 13.0

    def mm_per_px(self, width_px: int, height_px: int) -> tuple[float, float]:
        """Return (x, y) mm-per-pixel for an image of the given resolution."""
        return self.fov_width_mm / float(width_px), self.fov_height_mm / float(height_px)

    def area_per_px_mm2(self, width_px: int, height_px: int) -> float:
        """Physical area represented by one pixel (mm^2) at the given resolution."""
        mx, my = self.mm_per_px(width_px, height_px)
        return mx * my

    def mean_mm_per_px(self, width_px: int, height_px: int) -> float:
        """Mean isotropic mm-per-pixel (the imaging system is near-isotropic:
        18/640 vs 13/480 differ by < 4%)."""
        mx, my = self.mm_per_px(width_px, height_px)
        return (mx + my) / 2.0


CALIBRATION = Calibration()

# --- Visualization servers --------------------------------------------------
DASH_LENGTH_PORT = 8050
DASH_AREA_PORT = 8051

# --- Inference / batching ---------------------------------------------------
# Per-sample VRAM for the skeletonizer at 256x256 RGB, measured: ~5 GB / 64 ≈
# 80 MB/sample (activations + workspace, fp16/bf16 autocast). Used to auto-size
# the skeleton batch to the GPU instead of hardcoding 64 (which left a 16 GB
# card at ~5 GB used).
_SKELETON_BYTES_PER_SAMPLE = 80 * 1024 * 1024
_SKELETON_BATCH_DEFAULT = 64


def _auto_skeleton_batch(default: int = _SKELETON_BATCH_DEFAULT) -> int:
    """Size the skeleton inference batch to the GPU's free VRAM.

    Returns ``default`` on CPU or if the query fails. On CUDA, targets 70% of
    *currently free* memory (leaving headroom for the driver, fragmentation, and
    pinned host transfers), rounds down to a multiple of 8, and never goes below
    ``default``. BatchNorm runs in eval() (running stats), so the skeleton output
    is invariant to batch size -- only throughput changes.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            logger.info("CUDA unavailable; using default skeleton batch=%d", default)
            return default
        free, _ = torch.cuda.mem_get_info()  # bytes free on the active device
        est = int((free * 0.70) // _SKELETON_BYTES_PER_SAMPLE)
        result = max(default, (est // 8) * 8)
        logger.info("Auto skeleton batch: %.1f GB free VRAM → batch=%d", free / 1024**3, result)
        return result
    except Exception as exc:  # noqa: BLE001 -- batch sizing must never break startup
        logger.warning("CUDA mem query failed, using default batch=%d: %s", default, exc)
        return default


MASK_BATCH_SIZE = 16
SKELETON_BATCH_SIZE = _auto_skeleton_batch()
MASK_THRESHOLD = 0.5  # sigmoid threshold for binarizing the mask model output

# --- Output filenames / CSV schema -----------------------------------------
ROOT_LENGTHS_CSV = "root_lengths.csv"
ROOT_AREAS_CSV = "root_areas.csv"
LENGTH_CSV_HEADERS = ["Image", "Tube", "Position", "Date", "Time", "Length (mm)", "Error"]
AREA_CSV_HEADERS = ["Image", "Tube", "Position", "Date", "Time", "Area (mm²)", "Error"]

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

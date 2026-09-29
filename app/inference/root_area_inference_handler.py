"""Root-area calculation thread (thin wrapper over app.inference.measure_run)."""

from __future__ import annotations

from app.inference.measure_run import AREA, MeasureRun
from app.inference.metric_thread import MetricCalculatorThread
from app.inference.metrics import (
    area_row,
    calculate_root_area,
    load_mask,
    parse_image_name,
)

# Backwards-compatible name of the per-image worker.
process_single_mask = area_row

__all__ = [
    "calculate_root_area",
    "load_mask",
    "parse_image_name",
    "process_single_mask",
    "RootAreaCalculatorThread",
]


class RootAreaCalculatorThread(MetricCalculatorThread):
    """Measure root area of ``mask_images`` (name -> mask path) into
    ``<output_dir>/root_areas.csv``."""

    def __init__(self, mask_images: dict[str, str], output_dir: str) -> None:
        super().__init__(MeasureRun(AREA, mask_images, output_dir))

    @property
    def mask_images(self) -> dict[str, str]:
        return self.job.images

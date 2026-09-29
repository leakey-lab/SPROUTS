"""Root-length calculation thread (thin wrapper over app.inference.measure_run)."""

from __future__ import annotations

from app.inference.measure_run import LENGTH, MeasureRun
from app.inference.metric_thread import MetricCalculatorThread
from app.inference.metrics import (
    calculate_root_length,
    length_row,
    parse_image_name,
    preprocess_image,
)

# Backwards-compatible name of the per-image worker.
process_single_image = length_row

__all__ = [
    "calculate_root_length",
    "parse_image_name",
    "preprocess_image",
    "process_single_image",
    "RootLengthCalculatorThread",
]


class RootLengthCalculatorThread(MetricCalculatorThread):
    """Measure root length of ``fake_images`` (name -> skeleton path) into
    ``<output_dir>/root_lengths.csv``."""

    def __init__(self, fake_images: dict[str, str], output_dir: str) -> None:
        super().__init__(MeasureRun(LENGTH, fake_images, output_dir))

    @property
    def fake_images(self) -> dict[str, str]:
        return self.job.images

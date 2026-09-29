"""One Measure run (root length or root area), Qt-free.

A :class:`MeasureRun` computes the per-image rows once, keeps them, and writes
the metric CSV plus its provenance sidecar. Keeping the rows lets a caller retry
only the write (e.g. after the user closes the CSV in Excel) without measuring
46k images again. The Qt thread wrapper lives in
:mod:`app.inference.metric_thread`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app import provenance
from app.config import (
    AREA_CSV_HEADERS,
    AREA_VALUE_COLUMN,
    LENGTH_CSV_HEADERS,
    LENGTH_VALUE_COLUMN,
    ROOT_AREAS_CSV,
    ROOT_LENGTHS_CSV,
)
from app.inference.metrics import area_row, length_row, run_metric_pool, write_metric_csv

logger = logging.getLogger(__name__)

__all__ = ["AREA", "LENGTH", "MeasureRun", "MetricSpec", "SPECS"]


@dataclass(frozen=True)
class MetricSpec:
    """What one metric measures and where it is written."""

    kind: str  # "length" | "area" (also the SkeletonHandler.is_busy() key)
    activity: str  # provenance record kind
    label: str  # human-readable name, e.g. "root length"
    csv_name: str
    headers: tuple[str, ...]
    value_column: str
    row: Callable[[str, str], dict]  # (image name, path) -> CSV row
    parameters: Mapping[str, object]


LENGTH = MetricSpec(
    kind="length",
    activity="root_length",
    label="root length",
    csv_name=ROOT_LENGTHS_CSV,
    headers=tuple(LENGTH_CSV_HEADERS),
    value_column=LENGTH_VALUE_COLUMN,
    row=length_row,
    parameters={
        "input": "generated skeletons (*_fake.png)",
        "binarization": "Otsu, inverted if mostly white",
        "estimator": "8-connected arc length with per-axis pixel pitch",
    },
)

AREA = MetricSpec(
    kind="area",
    activity="root_area",
    label="root area",
    csv_name=ROOT_AREAS_CSV,
    headers=tuple(AREA_CSV_HEADERS),
    value_column=AREA_VALUE_COLUMN,
    row=area_row,
    parameters={"input": "masks (*.png)", "binarization": "gray > 127"},
)

SPECS: dict[str, MetricSpec] = {LENGTH.kind: LENGTH, AREA.kind: AREA}


class MeasureRun:
    """Measure ``images`` (``name -> path``) and write ``<output_dir>/<spec.csv_name>``.

    Args:
        spec: :data:`LENGTH` or :data:`AREA`.
        images: image name (stem) -> file path.
        output_dir: folder the CSV and its ``.provenance.json`` go to.
        project_root: folder paths in the provenance record are made relative
            to (default: the parent of ``output_dir``); keeps user names out.
    """

    def __init__(
        self,
        spec: MetricSpec,
        images: Mapping[str, str],
        output_dir: str,
        *,
        project_root: str | None = None,
    ) -> None:
        self.spec = spec
        self.images = dict(images)
        self.output_dir = output_dir
        self.project_root = project_root or os.path.dirname(os.path.abspath(output_dir))
        self.rows: list[dict] | None = None  # set once every image was measured

    @property
    def csv_path(self) -> str:
        return os.path.join(self.output_dir, self.spec.csv_name)

    def compute(
        self,
        progress_cb: Callable[[int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[dict] | None:
        """Measure every image (skipped if already done); ``None`` if stopped early."""
        if self.rows is None:
            rows = run_metric_pool(self.images, self.spec.row, progress_cb, should_stop)
            if should_stop is not None and should_stop():
                return None
            self.rows = rows
        return self.rows

    def write(self) -> str:
        """Atomically write the CSV (then its provenance sidecar); return the CSV path.

        Raises:
            app.io_utils.FileLockedError: the CSV is open in another program;
                the rows are kept, so calling ``write()`` again retries.
            OSError: any other write failure (the previous CSV is untouched).
        """
        if self.rows is None:
            raise RuntimeError("MeasureRun.write() called before compute() finished")
        csv_path = self.csv_path
        write_metric_csv(self.rows, csv_path, list(self.spec.headers))
        self._write_provenance(csv_path)
        return csv_path

    def _write_provenance(self, csv_path: str) -> None:
        """Best effort: a failed sidecar is logged and never fails the measurement."""
        rows = self.rows or []
        try:
            record = provenance.build_record(
                self.spec.activity,
                inputs={
                    "folder": provenance.portable_path(self.output_dir, self.project_root),
                    "n_images": len(self.images),
                    "names_sha256": provenance.names_digest(self.images),
                },
                parameters=dict(self.spec.parameters),
                outputs={
                    "csv": os.path.basename(csv_path),
                    "columns": list(self.spec.headers),
                    "value_column": self.spec.value_column,
                    "n_rows": len(rows),
                    "n_errors": sum(1 for r in rows if r.get("Error")),
                    "n_zero": sum(1 for r in rows if not r.get(self.spec.value_column)),
                },
            )
            provenance.write_sidecar(csv_path, record)
        except Exception:  # provenance is additive; the CSV is already safe
            logger.warning("Could not write provenance for %s", csv_path, exc_info=True)

"""One Measure run (root length or root area), Qt-free.

A :class:`MeasureRun` computes the per-image rows once, keeps them, and writes
the metric CSV plus its provenance sidecar and the record of the files it measured
(``<csv>.inputs.json``, :mod:`app.inference.measure_inputs`, R5). Keeping the rows lets
a caller retry only the write (e.g. after the user closes the CSV in Excel) without measuring
46k images again. The Qt thread wrapper lives in
:mod:`app.inference.metric_thread`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
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
from app.inference import measure_inputs
from app.inference.input_split import InputSplit, group_by_stem, split_by_image, split_inputs
from app.inference.measure_inputs import Fingerprint
from app.inference.metrics import area_row, length_row, run_metric_pool, write_metric_csv

logger = logging.getLogger(__name__)

__all__ = [
    "AREA",
    "LENGTH",
    "SPECS",
    "WITHOUT_IMAGE_NAMES_LIMIT",
    "InputSplit",
    "MeasureRun",
    "MetricSpec",
    "scan_png_files",
    "split_by_image",
    "split_inputs",
]

#: File names of each kind of skipped input a run's provenance lists (counts are exact).
WITHOUT_IMAGE_NAMES_LIMIT = 100


@dataclass(frozen=True)
class MetricSpec:
    """What one metric measures and where it is written."""

    kind: str  # "length" | "area" (also the SkeletonHandler.is_busy() key)
    activity: str  # provenance record kind
    label: str  # human-readable name, e.g. "root length"
    csv_name: str
    headers: tuple[str, ...]
    value_column: str
    row: Callable[..., dict]  # row(image name, path, data=<file bytes | None>) -> CSV row
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


def scan_png_files(folder: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """``(stem -> path, shadowed)`` of the ``*.png`` files of ``folder`` (extension in any
    case; one ``os.scandir``; a missing folder has none).

    Unlike ``ProjectLayout.scan_masks`` it keeps each of several files whose stems differ
    only in case, so :func:`split_inputs` can choose the one named like the image. Files
    that share a whole stem (``a.png`` and ``a.PNG``) are listed once and the others
    returned as ``shadowed`` (:func:`~app.inference.input_split.group_by_stem`).
    """
    files: list[tuple[str, str]] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name
                dot = name.rfind(".")
                if dot <= 0 or name[dot:].lower() != ".png" or not name[:dot].lstrip("."):
                    continue
                if entry.is_file():
                    files.append((name[:dot], entry.path))
    except (FileNotFoundError, NotADirectoryError):
        return {}, []
    return group_by_stem(files)


class MeasureRun:
    """Measure ``images`` (``name -> path``) and write ``<output_dir>/<spec.csv_name>``.

    Args:
        spec: :data:`LENGTH` or :data:`AREA`.
        images: image name (stem) -> file path.
        output_dir: folder the CSV and its ``.provenance.json`` go to.
        project_root: folder paths in the provenance record are made relative
            to (default: the parent of ``output_dir``); keeps user names out.
        without_image: file names of the inputs left out because their image is no longer
            in the project (:func:`split_inputs`); recorded in the provenance (O37).
        duplicates: file names left out because another file of the same image is measured.
    """

    def __init__(
        self,
        spec: MetricSpec,
        images: Mapping[str, str],
        output_dir: str,
        *,
        project_root: str | None = None,
        without_image: Sequence[str] = (),
        duplicates: Sequence[str] = (),
    ) -> None:
        self.spec = spec
        self.images = dict(images)
        self.output_dir = output_dir
        self.project_root = project_root or os.path.dirname(os.path.abspath(output_dir))
        self.without_image = tuple(without_image)
        self.duplicates = tuple(duplicates)
        self.rows: list[dict] | None = None  # set once every image was measured
        #: input key -> (size, mtime_ns, sha256) of each file measured (filled by compute()).
        self.fingerprints: dict[str, Fingerprint] = {}
        self._fingerprints_lock = threading.Lock()

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
            self.fingerprints = {}
            rows = run_metric_pool(self.images, self._measure, progress_cb, should_stop)
            if should_stop is not None and should_stop():
                return None
            self.rows = rows
        return self.rows

    def _measure(self, name: str, path: str) -> dict:
        """One row, from the file read once: its bytes are measured and hashed, and its
        size and time recorded as they were when it was read (R5). A file that cannot be
        opened gets the row ``spec.row(name, path)`` gives it, and no fingerprint."""
        try:
            with open(path, "rb") as fh:
                st = os.fstat(fh.fileno())
                data = fh.read()
        except OSError:
            return self.spec.row(name, path)
        digest = hashlib.sha256(data).hexdigest()
        measure_inputs.remember_sha256(path, st.st_size, st.st_mtime_ns, digest)
        key = measure_inputs.input_key(path, self.project_root)
        with self._fingerprints_lock:
            self.fingerprints[key] = (st.st_size, st.st_mtime_ns, digest)
        return self.spec.row(name, path, data=data)

    def write(self) -> str:
        """Atomically write the CSV (then its provenance sidecar and the record of its
        inputs); return the CSV path.

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
        self._write_inputs_record(csv_path)
        return csv_path

    def _write_inputs_record(self, csv_path: str) -> None:
        """Best effort: a failed record is logged and never fails the measurement. The
        record of an earlier run is removed then: it names that run's CSV, and a CSV
        measured again can hold the same bytes while its inputs differ."""
        try:
            measure_inputs.write_inputs_record(csv_path, self.fingerprints)
        except Exception:  # the record is additive; the CSV is already safe
            logger.warning("Could not record the inputs of %s", csv_path, exc_info=True)
            try:
                os.remove(measure_inputs.inputs_record_path(csv_path))
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove the earlier record of %s", csv_path)

    def unchanged(self) -> bool:
        """True when the CSV's record names exactly :attr:`images` and none of them
        changed since it was written (size and time, else content); False without a
        record that matches the CSV on disk."""
        record = measure_inputs.load_inputs_record(self.csv_path)
        if record is None:
            return False
        wanted = {measure_inputs.input_key(p, self.project_root) for p in self.images.values()}
        folders = {os.path.dirname(os.path.abspath(p)) for p in self.images.values()}
        current: dict[str, measure_inputs.Stamp] = {}
        for folder in folders:
            stamps = measure_inputs.scan_stamps(folder, "", self.project_root)
            current.update((key, stamp) for key, stamp in stamps.items() if key in wanted)
        return measure_inputs.is_unchanged(record, current)

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
                    "n_without_image": len(self.without_image),
                    "n_duplicate": len(self.duplicates),
                    "without_image": sorted(self.without_image)[:WITHOUT_IMAGE_NAMES_LIMIT],
                    "duplicates": sorted(self.duplicates)[:WITHOUT_IMAGE_NAMES_LIMIT],
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

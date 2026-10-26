"""One Measure run (root length or root area), Qt-free.

A :class:`MeasureRun` computes the per-image rows once, keeps them, and writes
the metric CSV plus its provenance sidecar and the record of the files it measured
(``<csv>.inputs.json``, :mod:`app.inference.measure_inputs`, R5). Keeping the rows lets
a caller retry only the write (e.g. after the user closes the CSV in Excel) without measuring
46k images again. The Qt thread wrapper lives in
:mod:`app.inference.metric_thread`.

GT first (owner decision O1): given the GT files (``gt_files``), each image is measured from
its GT file where it is valid, else from its prediction (:mod:`app.inference.measure_sources`).
Which file that is, is decided in :meth:`MeasureRun.compute`, on the job's worker, in the
same pool task that measures the image (the measuring mapper); each row's ``Source`` says
``gt`` or ``prediction``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from app import provenance
from app.config import (
    AREA_CSV_HEADERS,
    AREA_VALUE_COLUMN,
    LENGTH_CSV_HEADERS,
    LENGTH_VALUE_COLUMN,
    ROOT_AREAS_CSV,
    ROOT_LENGTHS_CSV,
    SOURCE_COLUMN,
)
from app.inference import measure_inputs, measure_sources
from app.inference.input_split import InputSplit, group_by_stem, split_by_image, split_inputs
from app.inference.measure_inputs import Fingerprint
from app.inference.measure_sources import GT, PREDICTION, GtReport, MeasureSource, SourceDecision
from app.inference.metrics import area_row, length_row, run_metric_pool, write_metric_csv
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

__all__ = [
    "AREA",
    "LENGTH",
    "PREDICTION_ONLY",
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
        "input": "gt/skeletons/<stem>.png where valid, else skeletons/<stem>_fake.png",
        "source_rule": "gt-first",
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
    parameters={
        "input": "gt/mask/<stem>.png where readable, else mask/<stem>.png",
        "source_rule": "gt-first",
        "binarization": "gray > 127",
    },
)

SPECS: dict[str, MetricSpec] = {LENGTH.kind: LENGTH, AREA.kind: AREA}

#: The ``input`` / ``source_rule`` parameters of a run that has no GT input (the legacy
#: shims measure the predictions they are given, nothing else): the inputs before O1.
PREDICTION_ONLY: dict[str, dict[str, str]] = {
    LENGTH.kind: {"input": "generated skeletons (*_fake.png)", "source_rule": "prediction"},
    AREA.kind: {"input": "masks (*.png)", "source_rule": "prediction"},
}


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
        images: image name (stem) -> file path: the predictions.
        output_dir: folder the CSV and its ``.provenance.json`` go to (created when the
            CSV is written: a project with GT files and no prediction folder gets one).
        project_root: folder paths in the provenance record are made relative
            to (default: the parent of ``output_dir``); keeps user names out.
        without_image: file names of the inputs left out because their image is no longer
            in the project (:func:`split_inputs`); recorded in the provenance (O37).
        duplicates: file names left out because another file of the same image is measured.
        gt_files: name -> path of the GT files (``gt/skeletons/`` or ``gt/mask/``, split by
            image like ``images``). Given, :meth:`compute` measures each image from its GT
            file where it is valid, else from its prediction, deciding which on the worker
            (O1); :attr:`images`, :attr:`sources` and :attr:`gt_report` then follow.
            ``None``: every image is measured from ``images``.
        photos: image name -> photo, for the size check of GT skeletons (length only).
        sources: name -> ``Source`` of each row when there are no ``gt_files`` (tests; a
            name not in it, or no mapping, is ``prediction``).
        gt_report: the GT report written to the provenance when there are no ``gt_files``.
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
        gt_files: Mapping[str, str] | None = None,
        photos: Mapping[str, str] | None = None,
        sources: Mapping[str, str] | None = None,
        gt_report: GtReport | None = None,
    ) -> None:
        self.spec = spec
        self.predictions = dict(images)
        self.gt_files = None if gt_files is None else dict(gt_files)
        self.photos = dict(photos or {})
        #: name -> the file each row is measured from. Before :meth:`compute` of a GT-first
        #: run: the prediction, else the GT file, of every image (its count is the job's).
        self.images = (
            dict(images)
            if self.gt_files is None
            else measure_sources.images_of(self.predictions, self.gt_files)
        )
        #: name -> ``Source`` of its row (``gt`` / ``prediction``).
        self.sources: dict[str, str] = dict(sources or {})
        self.gt_report = gt_report
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
        """Measure every image (skipped if already done); ``None`` if stopped early.

        With ``gt_files`` each image is decided and measured in one pool task
        (:meth:`_measure_gt_first`). Each row gets its ``Source``."""
        if self.rows is None:
            self.fingerprints = {}
            resolved = None
            if self.gt_files is None:
                rows = run_metric_pool(self.images, self._measure, progress_cb, should_stop)
            else:
                rows, resolved = self._measure_gt_first(progress_cb, should_stop)
            if should_stop is not None and should_stop():
                return None
            if resolved is not None:
                sources, self.gt_report = resolved
                self.images = {name: source.path for name, source in sources.items()}
                self.sources = {name: source.source for name, source in sources.items()}
            for row in rows:
                row[SOURCE_COLUMN] = self.sources.get(row["Image"], PREDICTION)
            self.rows = rows
        return self.rows

    def _measure_gt_first(
        self,
        progress_cb: Callable[[int], None] | None,
        should_stop: Callable[[], bool] | None,
    ) -> tuple[list[dict], tuple[dict[str, MeasureSource], GtReport]]:
        """The rows, and the sources and GT report, of a GT-first run: the resolver of
        :mod:`~app.inference.measure_sources` with a measuring mapper. Each pool task
        decides one image, records the GT file it read (used or not), measures the file
        the decision names (a used GT file from the bytes it validated) and keeps the
        decision without those bytes."""
        rows: list[dict] = []

        def measuring_map(decide: Callable[..., SourceDecision], *columns) -> list:
            names = list(columns[0])
            args_of = dict(zip(names, zip(*columns, strict=True), strict=True))
            decided: dict[str, SourceDecision] = {}

            def task(name: str, _job: str) -> dict | None:
                args = args_of[name]
                decision = decide(*args)
                gt = args[2]
                if gt is not None and decision.fingerprint is not None:
                    self._remember(gt, decision.fingerprint)
                decided[name] = replace(decision, data=None)
                return self._measure_decided(decision)

            rows.extend(run_metric_pool({n: n for n in names}, task, progress_cb, should_stop))
            # A stopped run has no decision for the images it never reached (unused then).
            return [decided.get(name) or SourceDecision(name, None) for name in names]

        layout = ProjectLayout(self.project_root)
        if self.spec.kind == LENGTH.kind:
            resolved = measure_sources.resolve_length_sources(
                layout, self.photos, self.predictions, self.gt_files, mapper=measuring_map
            )
        else:
            resolved = measure_sources.resolve_area_sources(
                layout, self.predictions, self.gt_files, mapper=measuring_map
            )
        return rows, resolved

    def _measure_decided(self, decision: SourceDecision) -> dict | None:
        """The row of one decided image (``None``: it gets none): a used GT file is measured
        from the bytes it was validated from, a prediction is read (:meth:`_measure`)."""
        source = decision.source
        if source is None:
            return None
        if source.source == GT and decision.data is not None:
            return self.spec.row(source.stem, source.path, data=decision.data)
        return self._measure(source.stem, source.path)

    def _remember(self, path: str, fingerprint: Fingerprint) -> None:
        """Record ``path`` as read with ``fingerprint`` (any thread)."""
        size, mtime_ns, digest = fingerprint
        measure_inputs.remember_sha256(path, size, mtime_ns, digest)
        key = measure_inputs.input_key(path, self.project_root)
        with self._fingerprints_lock:
            self.fingerprints[key] = fingerprint

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
        self._remember(path, (st.st_size, st.st_mtime_ns, digest))
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
        os.makedirs(self.output_dir, exist_ok=True)
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
        """True when the CSV's record names exactly :attr:`images` (and, for a GT-first run
        after :meth:`compute`, the GT files it read) and none of them changed since it was
        written (size and time, else content); False without a record that matches the CSV
        on disk."""
        record = measure_inputs.load_inputs_record(self.csv_path)
        if record is None:
            return False
        paths = {*self.images.values(), *(self.gt_files or {}).values()}
        wanted = {measure_inputs.input_key(p, self.project_root) for p in paths}
        folders = {os.path.dirname(os.path.abspath(p)) for p in paths}
        current: dict[str, measure_inputs.Stamp] = {}
        for folder in folders:
            stamps = measure_inputs.scan_stamps(folder, "", self.project_root)
            current.update((key, stamp) for key, stamp in stamps.items() if key in wanted)
        return measure_inputs.is_unchanged(record, current)

    def _parameters(self) -> dict:
        """The spec's parameters; a run without GT input (no ``gt_files`` and no
        ``gt_report``: the legacy shims) measured predictions only, and says so."""
        parameters = dict(self.spec.parameters)
        if self.gt_files is None and self.gt_report is None:
            parameters.update(PREDICTION_ONLY[self.spec.kind])
        return parameters

    def _write_provenance(self, csv_path: str) -> None:
        """Best effort: a failed sidecar is logged and never fails the measurement. The
        sidecar of an earlier run is removed then: it describes that run's CSV (its
        ``inputs.gt.skipped`` must never pair with this one)."""
        rows = self.rows or []
        try:
            inputs = {
                "folder": provenance.portable_path(self.output_dir, self.project_root),
                "n_images": len(self.images),
                "names_sha256": provenance.names_digest(self.images),
                "n_without_image": len(self.without_image),
                "n_duplicate": len(self.duplicates),
                "without_image": sorted(self.without_image)[:WITHOUT_IMAGE_NAMES_LIMIT],
                "duplicates": sorted(self.duplicates)[:WITHOUT_IMAGE_NAMES_LIMIT],
            }
            report = self.gt_report
            if report is not None:
                inputs["gt"] = {
                    "folder": report.folder,
                    "n_used": report.n_used,
                    "files": dict(report.files),  # a read-only mapping: JSON wants a dict
                    "skipped": [{"stem": stem, "reason": why} for stem, why in report.skipped],
                }
            record = provenance.build_record(
                self.spec.activity,
                inputs=inputs,
                parameters=self._parameters(),
                outputs={
                    "csv": os.path.basename(csv_path),
                    "columns": list(self.spec.headers),
                    "value_column": self.spec.value_column,
                    "n_rows": len(rows),
                    "n_errors": sum(1 for r in rows if r.get("Error")),
                    "n_zero": sum(1 for r in rows if not r.get(self.spec.value_column)),
                    "n_gt": sum(1 for r in rows if r.get(SOURCE_COLUMN) == GT),
                    "n_prediction": sum(1 for r in rows if r.get(SOURCE_COLUMN) == PREDICTION),
                },
            )
            provenance.write_sidecar(csv_path, record)
        except Exception:  # provenance is additive; the CSV is already safe
            logger.warning("Could not write provenance for %s", csv_path, exc_info=True)
            try:
                os.remove(provenance.sidecar_path(csv_path))
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove the earlier provenance of %s", csv_path)

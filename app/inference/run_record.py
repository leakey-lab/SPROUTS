"""Provenance manifest for one generation run (FR-02 / PI-09).

Every mask or skeleton run writes ``<output_dir>/_sprouts_run.json``
(``app.provenance.RUN_MANIFEST_FILENAME``): app version and git commit, model
checkpoint SHA-256, the resolved inference runtime (device, precision "tf32"/"fp32",
cuDNN benchmark/deterministic flags), run parameters, a digest of the input names,
and the outcome (status "completed" / "cancelled" / "failed" with the error, counts,
failed inputs with reasons, skipped stems, cancel flag).
Every image scanner filters by image extension, so the JSON is never read as data.
The file describes the latest run into that folder that wrote or failed an input
(including one that raised after writing outputs, R7); a run that did neither
leaves it alone, so a *Keep existing masks* run with nothing missing, a cancel
before the first batch and a run that raised before writing anything keep the
record of the run that made the outputs. Every such run is also one line of
``_sprouts_runs.jsonl`` (``provenance.RUN_HISTORY_FILENAME``; O16). When the full record cannot be
built or written, a minimal one (``provenance.minimal_record``: the outcome and
``provenance_error``) replaces it, and if even that fails the outdated file is
removed: the folder never describes an older run than its outputs (R5).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app import provenance
from app.inference.batch_engine import BatchReport

logger = logging.getLogger(__name__)

#: A history line lists the written names up to this many; beyond it, a count and a digest.
HISTORY_NAMES_LIMIT = 1000


def _reason(exc: BaseException) -> str:
    """``"<ExceptionType>: <message>"``, the form ``outputs.error`` uses."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def write_run_manifest(
    kind: str,
    *,
    input_dir: str,
    output_dir: str,
    input_names: Sequence[str],
    report: BatchReport,
    models: Mapping[str, str],
    parameters: Mapping[str, Any],
) -> str | None:
    """Write the run manifest and append the run to the history; returns the manifest path.

    A run that wrote nothing, failed nothing and did not raise (a *Keep existing
    masks* run with nothing missing, a cancel before the first batch) changed nothing:
    it writes nothing and returns None, so the record of the run that made the outputs
    stays. A provenance failure is logged and never fails the run (the outputs exist);
    the minimal record, or else the removal of the outdated one, keeps the folder from
    describing an older run (R5); None means it could not be written. Call from the
    worker thread: the first call hashes the checkpoints (~0.3 s).
    """
    if not report.written and not report.failed and report.error is None:
        logger.info("Run changed nothing in %s; the run manifest is left as is", output_dir)
        return None
    try:
        record = provenance.build_record(
            kind,
            inputs={
                "folder": provenance.portable_path(input_dir),
                "n_images": len(input_names),
                "names_sha256": provenance.names_digest(input_names),
            },
            models=models,
            parameters=parameters,
            outputs=report.summary(),
        )
        path = provenance.write_run_manifest(output_dir, record)
    except Exception as exc:  # noqa: BLE001 - (R7) any provenance failure, not only I/O
        logger.warning("Could not write the run manifest in %s: %s", output_dir, exc, exc_info=exc)
        return _write_minimal_manifest(kind, output_dir, report, exc)
    _append_history(output_dir, record, report)
    return path


def history_entry(record: Mapping[str, Any], report: BatchReport) -> dict[str, Any]:
    """The line of ``_sprouts_runs.jsonl`` for one run: the record's identity and the outcome.

    Written names are listed (sorted) up to ``HISTORY_NAMES_LIMIT``; a longer run
    carries ``written_count`` and ``written_sha256`` instead, so a line stays small.
    """
    app = record.get("app") or {}
    models = record.get("models") or {}
    entry: dict[str, Any] = {
        "created_utc": record.get("created_utc"),
        "kind": record.get("kind"),
        "app_version": app.get("version"),
        "git_commit": record.get("git_commit"),
        "models": {name: (info or {}).get("sha256") for name, info in models.items()},
        "status": report.status,
        "n_written": len(report.written),
        "n_failed": len(report.failed),
        "n_skipped": len(report.skipped),
    }
    if len(report.written) <= HISTORY_NAMES_LIMIT:
        entry["written"] = sorted(report.written)
    else:
        entry["written_count"] = len(report.written)
        entry["written_sha256"] = provenance.names_digest(report.written)
    return entry


def _append_history(output_dir: str, record: Mapping[str, Any], report: BatchReport) -> None:
    """Append this run to the history; a failure is logged and never fails the run."""
    try:
        provenance.append_run_history(output_dir, history_entry(record, report))
    except Exception as exc:  # noqa: BLE001 - same rule as the manifest: the outputs exist
        logger.warning("Could not append to the run history in %s: %s", output_dir, exc)


def _write_minimal_manifest(
    kind: str, output_dir: str, report: BatchReport, cause: BaseException
) -> str | None:
    """(R5) Replace the manifest by a minimal record, else remove the outdated one.

    Never raises. Returns the path written, or None.
    """
    try:
        record = provenance.minimal_record(kind, outputs=report.summary(), reason=_reason(cause))
        path = provenance.write_run_manifest(output_dir, record)
    except Exception as exc:  # noqa: BLE001 - e.g. the disk is full: nothing can be written
        logger.warning("Could not write a minimal run manifest in %s: %s", output_dir, exc)
    else:
        _append_history(output_dir, record, report)
        return path
    path = provenance.run_manifest_path(output_dir)
    try:
        os.remove(path)
        logger.warning("Removed the outdated run manifest %s: it described an older run", path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("Could not remove the outdated run manifest %s: %s", path, exc)
    return None


def record_failed_run(
    report: BatchReport, error: BaseException, write_manifest: Callable[[], object]
) -> None:
    """Describe a run that raised (R7); call from its ``except`` block, then re-raise.

    Outputs already on disk changed the folder, so ``write_manifest()`` replaces the
    previous manifest with one for this run (``status`` "failed", the error, the
    partial counts). A run that wrote nothing leaves the previous manifest alone: it
    still describes the outputs there. Never raises, so the run's own error is the
    one that propagates.
    """
    try:
        report.mark_failed(error)
        if report.written:
            write_manifest()
        else:
            logger.info("Run failed before writing any output; the run manifest is left as is")
    except Exception as exc:  # noqa: BLE001 - must not mask the run's own error
        logger.error("Could not record the failed run: %s", exc, exc_info=exc)


__all__ = ["HISTORY_NAMES_LIMIT", "history_entry", "record_failed_run", "write_run_manifest"]

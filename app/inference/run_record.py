"""Provenance manifest for one generation run (FR-02 / PI-09).

Every mask or skeleton run writes ``<output_dir>/_sprouts_run.json``
(``app.provenance.RUN_MANIFEST_FILENAME``): app version and git commit, model
checkpoint SHA-256, the resolved inference runtime (device, precision "tf32"/"fp32",
cuDNN benchmark/deterministic flags), run parameters, a digest of the input names,
and the outcome (status "completed" / "cancelled" / "failed" with the error, counts,
failed inputs with reasons, skipped stems, cancel flag).
Every image scanner filters by image extension, so the JSON is never read as data.
The file describes the latest run into that folder that returned (completed or
cancelled, even with no outputs) or that raised after writing outputs (R7); a run
that raised before writing anything leaves it alone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app import provenance
from app.inference.batch_engine import BatchReport

logger = logging.getLogger(__name__)


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
    """Write the run manifest; returns its path, or None if it could not be written.

    A provenance failure is logged and never fails the run (the outputs exist).
    Call from the worker thread: the first call hashes the checkpoints (~0.3 s).
    """
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
        return provenance.write_run_manifest(output_dir, record)
    except Exception as exc:  # noqa: BLE001 - (R7) any provenance failure, not only I/O
        logger.warning("Could not write the run manifest in %s: %s", output_dir, exc, exc_info=exc)
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


__all__ = ["record_failed_run", "write_run_manifest"]

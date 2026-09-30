"""Batch engine shared by mask and skeleton generation (Qt-free).

One loop for both models, so their failure, memory and cancel policies cannot drift:

* PI-03 / FR-05 / RB-10 -- an input that cannot be decoded is recorded in
  ``BatchReport.failed`` and never gets an output file. Its slot in the batch is
  zero-filled so the valid images still run in exactly the serial batch
  composition (bit-identical outputs); the zero tensor never reaches disk.
* PI-06 -- decode (thread pool, one batch ahead via ``PrefetchIterator``), the GPU
  forward (caller's thread) and PNG writes (``AsyncWriter``) overlap. Batches keep
  the serial order and composition, so outputs are unchanged.
* PI-07 -- CUDA out-of-memory halves the batch and retries down to one image
  (``OomBackoff``); the size that worked is kept for later batches. Only an
  image that still fails alone is recorded as failed.
* PI-08 -- ``should_stop()`` is checked at every batch boundary; outputs already
  computed are still written, and the report says the run was cancelled.
* R7 -- a caller's ``report`` is filled in place, also when a step raises: the
  caller still knows which outputs reached disk and can describe the failed run.

torch is imported lazily (only a running job needs it).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.inference.batch_pipeline import (
    AsyncWriter,
    PrefetchIterator,
    default_io_threads,
    progress_percent,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


@dataclass
class BatchReport:
    """Outcome of one batch run (also the ``outputs`` section of its run manifest)."""

    total: int = 0  # inputs processed by this run (after skips)
    written: list[str] = field(default_factory=list)  # input file names with an output
    failed: list[tuple[str, str]] = field(default_factory=list)  # (input file name, reason)
    skipped: list[str] = field(default_factory=list)  # stems left alone by the skip policy
    cancelled: bool = False
    batch_size: int = 0  # requested
    effective_batch_size: int = 0  # after any out-of-memory backoff
    elapsed_s: float = 0.0
    error: str | None = None  # (R7) "<ExceptionType>: <message>" when the run raised

    @property
    def failed_names(self) -> list[str]:
        return [name for name, _ in self.failed]

    @property
    def done(self) -> int:
        """Inputs finished either way (written or failed)."""
        return len(self.written) + len(self.failed)

    @property
    def status(self) -> str:
        """``"failed"`` (the run raised), ``"cancelled"`` or ``"completed"`` (R7).

        "completed" means the run reached the end, even if every input failed.
        """
        if self.error is not None:
            return "failed"
        return "cancelled" if self.cancelled else "completed"

    def mark_failed(self, exc: BaseException) -> None:
        """Record the exception that ended the run: ``status`` becomes "failed" (R7)."""
        self.error = _describe(exc)

    def summary(self) -> dict[str, Any]:
        """JSON-able counts + lists for a provenance record."""
        return {
            "status": self.status,
            "error": self.error,
            "n_inputs": self.total,
            "n_written": len(self.written),
            "n_failed": len(self.failed),
            "n_skipped": len(self.skipped),
            "cancelled": self.cancelled,
            "batch_size": self.batch_size,
            "effective_batch_size": self.effective_batch_size,
            "elapsed_s": round(self.elapsed_s, 3),
            "failed": [{"input": name, "reason": reason} for name, reason in self.failed],
            "skipped": sorted(self.skipped),
        }


@dataclass(frozen=True)
class BatchSpec:
    """The model-specific pieces of a batch run.

    Attributes:
        load: decode + transform one input path into a CHW tensor (raise if unreadable).
        step: forward + post-process one stacked batch; returns one host array per item.
        save: write one item's array as the output for its input path (raise on failure).
    """

    load: Callable[[str], Any]
    step: Callable[[Any], Sequence[Any]]
    save: Callable[[Any, str], None]


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class ItemOutOfMemory(Exception):
    """One image that ran out of GPU memory even alone (a recorded failure, no traceback)."""


def is_out_of_memory(exc: BaseException) -> bool:
    """True for a CUDA out-of-memory error (the only error worth a smaller retry)."""
    import torch

    return isinstance(exc, torch.cuda.OutOfMemoryError)


def _free_cuda_cache() -> None:
    import torch

    if torch.cuda.is_available() and torch.cuda.is_initialized():
        torch.cuda.empty_cache()


class OomBackoff:
    """Run a batch step, halving the batch on CUDA out-of-memory (PI-07).

    ``size`` starts at the requested batch size and only shrinks: once a size
    works, later batches are split to it up front instead of failing again.
    """

    def __init__(
        self,
        batch_size: int,
        *,
        is_oom: Callable[[BaseException], bool] = is_out_of_memory,
        on_oom: Callable[[], None] = _free_cuda_cache,
    ) -> None:
        self.size = max(1, int(batch_size))
        self._is_oom = is_oom
        self._on_oom = on_oom

    def run(self, step: Callable[[Any], Any], batch: Any) -> list[tuple[slice, Any]]:
        """``[(slice, result_or_exception), ...]`` covering ``batch`` in order.

        A result is whatever ``step`` returned for that slice; an exception
        appears only for single items that ran out of memory on their own.
        Any other exception propagates.
        """
        results: list[tuple[slice, Any]] = []
        n = len(batch)
        start = 0
        while start < n:
            stop = min(n, start + self.size)
            results.extend(self._run_slice(step, batch, start, stop))
            start = stop
        return results

    def _run_slice(self, step, batch, start: int, stop: int) -> list[tuple[slice, Any]]:
        try:
            return [(slice(start, stop), step(batch[start:stop]))]
        except Exception as exc:
            if not self._is_oom(exc):
                raise
            reason = _describe(exc)
        # Retry only after leaving the ``except`` block: the exception's traceback
        # keeps the failed forward's frames -- and their activations -- alive, so
        # a retry inside it would run out of memory again at every size.
        self._on_oom()
        if stop - start == 1:
            logger.error("Out of memory on a single image (index %d): %s", start, reason)
            return [(slice(start, stop), ItemOutOfMemory(reason))]
        half = (stop - start + 1) // 2
        self.size = min(self.size, half)
        logger.warning("Out of memory at batch %d; retrying at %d", stop - start, half)
        return self._run_slice(step, batch, start, start + half) + self._run_slice(
            step, batch, start + half, stop
        )


class _Progress:
    """Thread-safe done counter that reports only when the integer percent grows."""

    def __init__(self, total: int, callback: ProgressCallback | None) -> None:
        self._total = total
        self._callback = callback
        self._done = 0
        self._last_pct = -1
        self._lock = threading.Lock()

    def add(self, n: int = 1) -> None:
        if self._callback is None:
            return
        with self._lock:
            self._done += n
            pct = progress_percent(self._done, self._total)
            if pct <= self._last_pct:
                return
            self._last_pct = pct
            done = self._done
        self._callback(done, self._total)


def run_batches(
    paths: Sequence[str],
    spec: BatchSpec,
    *,
    batch_size: int,
    io_threads: int | None = None,
    pin_memory: bool = False,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    report: BatchReport | None = None,
) -> BatchReport:
    """Run ``spec`` over ``paths`` in fixed-size batches; see the module docstring.

    ``report`` (R7): filled in place and returned when given, so a caller still
    has the partial outcome (the outputs on disk) when a step raises.
    """
    started = time.perf_counter()
    if report is None:
        report = BatchReport()
    report.total = len(paths)
    report.batch_size = report.effective_batch_size = batch_size
    if not paths:
        return report

    import torch

    io = io_threads or default_io_threads()
    ticks = _Progress(len(paths), progress)
    batches = [list(paths[i : i + batch_size]) for i in range(0, len(paths), batch_size)]
    backoff = OomBackoff(batch_size)
    written: list[str] = []
    written_lock = threading.Lock()
    writer: AsyncWriter | None = None

    def save_one(arr: Any, path: str) -> None:
        try:
            spec.save(arr, path)
            with written_lock:
                written.append(os.path.basename(path))
        finally:
            ticks.add()  # progress counts outputs once they are on disk (or failed)

    def record_failure(path: str, reason: str) -> None:
        report.failed.append((os.path.basename(path), reason))
        ticks.add()

    with ThreadPoolExecutor(io, thread_name_prefix="decode") as decode_pool:

        def load_safe(path: str) -> tuple[Any, str | None]:
            try:
                return spec.load(path), None
            except Exception as exc:  # noqa: BLE001 - any decode failure is per-image
                logger.warning("Unreadable input %s: %s", path, exc)
                return None, _describe(exc)

        def load_batch(batch_paths: list[str]) -> tuple[Any, dict[int, str]]:
            loaded = list(decode_pool.map(load_safe, batch_paths))
            bad = {i: err for i, (_, err) in enumerate(loaded) if err is not None}
            good = [t for t, err in loaded if err is None]
            if not good:
                return None, bad
            template = good[0]
            x = torch.stack([t if err is None else torch.zeros_like(template) for t, err in loaded])
            return (x.pin_memory() if pin_memory else x), bad

        try:
            with (
                PrefetchIterator.map(
                    load_batch, batches, depth=2, workers=1, should_stop=should_stop
                ) as prefetched,
                AsyncWriter(io, max_pending=2 * batch_size) as writer,
            ):
                for batch_paths, (x, bad) in zip(
                    batches, prefetched, strict=False
                ):  # cancel ends early
                    for i, reason in bad.items():
                        record_failure(batch_paths[i], reason)
                    if x is None:
                        continue
                    for chunk, result in backoff.run(spec.step, x):
                        for offset, i in enumerate(range(chunk.start, chunk.stop)):
                            if i in bad:
                                continue  # zero-filled slot: never written
                            if isinstance(result, BaseException):
                                record_failure(batch_paths[i], _describe(result))
                                continue
                            writer.submit(batch_paths[i], save_one, result[offset], batch_paths[i])
        finally:
            # (R7) Leaving the ``with`` drained the writer (``AsyncWriter.close`` waits
            # for every queued save), so this is what is on disk even if a step raised.
            if writer is not None:
                for key, exc in writer.errors():
                    report.failed.append((os.path.basename(str(key)), _describe(exc)))
            report.written = sorted(written)
            report.effective_batch_size = backoff.size
            report.elapsed_s = time.perf_counter() - started
            report.cancelled = bool(should_stop and should_stop()) and report.done < report.total
    return report


__all__ = [
    "BatchReport",
    "BatchSpec",
    "ItemOutOfMemory",
    "OomBackoff",
    "is_out_of_memory",
    "run_batches",
]

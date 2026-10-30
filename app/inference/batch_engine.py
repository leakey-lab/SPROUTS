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
* R7 -- a save that finds the disk full ends the run with that error at the next
  batch boundary (every later save would fail too); other save errors stay
  per-image failures.
* R1 (O4) -- ``run_batches(..., fixed=True)``: every forward gets exactly
  ``batch_size`` rows, so an image's output no longer depends on how many images
  share its batch. A short last batch is padded with zero rows whose outputs are
  never saved. CUDA out-of-memory stops the run (``FixedBatchOutOfMemory``): no
  halving and no retry, because a retry can succeed on another cuDNN kernel and
  change outputs without an error (``research/o4-gpu-batch.md``, Exp 1c).
* S12-01 -- ``run_batches(..., torch_threads=1)`` (GPU runs): the decode, prefetch and
  save threads each run their small tensor ops on one torch thread instead of each
  starting an OpenMP team of all the cores; the caller's count is back afterwards.
* C6-02 -- per-input failures are logged for the first ``FAILURE_LOG_LIMIT`` inputs
  of a kind, then counted; the report keeps every one.

torch is imported lazily (only a running job needs it).
"""

from __future__ import annotations

import contextlib
import errno
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.inference.batch_pipeline import (
    AsyncWriter,
    LogLimiter,
    PrefetchIterator,
    default_io_threads,
    progress_percent,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]

_FAILED_SHOWN = 5  # (C6-02) failures named in a run's end-of-run log line


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
    # (R1) forward size (rows of the stacked tensor, zero-filled slots included) -> number of
    # input slots that ran at that size; only forwards that returned a result count. (O4)
    # Under ``fixed`` the size includes the tail's padding rows; the count never does.
    batch_shapes: dict[int, int] = field(default_factory=dict)
    # (R1) one {"at": first input of the slice, "from": length, "to": retry length} per backoff
    oom_events: list[dict[str, Any]] = field(default_factory=list)
    # (O4) free GPU memory (GiB) the pre-flight of a fixed-policy GPU run measured, else None
    free_vram_gib: float | None = None

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
            "batch_shapes": {
                str(size): self.batch_shapes[size]
                for size in sorted(self.batch_shapes, reverse=True)
            },
            "oom_events": [dict(event) for event in self.oom_events],
            "free_vram_gib": None if self.free_vram_gib is None else round(self.free_vram_gib, 2),
            "elapsed_s": round(self.elapsed_s, 3),
            "failed": [{"input": name, "reason": reason} for name, reason in self.failed],
            "skipped": sorted(self.skipped),
        }


def log_failed_inputs(
    log: logging.Logger, label: str, report: BatchReport, manifest: str | None
) -> None:
    """One bounded warning for the inputs of a finished run that got no output (C6-02).

    It names the first few with their reasons and counts the rest. ``manifest``, the run
    record that lists every one, is named only when it was written. Nothing is logged
    for a run without failures.
    """
    if not report.failed:
        return
    shown = "; ".join(f"{name}: {reason}" for name, reason in report.failed[:_FAILED_SHOWN])
    more = len(report.failed) - _FAILED_SHOWN
    log.warning(
        "%s: %d input(s) failed: %s%s%s",
        label,
        len(report.failed),
        shown,
        f"; and {more} more" if more > 0 else "",
        f"; every one is listed in {manifest}" if manifest else "",
        stacklevel=2,
    )


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
    if isinstance(exc, ItemOutOfMemory):
        return str(exc)  # (R5) already the original error's description: no second prefix
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# (R7) Errors that mean the disk (or the user's quota) is full: every later save fails.
_DISK_FULL_ERRNOS = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})
_DISK_FULL_WINERRORS = frozenset({39, 112})  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL


def is_disk_full(exc: BaseException) -> bool:
    """True for a write error that means no more space: the run cannot go on (R7)."""
    return isinstance(exc, OSError) and (
        exc.errno in _DISK_FULL_ERRNOS or getattr(exc, "winerror", None) in _DISK_FULL_WINERRORS
    )


class ItemOutOfMemory(Exception):
    """One image that ran out of GPU memory even alone (a recorded failure, no traceback)."""


_FIXED_WAY_OUT = (
    "Close other GPU programs, or set SPROUTS_BATCH_POLICY=auto to let SPROUTS use "
    "smaller batches (a few pixels can then differ)."
)
#: (O4) A fixed-policy run that ran out of GPU memory; wording from research/o4-gpu-batch.md.
FIXED_OOM_MESSAGE = "Out of GPU memory at the fixed batch size ({n}). " + _FIXED_WAY_OUT
#: (O4) The pre-flight check found less free GPU memory than the fixed size needs.
FIXED_MEMORY_MESSAGE = (
    "Not enough free GPU memory for the fixed batch size ({n}): {free:.1f} GiB free, "
    "{needed:.1f} GiB needed. " + _FIXED_WAY_OUT
)


class FixedBatchOutOfMemory(RuntimeError):
    """A fixed-policy run ran out of GPU memory: the run stops (O4, no smaller retry)."""

    def __init__(self, batch_size: int) -> None:
        self.batch_size = int(batch_size)
        super().__init__(FIXED_OOM_MESSAGE.format(n=self.batch_size))


class FixedBatchMemoryTooLow(FixedBatchOutOfMemory):
    """The pre-flight check stopped a fixed-policy GPU run before its first batch (O4).

    ``free_gib`` / ``needed_gib`` are GiB; the message shows them with one decimal,
    the free amount rounded down so it never reads as enough.
    """

    def __init__(self, batch_size: int, *, free_gib: float, needed_gib: float) -> None:
        self.batch_size = int(batch_size)
        self.free_gib = float(free_gib)
        self.needed_gib = float(needed_gib)
        RuntimeError.__init__(
            self,
            FIXED_MEMORY_MESSAGE.format(
                n=self.batch_size,
                free=math.floor(self.free_gib * 10) / 10,
                needed=self.needed_gib,
            ),
        )


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
    A slice of more than one image that is at least as long as one that already
    ran out of memory is split untried (R5); every shorter slice is tried as
    before, so the slices that run are the plain halving's (outputs unchanged).
    """

    def __init__(
        self,
        batch_size: int,
        *,
        is_oom: Callable[[BaseException], bool] = is_out_of_memory,
        on_oom: Callable[[], None] = _free_cuda_cache,
    ) -> None:
        self.size = max(1, int(batch_size))
        self._oom_len: float = float("inf")  # (R5) shortest slice split after out-of-memory
        self.events: list[tuple[int, int, int]] = []  # (R1) (start index, from, to) per backoff
        self._is_oom = is_oom
        self._on_oom = on_oom
        # (C6-02) A GPU too small for one image fails every image alone.
        self.single_failures = LogLimiter(logger, "single images out of GPU memory")

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
        """Run ``batch[start:stop]``, halving it on out-of-memory (see the class docstring)."""
        if stop - start > 1 and stop - start >= self._oom_len:
            # (R5) At least as long as a slice that already ran out of memory (e.g. the
            # sibling half): it would run out again, so split it untried. A single
            # image is always tried; shorter slices are tried as before.
            return self._split(step, batch, start, stop)
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
            self.single_failures.log(
                logging.ERROR, "Out of memory on a single image (index %d): %s", start, reason
            )
            return [(slice(start, stop), ItemOutOfMemory(reason))]
        self._oom_len = min(self._oom_len, stop - start)  # (R5)
        self.events.append((start, stop - start, (stop - start + 1) // 2))
        logger.warning(
            "Out of memory at batch %d; retrying at %d", stop - start, (stop - start + 1) // 2
        )
        return self._split(step, batch, start, stop)

    def _split(self, step, batch, start: int, stop: int) -> list[tuple[slice, Any]]:
        """Run both halves (the first takes an odd item); ``size`` shrinks as before (R5)."""
        half = (stop - start + 1) // 2
        self.size = min(self.size, half)
        return self._run_slice(step, batch, start, start + half) + self._run_slice(
            step, batch, start + half, stop
        )


def _run_fixed(step: Callable[[Any], Any], x: Any, n_real: int) -> list[tuple[slice, Any]]:
    """Run one fixed-size forward; ``[(slice(0, n_real), result)]`` (padding rows dropped).

    CUDA out-of-memory raises ``FixedBatchOutOfMemory`` after leaving the ``except``
    block (its traceback would keep the failed forward's activations alive) and
    freeing the cache. Any other error propagates unchanged.
    """
    try:
        return [(slice(0, n_real), step(x))]
    except Exception as exc:
        if not is_out_of_memory(exc):
            raise
        reason = _describe(exc)
    _free_cuda_cache()
    logger.error("Out of memory at the fixed batch size %d; stopping: %s", len(x), reason)
    raise FixedBatchOutOfMemory(len(x))


class _Progress:
    """Thread-safe done counter that reports only when the integer percent grows.

    The callback runs under the lock, so updates from several writer threads
    arrive in order: an early count delivered late can no longer send the
    progress bar backwards or leave it below the total (R3). A callback that
    raises is logged and ignored (R3): progress is advisory, and a failure there
    must neither mark a written input as failed nor end the run.
    """

    def __init__(self, total: int, callback: ProgressCallback | None) -> None:
        self._total = total
        self._callback = callback
        self._done = 0
        self._last_pct = -1
        self._lock = threading.Lock()
        self._callback_failed = False

    def add(self, n: int = 1) -> None:
        if self._callback is None:
            return
        with self._lock:
            self._done += n
            pct = progress_percent(self._done, self._total)
            if pct <= self._last_pct:
                return
            self._last_pct = pct
            try:
                self._callback(self._done, self._total)
            except Exception as exc:  # noqa: BLE001 - see the class docstring
                # The first failure with its traceback; repeats would flood the log.
                logger.warning(
                    "Progress callback failed (ignored): %s",
                    exc,
                    exc_info=None if self._callback_failed else exc,
                )
                self._callback_failed = True


@contextlib.contextmanager
def _torch_threads(n: int | None) -> Iterator[None]:
    """torch's intra-op thread count at ``n`` inside the block, the previous one after (S12-01).

    A thread takes the count at its first parallel torch op and keeps it, so the
    run's pools must be created inside the block. ``None`` changes nothing, and so
    does a build without OpenMP: torch's own thread pool is shared by every thread
    (no per-thread teams), and its size cannot be changed once it has started.
    """
    import torch

    if n is None or not torch.backends.openmp.is_available():
        yield
        return
    previous = torch.get_num_threads()
    torch.set_num_threads(n)
    try:
        yield
    finally:
        torch.set_num_threads(previous)


def run_batches(
    paths: Sequence[str],
    spec: BatchSpec,
    *,
    batch_size: int,
    io_threads: int | None = None,
    pin_memory: bool = False,
    torch_threads: int | None = None,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    report: BatchReport | None = None,
    fixed: bool = False,
) -> BatchReport:
    """Run ``spec`` over ``paths`` in fixed-size batches; see the module docstring.

    ``report`` (R7): filled in place and returned when given, so a caller still
    has the partial outcome (the outputs on disk) when a step raises.
    ``fixed`` (R1, O4): every forward runs at exactly ``batch_size`` rows (the last
    batch zero-padded); out of GPU memory raises ``FixedBatchOutOfMemory``.
    ``torch_threads`` (S12-01): torch's intra-op thread count while the run lasts
    (``RuntimeConfig.batch_torch_threads``: 1 on CUDA); None leaves it alone.
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
    unreadable = LogLimiter(logger, "unreadable inputs")  # (C6-02)
    shapes: dict[int, int] = {}
    oom_events: list[dict[str, Any]] = []
    written: list[str] = []
    written_lock = threading.Lock()
    writer: AsyncWriter | None = None
    disk_full: list[BaseException] = []  # (R7) the first save that found no space left

    def save_one(arr: Any, path: str) -> None:
        try:
            spec.save(arr, path)
            with written_lock:
                written.append(os.path.basename(path))
        except OSError as exc:
            if is_disk_full(exc) and not disk_full:
                disk_full.append(exc)
            raise  # still this input's recorded failure (AsyncWriter.errors)
        finally:
            ticks.add()  # progress counts outputs once they are on disk (or failed)

    def stop_if_disk_full() -> None:
        if disk_full:
            logger.error("The disk is full; stopping the run: %s", disk_full[0])
            raise disk_full[0]

    current_paths: list[str] = []  # the batch whose forwards are running

    def flush_oom_events() -> None:
        """Turn the backoff's new events into report entries named by input file."""
        for start, n_from, n_to in backoff.events[len(oom_events) :]:
            at = os.path.basename(current_paths[start])
            oom_events.append({"at": at, "from": n_from, "to": n_to})

    def record_failure(path: str, reason: str) -> None:
        report.failed.append((os.path.basename(path), reason))
        ticks.add()

    # (S12-01) The thread count is set before any pool thread exists and restored once
    # every pool is joined (the decode pool's ``with`` exits first), also on a raise.
    with (
        _torch_threads(torch_threads),
        ThreadPoolExecutor(io, thread_name_prefix="decode") as decode_pool,
    ):

        def load_safe(path: str) -> tuple[Any, str | None]:
            try:
                return spec.load(path), None
            except Exception as exc:  # noqa: BLE001 - any decode failure is per-image
                unreadable.log(logging.WARNING, "Unreadable input %s: %s", path, exc)
                return None, _describe(exc)

        def load_batch(batch_paths: list[str]) -> tuple[Any, dict[int, str]]:
            loaded = list(decode_pool.map(load_safe, batch_paths))
            bad = {i: err for i, (_, err) in enumerate(loaded) if err is not None}
            good = [t for t, err in loaded if err is None]
            if not good:
                return None, bad
            template = good[0]
            rows = [t if err is None else torch.zeros_like(template) for t, err in loaded]
            if fixed and len(batch_paths) < batch_size:  # (O4) pad the tail; never saved
                rows.extend(torch.zeros_like(template) for _ in range(batch_size - len(rows)))
            x = torch.stack(rows)
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
                    stop_if_disk_full()  # (R7) at a batch boundary, as Cancel
                    for i, reason in bad.items():
                        record_failure(batch_paths[i], reason)
                    if x is None:
                        continue
                    current_paths = batch_paths
                    if fixed:
                        results = _run_fixed(spec.step, x, len(batch_paths))
                    else:
                        results = backoff.run(spec.step, x)
                        flush_oom_events()
                    for chunk, result in results:
                        if not isinstance(result, BaseException):
                            n = chunk.stop - chunk.start
                            size = batch_size if fixed else n  # (O4) padding rows not counted
                            shapes[size] = shapes.get(size, 0) + n
                        for offset, i in enumerate(range(chunk.start, chunk.stop)):
                            if i in bad:
                                continue  # zero-filled slot: never written
                            if isinstance(result, BaseException):
                                record_failure(batch_paths[i], _describe(result))
                                continue
                            writer.submit(batch_paths[i], save_one, result[offset], batch_paths[i])
            stop_if_disk_full()  # (R7) also when the last batch's saves found it full
        finally:
            # (R7) Leaving the ``with`` drained the writer (``AsyncWriter.close`` waits
            # for every queued save), so this is what is on disk even if a step raised.
            if writer is not None:
                for key, exc in writer.errors():
                    report.failed.append((os.path.basename(str(key)), _describe(exc)))
            report.written = sorted(written)
            report.effective_batch_size = backoff.size
            flush_oom_events()  # also those of a batch whose step then raised
            report.batch_shapes = dict(shapes)
            report.oom_events = oom_events
            report.elapsed_s = time.perf_counter() - started
            report.cancelled = bool(should_stop and should_stop()) and report.done < report.total
            unreadable.summary()
            backoff.single_failures.summary()
    return report


__all__ = [
    "FIXED_MEMORY_MESSAGE",
    "FIXED_OOM_MESSAGE",
    "BatchReport",
    "BatchSpec",
    "FixedBatchMemoryTooLow",
    "FixedBatchOutOfMemory",
    "ItemOutOfMemory",
    "OomBackoff",
    "is_disk_full",
    "is_out_of_memory",
    "log_failed_inputs",
    "run_batches",
]

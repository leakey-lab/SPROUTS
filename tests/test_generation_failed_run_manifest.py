"""R7: a generation run that raises part-way leaves a manifest that describes it.

Before R7 only a run that returned wrote ``_sprouts_run.json``: a non-out-of-memory
error after some outputs were on disk left no manifest, and an older one in the folder
then described a different run. Now ``outputs`` carries ``status`` ("completed",
"cancelled" or "failed") and ``error``; a run that raises after writing outputs
replaces the manifest with its partial outcome (``n_written`` = the files on disk),
while a run that wrote nothing keeps the previous one. The run's own exception always
propagates unchanged. CPU stub models as in test_generation_inference, no checkpoints.
"""

from __future__ import annotations

import json
import logging
import os

import pytest
import torch

from app import provenance
from app.inference import mask_inference, runtime
from app.inference import skeleton_inference as si
from app.inference.batch_engine import BatchReport, run_batches
from tests.test_generation_batch_engine import NAMES, _make_images, _spec, _step
from tests.test_generation_inference import _images, _StubMaskModel, _StubSkeletonModel

STALE = b'{"kind": "an older run into this folder"}\n'
ERROR_TEXT = "cuDNN error: CUDNN_STATUS_INTERNAL_ERROR (simulated)"
KINDS = ["mask", "skeleton"]


@pytest.fixture
def cpu_runtime(monkeypatch):
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    yield runtime.get_runtime()
    runtime.get_runtime.cache_clear()


class _MaskModel(_StubMaskModel):
    """Stub mask model that raises a non-out-of-memory error on forward ``fail_on``."""

    def __init__(self, fail_on=None):
        super().__init__()
        self.fail_on = fail_on
        self.error = RuntimeError(ERROR_TEXT)

    def forward(self, x):
        if self.calls + 1 == self.fail_on:
            raise self.error
        return super().forward(x)


class _SkeletonModel(_StubSkeletonModel):
    """Stub skeleton model that raises a non-out-of-memory error on batch ``fail_on``."""

    def __init__(self, fail_on=None):
        super().__init__()
        self.fail_on = fail_on
        self.calls = 0
        self.error = RuntimeError(ERROR_TEXT)

    def run_batch(self, x):
        if self.calls + 1 == self.fail_on:
            raise self.error
        self.calls += 1
        return super().run_batch(x)


def _model(kind, fail_on=None):
    return _MaskModel(fail_on) if kind == "mask" else _SkeletonModel(fail_on)


def _run(kind, src, out, model=None, **kwargs):
    if kind == "mask":
        return mask_inference.generate_masks(src, out, model=model, batch_size=2, **kwargs)
    return si.run_skeleton_generation(src, out, model=model, batch_size=2, **kwargs)


def _folder_with_stale_manifest(path):
    os.makedirs(path)
    with open(provenance.run_manifest_path(path), "wb") as fh:
        fh.write(STALE)
    return str(path)


def _manifest_bytes(out):
    with open(provenance.run_manifest_path(out), "rb") as fh:
        return fh.read()


def _manifest(out):
    return json.loads(_manifest_bytes(out).decode("utf-8"))


def _pngs(out):
    return sorted(name for name in os.listdir(out) if name.endswith(".png"))


@pytest.mark.parametrize("kind", KINDS)
def test_a_run_that_fails_mid_run_replaces_the_stale_manifest(
    tmp_path, cpu_runtime, monkeypatch, kind
):
    released = []
    monkeypatch.setattr(runtime, "release_cuda_cache", lambda: released.append(1))
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind, fail_on=2)

    with pytest.raises(RuntimeError) as caught:
        _run(kind, src, out, model)

    assert caught.value is model.error  # the run's own error, unchanged
    record = _manifest(out)
    assert record["kind"] == (mask_inference.RUN_KIND if kind == "mask" else si.RUN_KIND)
    outputs = record["outputs"]
    assert outputs["status"] == "failed"
    assert outputs["error"] == f"RuntimeError: {ERROR_TEXT}"
    assert outputs["n_written"] == len(_pngs(out)) == 2  # the first batch of two
    assert outputs["n_inputs"] == 5 and outputs["cancelled"] is False
    assert released == [1]


def test_a_failed_mask_run_still_records_its_skipped_stems(tmp_path, cpu_runtime):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "mask")

    with pytest.raises(RuntimeError):
        _run("mask", src, out, _MaskModel(fail_on=2), skip_stems=frozenset({"i0"}))

    outputs = _manifest(out)["outputs"]
    assert outputs["status"] == "failed"
    assert outputs["skipped"] == ["i0"] and outputs["n_skipped"] == 1
    assert outputs["n_written"] == len(_pngs(out)) == 2


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stage", ["first batch", "model load"])
def test_a_run_that_fails_before_writing_keeps_the_previous_manifest(
    tmp_path, cpu_runtime, monkeypatch, kind, stage
):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    if stage == "first batch":
        model = _model(kind, fail_on=1)
        error = model.error
    else:
        model, error = None, RuntimeError("weights missing (simulated)")

        def fail_to_load(device=None):
            raise error

        getter = "get_mask_model" if kind == "mask" else "get_skeleton_model"
        monkeypatch.setattr(runtime, getter, fail_to_load)

    with pytest.raises(RuntimeError) as caught:
        _run(kind, src, out, model)

    assert caught.value is error
    assert _pngs(out) == []
    assert _manifest_bytes(out) == STALE  # still describes the outputs in the folder


@pytest.mark.parametrize("kind", KINDS)
def test_a_manifest_failure_never_masks_the_run_error(
    tmp_path, cpu_runtime, monkeypatch, caplog, kind
):
    def broken_record(*args, **kwargs):
        raise KeyError("provenance bug (simulated)")

    monkeypatch.setattr(provenance, "build_record", broken_record)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind, fail_on=2)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as caught:
        _run(kind, src, out, model)

    assert caught.value is model.error
    assert "provenance bug (simulated)" in caplog.text  # logged, not raised


@pytest.mark.parametrize("kind", KINDS)
def test_a_provenance_error_never_fails_a_normal_run(
    tmp_path, cpu_runtime, monkeypatch, caplog, kind
):
    def broken_record(*args, **kwargs):
        raise RuntimeError("provenance bug (simulated)")

    monkeypatch.setattr(provenance, "build_record", broken_record)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = str(tmp_path / "imgs" / "out")

    with caplog.at_level(logging.WARNING):
        report = _run(kind, src, out, _model(kind))

    assert report.status == "completed" and len(report.written) == len(_pngs(out)) == 3
    # (R5) A minimal record says what happened instead of the full one.
    record = _manifest(out)
    assert record["outputs"]["status"] == "completed" and record["outputs"]["n_written"] == 3
    assert "provenance bug (simulated)" in record["provenance_error"]
    assert "provenance bug (simulated)" in caplog.text  # logged, not raised


@pytest.mark.parametrize("kind", KINDS)
def test_a_normal_run_is_completed_without_an_error(tmp_path, cpu_runtime, kind):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")

    report = _run(kind, src, out, _model(kind))

    outputs = _manifest(out)["outputs"]
    assert outputs["status"] == report.status == "completed"
    assert outputs["error"] is None and report.error is None
    assert outputs["n_written"] == len(_pngs(out)) == 3


@pytest.mark.parametrize("kind", KINDS)
def test_a_cancelled_run_is_cancelled(tmp_path, cpu_runtime, kind):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind)

    report = _run(kind, src, out, model, should_stop=lambda: model.calls >= 1)

    outputs = _manifest(out)["outputs"]
    assert outputs["status"] == report.status == "cancelled"
    assert outputs["error"] is None
    assert outputs["n_written"] == len(_pngs(out)) == 2


def test_the_summary_states_the_outcome():
    assert BatchReport().summary()["status"] == "completed"
    assert BatchReport(cancelled=True).summary()["status"] == "cancelled"
    summary = BatchReport(cancelled=True, error="RuntimeError: boom").summary()
    assert summary["status"] == "failed" and summary["error"] == "RuntimeError: boom"
    assert BatchReport().summary()["error"] is None


def test_run_batches_fills_the_given_report_even_when_a_step_raises(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    out = str(tmp_path / "out")
    calls = []

    def step(x):
        calls.append(len(x))
        if len(calls) == 2:
            raise RuntimeError("boom")
        return _step(x)

    report = BatchReport()
    with pytest.raises(RuntimeError, match="boom"):
        run_batches(paths, _spec(out, step=step), batch_size=3, report=report)

    assert report.written == sorted(os.listdir(out)) == NAMES[:3]  # what is on disk
    assert report.total == 7 and report.done == 3
    assert report.batch_size == report.effective_batch_size == 3
    assert report.elapsed_s > 0


def test_a_run_cancelled_as_a_step_raises_still_records_the_cancel(tmp_path):
    # Cancel pressed during a batch that then fails: the error decides the status
    # ("failed"), and the cancel flag still tells the truth.
    paths = _make_images(str(tmp_path / "in"), NAMES)
    out = str(tmp_path / "out")
    cancelled, calls = [], []

    def step(x):
        calls.append(len(x))
        if len(calls) == 2:
            cancelled.append(True)  # Cancel pressed during the second batch...
            raise RuntimeError("boom")  # ...which then fails
        return _step(x)

    report = BatchReport()
    with pytest.raises(RuntimeError, match="boom"):
        run_batches(
            paths,
            _spec(out, step=step),
            batch_size=3,
            report=report,
            should_stop=lambda: bool(cancelled),
        )
    assert report.done < report.total
    assert report.cancelled is True


def test_run_batches_returns_the_report_it_was_given(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    report = BatchReport()

    returned = run_batches(paths, _spec(str(tmp_path / "out")), batch_size=3, report=report)

    assert returned is report
    assert len(report.written) == 7 and report.status == "completed"

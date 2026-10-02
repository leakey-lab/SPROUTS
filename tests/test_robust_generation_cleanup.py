"""R2: releasing the CUDA cache at the end of a generation run never masks its outcome.

``mask_inference.generate_masks`` and ``skeleton_inference.run_skeleton_generation``
call ``runtime.release_cuda_cache()`` in a ``finally``. After a sticky CUDA error
(e.g. "an illegal memory access was encountered") every later CUDA call raises the
same error again, ``torch.cuda.empty_cache()`` included: the cleanup's exception then
replaced the run's own error, and on a run that had finished it turned the success
into a failure with no run manifest. Releasing the cache is housekeeping: it now logs
its failure and never raises. CPU stub models, no checkpoints.
"""

from __future__ import annotations

import logging

import pytest
import torch

from app.inference import runtime
from tests.test_generation_failed_run_manifest import (
    ERROR_TEXT,
    KINDS,
    _folder_with_stale_manifest,
    _manifest,
    _model,
    _pngs,
    _run,
    cpu_runtime,  # noqa: F401 - fixture
)
from tests.test_generation_inference import _images

STICKY = "CUDA error: an illegal memory access was encountered (simulated, sticky)"


def _sticky_cuda(monkeypatch):
    """From now on CUDA looks initialised and every cache release fails (sticky error).

    Armed after the stub model is built: its ``torch.manual_seed`` would otherwise
    try to seed the (absent) CUDA generators.
    """
    calls = []

    def empty_cache():
        calls.append(1)
        raise RuntimeError(STICKY)

    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", empty_cache)
    return calls


def test_release_cuda_cache_logs_a_failure_instead_of_raising(monkeypatch, caplog):
    calls = _sticky_cuda(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=runtime.__name__):
        runtime.release_cuda_cache()  # must not raise
    assert calls == [1]
    assert STICKY in caplog.text


@pytest.mark.parametrize("kind", KINDS)
def test_a_failing_cache_release_never_replaces_the_run_error(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    monkeypatch,
    kind,
):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind, fail_on=2)
    sticky_cuda = _sticky_cuda(monkeypatch)

    with pytest.raises(RuntimeError) as caught:
        _run(kind, src, out, model)

    assert caught.value is model.error  # the run's own error, not the cleanup's
    assert sticky_cuda == [1]  # the release was still attempted, once
    outputs = _manifest(out)["outputs"]
    assert outputs["status"] == "failed" and outputs["error"] == f"RuntimeError: {ERROR_TEXT}"


@pytest.mark.parametrize("kind", KINDS)
def test_a_failing_cache_release_never_fails_a_finished_run(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    monkeypatch,
    kind,
):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind)
    sticky_cuda = _sticky_cuda(monkeypatch)

    report = _run(kind, src, out, model)

    assert report.status == "completed" and len(report.written) == len(_pngs(out)) == 3
    outputs = _manifest(out)["outputs"]  # the finished run's manifest was written
    assert outputs["status"] == "completed" and outputs["n_written"] == 3
    assert sticky_cuda == [1]

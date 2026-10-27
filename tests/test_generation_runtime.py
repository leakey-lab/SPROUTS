"""Inference runtime policy (PI-05 / FR-08 / PI-09 / PI-11 / PI-13 / UXS-06).

CPU-only safe: every test runs on Linux CI with CPU torch.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys

import pytest

from app.inference import runtime


@pytest.fixture
def fresh_runtime(monkeypatch):
    """Resolve the runtime from scratch and restore the global cuDNN flags afterwards."""
    import torch

    saved = (torch.backends.cudnn.benchmark, torch.backends.cudnn.deterministic)
    runtime.get_runtime.cache_clear()
    yield runtime
    runtime.get_runtime.cache_clear()
    torch.backends.cudnn.benchmark, torch.backends.cudnn.deterministic = saved


def test_importing_runtime_does_not_import_torch():
    code = (
        "import sys, app.inference.runtime as r; "
        "print('torch' in sys.modules, r.peek_runtime() is None)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.split()
    assert out == ["False", "True"]


def test_cudnn_benchmark_is_off_and_deterministic_by_default(fresh_runtime, monkeypatch):
    import torch

    monkeypatch.delenv(runtime.CUDNN_BENCHMARK_ENV, raising=False)
    torch.backends.cudnn.benchmark = True
    rt = fresh_runtime.get_runtime()
    assert rt.cudnn_benchmark is False
    assert rt.cudnn_deterministic is True
    assert torch.backends.cudnn.benchmark is False
    assert torch.backends.cudnn.deterministic is True


def test_cudnn_benchmark_env_opt_in(fresh_runtime, monkeypatch):
    import torch

    monkeypatch.setenv(runtime.CUDNN_BENCHMARK_ENV, "1")
    rt = fresh_runtime.get_runtime()
    assert rt.cudnn_benchmark is True and rt.cudnn_deterministic is False
    assert torch.backends.cudnn.benchmark is True


def test_precision_is_reported_truthfully(fresh_runtime, monkeypatch):
    monkeypatch.delenv(runtime.CUDNN_BENCHMARK_ENV, raising=False)
    rt = fresh_runtime.get_runtime()
    if rt.is_cuda and rt.compute_capability and rt.compute_capability >= (8, 0):
        assert rt.precision == "tf32"
    else:
        assert rt.precision == "fp32"


def test_peek_runtime_never_resolves(fresh_runtime):
    assert fresh_runtime.peek_runtime() is None
    rt = fresh_runtime.get_runtime()
    assert fresh_runtime.peek_runtime() is rt


def test_describe_runtime_before_resolution_loads_nothing(fresh_runtime, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("describe_runtime must not load a model")

    monkeypatch.setattr(runtime, "get_skeleton_model", boom)
    monkeypatch.setattr(runtime, "get_mask_model", boom)
    summary = fresh_runtime.describe_runtime()
    assert fresh_runtime.peek_runtime() is None
    assert summary.device == "unknown"
    assert isinstance(summary.text, str) and summary.text
    assert summary.mask_loaded is False and summary.skeleton_loaded is False


def test_describe_runtime_after_resolution(fresh_runtime, monkeypatch, tmp_path):
    missing = str(tmp_path / "nope.pth")
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: missing)
    fresh_runtime.get_runtime()
    summary = fresh_runtime.describe_runtime()
    assert summary.device in ("GPU", "CPU")
    assert summary.mask_weights is False
    assert summary.text.startswith(summary.device)
    assert "mask model missing" in summary.text


def test_get_mask_model_reports_missing_weights(monkeypatch, tmp_path):
    runtime.get_mask_model.cache_clear()
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(tmp_path / "nope.pth"))
    with pytest.raises(runtime.ModelUnavailableError, match="nope.pth"):
        runtime.get_mask_model()
    runtime.get_mask_model.cache_clear()


def test_only_a_gpu_run_gives_its_pool_threads_one_torch_thread():
    """(S12-01) A property, not a field: the run records stay as they were."""
    import torch

    cpu = runtime.RuntimeConfig(
        device=torch.device("cpu"),
        use_channels_last=False,
        pin_memory=False,
        io_threads=2,
        cudnn_benchmark=False,
        cudnn_deterministic=True,
        precision="fp32",
    )
    cuda = dataclasses.replace(cpu, device=torch.device("cuda"), use_channels_last=True)
    assert cuda.batch_torch_threads == 1
    assert cpu.batch_torch_threads is None
    assert "batch_torch_threads" not in {f.name for f in dataclasses.fields(runtime.RuntimeConfig)}


def test_release_cuda_cache_runs_once_and_only_with_cuda(monkeypatch):
    import torch

    calls = []
    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: calls.append("empty"))
    runtime.release_cuda_cache()
    assert calls == ["empty"]
    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: False)
    runtime.release_cuda_cache()
    assert calls == ["empty"]

"""O4 / R1 step 2: fixed-shape batches (``SPROUTS_BATCH_POLICY``).

At one batch shape every image's output is bit-identical whatever the batch composition,
slot, zero padding or process (``research/o4-gpu-batch.md``, Exp 1). So under the
``fixed`` policy every forward gets exactly ``batch_size`` rows: a short last batch is
padded with zero rows whose outputs are never saved, out of GPU memory stops the run
(no retry, no halving: a retry can land on another kernel), and a pre-flight check stops
a GPU run before the first batch when free memory is below the measured floor.
``auto`` keeps the former path (VRAM-sized skeleton batch, halving on out of memory).
CPU stub models, no checkpoint, no GPU.
"""

from __future__ import annotations

import json
import logging
import os

import pytest
import torch

from app import config, provenance
from app.inference import batch_engine, mask_inference, runtime
from app.inference import skeleton_inference as si
from app.inference.batch_engine import (
    FIXED_MEMORY_MESSAGE,
    FIXED_OOM_MESSAGE,
    BatchReport,
    FixedBatchMemoryTooLow,
    FixedBatchOutOfMemory,
    run_batches,
)
from tests.test_generation_batch_engine import NAMES, _make_images, _outputs, _spec, _step
from tests.test_generation_handlers import qapp  # noqa: F401 - fixture
from tests.test_generation_inference import _images, _StubMaskModel, _StubSkeletonModel

GIB = 1024**3


@pytest.fixture
def cpu_runtime(monkeypatch):
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    yield runtime.get_runtime()
    runtime.get_runtime.cache_clear()


def _recording(sizes, step=_step):
    def recording_step(x):
        sizes.append(x.shape[0])
        return step(x)

    return recording_step


def _manifest(out):
    with open(provenance.run_manifest_path(out), encoding="utf-8") as fh:
        return json.load(fh)


def _pngs(out):
    return sorted(name for name in os.listdir(out) if name.endswith(".png"))


# --- the engine --------------------------------------------------------------------


def test_a_padded_tail_runs_every_forward_at_the_fixed_size(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    sizes = []
    out = str(tmp_path / "out")

    report = run_batches(paths, _spec(out, step=_recording(sizes)), batch_size=3, fixed=True)
    ref = str(tmp_path / "ref")
    run_batches(paths, _spec(ref), batch_size=3, fixed=False)

    assert sizes == [3, 3, 3]
    assert len(report.written) == 7
    assert _outputs(out) == _outputs(ref)
    assert report.batch_shapes == {3: 7}
    assert report.effective_batch_size == 3
    assert report.oom_events == []


def test_padding_rows_are_never_saved(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    saved = []

    def save(arr, path):
        saved.append(os.path.basename(path))

    run_batches(paths, _spec(str(tmp_path / "out"), save=save), batch_size=3, fixed=True)
    assert sorted(saved) == NAMES


def test_an_unreadable_input_keeps_its_slot_and_the_tail_is_padded(tmp_path):
    dirty = _make_images(str(tmp_path / "in"), NAMES + ["bad.jpg"], corrupt={"bad.jpg"})
    sizes = []

    report = run_batches(
        dirty, _spec(str(tmp_path / "out"), step=_recording(sizes)), batch_size=3, fixed=True
    )

    assert sizes == [3, 3, 3]
    assert report.failed_names == ["bad.jpg"]
    assert report.batch_shapes == {3: 8}


def test_auto_keeps_the_short_tail(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    sizes = []
    run_batches(paths, _spec(str(tmp_path / "out"), step=_recording(sizes)), batch_size=3)
    assert sizes == [3, 3, 1]


def test_fixed_out_of_memory_stops_without_a_retry(tmp_path, monkeypatch):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    sizes, frees = [], []
    monkeypatch.setattr(batch_engine, "_free_cuda_cache", lambda: frees.append(1))

    def oom_step(x):
        sizes.append(x.shape[0])
        raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")

    report = BatchReport()
    with pytest.raises(FixedBatchOutOfMemory) as caught:
        run_batches(
            paths,
            _spec(str(tmp_path / "out"), step=oom_step),
            batch_size=3,
            fixed=True,
            report=report,
        )

    assert str(caught.value) == FIXED_OOM_MESSAGE.format(n=3)
    assert caught.value.batch_size == 3
    assert sizes == [3]  # no retry, no halving
    assert frees == [1]
    assert report.oom_events == []
    assert report.written == []
    assert report.effective_batch_size == 3


def test_the_out_of_memory_message_names_the_size_and_the_way_out():
    text = str(FixedBatchOutOfMemory(4))
    assert text.startswith("Out of GPU memory at the fixed batch size (4). ")
    assert "SPROUTS_BATCH_POLICY=auto" in text


def test_a_non_memory_error_still_propagates_unchanged_when_fixed(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    error = ValueError("bad model")

    def step(x):
        raise error

    with pytest.raises(ValueError) as caught:
        run_batches(paths, _spec(str(tmp_path / "out"), step=step), batch_size=3, fixed=True)
    assert caught.value is error


# --- the generation entry points ---------------------------------------------------


class _OomMaskModel(_StubMaskModel):
    """Succeeds on forward 1, then runs out of GPU memory on every later forward."""

    def forward(self, x):
        if self.calls >= 1:
            self.calls += 1
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        return super().forward(x)


class _OomSkeletonModel(_StubSkeletonModel):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def run_batch(self, x):
        self.calls += 1
        if self.calls > 1:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        return super().run_batch(x)


def _run(kind, src, out, model, **kwargs):
    if kind == "mask":
        return mask_inference.generate_masks(src, out, model=model, **kwargs)
    return si.run_skeleton_generation(src, out, model=model, **kwargs)


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_a_fixed_stop_is_a_failed_run_record(tmp_path, cpu_runtime, kind):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = str(tmp_path / "imgs" / "out")
    model = _OomMaskModel() if kind == "mask" else _OomSkeletonModel()

    with pytest.raises(FixedBatchOutOfMemory):
        _run(kind, src, out, model, batch_size=2, batch_policy="fixed")

    record = _manifest(out)
    assert record["outputs"]["status"] == "failed"
    assert record["outputs"]["error"] == "FixedBatchOutOfMemory: " + FIXED_OOM_MESSAGE.format(n=2)
    assert record["parameters"]["batch_policy"] == "fixed"
    assert record["outputs"]["oom_events"] == []
    assert len(_pngs(out)) == 2


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
@pytest.mark.parametrize("policy", ["auto", "fixed"])
def test_the_manifest_records_the_batch_policy(tmp_path, cpu_runtime, kind, policy):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = str(tmp_path / "imgs" / "out")
    model = _StubMaskModel() if kind == "mask" else _StubSkeletonModel()

    report = _run(kind, src, out, model, batch_size=2, batch_policy=policy)

    record = _manifest(out)
    assert record["parameters"]["batch_policy"] == policy
    assert "free_vram_gib" not in record["parameters"]
    assert record["outputs"]["free_vram_gib"] is None  # a CPU run measures no GPU memory
    assert len(report.written) == 3


def test_a_fixed_mask_run_pads_its_tail(tmp_path, cpu_runtime):
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    model = _StubMaskModel()
    mask_inference.generate_masks(
        src, str(tmp_path / "imgs" / "mask"), model=model, batch_size=2, batch_policy="fixed"
    )
    assert (model.calls, model.items) == (3, 6)


def test_the_default_policy_comes_from_the_environment(tmp_path, cpu_runtime, monkeypatch):
    monkeypatch.setenv(runtime.BATCH_POLICY_ENV, "auto")
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = str(tmp_path / "imgs" / "mask")
    model = _StubMaskModel()

    mask_inference.generate_masks(src, out, model=model, batch_size=2)

    assert (model.calls, model.items) == (3, 5)  # auto: the short tail is not padded
    assert _manifest(out)["parameters"]["batch_policy"] == "auto"


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_an_unknown_explicit_policy_is_refused(tmp_path, cpu_runtime, kind):
    src = _images(str(tmp_path / "imgs"), ["a.jpg"])
    with pytest.raises(ValueError, match="bogus"):
        _run(kind, src, str(tmp_path / "imgs" / "out"), None, batch_policy="bogus")


# --- the pre-flight memory check -----------------------------------------------------


@pytest.fixture
def gpu_memory(monkeypatch):
    """Fake ``torch.cuda.mem_get_info``; the release before it is recorded (no GPU use)."""
    state = {"free": 8 * GIB, "calls": 0, "events": []}

    def mem_get_info(device=None):
        state["calls"] += 1
        state["events"].append("measure")
        return state["free"], 16 * GIB

    monkeypatch.setattr(torch.cuda, "mem_get_info", mem_get_info)
    monkeypatch.setattr(runtime, "release_cuda_cache", lambda: state["events"].append("release"))
    return state


def test_the_floors_are_the_measured_ones():
    # Driver free memory after the model loads (weights excluded), plus a margin.
    assert runtime.FIXED_MASK_VRAM_FLOOR_GIB == 3.6
    assert runtime.FIXED_SKELETON_VRAM_FLOOR_GIB == 3.8
    assert runtime.FIXED_MASK_16_VRAM_FLOOR_GIB == 12.0


@pytest.mark.parametrize(
    ("model", "n", "needed"), [("mask", 4, 3.6), ("skeleton", 64, 3.8), ("mask", 16, 12.0)]
)
def test_too_little_free_memory_stops_before_the_first_batch(gpu_memory, model, n, needed):
    gpu_memory["free"] = int(2.05 * GIB)

    with pytest.raises(FixedBatchMemoryTooLow) as caught:
        runtime.check_fixed_batch_memory(model, n, "cuda")

    assert str(caught.value) == FIXED_MEMORY_MESSAGE.format(n=n, free=2.0, needed=needed)
    assert str(caught.value).startswith(
        f"Not enough free GPU memory for the fixed batch size ({n}): 2.0 GiB free, "
        f"{needed:.1f} GiB needed. "
    )
    assert isinstance(caught.value, FixedBatchOutOfMemory)
    assert caught.value.free_gib == pytest.approx(2.05)
    assert caught.value.needed_gib == needed
    assert gpu_memory["events"] == ["release", "measure"]  # collected + emptied first


def test_free_memory_just_below_the_floor_never_reads_as_enough(gpu_memory):
    gpu_memory["free"] = int(3.59 * GIB)
    with pytest.raises(FixedBatchMemoryTooLow, match=r"3\.5 GiB free, 3\.6 GiB needed"):
        runtime.check_fixed_batch_memory("mask", 4, "cuda")


def test_the_skeleton_floor_keeps_a_margin(gpu_memory):
    gpu_memory["free"] = int(3.7 * GIB)  # enough for the bare cap (about 3.4 GiB), no margin
    with pytest.raises(FixedBatchMemoryTooLow):
        runtime.check_fixed_batch_memory("skeleton", 64, "cuda")


def test_enough_free_memory_returns_it(gpu_memory):
    gpu_memory["free"] = int(7.5 * GIB)
    assert runtime.check_fixed_batch_memory("mask", 4, "cuda") == pytest.approx(7.5, abs=0.01)


def test_a_size_without_a_measured_floor_is_measured_but_not_stopped(gpu_memory):
    gpu_memory["free"] = 1 * GIB
    assert runtime.check_fixed_batch_memory("mask", 2, "cuda") == pytest.approx(1.0)
    assert runtime.check_fixed_batch_memory("skeleton", 32, "cuda") == pytest.approx(1.0)


def test_the_cpu_never_asks_the_gpu(gpu_memory):
    assert runtime.check_fixed_batch_memory("mask", 4, "cpu") is None
    assert gpu_memory["calls"] == 0


def test_a_failed_memory_query_does_not_stop_the_run(monkeypatch, caplog):
    def broken(device=None):
        raise RuntimeError("driver says no (simulated)")

    monkeypatch.setattr(torch.cuda, "mem_get_info", broken)
    monkeypatch.setattr(runtime, "release_cuda_cache", lambda: None)
    with caplog.at_level(logging.WARNING):
        assert runtime.check_fixed_batch_memory("mask", 4, "cuda") is None
    assert "driver says no" in caplog.text


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_a_fixed_run_checks_memory_and_records_it(tmp_path, cpu_runtime, monkeypatch, kind):
    calls = []

    def check(model, batch_size, device_type):
        calls.append((model, batch_size, device_type))
        return 7.4567

    monkeypatch.setattr(runtime, "check_fixed_batch_memory", check)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = str(tmp_path / "imgs" / "out")
    model = _StubMaskModel() if kind == "mask" else _StubSkeletonModel()

    _run(kind, src, out, model, batch_size=2, batch_policy="fixed")

    assert calls == [(kind, 2, "cpu")]
    record = _manifest(out)
    assert record["outputs"]["free_vram_gib"] == 7.46  # next to batch_shapes / oom_events
    assert "free_vram_gib" not in record["parameters"]


def test_the_summary_carries_the_free_memory():
    assert BatchReport().summary()["free_vram_gib"] is None
    assert BatchReport(free_vram_gib=7.4567).summary()["free_vram_gib"] == 7.46


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_an_auto_run_never_checks_memory(tmp_path, cpu_runtime, monkeypatch, kind):
    monkeypatch.setattr(runtime, "check_fixed_batch_memory", lambda *a: pytest.fail("checked"))
    src = _images(str(tmp_path / "imgs"), ["a.jpg"])
    model = _StubMaskModel() if kind == "mask" else _StubSkeletonModel()
    _run(kind, src, str(tmp_path / "imgs" / "out"), model, batch_size=2, batch_policy="auto")


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
def test_a_memory_stop_runs_no_forward(tmp_path, cpu_runtime, monkeypatch, kind):
    def check(model, batch_size, device_type):
        raise FixedBatchMemoryTooLow(batch_size, free_gib=1.0, needed_gib=3.6)

    monkeypatch.setattr(runtime, "check_fixed_batch_memory", check)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = str(tmp_path / "imgs" / "out")
    model = _OomMaskModel() if kind == "mask" else _OomSkeletonModel()

    with pytest.raises(FixedBatchMemoryTooLow):
        _run(kind, src, out, model, batch_size=2, batch_policy="fixed")

    assert model.calls == 0
    assert _pngs(out) == []


# --- policy and sizes ------------------------------------------------------------------


def test_batch_policy_reads_the_environment(monkeypatch, caplog):
    monkeypatch.delenv(runtime.BATCH_POLICY_ENV, raising=False)
    assert runtime.DEFAULT_BATCH_POLICY == "fixed"
    assert runtime.BATCH_POLICY_ENV == "SPROUTS_BATCH_POLICY"
    assert runtime.BATCH_POLICIES == ("fixed", "auto")
    assert runtime.batch_policy() == "fixed"
    monkeypatch.setenv(runtime.BATCH_POLICY_ENV, "AUTO")
    assert runtime.batch_policy() == "auto"
    monkeypatch.setenv(runtime.BATCH_POLICY_ENV, " fixed ")
    assert runtime.batch_policy() == "fixed"
    monkeypatch.setenv(runtime.BATCH_POLICY_ENV, "bogus")
    with caplog.at_level(logging.WARNING):
        assert runtime.batch_policy() == runtime.DEFAULT_BATCH_POLICY
    assert len([r for r in caplog.records if "bogus" in r.getMessage()]) == 1


def test_fixed_sizes_are_the_owner_sizes():
    # O4 memo: masks at 4 on a GPU (16 needs about 12 GiB free), skeletons at 64.
    assert runtime.fixed_batch_size("mask", "cuda") == 4 == config.MASK_BATCH_SIZE_CPU
    assert runtime.fixed_batch_size("skeleton", "cuda") == 64
    assert runtime.fixed_batch_size("mask", "cpu") == config.MASK_BATCH_SIZE_CPU == 4
    assert runtime.fixed_batch_size("skeleton", "cpu") == config.SKELETON_BATCH_SIZE_CPU == 8
    with pytest.raises(ValueError):
        runtime.fixed_batch_size("roots", "cuda")


def test_fixed_skeleton_batch_never_asks_the_gpu(monkeypatch):
    monkeypatch.setattr(config, "get_skeleton_batch_size", lambda refresh=False: pytest.fail())
    assert si.default_skeleton_batch_size("cuda", policy="fixed") == 64
    assert si.default_skeleton_batch_size("cpu", policy="fixed") == 8
    monkeypatch.setattr(config, "get_skeleton_batch_size", lambda refresh=False: 104)
    assert si.default_skeleton_batch_size("cuda", policy="auto") == 104
    assert si.default_skeleton_batch_size("cpu", policy="auto") == 8


@pytest.mark.parametrize(("device", "auto_size"), [("cpu", 4), ("cuda", config.MASK_BATCH_SIZE)])
def test_the_mask_default_size_follows_the_policy(monkeypatch, device, auto_size):
    fake = runtime.RuntimeConfig(
        device=torch.device(device),
        use_channels_last=False,
        pin_memory=False,
        io_threads=1,
        cudnn_benchmark=False,
        cudnn_deterministic=True,
        precision="fp32",
    )
    monkeypatch.setattr(runtime, "get_runtime", lambda: fake)
    assert mask_inference.default_mask_batch_size(policy="fixed") == 4
    assert mask_inference.default_mask_batch_size(policy="auto") == auto_size


# --- the message the user sees -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [FixedBatchOutOfMemory(4), FixedBatchMemoryTooLow(64, free_gib=2.0, needed_gib=3.8)],
    ids=["out-of-memory", "pre-flight"],
)
def test_a_fixed_memory_stop_is_shown_as_its_sentence(exc, caplog):
    from app.workers import describe_error, is_user_condition

    assert is_user_condition(exc)
    assert describe_error(exc) == str(exc)  # no "FixedBatchOutOfMemory: " prefix


def test_a_fixed_memory_stop_logs_no_traceback(qapp, caplog):  # noqa: F811
    from app.workers import LOGGER_NAME, GuardedThread

    class _Thread(GuardedThread):
        def work(self):
            raise FixedBatchOutOfMemory(4)

    messages = []
    thread = _Thread()
    thread.failed.connect(messages.append)
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        thread.run()  # synchronously, on this thread
    assert messages == [FIXED_OOM_MESSAGE.format(n=4)]
    assert not any(record.exc_info for record in caplog.records)

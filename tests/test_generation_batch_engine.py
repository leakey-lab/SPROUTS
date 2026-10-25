"""Shared batch engine for mask + skeleton generation.

PI-03 / FR-05 / RB-10: unreadable inputs are recorded failures (never a made-up output).
PI-06: decode / forward / save overlap without changing batch composition or outputs.
PI-07: CUDA out-of-memory halves the batch and retries (floor 1), never skips silently.
PI-08: cooperative cancel keeps finished outputs and reports the partial run.
CPU-only stubs: no checkpoint, no GPU.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
import torch
from PIL import Image

from app.inference.batch_engine import BatchReport, BatchSpec, OomBackoff, run_batches


def _make_images(folder, names, *, corrupt=()):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(0)
    for name in names:
        path = os.path.join(folder, name)
        if name in corrupt:
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8\xff\xe0 truncated jpeg")
        else:
            Image.fromarray(rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)).save(path)
    return [os.path.join(folder, n) for n in sorted(names)]


def _load(path):
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1).contiguous()


def _step(x):
    return (x.mean(1) * 255).to(torch.uint8).numpy()


def _spec(out_dir, step=_step, save=None):
    def default_save(arr, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        Image.fromarray(arr, mode="L").save(os.path.join(out_dir, stem + ".png"))

    os.makedirs(out_dir, exist_ok=True)
    return BatchSpec(load=_load, step=step, save=save or default_save)


def _outputs(folder):
    out = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), "rb") as fh:
            out[name] = fh.read()
    return out


NAMES = [f"img{i:02d}.png" for i in range(7)]


def test_unreadable_input_is_a_recorded_failure_not_an_output(tmp_path):
    clean = _make_images(str(tmp_path / "clean"), NAMES)
    ref = run_batches(clean, _spec(str(tmp_path / "ref")), batch_size=3)
    assert ref.failed == [] and len(ref.written) == 7

    dirty = _make_images(str(tmp_path / "dirty"), NAMES + ["bad.jpg"], corrupt={"bad.jpg"})
    report = run_batches(dirty, _spec(str(tmp_path / "out")), batch_size=3)

    assert [name for name, _ in report.failed] == ["bad.jpg"]
    assert report.failed_names == ["bad.jpg"]
    assert not os.path.exists(tmp_path / "out" / "bad.png")
    assert _outputs(tmp_path / "out") == _outputs(tmp_path / "ref")
    assert report.total == 8 and report.done == 8 and not report.cancelled


def test_batches_keep_serial_composition_and_order(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    seen = []

    def recording_step(x):
        seen.append(x.shape[0])
        return _step(x)

    run_batches(paths, _spec(str(tmp_path / "out"), step=recording_step), batch_size=3)
    assert seen == [3, 3, 1]


def test_out_of_memory_halves_the_batch_and_writes_everything(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    sizes = []

    def oom_step(x):
        sizes.append(x.shape[0])
        if x.shape[0] > 2:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        return _step(x)

    report = run_batches(paths, _spec(str(tmp_path / "out"), step=oom_step), batch_size=4)
    assert report.failed == []
    assert len(report.written) == 7
    assert report.effective_batch_size == 2
    run_batches(paths, _spec(str(tmp_path / "ref")), batch_size=4)
    assert _outputs(tmp_path / "out") == _outputs(tmp_path / "ref")
    # the working size is remembered: later batches do not retry at 4
    assert sizes.count(4) == 1


def test_the_report_records_the_batch_shapes(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    report = run_batches(paths, _spec(str(tmp_path / "out")), batch_size=3)
    assert report.batch_shapes == {3: 6, 1: 1}
    assert report.oom_events == []
    assert report.summary()["batch_shapes"] == {"3": 6, "1": 1}


def test_the_report_records_out_of_memory_events(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)

    def oom_step(x):
        if x.shape[0] > 2:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")
        return _step(x)

    report = run_batches(paths, _spec(str(tmp_path / "out"), step=oom_step), batch_size=4)
    assert report.batch_shapes == {2: 6, 1: 1}
    assert report.oom_events == [{"at": "img00.png", "from": 4, "to": 2}]
    assert report.summary()["oom_events"] == report.oom_events


def test_zero_filled_slots_count_in_the_forward_shape(tmp_path):
    dirty = _make_images(str(tmp_path / "dirty"), NAMES + ["bad.jpg"], corrupt={"bad.jpg"})
    report = run_batches(dirty, _spec(str(tmp_path / "out")), batch_size=3)
    assert report.batch_shapes == {3: 6, 2: 2}


def test_an_empty_report_has_empty_shapes():
    summary = BatchReport().summary()
    assert summary["batch_shapes"] == {}
    assert summary["oom_events"] == []


def test_out_of_memory_at_size_one_fails_only_that_item():
    backoff = OomBackoff(4)

    def step(x):
        if x.shape[0] > 1 or float(x[0, 0]) == 2.0:
            raise torch.cuda.OutOfMemoryError("stub")
        return x.clone()

    results = backoff.run(step, torch.arange(4.0).reshape(4, 1))
    ok = [(s.start, out) for s, out in results if not isinstance(out, BaseException)]
    bad = [s.start for s, out in results if isinstance(out, BaseException)]
    assert bad == [2]
    assert [start for start, _ in ok] == [0, 1, 3]
    assert backoff.size == 1


def test_failed_batch_memory_is_released_before_the_retry():
    """The OOM traceback pins the failed forward's activations; free them first."""
    import gc
    import weakref

    refs = []
    alive_at_retry = []

    def step(x):
        if x.shape[0] > 1:
            activation = torch.zeros(1000)
            refs.append(weakref.ref(activation))
            raise torch.cuda.OutOfMemoryError("stub")
        return x.clone()

    def on_oom():
        gc.collect()
        alive_at_retry.append(any(r() is not None for r in refs))

    results = OomBackoff(2, on_oom=on_oom).run(step, torch.zeros(2, 1))
    assert alive_at_retry == [False]
    assert len(results) == 2


def test_single_item_failure_carries_no_traceback():
    def step(x):
        raise torch.cuda.OutOfMemoryError("stub")

    [(chunk, result)] = OomBackoff(1).run(step, torch.zeros(1, 1))
    assert isinstance(result, BaseException) and result.__traceback__ is None
    assert "stub" in str(result)


def test_non_oom_errors_are_not_retried():
    backoff = OomBackoff(4)

    def step(x):
        raise ValueError("bad model")

    with pytest.raises(ValueError):
        backoff.run(step, torch.zeros(4, 1))


def test_cancel_stops_at_a_batch_boundary_and_keeps_finished_outputs(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    calls = []

    def step(x):
        calls.append(x.shape[0])
        return _step(x)

    report = run_batches(
        paths,
        _spec(str(tmp_path / "out"), step=step),
        batch_size=2,
        should_stop=lambda: len(calls) >= 1,
    )
    assert report.cancelled
    assert calls == [2]
    assert len(report.written) == 2
    written = sorted(os.listdir(tmp_path / "out"))
    assert written == ["img00.png", "img01.png"]
    for name in written:  # complete, readable files only
        with Image.open(tmp_path / "out" / name) as im:
            im.load()


def test_progress_is_throttled_monotonic_and_complete(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    ticks = []
    run_batches(
        paths,
        _spec(str(tmp_path / "out")),
        batch_size=3,
        progress=lambda done, total: ticks.append((done, total)),
    )
    dones = [d for d, _ in ticks]
    assert dones == sorted(dones) and dones[-1] == 7
    assert all(total == 7 for _, total in ticks)


def test_save_errors_are_recorded_failures(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES[:3])

    def bad_save(arr, path):
        if path.endswith("img01.png"):
            raise OSError("disk full")

    report = run_batches(paths, _spec(str(tmp_path / "out"), save=bad_save), batch_size=2)
    assert report.failed_names == ["img01.png"]
    assert sorted(report.written) == ["img00.png", "img02.png"]


def test_empty_input_is_an_empty_report(tmp_path):
    report = run_batches([], _spec(str(tmp_path / "out")), batch_size=4)
    assert report == BatchReport(total=0, batch_size=4, effective_batch_size=4)

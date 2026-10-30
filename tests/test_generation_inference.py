"""Mask + skeleton generation cores (Qt-free), CPU stub models, no checkpoints.

PI-03 / FR-05 / RB-10: unreadable inputs -> recorded failure, no output, run continues.
PI-06: outputs byte-identical to the legacy serial loops.
RB-01 / PI-04: skip_stems leaves existing masks untouched and never runs the model on them.
FR-02 / PI-09: every run writes a provenance manifest next to its outputs.
PI-13: the CUDA cache is released once per job.
S12-01: only a GPU run limits the torch threads of its pool threads.
S12-03: the real skeleton wrapper loads the checkpoint at ``runtime.skeleton_weights_path()``.
C6-02: a run with failures ends with one bounded log line.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os

import numpy as np
import pytest
import torch
from PIL import Image

from app import provenance
from app.config import MASK_THRESHOLD
from app.inference import mask_inference, runtime
from app.inference import skeleton_inference as si


def _images(folder, names, *, corrupt=(), size=(64, 48)):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(1)
    for name in names:
        path = os.path.join(folder, name)
        if name in corrupt:
            with open(path, "wb") as fh:
                fh.write(b"this is not an image")
        else:
            arr = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
            Image.fromarray(arr).save(path)
    return folder


class _StubMaskModel(torch.nn.Module):
    """Tiny deterministic stand-in for ResNetSkeleton (sigmoid output, 1 channel)."""

    def __init__(self):
        super().__init__()
        torch.manual_seed(0)
        self.conv = torch.nn.Conv2d(3, 1, 1)
        self.calls = 0
        self.items = 0

    def forward(self, x):
        self.calls += 1
        self.items += x.shape[0]
        return torch.sigmoid(self.conv(x))


def _legacy_masks(input_dir, model):
    """The pre-refactor MaskGenerationThread loop, for byte equality."""
    tf = mask_inference.mask_transform()
    out = {}
    for name in sorted(os.listdir(input_dir)):
        try:
            img = Image.open(os.path.join(input_dir, name)).convert("RGB")
        except OSError:
            continue
        with torch.inference_mode():
            mask = model(tf(img).unsqueeze(0))[0]
        mask_np = mask.squeeze().float().cpu().numpy()
        binary = (mask_np > MASK_THRESHOLD).astype(np.uint8) * 255
        buf = io.BytesIO()
        Image.fromarray(binary, mode="L").save(buf, "PNG")
        out[os.path.splitext(name)[0] + ".png"] = buf.getvalue()
    return out


def _read_all(folder, suffix=".png"):
    return {
        n: open(os.path.join(folder, n), "rb").read()
        for n in sorted(os.listdir(folder))
        if n.endswith(suffix)
    }


@pytest.fixture
def cpu_runtime(monkeypatch):
    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    yield runtime.get_runtime()
    runtime.get_runtime.cache_clear()


def test_mask_generation_records_unreadable_images_and_matches_legacy(tmp_path, cpu_runtime):
    src = _images(
        str(tmp_path / "imgs"), ["a.jpg", "b.png", "garbage.jpg", "c.jpeg"], corrupt={"garbage.jpg"}
    )
    out = str(tmp_path / "imgs" / "mask")
    model = _StubMaskModel().eval()
    report = mask_inference.generate_masks(src, out, model=model, batch_size=2)

    assert report.failed_names == ["garbage.jpg"]
    assert sorted(report.written) == ["a.jpg", "b.png", "c.jpeg"]
    assert not os.path.exists(os.path.join(out, "garbage.png"))
    assert _read_all(out) == _legacy_masks(src, model)
    with Image.open(os.path.join(out, "a.png")) as im:
        assert im.mode == "L"


def test_mask_generation_skips_existing_stems_without_inference(tmp_path, cpu_runtime):
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "b.jpg", "c.jpg"])
    out = str(tmp_path / "imgs" / "mask")
    os.makedirs(out)
    traced = os.path.join(out, "a.png")
    Image.new("RGBA", (64, 48), (255, 255, 255, 255)).save(traced)
    before = open(traced, "rb").read()
    model = _StubMaskModel().eval()

    report = mask_inference.generate_masks(
        src, out, model=model, batch_size=4, skip_stems=frozenset({"a"}), batch_policy="auto"
    )
    assert open(traced, "rb").read() == before
    assert report.skipped == ["a"]
    assert sorted(report.written) == ["b.jpg", "c.jpg"]
    assert model.items == 2


def test_mask_run_writes_provenance_manifest(tmp_path, cpu_runtime, monkeypatch):
    weights = tmp_path / "w.pth"
    weights.write_bytes(b"fake weights")
    monkeypatch.setattr(runtime, "mask_weights_path", lambda: str(weights))
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "bad.jpg"], corrupt={"bad.jpg"})
    out = str(tmp_path / "imgs" / "mask")
    mask_inference.generate_masks(
        src, out, model=_StubMaskModel().eval(), batch_size=2, policy="keep_existing"
    )
    with open(provenance.run_manifest_path(out), encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["kind"] == "mask_generation"
    assert record["models"]["mask"]["sha256"] == hashlib.sha256(b"fake weights").hexdigest()
    assert record["parameters"]["mask_threshold"] == MASK_THRESHOLD
    assert record["parameters"]["overwrite_policy"] == "keep_existing"
    assert record["outputs"]["n_written"] == 1
    assert record["outputs"]["failed"][0]["input"] == "bad.jpg"
    assert record["runtime"]["inference"]["precision"] == "fp32"
    assert record["inputs"]["names_sha256"] == provenance.names_digest(["a.jpg", "bad.jpg"])


def test_mask_run_releases_cuda_cache_once(tmp_path, cpu_runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "release_cuda_cache", lambda: calls.append(1))
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    mask_inference.generate_masks(
        src, str(tmp_path / "imgs" / "mask"), model=_StubMaskModel().eval(), batch_size=2
    )
    assert calls == [1]


def test_mask_run_loads_model_lazily_with_a_stage(tmp_path, cpu_runtime, monkeypatch):
    stub = _StubMaskModel().eval()
    monkeypatch.setattr(runtime, "get_mask_model", lambda device=None: stub)
    stages = []
    src = _images(str(tmp_path / "imgs"), ["a.jpg"])
    mask_inference.generate_masks(
        src, str(tmp_path / "imgs" / "mask"), on_stage=stages.append, batch_policy="auto"
    )
    assert stages == [mask_inference.STAGE_LOADING, mask_inference.STAGE_RUNNING]
    assert stub.items == 1


class _StubSkeletonModel:
    def __init__(self):
        torch.manual_seed(0)
        self.net = torch.nn.Conv2d(3, 3, 1)

    def run_batch(self, x):
        with torch.inference_mode():
            return torch.tanh(self.net(x))


def _legacy_skeletons(input_dir, model):
    tf = si.get_transform(grayscale=False)
    out = {}
    for path in si.make_dataset(input_dir):
        try:
            img = Image.open(path).convert("RGB")
        except OSError:
            continue
        # the former per-image tensor2im: numpy fp32 (x + 1) / 2 * 255 -> uint8
        chw = model.run_batch(tf(img).unsqueeze(0))[0].cpu().float().numpy()
        arr = ((np.transpose(chw, (1, 2, 0)) + 1) / 2.0 * 255.0).astype(np.uint8)
        img_pil = Image.fromarray(arr)
        h, w, _ = arr.shape
        img_pil = img_pil.resize((int(h / si.ASPECT_RATIO), w), Image.BICUBIC)
        buf = io.BytesIO()
        img_pil.save(buf, "PNG")
        stem = os.path.splitext(os.path.basename(path))[0]
        out[f"{stem}_fake.png"] = buf.getvalue()
    return out


def test_skeleton_run_continues_past_unreadable_images(tmp_path, cpu_runtime):
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "bad.jpg", "c.jpg"], corrupt={"bad.jpg"})
    out = str(tmp_path / "imgs" / "skeletons")
    model = _StubSkeletonModel()
    report = si.run_skeleton_generation(src, out, model=model, batch_size=2)
    assert report.failed_names == ["bad.jpg"]
    assert sorted(report.written) == ["a.jpg", "c.jpg"]
    assert _read_all(out) == _legacy_skeletons(src, model)
    with open(provenance.run_manifest_path(out), encoding="utf-8") as fh:
        assert json.load(fh)["kind"] == "skeleton_generation"


def test_run_inference_keeps_its_contract(tmp_path, cpu_runtime, monkeypatch):
    model = _StubSkeletonModel()
    monkeypatch.setattr(runtime, "get_skeleton_model", lambda device=None: model)
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "b.jpg"])
    out = str(tmp_path / "imgs" / "skeletons")
    ticks = []
    result = si.run_inference(
        src, out, progress_callback=lambda d, t: ticks.append((d, t)), batch_size=1
    )
    assert result == out
    assert sorted(_read_all(out)) == ["a_fake.png", "b_fake.png"]
    assert ticks[-1] == (2, 2)


def _run(kind, src, out, model, **kwargs):
    if kind == "mask":
        return mask_inference.generate_masks(src, out, model=model, **kwargs)
    return si.run_skeleton_generation(src, out, model=model, **kwargs)


def _model(kind):
    return _StubMaskModel().eval() if kind == "mask" else _StubSkeletonModel()


@pytest.mark.parametrize("kind", ["mask", "skeleton"])
@pytest.mark.parametrize(("device_type", "threads"), [("cpu", None), ("cuda", 1)])
def test_a_run_passes_the_runtimes_torch_threads(tmp_path, monkeypatch, kind, device_type, threads):
    rt = runtime.RuntimeConfig(
        device=torch.device(device_type),
        use_channels_last=False,
        pin_memory=False,
        io_threads=2,
        cudnn_benchmark=False,
        cudnn_deterministic=True,
        precision="fp32",
    )
    monkeypatch.setattr(runtime, "get_runtime", lambda: rt)
    seen = []

    def fake_run_batches(paths, spec, **kwargs):
        seen.append(kwargs["torch_threads"])
        return kwargs["report"]

    monkeypatch.setattr(mask_inference if kind == "mask" else si, "run_batches", fake_run_batches)
    src = _images(str(tmp_path / "imgs"), ["a.jpg"])
    _run(kind, src, str(tmp_path / "out"), _model(kind), batch_size=1, batch_policy="auto")
    assert seen == [threads]


@pytest.mark.parametrize(("kind", "label"), [("mask", "Mask"), ("skeleton", "Skeleton")])
def test_a_run_with_failures_ends_with_one_bounded_line(tmp_path, cpu_runtime, caplog, kind, label):
    bad = [f"bad{i:02d}.jpg" for i in range(8)]
    src = _images(str(tmp_path / "imgs"), bad + ["good.jpg"], corrupt=set(bad))
    out = str(tmp_path / "imgs" / "out")
    with caplog.at_level(logging.WARNING):
        report = _run(kind, src, out, _model(kind), batch_size=2)
    assert report.failed_names == bad
    [line] = [r.getMessage() for r in caplog.records if "input(s) failed" in r.getMessage()]
    assert line.startswith(f"{label} generation: 8 input(s) failed: bad00.jpg: ")
    assert "bad04.jpg" in line and "bad05.jpg" not in line and "; and 3 more; " in line
    assert line.endswith(f"every one is listed in {provenance.run_manifest_path(out)}")


def test_the_skeleton_model_loads_its_checkpoint_from_the_runtime_path(tmp_path, monkeypatch):
    torch.manual_seed(0)
    net = si.EnhancedResnetGenerator(
        si.INPUT_NC,
        si.OUTPUT_NC,
        si.NGF,
        norm_layer=si.get_norm_layer(norm_type=si.NORM),
        use_dropout=si.USE_DROPOUT,
        n_blocks=9,
    )
    saved = {key: value.clone() for key, value in net.state_dict().items()}
    path = tmp_path / "latest_net_G.pth"
    torch.save(net.state_dict(), path)
    monkeypatch.setattr(runtime, "skeleton_weights_path", lambda: str(path))

    model = si.SkeletonModel(gpu_ids=[])

    loaded = model.netG.state_dict()
    assert model.device == torch.device("cpu") and not model.netG.training
    assert loaded.keys() == saved.keys()
    assert all(torch.equal(loaded[key], saved[key]) for key in saved)
    assert not any(t.is_meta for t in [*model.netG.parameters(), *model.netG.buffers()])
    assert model.run_batch(torch.zeros(1, 3, 64, 64)).shape == (1, 3, 64, 64)


def test_a_missing_skeleton_checkpoint_names_its_path(tmp_path, monkeypatch):
    missing = str(tmp_path / "nope_net_G.pth")
    monkeypatch.setattr(runtime, "skeleton_weights_path", lambda: missing)
    with pytest.raises(
        RuntimeError, match="Failed to load skeleton model weights from .*nope_net_G"
    ):
        si.SkeletonModel(gpu_ids=[])

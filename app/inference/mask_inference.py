"""ResNet mask generation core (Qt-free; torch/torchvision imported lazily).

``generate_masks(input_dir, output_dir, ...)`` lists the images (sorted, so batch
composition no longer depends on the file system), leaves ``skip_stems`` alone
(RB-01: existing / hand-corrected masks), loads the cached mask model on first
use (PG-04 / PI-11: never at app start-up), runs the shared batch engine
(failures recorded, OOM backoff, overlap, cancel), writes the run manifest and
releases the CUDA cache once (PI-13).

Output format is unchanged: ``<output_dir>/<stem>.png``, 8-bit greyscale, 255
where the sigmoid output exceeds ``config.MASK_THRESHOLD``. Numerics: fp32
tensors; on CUDA with Ampere+ the convolutions and the DualAttention matmuls use
TF32 tensor-core math (see ``runtime.RuntimeConfig.precision``). No autocast:
fp16/bf16 mixed precision degrades the attention blocks' masks.
"""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Callable
from typing import Any

from app import config
from app.inference import runtime
from app.inference.batch_engine import BatchReport, BatchSpec, ProgressCallback, run_batches
from app.inference.run_record import write_run_manifest
from app.io_utils import atomic_write_bytes

logger = logging.getLogger(__name__)

RUN_KIND = "mask_generation"
MASK_INPUT_SIZE = (480, 640)  # (h, w) the mask model was trained on
MASK_MEAN = (0.5514, 0.4094, 0.3140)
MASK_STD = (0.1299, 0.1085, 0.0914)

STAGE_LOADING = "Loading mask model"
STAGE_RUNNING = "Generating masks"


def list_inputs(input_dir: str, skip_stems: frozenset[str] = frozenset()):
    """``(all image names, paths to process, skipped stems)``, names sorted."""
    names = sorted(n for n in os.listdir(input_dir) if config.is_image_file(n))
    todo, skipped = [], []
    for name in names:
        stem = os.path.splitext(name)[0]
        if stem in skip_stems:
            skipped.append(stem)
        else:
            todo.append(os.path.join(input_dir, name))
    return names, todo, skipped


def mask_transform():
    """The model's input transform: resize to 480x640, to tensor, normalise."""
    import torchvision.transforms as transforms

    return transforms.Compose(
        [
            transforms.Resize(MASK_INPUT_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(mean=list(MASK_MEAN), std=list(MASK_STD)),
        ]
    )


def mask_output_path(output_dir: str, input_path: str) -> str:
    """``<output_dir>/<stem>.png`` (the path the mask editor also saves to)."""
    return os.path.join(output_dir, os.path.splitext(os.path.basename(input_path))[0] + ".png")


def make_mask_spec(model: Any, output_dir: str, *, threshold: float = config.MASK_THRESHOLD):
    """BatchSpec: decode -> forward + threshold on the device -> atomic PNG write."""
    import torch
    from PIL import Image

    rt = runtime.get_runtime()
    device = rt.device
    channels_last = rt.use_channels_last and device.type == "cuda"
    transform = mask_transform()

    def load(path: str):
        with Image.open(path) as image:
            return transform(image.convert("RGB"))

    def step(x):
        x = x.to(device, non_blocking=True)
        if channels_last:
            x = x.contiguous(memory_format=torch.channels_last)
        with torch.inference_mode():
            masks = model(x).squeeze(1).float()
            # Same comparison the per-image numpy loop made; one D2H copy per batch.
            return ((masks > threshold).to(torch.uint8) * 255).cpu().numpy()

    def save(binary_mask, input_path: str) -> None:
        buf = io.BytesIO()
        Image.fromarray(binary_mask, mode="L").save(buf, "PNG")
        # Regenerable bulk output: atomic (never half-written) but not fsync'ed.
        atomic_write_bytes(mask_output_path(output_dir, input_path), buf.getvalue(), durable=False)

    return BatchSpec(load=load, step=step, save=save)


def default_mask_batch_size() -> int:
    """``MASK_BATCH_SIZE`` on a GPU; at most ``MASK_BATCH_SIZE_CPU`` on the CPU fallback."""
    if runtime.get_runtime().device.type == "cuda":
        return config.MASK_BATCH_SIZE
    return min(config.MASK_BATCH_SIZE, config.MASK_BATCH_SIZE_CPU)


def generate_masks(
    input_dir: str,
    output_dir: str,
    *,
    skip_stems: frozenset[str] = frozenset(),
    model: Any = None,
    batch_size: int | None = None,
    policy: str | None = None,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> BatchReport:
    """Generate ``<output_dir>/<stem>.png`` for every image not in ``skip_stems``.

    Args:
        model: a ready model (tests); default: ``runtime.get_mask_model()``, loaded
            here on first use (``ModelUnavailableError`` if the weights are missing).
        batch_size: default ``config.MASK_BATCH_SIZE`` on a GPU (halved on
            out-of-memory), ``config.MASK_BATCH_SIZE_CPU`` on CPU so Cancel is
            honoured within a second or two.
        policy: the overwrite choice, recorded in the manifest.
        on_stage: called with ``STAGE_LOADING`` / ``STAGE_RUNNING``.
    """
    batch_size = batch_size or default_mask_batch_size()
    os.makedirs(output_dir, exist_ok=True)
    names, todo, skipped = list_inputs(input_dir, skip_stems)
    try:
        if todo:
            if model is None:
                if on_stage:
                    on_stage(STAGE_LOADING)
                model = runtime.get_mask_model()
            model.eval()
        if on_stage:
            on_stage(STAGE_RUNNING)
        if todo:
            rt = runtime.get_runtime()
            report = run_batches(
                todo,
                make_mask_spec(model, output_dir),
                batch_size=batch_size,
                io_threads=rt.io_threads,
                pin_memory=rt.pin_memory,
                progress=progress,
                should_stop=should_stop,
            )
        else:
            report = BatchReport(batch_size=batch_size, effective_batch_size=batch_size)
    finally:
        runtime.release_cuda_cache()
    report.skipped = skipped
    write_run_manifest(
        RUN_KIND,
        input_dir=input_dir,
        output_dir=output_dir,
        input_names=names,
        report=report,
        models={"mask": runtime.mask_weights_path()},
        parameters={
            "mask_threshold": config.MASK_THRESHOLD,
            "batch_size": batch_size,
            "input_size_hw": list(MASK_INPUT_SIZE),
            "overwrite_policy": policy,
        },
    )
    if report.failed:
        logger.warning("Mask generation: %d input(s) failed: %s", len(report.failed), report.failed)
    return report


__all__ = [
    "MASK_INPUT_SIZE",
    "RUN_KIND",
    "STAGE_LOADING",
    "STAGE_RUNNING",
    "generate_masks",
    "list_inputs",
    "make_mask_spec",
    "mask_output_path",
    "mask_transform",
]

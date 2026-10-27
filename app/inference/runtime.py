"""
Shared inference runtime auto-tuner.

Single source of truth for *how* both inference paths (skeleton_inference.py and
mask_inference.py) talk to the hardware: which device, which numeric precision,
whether to use channels_last memory format and pinned host memory, and how many
I/O threads feed the GPU.

Design rules (all enforced here so the two paths can never drift):
  * Detect the runtime environment at first call -- never hardcode a GPU index.
    Always fall back to CPU when CUDA is unavailable.
  * Precision: tensors and weights are fp32 everywhere. On CUDA, cuDNN
    convolutions (``cudnn.allow_tf32``, PyTorch's default) and float32 matmuls
    (``set_float32_matmul_precision("high")``) use TF32 tensor-core math on
    Ampere and newer GPUs (10 explicit mantissa bits). Older GPUs and the CPU run
    strict fp32. The effective mode is reported as ``RuntimeConfig.precision``
    and recorded in every run manifest, because it changes low bits of outputs.
  * Reproducibility: ``cudnn.benchmark`` is OFF and ``cudnn.deterministic`` ON by
    default. Autotuning cost 40-70 s before the first batch, added no measurable
    throughput and picked different algorithms per session (masks differed by up
    to 5 px between sessions). Set ``SPROUTS_CUDNN_BENCHMARK=1`` to opt in for a
    GPU/model where autotuning measurably helps; the choice is recorded.
  * channels_last and pin_memory are CUDA-only; so is one torch thread for the
    decode / prefetch / save threads of a batch run (``batch_torch_threads``).
  * torch is imported lazily: importing this module (e.g. for the status line's
    ``describe_runtime()``) never loads torch or creates a CUDA context.

Public API:
  get_runtime() -> RuntimeConfig          (cached, resolves the device)
  peek_runtime() -> RuntimeConfig | None  (never resolves)
  warm_up(models=True) -> RuntimeConfig   (imports for the first job + get_runtime(); no model)
  describe_runtime(resolve=False) -> RuntimeSummary  (status line; never loads models)
  get_skeleton_model(device) -> SkeletonModel  (cached singleton)
  get_mask_model(device) -> torch.nn.Module     (cached singleton)
  release_cuda_cache() -> None            (call once when a batch job ends)
  batch_policy() -> str                   ("fixed" | "auto", SPROUTS_BATCH_POLICY; O4)
  resolve_batch_policy(policy) -> str     (a caller's explicit policy, else batch_policy())
  fixed_batch_size(model, device_type) -> int   (never queries the GPU)
  check_fixed_batch_memory(model, batch_size, device_type) -> float | None
                                          (pre-flight: free GiB, stops below the floor)

Before torch is loaded, the status line names the device through the NVIDIA driver
instead (``app.inference.device_probe``); it never reaches a run record.
"""

from __future__ import annotations

import functools
import gc
import importlib
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.inference.batch_pipeline import default_io_threads

if TYPE_CHECKING:  # pragma: no cover - typing only; torch is imported lazily
    import torch

logger = logging.getLogger(__name__)

CUDNN_BENCHMARK_ENV = "SPROUTS_CUDNN_BENCHMARK"

# (O4, R1) Batch policy. "fixed": every forward runs at one batch size (the last batch
# zero-padded), so an image's output does not depend on the folder or on free memory;
# out of GPU memory stops the run. "auto": the former path (skeleton batch sized to
# free VRAM, batches halved on out of memory).
BATCH_POLICY_ENV = "SPROUTS_BATCH_POLICY"
BATCH_POLICIES = ("fixed", "auto")
DEFAULT_BATCH_POLICY = "fixed"

# Fixed GPU batch sizes (research/o4-gpu-batch.md, Recommendation): masks at 16 need about
# 12 GiB free to keep their kernel, 4 fits a 6 GB card at no speed cost; skeletons at 64
# give the same output as every N >= 3 and are not slower than 128. Off the GPU the
# fixed sizes are config.MASK_BATCH_SIZE_CPU / SKELETON_BATCH_SIZE_CPU (as before).
FIXED_MASK_BATCH_SIZE_CUDA = 4
FIXED_SKELETON_BATCH_SIZE_CUDA = 64

# Pre-flight floors (GiB) of a fixed-size GPU run (research/o4-gpu-batch.md, Exp 1c /
# Exp 4). Below them cuDNN can switch kernels without an error and outputs change. They
# are compared with the DRIVER's free memory (torch.cuda.mem_get_info) read after the
# model has loaded and the cache was released, so the weights (mask 0.32 GiB, skeleton
# 0.05 GiB) are not part of it. The memo's allocator caps include the weights; cap minus
# weights is what a run needs free here:
#   masks 4:     identical down to 3600 MiB (about 3.2 GiB free); floor 3.6 leaves ~0.4 GiB.
#   skeletons 64: identical down to 3500 MiB, OOM at 3300 (about 3.37 GiB free); floor 3.8
#                 leaves ~0.4 GiB, the margin for the cuDNN / cuBLAS handles that load after
#                 the pre-flight (a Generate run, mask model loaded, needs about 3.8 GiB).
#   masks 16:    only for a caller's explicit 16: identical down to 12 000 MiB; floor 12.0.
FIXED_MASK_VRAM_FLOOR_GIB = 3.6
FIXED_SKELETON_VRAM_FLOOR_GIB = 3.8
FIXED_MASK_16_VRAM_FLOOR_GIB = 12.0

_GIB = 1024**3

# TF32 tensor cores exist from compute capability 8.0 (Ampere) on.
_TF32_MIN_CAPABILITY = (8, 0)


class ModelUnavailableError(RuntimeError):
    """A model checkpoint is missing or cannot be loaded (user-facing message)."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def mask_weights_path() -> str:
    """Checkpoint path of the mask model (read at call time; a test seam)."""
    from app import config

    return config.MASK_WEIGHTS_PATH


def skeleton_weights_path() -> str:
    """Checkpoint path of the skeleton model (read at call time; a test seam)."""
    from app import config

    return config.SKELETON_WEIGHTS_PATH


def batch_policy() -> str:
    """``SPROUTS_BATCH_POLICY`` (stripped, lower-cased) if valid, else the default (O4).

    Read on every call: unlike ``RuntimeConfig`` it is not cached. An unknown value
    logs a warning and falls back to ``DEFAULT_BATCH_POLICY``.
    """
    raw = os.environ.get(BATCH_POLICY_ENV)
    if raw is None:
        return DEFAULT_BATCH_POLICY
    value = raw.strip().lower()
    if value in BATCH_POLICIES:
        return value
    logger.warning(
        "Unknown %s=%r; using %r (expected one of %s)",
        BATCH_POLICY_ENV,
        raw,
        DEFAULT_BATCH_POLICY,
        ", ".join(BATCH_POLICIES),
    )
    return DEFAULT_BATCH_POLICY


def resolve_batch_policy(policy: str | None) -> str:
    """A caller's explicit policy (validated), or :func:`batch_policy` for ``None``."""
    if policy is None:
        return batch_policy()
    if policy not in BATCH_POLICIES:
        raise ValueError(f"Unknown batch policy {policy!r}; expected one of {BATCH_POLICIES}")
    return policy


def fixed_batch_size(model: str, device_type: str) -> int:
    """The fixed batch size of ``model`` ("mask" / "skeleton") on ``device_type`` (O4).

    Never queries the GPU.
    """
    from app import config

    sizes = {
        "mask": (FIXED_MASK_BATCH_SIZE_CUDA, config.MASK_BATCH_SIZE_CPU),
        "skeleton": (FIXED_SKELETON_BATCH_SIZE_CUDA, config.SKELETON_BATCH_SIZE_CPU),
    }
    if model not in sizes:
        raise ValueError(f"Unknown model {model!r}; expected 'mask' or 'skeleton'")
    gpu, cpu = sizes[model]
    return gpu if device_type == "cuda" else cpu


def check_fixed_batch_memory(model: str, batch_size: int, device_type: str) -> float | None:
    """Pre-flight of a fixed-policy run (O4): free GPU memory in GiB, None off the GPU.

    Call it once the model is loaded, before the first batch. Garbage is collected
    and the cache emptied (``release_cuda_cache``), then the driver's free memory
    (``torch.cuda.mem_get_info()``) read. Below the measured floor of the size
    (``FIXED_*_VRAM_FLOOR_GIB``) it raises ``FixedBatchMemoryTooLow``: the
    run would stop on out of memory, or worse change outputs without an error. A
    caller's other size has no measured floor and is only measured. A failed query
    returns None (logged): the check never breaks a run by itself.
    """
    if device_type != "cuda":
        return None
    import torch

    from app.inference import batch_engine

    release_cuda_cache()  # collected + emptied: this process's cached blocks count as free
    try:
        free_bytes, _total = torch.cuda.mem_get_info()
    except Exception as exc:  # noqa: BLE001 - a failed query must not stop the run
        logger.warning("Could not read the free GPU memory: %s", exc)
        return None
    free_gib = free_bytes / _GIB
    floors = {
        ("mask", FIXED_MASK_BATCH_SIZE_CUDA): FIXED_MASK_VRAM_FLOOR_GIB,
        ("skeleton", FIXED_SKELETON_BATCH_SIZE_CUDA): FIXED_SKELETON_VRAM_FLOOR_GIB,
        ("mask", 16): FIXED_MASK_16_VRAM_FLOOR_GIB,
    }
    needed = floors.get((model, batch_size))
    logger.info(
        "Fixed %s batch %d: %.2f GiB free GPU memory (floor %s)",
        model,
        batch_size,
        free_gib,
        "none measured" if needed is None else f"{needed} GiB",
    )
    if needed is not None and free_gib < needed:
        raise batch_engine.FixedBatchMemoryTooLow(batch_size, free_gib=free_gib, needed_gib=needed)
    return free_gib


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimeConfig:
    """Resolved, immutable per-machine inference runtime configuration.

    Attributes:
        device:              torch.device to run inference on (cuda or cpu).
        use_channels_last:   apply channels_last memory format (CUDA only).
        pin_memory:          pin host batches before the H2D copy (CUDA only).
        io_threads:          decode/save threads that overlap the GPU forward.
        cudnn_benchmark:     cuDNN autotuning (off unless SPROUTS_CUDNN_BENCHMARK=1).
        cudnn_deterministic: cuDNN deterministic algorithm selection.
        precision:           effective float math: "tf32" (CUDA, Ampere+) or "fp32".
        device_name:         human-readable accelerator name for logging.
        bf16_supported:      whether the CUDA device advertises bf16 support.
        compute_capability:  (major, minor) CUDA capability, or None on CPU.
        cuda_version:        torch's CUDA runtime version, or None on CPU.
    """

    device: torch.device
    use_channels_last: bool
    pin_memory: bool
    io_threads: int
    cudnn_benchmark: bool
    cudnn_deterministic: bool
    precision: str
    device_name: str = "cpu"
    bf16_supported: bool = False
    compute_capability: tuple[int, int] | None = None
    cuda_version: str | None = None

    @property
    def is_cuda(self) -> bool:
        return self.device.type == "cuda"

    @property
    def batch_torch_threads(self) -> int | None:
        """torch intra-op threads while a batch run lasts (``run_batches``' ``torch_threads``).

        1 on CUDA: every decode, prefetch and save thread would otherwise start its own
        OpenMP team of all the cores for its small tensor ops, and the busy teams keep
        the next batch late and the GPU waiting. None (unchanged) on the CPU, where the
        forward on the job thread needs the whole team. Outputs are the same either way.
        """
        return 1 if self.is_cuda else None


# ---------------------------------------------------------------------------
# Cached runtime detection
# ---------------------------------------------------------------------------
def cuda_usable(torch_module: Any = None) -> bool:
    """True when CUDA reports itself available AND exposes at least one device.

    ``CUDA_VISIBLE_DEVICES=""`` makes ``torch.cuda.is_available()`` return True
    with ``device_count() == 0``; loading a checkpoint onto "cuda" then fails, so
    that case must take the CPU fallback.
    """
    if torch_module is None:
        import torch as torch_module
    try:
        return bool(torch_module.cuda.is_available()) and torch_module.cuda.device_count() > 0
    except (RuntimeError, AssertionError):
        return False


@functools.lru_cache(maxsize=1)
def get_runtime() -> RuntimeConfig:
    """Detect and cache the inference runtime for this machine.

    Auto-detects CUDA vs CPU and applies the global cuDNN/TF32 policy described
    in the module docstring. Never hardcodes a GPU index; always falls back to CPU.
    """
    import torch

    benchmark = _env_flag(CUDNN_BENCHMARK_ENV)
    torch.backends.cudnn.benchmark = benchmark
    torch.backends.cudnn.deterministic = not benchmark

    is_cuda = cuda_usable(torch)
    compute_capability: tuple[int, int] | None = None
    bf16_supported = False
    precision = "fp32"
    if is_cuda:
        device = torch.device("cuda")
        try:
            device_name = torch.cuda.get_device_name(device)
        except (RuntimeError, AssertionError):
            device_name = "cuda"
        try:
            cc = torch.cuda.get_device_capability(device)
            compute_capability = (int(cc[0]), int(cc[1]))
        except (RuntimeError, AssertionError):
            compute_capability = None
        try:
            bf16_supported = bool(torch.cuda.is_bf16_supported())
        except (RuntimeError, AssertionError):
            bf16_supported = False
        # TF32 for float32 matmuls (the mask model's DualAttention bmm); cuDNN
        # convolutions already default to allow_tf32=True. Only Ampere+ has TF32.
        torch.set_float32_matmul_precision("high")
        if (
            compute_capability is not None
            and compute_capability >= _TF32_MIN_CAPABILITY
            and torch.backends.cudnn.allow_tf32
        ):
            precision = "tf32"
    else:
        device = torch.device("cpu")
        device_name = "cpu"

    config = RuntimeConfig(
        device=device,
        use_channels_last=is_cuda,
        pin_memory=is_cuda,
        io_threads=default_io_threads(),
        cudnn_benchmark=benchmark,
        cudnn_deterministic=not benchmark,
        precision=precision,
        device_name=device_name,
        bf16_supported=bf16_supported,
        compute_capability=compute_capability,
        cuda_version=torch.version.cuda if is_cuda else None,
    )
    logger.info(
        "Inference runtime resolved: device=%s (%s) precision=%s cudnn_benchmark=%s "
        "cudnn_deterministic=%s channels_last=%s io_threads=%d cc=%s",
        config.device,
        config.device_name,
        config.precision,
        config.cudnn_benchmark,
        config.cudnn_deterministic,
        config.use_channels_last,
        config.io_threads,
        config.compute_capability,
    )
    return config


def peek_runtime() -> RuntimeConfig | None:
    """The resolved runtime if ``get_runtime()`` already ran, else None (never resolves)."""
    if get_runtime.cache_info().currsize:
        return get_runtime()
    return None


#: What the first generation job imports besides torch: the Pix2Pix generator (with
#: torchvision) and the ResNet mask model's code.
_FIRST_JOB_MODULES = ("app.inference.skeleton_inference", "app.mask_model.model")


def warm_up(*, models: bool = True) -> RuntimeConfig:
    """Import what the first inference job needs, then resolve the runtime; load no model.

    torch and the device first (:func:`get_runtime`, ~1.3 s, ~150k long-lived Python
    objects), then with ``models`` torchvision and both models' code (~1 s, ~120k more):
    call it off the GUI thread (the warm-up queue does when the user heads for Generate).
    No checkpoint is read. Settings and outputs are those of a job that resolves the
    runtime itself.
    """
    config = get_runtime()
    if models:
        for name in _FIRST_JOB_MODULES:
            importlib.import_module(name)
    return config


# ---------------------------------------------------------------------------
# Status-line summary (UXS-06)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimeSummary:
    """Short device / precision / model-availability summary for the status line."""

    device: str  # "GPU", "CPU" or "unknown" (runtime not resolved yet)
    device_name: str | None
    cuda_version: str | None
    precision: str | None
    mask_weights: bool
    skeleton_weights: bool
    mask_loaded: bool
    skeleton_loaded: bool

    @property
    def models_ok(self) -> bool:
        return self.mask_weights and self.skeleton_weights

    @property
    def text(self) -> str:
        """e.g. ``"GPU · NVIDIA RTX 4080 · CUDA 12.8 · TF32"`` or ``"CPU · FP32"``."""
        if self.device == "unknown":
            parts = ["Device not detected yet"]
        else:
            parts = [self.device]
            if self.device == "GPU" and self.device_name:
                parts.append(self.device_name)
            if self.cuda_version:
                parts.append(f"CUDA {self.cuda_version}")
            if self.precision:
                parts.append(self.precision.upper())
        if not self.mask_weights:
            parts.append("mask model missing")
        if not self.skeleton_weights:
            parts.append("skeleton model missing")
        return " · ".join(parts)


def describe_runtime(resolve: bool = False) -> RuntimeSummary:
    """Summarise the inference runtime without loading any model.

    With ``resolve=False`` (default) this never imports torch or creates a CUDA
    context: the device reads "unknown" until a job (or a caller passing
    ``resolve=True``, preferably off the GUI thread) has resolved the runtime.
    """
    rt = get_runtime() if resolve else peek_runtime()
    return RuntimeSummary(
        device="unknown" if rt is None else ("GPU" if rt.is_cuda else "CPU"),
        device_name=None if rt is None else rt.device_name,
        cuda_version=None if rt is None else rt.cuda_version,
        precision=None if rt is None else rt.precision,
        mask_weights=os.path.isfile(mask_weights_path()),
        skeleton_weights=os.path.isfile(skeleton_weights_path()),
        mask_loaded=_is_loaded(get_mask_model),
        skeleton_loaded=_is_loaded(get_skeleton_model),
    )


def _is_loaded(factory: Any) -> bool:
    """True when a cached model factory has already built its model."""
    cache_info = getattr(factory, "cache_info", None)
    return bool(cache_info is not None and cache_info().currsize)


# ---------------------------------------------------------------------------
# Model singletons (loaded lazily, on the worker thread that first needs them)
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()


@functools.lru_cache(maxsize=1)
def get_skeleton_model(device: torch.device | None = None):
    """Return a cached singleton SkeletonModel for skeleton inference.

    The generator network and its checkpoint are loaded exactly once per
    process, on the runtime device, in eval mode and (on CUDA) channels_last.
    Imported lazily to avoid a circular import with skeleton_inference.
    """
    import torch

    from app.inference.skeleton_inference import SkeletonModel  # local import

    rt = get_runtime()
    target = device if device is not None else rt.device
    with _model_lock:
        gpu_ids = [target.index or 0] if target.type == "cuda" else []
        model = SkeletonModel(gpu_ids=gpu_ids)
        net = model.netG
        if rt.use_channels_last and target.type == "cuda":
            try:
                net.to(memory_format=torch.channels_last)
            except (RuntimeError, ValueError) as exc:
                logger.debug("channels_last not applied to skeleton model: %s", exc)
        net.eval()
    logger.info("Skeleton model singleton created on %s", target)
    return model


@functools.lru_cache(maxsize=1)
def get_mask_model(device: torch.device | None = None):
    """Return the cached ResNet mask model, built on first use (PG-04 / PI-11).

    The network is constructed on the ``meta`` device (no random init) and the
    checkpoint is loaded straight onto the target device with ``assign=True``,
    so the weights are copied once. Raises ``ModelUnavailableError`` when the
    checkpoint is missing or does not load.
    """
    import torch

    from app.mask_model.model import ResNetSkeleton

    rt = get_runtime()
    target = device if device is not None else rt.device
    weights_path = mask_weights_path()
    if not os.path.isfile(weights_path):
        raise ModelUnavailableError(f"Mask model weights not found: {weights_path}")
    with _model_lock:
        try:
            # weights_only=True prevents arbitrary code execution via pickle (F-023).
            state_dict = torch.load(weights_path, map_location=target, weights_only=True)
            with torch.device("meta"):
                model = ResNetSkeleton(num_classes=1, pretrained=False)
            model.load_state_dict(state_dict, assign=True)
        except (RuntimeError, OSError, ValueError) as exc:
            raise ModelUnavailableError(
                f"Could not load mask model weights from {weights_path}: {exc}"
            ) from exc
        leftover = [n for n, t in _named_tensors(model) if t.is_meta]
        if leftover:  # a buffer the checkpoint does not carry: fall back to a real init
            logger.warning("Mask model has non-checkpoint tensors %s; full init", leftover)
            model = ResNetSkeleton(num_classes=1, pretrained=False)
            model.load_state_dict(state_dict)
        model = model.to(target).eval()
        if rt.use_channels_last and target.type == "cuda":
            model = model.to(memory_format=torch.channels_last)
    logger.info("Mask model loaded from %s on %s", weights_path, target)
    return model


def _named_tensors(module: Any):
    yield from module.named_parameters()
    yield from module.named_buffers()


def release_cuda_cache() -> None:
    """Return PyTorch's cached GPU memory to the driver once a batch job ends (PI-13).

    No-op when torch is not loaded or CUDA was never initialised. Never call it
    per batch: that defeats the caching allocator.

    Never raises (R2): it runs in the ``finally`` of every generation run, and
    after a sticky CUDA error (e.g. an illegal memory access) every CUDA call
    fails again, ``empty_cache()`` included. Raising there would replace the
    run's own error, or fail a run that had finished; the failure is logged.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if not torch.cuda.is_initialized():
            return
        gc.collect()
        torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001 - housekeeping must not mask the outcome
        logger.warning("Could not release the CUDA cache: %s", exc)


__all__ = [
    "BATCH_POLICIES",
    "BATCH_POLICY_ENV",
    "CUDNN_BENCHMARK_ENV",
    "DEFAULT_BATCH_POLICY",
    "FIXED_MASK_16_VRAM_FLOOR_GIB",
    "FIXED_MASK_BATCH_SIZE_CUDA",
    "FIXED_MASK_VRAM_FLOOR_GIB",
    "FIXED_SKELETON_BATCH_SIZE_CUDA",
    "FIXED_SKELETON_VRAM_FLOOR_GIB",
    "ModelUnavailableError",
    "batch_policy",
    "check_fixed_batch_memory",
    "fixed_batch_size",
    "resolve_batch_policy",
    "RuntimeConfig",
    "RuntimeSummary",
    "describe_runtime",
    "get_mask_model",
    "get_runtime",
    "get_skeleton_model",
    "mask_weights_path",
    "peek_runtime",
    "release_cuda_cache",
    "skeleton_weights_path",
    "warm_up",
]

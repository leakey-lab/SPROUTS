"""
Consolidated Pix2Pix Skeleton Inference Script

This script contains all necessary components for running skeleton inference:
- EnhancedResnetGenerator network architecture
- Image loading and preprocessing
- Model weight loading
- Batch inference through the shared engine (app.inference.batch_engine) and saving

All constants are hardcoded for the skeletonizer model.

Numerics: fp32 tensors and weights, no autocast (the pix2pix generator drifts
visibly under fp16/bf16). On CUDA with an Ampere+ GPU, cuDNN convolutions use TF32
tensor-core math (``runtime.RuntimeConfig.precision`` records which one ran).
"""

from __future__ import annotations

import functools
import io
import logging
import os
from collections.abc import Callable
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torchvision.transforms as transforms
from PIL import Image

from app import config
from app.config import SKELETONIZER_DIR
from app.inference.batch_engine import BatchReport, BatchSpec, ProgressCallback, run_batches
from app.inference.run_record import record_failed_run, write_run_manifest
from app.io_utils import atomic_write_bytes
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS - Hardcoded configuration for skeletonizer model
# =============================================================================

# SKELETONIZER_DIR is resolved from app.config (F-005): absolute path relative to
# the project root, so CWD no longer matters.
RUN_KIND = "skeleton_generation"
NORM = "batch"
INPUT_NC = 3
OUTPUT_NC = 3
NGF = 64
LOAD_SIZE = 256
CROP_SIZE = 256  # square generator output; with ASPECT_RATIO -> config.SKELETON_OUTPUT_SIZE
ASPECT_RATIO = 3 / 4
USE_DROPOUT = True  # no_dropout is False by default
EPOCH = "latest"
SKELETON_SUFFIX = "_fake.png"

IMG_EXTENSIONS = [
    ".jpg",
    ".JPG",
    ".jpeg",
    ".JPEG",
    ".png",
    ".PNG",
    ".ppm",
    ".PPM",
    ".bmp",
    ".BMP",
    ".tif",
    ".TIF",
    ".tiff",
    ".TIFF",
]


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def is_image_file(filename):
    """Check if a file is an image based on extension (case-insensitive)."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in {e.lower() for e in IMG_EXTENSIONS}


# The extensions make_dataset reads, in stem-clash priority: the Library's
# (config.IMAGE_EXTENSIONS) first, then the formats only skeleton generation reads.
_INPUT_EXTENSIONS = tuple(
    dict.fromkeys((*config.IMAGE_EXTENSIONS, *(e.lower() for e in IMG_EXTENSIONS)))
)


def make_dataset(directory):
    """Find all image files in a directory (sorted), one per stem.

    Two files with one stem (``a.png`` and ``a.jpg``) would race for one
    ``<stem>_fake.png``; the one the Library shows is taken (R7).
    """
    if not os.path.isdir(directory):
        raise ValueError(f"{directory} is not a valid directory")
    chosen = ProjectLayout(directory).scan_images(extensions=_INPUT_EXTENSIONS)
    return [
        os.path.join(directory, fname)
        for fname in sorted(os.path.basename(path) for path in chosen.values())
    ]


def skeleton_to_uint8(output_batch: torch.Tensor) -> np.ndarray:
    """Generator output in [-1, 1] (N, C, H, W) -> uint8 images (N, H, W, C).

    The same fp32 arithmetic the former per-image ``tensor2im`` did in numpy
    (``(x + 1) / 2 * 255`` truncated to uint8), done on the device for the whole
    batch so there is one device-to-host copy per batch.
    """
    images = (output_batch.float() + 1) / 2.0 * 255.0
    return images.to(torch.uint8).permute(0, 2, 3, 1).contiguous().cpu().numpy()


def save_image(image_numpy, image_path, aspect_ratio=1.0):
    """Save a numpy image to disk (atomically: never a half-written file).

    Resize uses the exact axis order from the validated V2 pipeline -- the
    skeletonizer weights and all downstream length/area metrics were calibrated
    against this geometry. Do NOT "fix" the apparent (w, h) swap: changing it
    transposes the skeleton (341x256 -> 256x341) and corrupts the metrics.
    """
    image_pil = Image.fromarray(image_numpy)
    h, w, _ = image_numpy.shape

    if aspect_ratio > 1.0:
        image_pil = image_pil.resize((h, int(w * aspect_ratio)), Image.BICUBIC)
    if aspect_ratio < 1.0:
        image_pil = image_pil.resize((int(h / aspect_ratio), w), Image.BICUBIC)
    ext = os.path.splitext(image_path)[1].lower()
    buf = io.BytesIO()
    image_pil.save(buf, Image.registered_extensions().get(ext, "PNG"))
    # Regenerable bulk output: atomic but not fsync'ed.
    atomic_write_bytes(image_path, buf.getvalue(), durable=False)


def get_transform(grayscale=False):
    """Create image transformation pipeline."""
    transform_list = []
    if grayscale:
        transform_list.append(transforms.Grayscale(1))

    # Resize
    osize = [LOAD_SIZE, LOAD_SIZE]
    transform_list.append(transforms.Resize(osize, transforms.InterpolationMode.BICUBIC))

    # Convert to tensor and normalize
    transform_list.append(transforms.ToTensor())
    if grayscale:
        transform_list.append(transforms.Normalize((0.5,), (0.5,)))
    else:
        transform_list.append(transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)))

    return transforms.Compose(transform_list)


# =============================================================================
# NETWORK ARCHITECTURE
# =============================================================================


class Identity(nn.Module):
    def forward(self, x):
        return x


def get_norm_layer(norm_type="instance"):
    """Return a normalization layer."""
    if norm_type == "batch":
        norm_layer = functools.partial(nn.BatchNorm2d, affine=True, track_running_stats=True)
    elif norm_type == "instance":
        norm_layer = functools.partial(nn.InstanceNorm2d, affine=False, track_running_stats=False)
    elif norm_type == "none":

        def norm_layer(x):
            return Identity()

    else:
        raise NotImplementedError(f"normalization layer [{norm_type}] is not found")
    return norm_layer


def move_net_to_device(net, gpu_ids=None):
    """Move network to GPU without initializing weights (for loading from checkpoint).

    DataParallel is only used for genuine multi-GPU runs; on a single GPU it is
    pure scatter/gather overhead (and forces ``.module`` unwrap branches), so we
    do a plain ``net.to(device)`` instead.
    """
    gpu_ids = gpu_ids or []
    if len(gpu_ids) > 0:
        assert torch.cuda.is_available()
        net.to(gpu_ids[0])
        if len(gpu_ids) > 1:
            net = torch.nn.DataParallel(net, gpu_ids)
    return net


class ResnetBlock(nn.Module):
    """ResNet block with skip connections."""

    def __init__(self, dim, padding_type, norm_layer, use_dropout, use_bias):
        super().__init__()
        self.conv_block = self.build_conv_block(
            dim, padding_type, norm_layer, use_dropout, use_bias
        )

    def build_conv_block(self, dim, padding_type, norm_layer, use_dropout, use_bias):
        conv_block = []
        p = 0
        if padding_type == "reflect":
            conv_block += [nn.ReflectionPad2d(1)]
        elif padding_type == "replicate":
            conv_block += [nn.ReplicationPad2d(1)]
        elif padding_type == "zero":
            p = 1
        else:
            raise NotImplementedError(f"padding [{padding_type}] is not implemented")

        conv_block += [
            nn.Conv2d(dim, dim, kernel_size=3, padding=p, bias=use_bias),
            norm_layer(dim),
            nn.ReLU(True),
        ]
        if use_dropout:
            conv_block += [nn.Dropout(0.5)]

        p = 0
        if padding_type == "reflect":
            conv_block += [nn.ReflectionPad2d(1)]
        elif padding_type == "replicate":
            conv_block += [nn.ReplicationPad2d(1)]
        elif padding_type == "zero":
            p = 1
        else:
            raise NotImplementedError(f"padding [{padding_type}] is not implemented")
        conv_block += [
            nn.Conv2d(dim, dim, kernel_size=3, padding=p, bias=use_bias),
            norm_layer(dim),
        ]

        return nn.Sequential(*conv_block)

    def forward(self, x):
        out = x + self.conv_block(x)
        return out


class SELayer(nn.Module):
    """Squeeze-and-Excitation layer."""

    def __init__(self, channel, reduction=16):
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid(),
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)


class DilatedConvBlock(nn.Module):
    """Dilated convolution block."""

    def __init__(self, in_channels, out_channels, norm_layer, stride=1):
        super().__init__()
        self.conv1 = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=3,
            stride=stride,
            padding=1,
            dilation=1,
            bias=False,
        )
        self.bn1 = norm_layer(out_channels)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(
            out_channels,
            out_channels,
            kernel_size=3,
            stride=1,
            padding=2,
            dilation=2,
            bias=False,
        )
        self.bn2 = norm_layer(out_channels)
        self.conv3 = nn.Conv2d(
            out_channels,
            out_channels,
            kernel_size=3,
            stride=1,
            padding=4,
            dilation=4,
            bias=False,
        )
        self.bn3 = norm_layer(out_channels)

        if stride != 1 or in_channels != out_channels:
            self.downsample = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                norm_layer(out_channels),
            )
        else:
            self.downsample = None

    def forward(self, x):
        residual = x
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)
        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)
        return out


class EnhancedResnetGenerator(nn.Module):
    """Enhanced ResNet-based generator with dilated convolutions and SE attention."""

    def __init__(
        self,
        input_nc,
        output_nc,
        ngf=64,
        norm_layer=nn.BatchNorm2d,
        use_dropout=False,
        n_blocks=9,
        padding_type="reflect",
        use_attention=True,
    ):
        super().__init__()
        if isinstance(norm_layer, functools.partial):
            use_bias = norm_layer.func == nn.InstanceNorm2d
        else:
            use_bias = norm_layer == nn.InstanceNorm2d

        self.input_nc = input_nc
        self.output_nc = output_nc
        self.ngf = ngf

        # Initial convolution
        model = [
            nn.ReflectionPad2d(3),
            nn.Conv2d(input_nc, ngf, kernel_size=7, padding=0, bias=use_bias),
            norm_layer(ngf),
            nn.ReLU(True),
        ]

        # Downsampling with dilated convolutions
        n_downsampling = 2
        for i in range(n_downsampling):
            mult = 2**i
            model += [DilatedConvBlock(ngf * mult, ngf * mult * 2, norm_layer, stride=2)]

        # ResNet blocks with SE attention
        mult = 2**n_downsampling
        for i in range(n_blocks):
            model += [
                ResnetBlock(
                    ngf * mult,
                    padding_type=padding_type,
                    norm_layer=norm_layer,
                    use_dropout=use_dropout,
                    use_bias=use_bias,
                )
            ]
            if use_attention and i == n_blocks // 2:
                model += [SELayer(ngf * mult)]

        # Upsampling
        for i in range(n_downsampling):
            mult = 2 ** (n_downsampling - i)
            model += [
                nn.ConvTranspose2d(
                    ngf * mult,
                    int(ngf * mult / 2),
                    kernel_size=3,
                    stride=2,
                    padding=1,
                    output_padding=1,
                    bias=use_bias,
                ),
                norm_layer(int(ngf * mult / 2)),
                nn.ReLU(True),
            ]

        # Final convolution
        model += [nn.ReflectionPad2d(3)]
        model += [nn.Conv2d(ngf, output_nc, kernel_size=7, padding=0)]
        model += [nn.Tanh()]

        self.model = nn.Sequential(*model)

    def forward(self, input):
        return self.model(input)


# =============================================================================
# INFERENCE MODEL
# =============================================================================


class SkeletonModel:
    """Skeleton inference model wrapper."""

    def __init__(self, gpu_ids=None):
        gpu_ids = gpu_ids or []
        self.gpu_ids = gpu_ids
        self.device = torch.device(f"cuda:{gpu_ids[0]}") if gpu_ids else torch.device("cpu")

        # Create generator network
        norm_layer = get_norm_layer(norm_type=NORM)
        self.netG = EnhancedResnetGenerator(
            INPUT_NC,
            OUTPUT_NC,
            NGF,
            norm_layer=norm_layer,
            use_dropout=USE_DROPOUT,
            n_blocks=9,
        )
        # Move to GPU without initializing weights (weights will be loaded from checkpoint)
        self.netG = move_net_to_device(self.netG, gpu_ids)

        # Load pretrained weights from checkpoint
        self._load_network()

        # Ensure the model is in eval mode (ML correctness: no BatchNorm train-mode noise)
        self.netG.eval()

    def _load_network(self):
        """Load pretrained weights.

        ML correctness: weights_only=True guards against arbitrary-code execution
        via pickle (F-023 / SS-01).  SKELETONIZER_DIR from app.config provides an
        absolute path independent of CWD (F-005).
        """
        load_filename = f"{EPOCH}_net_G.pth"
        load_path = os.path.join(SKELETONIZER_DIR, load_filename)

        net = self.netG
        if isinstance(net, torch.nn.DataParallel):
            net = net.module

        logger.info("Loading skeleton model from %s", load_path)
        try:
            state_dict = torch.load(load_path, map_location=str(self.device), weights_only=True)
        except (FileNotFoundError, RuntimeError) as exc:
            raise RuntimeError(
                f"Failed to load skeleton model weights from {load_path!r}: {exc}"
            ) from exc

        if hasattr(state_dict, "_metadata"):
            del state_dict._metadata

        # Patch InstanceNorm checkpoints
        for key in list(state_dict.keys()):
            self._patch_instance_norm_state_dict(state_dict, net, key.split("."))

        net.load_state_dict(state_dict)

    def _patch_instance_norm_state_dict(self, state_dict, module, keys, i=0):
        """Fix InstanceNorm checkpoints incompatibility."""
        key = keys[i]
        if i + 1 == len(keys):
            if module.__class__.__name__.startswith("InstanceNorm") and (
                key == "running_mean" or key == "running_var"
            ):
                if getattr(module, key) is None:
                    state_dict.pop(".".join(keys))
            if module.__class__.__name__.startswith("InstanceNorm") and (
                key == "num_batches_tracked"
            ):
                state_dict.pop(".".join(keys))
        else:
            self._patch_instance_norm_state_dict(state_dict, getattr(module, key), keys, i + 1)

    def run(self, input_tensor):
        """Run inference on input tensor.

        fp32 tensors, NO autocast (see the module docstring on TF32): the pix2pix
        EnhancedResnetGenerator (BatchNorm + Tanh) drifts visibly under fp16/bf16.
        """
        # non_blocking pairs with the engine's pinned host batches to overlap the
        # H2D copy with compute (forward provides the sync point).
        input_tensor = input_tensor.to(self.device, non_blocking=True)
        with torch.inference_mode():
            output = self.netG(input_tensor)
        return output

    def run_batch(self, input_batch):
        """
        Run inference on a batch of input tensors.

        Args:
            input_batch: Batch of input tensors [batch_size, channels, height, width]

        Returns:
            Batch of output tensors
        """
        # non_blocking pairs with the engine's pinned host batches to overlap the
        # H2D copy with compute. fp32 tensors, NO autocast (fp16/bf16 drifts the
        # pix2pix generator output); TF32 math on Ampere+ CUDA.
        input_batch = input_batch.to(self.device, non_blocking=True)
        with torch.inference_mode():
            output_batch = self.netG(input_batch)
        return output_batch


# =============================================================================
# MAIN INFERENCE FUNCTIONS
# =============================================================================


def skeleton_output_path(output_dir: str, input_path: str) -> str:
    """``<output_dir>/<stem>_fake.png`` for one input image."""
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, f"{stem}{SKELETON_SUFFIX}")


def make_skeleton_spec(model: Any, output_dir: str) -> BatchSpec:
    """BatchSpec: decode + transform -> generator forward -> uint8 -> resized PNG."""
    transform = get_transform(grayscale=(INPUT_NC == 1))

    def load(path: str) -> torch.Tensor:
        with Image.open(path) as image:
            return transform(image.convert("RGB"))

    def step(x: torch.Tensor) -> np.ndarray:
        return skeleton_to_uint8(model.run_batch(x))

    def save(image_numpy: np.ndarray, input_path: str) -> None:
        save_image(image_numpy, skeleton_output_path(output_dir, input_path), ASPECT_RATIO)

    return BatchSpec(load=load, step=step, save=save)


def default_skeleton_batch_size(device_type: str) -> int:
    """VRAM-sized batch on a GPU; at most ``SKELETON_BATCH_SIZE_CPU`` on the CPU fallback."""
    batch_size = config.get_skeleton_batch_size(refresh=True)
    if device_type == "cuda":
        return batch_size
    return min(batch_size, config.SKELETON_BATCH_SIZE_CPU)


def run_skeleton_generation(
    input_dir: str,
    output_dir: str,
    *,
    batch_size: int | None = None,
    model: Any = None,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> BatchReport:
    """Generate ``<output_dir>/<stem>_fake.png`` for every image in ``input_dir``.

    Unreadable images are recorded in ``report.failed`` (the run continues);
    out-of-memory batches are split and retried; ``should_stop`` cancels at the
    next batch boundary. Writes the run manifest and releases the CUDA cache once;
    a run that raises after writing skeletons still replaces the manifest
    (``status`` "failed", R7) and its error then propagates unchanged.

    Args:
        batch_size: default: auto-sized from free VRAM once the model is loaded;
            at most ``config.SKELETON_BATCH_SIZE_CPU`` on CPU, so Cancel is
            honoured within a second or two.
        model: a ready SkeletonModel-like object with ``run_batch`` (tests);
            default: the cached ``runtime.get_skeleton_model()``.
    """
    from app.inference import runtime

    os.makedirs(output_dir, exist_ok=True)
    image_paths = make_dataset(input_dir)
    # (R7) Filled in place by run_batches, so a run that raises still knows what it wrote.
    report = BatchReport()

    def write_manifest() -> None:
        write_run_manifest(
            RUN_KIND,
            input_dir=input_dir,
            output_dir=output_dir,
            input_names=[os.path.basename(p) for p in image_paths],
            report=report,
            models={"skeleton": runtime.skeleton_weights_path()},
            parameters={
                "batch_size": batch_size,
                "load_size": LOAD_SIZE,
                "aspect_ratio": ASPECT_RATIO,
                "output_size_wh": list(config.SKELETON_OUTPUT_SIZE),
            },
        )

    try:
        rt = runtime.get_runtime()
        if model is None and image_paths:
            model = runtime.get_skeleton_model(rt.device)
        if batch_size is None:
            batch_size = default_skeleton_batch_size(rt.device.type)
        logger.info(
            "Skeleton generation: input=%s output=%s images=%d batch_size=%d",
            input_dir,
            output_dir,
            len(image_paths),
            batch_size,
        )
        if image_paths:
            run_batches(
                image_paths,
                make_skeleton_spec(model, output_dir),
                batch_size=batch_size,
                io_threads=rt.io_threads,
                pin_memory=rt.pin_memory,
                progress=progress,
                should_stop=should_stop,
                report=report,
            )
        else:
            logger.warning("No images found in input directory: %s", input_dir)
            report.batch_size = report.effective_batch_size = batch_size
    except BaseException as exc:
        record_failed_run(report, exc, write_manifest)
        raise
    finally:
        runtime.release_cuda_cache()
    write_manifest()
    if report.failed:
        logger.warning(
            "Skeleton generation: %d input(s) failed: %s", len(report.failed), report.failed
        )
    return report


def run_inference(input_dir: str, output_dir: str, progress_callback=None, batch_size=None):
    """
    Run skeleton inference on all images in input_dir (legacy entry point).

    Args:
        input_dir: Directory containing input images
        output_dir: Directory to save output images
        progress_callback: Optional callback function(current, total) for progress updates
        batch_size: Images per batch (default: auto-sized, see run_skeleton_generation)

    Returns:
        Path to the results directory. Use ``run_skeleton_generation`` for the
        per-image failures and the cancel flag.
    """
    run_skeleton_generation(
        input_dir, output_dir, batch_size=batch_size, progress=progress_callback
    )
    return output_dir

"""
Registry of the model weights SPROUTS runs, with an identity check.

Each checkpoint is identified by its SHA-256 (FAIR4RS F1.1: every component
has a distinct identifier), so provenance records and the About dialog can say
exactly which weights produced an output, and a replaced or corrupted file is
noticed. A mismatch is logged as a warning and never stops the app: labs may
legitimately run retrained weights.

The checkpoints are not in git; ``url``/``doi`` stay ``None`` until the weights
are published (see docs/MODELS.md once it exists). Qt-free and torch-free.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Literal

from app import config
from app.provenance import file_sha256

logger = logging.getLogger(__name__)

__all__ = ["MODELS", "ModelSpec", "ModelState", "ModelStatus", "verify", "verify_all"]

ModelState = Literal["ok", "mismatch", "missing"]


_CHECKPOINTS_PREFIX = "checkpoints"  # first relpath segment, resolved via CHECKPOINTS_DIR


@dataclass(frozen=True)
class ModelSpec:
    """A known checkpoint: where it lives (relative to the app folder) and its digest."""

    key: str
    relpath: str  # "/"-separated, relative to the application folder
    sha256: str
    url: str | None = None
    doi: str | None = None
    description: str = ""

    def path(self, root: str | None = None) -> str:
        """Absolute path of the checkpoint under ``root`` (default: the app folder).

        Without ``root``, a ``checkpoints/...`` relpath resolves under
        ``config.CHECKPOINTS_DIR``, so ``SPROUTS_CHECKPOINTS_DIR`` relocates the
        weights (a pip-installed SPROUTS); provenance keeps the relpath.
        """
        parts = self.relpath.split("/")
        if root is None and parts[0] == _CHECKPOINTS_PREFIX:
            return os.path.normpath(os.path.join(config.CHECKPOINTS_DIR, *parts[1:]))
        return os.path.normpath(os.path.join(root or config.PROJECT_ROOT, *parts))


@dataclass(frozen=True)
class ModelStatus:
    """Result of :func:`verify`: ``state`` is ``"ok"``, ``"mismatch"`` or ``"missing"``."""

    state: ModelState
    path: str
    sha256: str | None

    @property
    def ok(self) -> bool:
        return self.state == "ok"


MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            key="mask",
            relpath="checkpoints/mask_weights/best_mask_model_V5.pth",
            sha256="ca1f92d21609f9905bd407bd0562f8456cfff3987d1493c701a5585ee3a3a43f",
            description=(
                "Root mask segmentation, ResNet generator with SE/DualAttention blocks "
                "(app.mask_model); sigmoid output binarised at MASK_THRESHOLD 0.5."
            ),
        ),
        ModelSpec(
            key="skeleton",
            relpath="checkpoints/skeletonizer/latest_net_G.pth",
            sha256="a312586ce086aa913b6823f1c99c064f9189d71f77bd575a6529ee9f6a0d2daa",
            description=(
                "Pix2Pix skeletonizer generator (256x256 RGB input, 341x256 skeleton "
                "output; app.inference.skeleton_inference)."
            ),
        ),
    )
}

_status_cache: dict[tuple[str, str, int, int], ModelStatus] = {}
_reported: set[tuple[str, str, str]] = set()
_lock = threading.Lock()


def verify(spec: ModelSpec, root: str | None = None) -> ModelStatus:
    """Check the checkpoint of ``spec`` against its registered SHA-256.

    Never raises. Cached by (path, size, mtime), so repeated calls are free
    until the file changes; the first call hashes the file (~0.25 s for the
    mask model), so run it off the GUI thread. A mismatching or missing file
    is logged once.
    """
    path = spec.path(root)
    try:
        st = os.stat(path)
    except OSError:
        return _report(spec, ModelStatus("missing", path, None))
    key = (spec.key, path, st.st_size, st.st_mtime_ns)
    cached = _status_cache.get(key)
    if cached is not None:
        return cached
    try:
        digest = file_sha256(path)
    except Exception as exc:  # noqa: BLE001 - verification must never break the app
        logger.warning("Could not hash %s weights %s: %s", spec.key, path, exc)
        digest = None
    if digest is None:
        status = ModelStatus("missing", path, None)
    else:
        status = ModelStatus("ok" if digest == spec.sha256 else "mismatch", path, digest)
    with _lock:
        _status_cache[key] = status
    return _report(spec, status)


def _report(spec: ModelSpec, status: ModelStatus) -> ModelStatus:
    """Log a problem status the first time it is seen for this file and digest."""
    if status.ok:
        return status
    marker = (spec.key, status.path, status.sha256 or status.state)
    with _lock:
        if marker in _reported:
            return status
        _reported.add(marker)
    if status.state == "mismatch":
        logger.warning(
            "%s weights %s do not match the registered checkpoint (sha256 %s, expected %s)",
            spec.key,
            status.path,
            status.sha256,
            spec.sha256,
        )
    else:
        logger.info("%s weights not found at %s", spec.key, status.path)
    return status


def verify_all(root: str | None = None) -> dict[str, ModelStatus]:
    """:func:`verify` every registered model (keyed like :data:`MODELS`)."""
    return {key: verify(spec, root) for key, spec in MODELS.items()}

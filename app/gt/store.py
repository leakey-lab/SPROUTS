"""
Palette-PNG store for ground-truth (GT) tracings (``gt/<stem>.png``).

Each tracing is an 8-bit palette PNG whose pixel value is the class id
(0 = background, 1-254 = registry classes, 255 = Ignore). The palette gives
every image viewer the tracing in class colours for free; a PNG text chunk
(``app.config.GT_META_CHUNK``) records the file's own id-to-key map and which
classes the annotator has reviewed, so a file is self-describing even before
it is matched against the current registry (see
``docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`` §1).

No Qt dependency: this module is pure I/O over Pillow + numpy, read/written
through Python file handles (so non-ASCII Windows paths work), and writes are
atomic (``<path>.tmp`` then ``os.replace``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, PngImagePlugin

from app.config import GT_BACKGROUND_ID, GT_DIRNAME, GT_IGNORE_ID, GT_META_CHUNK, GT_UNKNOWN_COLOR
from app.gt.registry import Registry

logger = logging.getLogger(__name__)

_VALID_MODES = ("P", "L")


class GtFileError(ValueError):
    """Raised for a GT PNG that cannot be read: wrong mode or bad metadata chunk."""


@dataclass
class Tracing:
    """One image's GT: per-pixel class ids plus the annotator's review state.

    ``ids`` is an ``(h, w)`` uint8 array (0 background, 1-254 class ids, 255
    Ignore) at the source image's resolution. ``reviewed`` holds the keys of
    classes checked on this image ("looked for it"), and ``file_classes`` the
    id -> key map the file was written with.
    """

    ids: np.ndarray  # (h, w) uint8
    reviewed: frozenset[str] = frozenset()
    file_classes: dict[int, str] = field(default_factory=dict)  # id -> key from the file


def _hex_to_rgb(color: str) -> list[int]:
    return [int(color[i : i + 2], 16) for i in (1, 3, 5)]


def gt_dir(project_dir: str) -> str:
    """``<project_dir>/gt``: the folder holding the tracings and ``classes.json``."""
    return os.path.join(project_dir, GT_DIRNAME)


def gt_path(image_path: str) -> str:
    """``<project_dir>/gt/<stem>.png`` for an image at ``image_path``."""
    project_dir = os.path.dirname(image_path)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(gt_dir(project_dir), f"{stem}.png")


def empty_tracing(height: int, width: int) -> Tracing:
    """An all-background (id 0) tracing of ``height`` x ``width`` pixels, nothing reviewed."""
    return Tracing(ids=np.zeros((height, width), dtype=np.uint8))


def write_tracing(path: str, tracing: Tracing, registry: Registry) -> None:
    """Atomically write ``tracing`` as an 8-bit palette PNG at ``path``.

    Pixel value = class id; the palette holds each registry class colour (ids
    painted but unknown to ``registry`` get ``GT_UNKNOWN_COLOR``); background
    (0) is the transparent entry. The ``GT_META_CHUNK`` text chunk records the
    id -> key map and the reviewed keys. Raises ``GtFileError`` for non 2-D ids
    and ``OSError`` if the file cannot be written (any old file is kept).
    """
    ids = np.ascontiguousarray(tracing.ids, dtype=np.uint8)
    if ids.ndim != 2:
        raise GtFileError(f"GT ids must be 2-D, got shape {ids.shape}")
    palette = [0, 0, 0] * 256
    unknown = _hex_to_rgb(GT_UNKNOWN_COLOR)
    for i in np.unique(ids).tolist():
        if i != GT_BACKGROUND_ID:
            palette[3 * i : 3 * i + 3] = unknown
    for c in registry.classes:
        palette[3 * c.id : 3 * c.id + 3] = _hex_to_rgb(c.color)
    image = Image.fromarray(ids, mode="P")
    image.putpalette(palette)
    meta = {
        "v": 1,
        "classes": {str(c.id): c.key for c in registry.classes},
        "reviewed": sorted(tracing.reviewed),
    }
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, json.dumps(meta))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            image.save(fh, format="PNG", pnginfo=info, transparency=GT_BACKGROUND_ID)
        os.replace(tmp, path)
    except BaseException:
        # Never leave a half-written .tmp behind; the old file (if any) is intact.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_tracing(path: str) -> Tracing | None:
    """Read a GT PNG. Returns ``None`` if missing; raises ``GtFileError`` if unreadable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            image = Image.open(fh)
            image.load()
            if image.mode not in _VALID_MODES:
                raise GtFileError(f"Unsupported GT PNG mode in {path}: {image.mode}")
            ids = np.array(image, dtype=np.uint8)
            text = getattr(image, "text", {}) or {}
    except GtFileError:
        raise
    except (OSError, ValueError) as exc:
        raise GtFileError(f"Could not read GT file {path}: {exc}") from exc

    raw_meta = text.get(GT_META_CHUNK)
    if raw_meta is None:
        return Tracing(ids=ids, reviewed=frozenset(), file_classes={})

    try:
        meta = json.loads(raw_meta)
        raw_classes = meta.get("classes", {})
        file_classes = {int(k): str(v) for k, v in raw_classes.items()}
        reviewed = frozenset(str(k) for k in meta.get("reviewed", []))
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        raise GtFileError(f"Bad GT metadata chunk in {path}: {exc}") from exc

    return Tracing(ids=ids, reviewed=reviewed, file_classes=file_classes)


def align_to_registry(tracing: Tracing, registry: Registry) -> tuple[Tracing, list[str], bool]:
    """Remap ``tracing.ids`` from the file's id->key map onto ``registry`` ids.

    Per spec §1.4: a file id with a key the registry knows becomes that
    class's registry id; a file id whose key the registry does not know, or
    an id missing from both the file's map and the registry, becomes Ignore
    (255) with a warning naming the key/id; 0 always stays 0.
    """
    warnings: list[str] = []
    lut = np.arange(256, dtype=np.uint8)

    for old_id in np.unique(tracing.ids).tolist():
        if old_id == GT_BACKGROUND_ID:
            continue
        key = tracing.file_classes.get(old_id)
        if key is not None:
            try:
                lut[old_id] = registry.by_key(key).id
            except KeyError:
                lut[old_id] = GT_IGNORE_ID
                warnings.append(f"File id {old_id} has unknown key {key!r}; mapped to Ignore")
        elif registry.by_id(old_id) is not None:
            lut[old_id] = old_id
        else:
            lut[old_id] = GT_IGNORE_ID
            warnings.append(
                f"File id {old_id} has no key mapping and is unknown to the registry; "
                "mapped to Ignore"
            )

    new_ids = lut[tracing.ids]
    changed = not np.array_equal(new_ids, tracing.ids)
    new_tracing = Tracing(
        ids=new_ids.astype(np.uint8),
        reviewed=tracing.reviewed,
        file_classes=tracing.file_classes,
    )
    return new_tracing, warnings, changed


def present_keys(tracing: Tracing, registry: Registry) -> set[str]:
    """Keys of classes that have at least one pixel, Ignore excluded."""
    present_ids = set(np.unique(tracing.ids).tolist())
    return {c.key for c in registry.classes if not c.is_ignore and c.id in present_ids}


def is_complete(tracing: Tracing, registry: Registry) -> bool:
    """True once every active, non-Ignore class has been reviewed."""
    required = {c.key for c in registry.labelled()}
    return required.issubset(tracing.reviewed)

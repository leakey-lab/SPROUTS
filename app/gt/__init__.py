"""
Ground-truth (GT) tracing core package.

Qt-free: the class registry (``registry.py``) and the palette-PNG tracing
store (``store.py``), re-exported here for convenient importing
(``from app.gt import Registry, read_tracing, ...``).
"""

from __future__ import annotations

from app.gt.registry import (
    ClassDef,
    Registry,
    RegistryError,
    default_registry,
    load_registry,
    registry_path,
    save_registry,
    slugify_key,
)
from app.gt.store import (
    GtFileError,
    Tracing,
    align_to_registry,
    empty_tracing,
    gt_dir,
    gt_path,
    is_complete,
    present_keys,
    read_tracing,
    write_tracing,
)

__all__ = [
    "ClassDef",
    "Registry",
    "RegistryError",
    "default_registry",
    "load_registry",
    "registry_path",
    "save_registry",
    "slugify_key",
    "GtFileError",
    "Tracing",
    "align_to_registry",
    "empty_tracing",
    "gt_dir",
    "gt_path",
    "is_complete",
    "present_keys",
    "read_tracing",
    "write_tracing",
]

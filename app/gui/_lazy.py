"""
PEP 562 lazy-import helper for :mod:`app.gui`.

Importing ``app.gui`` happens transitively very early at launch (the package is
pulled in just to reach the entry-point window). The eager re-exports in
``app/gui/__init__.py`` used to drag in the entire heavy dependency stack
(cv2/scipy/skimage/dash/plotly/pandas via the submodules) before the GUI even
needed them, inflating startup time.

This module provides a single ``__getattr__`` (PEP 562, module-level) that
maps each re-exported public name to the submodule that defines it and imports
that submodule lazily, on first attribute access. ``app.gui`` therefore stays
cheap to import; the cost of any given component is paid only when something
actually touches it.

Usage in ``app/gui/__init__.py``::

    from ._lazy import __getattr__, __dir__, __all__  # noqa: F401

``__all__`` is preserved so ``dir(app.gui)`` and tab-completion keep working.
"""

from __future__ import annotations

import importlib
from typing import Any

# name -> submodule (relative to app.gui) that defines it.
# Submodule re-exports (whole modules) map to themselves.
_NAME_TO_MODULE: dict[str, str] = {
    # Main window and core components
    "MainWindow": "main_window",
    "DisplayController": "display_controller",
    "MagnifyingGraphicsView": "display_controller",
    "ImageManager": "image_manager",
    "ImageLoaderThread": "image_manager",
    # The Correct stage's editor (the mask and the skeleton of an image on one canvas)
    "CorrectEditor": "correct.editor",
    # Image normalization
    "NormalizationControls": "image_normalization_interface",
    # Submodules re-exported as modules
    "ui_panels": "ui_panels",
    "file_tree_manager": "file_tree_manager",
    "visualization_manager": "visualization_manager",
}

# Public, discoverable surface (tests/test_gui_public_names.py pins that each resolves).
__all__ = list(_NAME_TO_MODULE.keys())


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name by importing its submodule on first access."""
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__package__!r} has no attribute {name!r}")

    module = importlib.import_module(f"{__package__}.{module_name}")

    # Submodule re-export (e.g. ui_panels): the module object itself is the value.
    if module_name == name:
        return module

    # Symbol re-export: pull the named attribute out of the submodule.
    try:
        return getattr(module, name)
    except AttributeError as exc:  # pragma: no cover - mapping bug guard
        raise AttributeError(f"submodule {module_name!r} does not define {name!r}") from exc


def __dir__() -> list[str]:
    """Expose the re-exported names for dir()/autocomplete without importing."""
    return sorted(__all__)

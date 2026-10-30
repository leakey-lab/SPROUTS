"""
Visualization components: the Dash dashboards and the Qt windows embedding them.

The public names below are re-exported for backward compatibility and resolved
lazily (PEP 562): importing any submodule of this package, or the package
itself, no longer imports dash, plotly and pandas (~1 s). They load when a
dashboard is built, on its worker thread.
"""

from __future__ import annotations

import importlib
from typing import Any

# name -> submodule (relative to this package) that defines it
_EXPORTS: dict[str, str] = {
    # Dash apps
    "DashApp": ".dash_app",
    "DashAppArea": ".dash_app_area",
    "DashVisualizations": ".dash_visualizations",
    "DataCache": ".dash_data_cache",
    "build_available_images_map": ".dash_image_utils",
    "get_encoded_image": ".dash_image_utils",
    # Qt windows
    "DashVizWindow": ".viz_window",
    "RootLengthVisualization": ".root_length_visulization",
    "DashServerThread": ".root_length_visulization",
    "RootAreaVisualization": ".root_area_visualization",
    "DashServerThreadArea": ".root_area_visualization",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import the submodule defining *name* on first access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name, __name__), name)
    globals()[name] = value  # later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})

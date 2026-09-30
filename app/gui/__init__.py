"""
GUI Components Package

This package contains all GUI-related components of SPROUTS.
Key classes are re-exported here for backward compatibility.

Re-exports are resolved lazily via PEP 562 ``__getattr__`` (see ``_lazy.py``) so
that importing ``app.gui`` does not eagerly drag in the heavy dependency stack
(cv2/scipy/skimage/dash/plotly/pandas) at cold start. ``main.py`` imports
``app.gui.main_window`` directly, so that path is unaffected.
"""

# PEP 562 lazy attribute access. ``__all__`` is preserved for dir()/autocomplete
# and ``from app.gui import *`` semantics.
from ._lazy import __all__, __dir__, __getattr__  # noqa: F401

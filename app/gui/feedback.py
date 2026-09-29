"""Compatibility re-export of the ``UiFeedback`` port.

The Qt-free port lives in :mod:`app.feedback` (below both ``app.gui`` and
``app.handlers``); import it from there in new code.
"""

from __future__ import annotations

from app.feedback import *  # noqa: F403  (compat re-export of the neutral port)
from app.feedback import __all__  # noqa: F401

"""Compatibility re-export of the worker error boundary.

``GuardedThread``, ``JobCancelled``, ``describe_error`` and ``is_user_condition``
live in the layer-neutral :mod:`app.workers` (so ``app.inference`` threads need not
import ``app.handlers``); import them from there in new code.
"""

from __future__ import annotations

from app.workers import *  # noqa: F403  (compat re-export of the neutral boundary)
from app.workers import __all__, logger  # noqa: F401

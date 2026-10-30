"""Where the owner's real image data lives, for the tests that use it when present.

The full 2024 field-campaign project (46k photos with masks and skeletons) is not in the
repository. A test that reads it asks :func:`real_data_dir` and skips on ``None``:

1. ``$SPROUTS_REAL_DATA``, when it names a folder;
2. else the owner's default ``D:/2024 EF image data``, when that exists;
3. else ``None``.

The default is the only place the code names the real data's location: it is the owner's
machine, and any other machine sets ``SPROUTS_REAL_DATA``. The three images of ``docs/examples`` are
committed and need none of this (``tests/test_correct_goldens_real.py``).
"""

from __future__ import annotations

import os

__all__ = ["DEFAULT_REAL_DATA", "ENV_VAR", "real_data_dir"]

ENV_VAR = "SPROUTS_REAL_DATA"
DEFAULT_REAL_DATA = "D:/2024 EF image data"


def real_data_dir() -> str | None:
    """The folder of the real data, or ``None`` when this machine has none."""
    chosen = os.environ.get(ENV_VAR)
    if chosen and os.path.isdir(chosen):
        return chosen
    return DEFAULT_REAL_DATA if os.path.isdir(DEFAULT_REAL_DATA) else None

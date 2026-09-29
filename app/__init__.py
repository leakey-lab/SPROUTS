"""
SPROUTS – Root Mask and Skeletons application package.

Single source of the release identity (FAIR4RS F1.2: every version has a
distinct identifier). ``pyproject.toml`` reads ``__version__`` from here, and
the window title, About dialog, logs and provenance records all use these
names. Keep this module import-light: it runs before logging is configured and
before any Qt or torch import.
"""

__version__ = "3.0.0"

#: Short product name (window title, QSettings application name, user dirs).
APP_NAME = "SPROUTS"

#: Long product name for About dialogs, citations and provenance records.
APP_FULL_NAME = "SPROUTS – Root Mask and Skeletons"

#: Organisation used for QSettings (matches the existing QSettings store).
ORGANIZATION_NAME = "LeakeyLab"

__all__ = ["APP_FULL_NAME", "APP_NAME", "ORGANIZATION_NAME", "__version__"]

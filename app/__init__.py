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

#: SPDX id of the code licence (the model weights are CC-BY-4.0, docs/MODELS.md); keep in step
#: with pyproject.toml, CITATION.cff and codemeta.json (tests/test_license_file.py).
LICENSE_SPDX = "GPL-3.0-or-later"

#: Copyright holder named in LICENSE and the README.
COPYRIGHT_HOLDER = "the SPROUTS authors"

__all__ = [
    "APP_FULL_NAME",
    "APP_NAME",
    "COPYRIGHT_HOLDER",
    "LICENSE_SPDX",
    "ORGANIZATION_NAME",
    "__version__",
]

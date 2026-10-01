"""Helpers the parts of the main window share."""

import os

from PyQt6.QtCore import Qt

#: The library tree's item data role that holds an image's stored name.
USER_ROLE = Qt.ItemDataRole.UserRole


def same_path(a: str | None, b: str | None) -> bool:
    """True when both are set and name the same file (case/separator-insensitive on Windows)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def released(editor) -> bool:
    """Ask ``editor`` to save or discard pending edits; False only when it refuses.

    Editors return False when the user cancelled or kept editing after a failed
    save; anything else (True, or ``None`` from a legacy stub) lets the caller move on.
    """
    return editor.maybe_save_changes() is not False

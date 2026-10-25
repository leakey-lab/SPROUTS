"""The unsaved-changes prompts of the canvas editors (Correct and Trace GT), contract C11.

One Save / Discard / Cancel box names what is unsaved ("mask", "skeleton", "mask and
skeleton", "GT"), and a second box follows a failed save (Discard / Keep editing). A third
asks before a save replaces a file the editor could not show (Replace / Keep it), a fourth
before a save overwrites a file that changed on disk since it was opened (Overwrite / Reload
(lose my edits) / Cancel). The wording of the first two is the editors' of before the merge,
word for word. Answers come back as plain strings (:data:`SAVE`, :data:`DISCARD`,
:data:`CANCEL`, :data:`KEEP`, :data:`REPLACE`, :data:`OVERWRITE`, :data:`RELOAD`), so the
session logic that asks them stays Qt-free; Esc or closing the box is the safe answer (Cancel /
Keep editing / Keep it). Each box is freed once answered.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import QMessageBox, QWidget

__all__ = [
    "CANCEL",
    "DISCARD",
    "KEEP",
    "OVERWRITE",
    "RELOAD",
    "REPLACE",
    "SAVE",
    "ask_after_failed_save",
    "ask_changed_on_disk",
    "ask_replace_unloaded",
    "ask_unsaved",
    "join_nouns",
]

SAVE, DISCARD, CANCEL, KEEP, REPLACE = "save", "discard", "cancel", "keep", "replace"
OVERWRITE, RELOAD = "overwrite", "reload"

_Button = QMessageBox.StandardButton


def join_nouns(nouns: Sequence[str]) -> str:
    """``["mask"]`` -> ``"mask"``; ``["mask", "skeleton"]`` -> ``"mask and skeleton"``."""
    nouns = list(nouns)
    if len(nouns) < 2:
        return "".join(nouns)
    return f"{', '.join(nouns[:-1])} and {nouns[-1]}"


def _ask(
    parent: QWidget | None,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    info: str,
    buttons: _Button,
    default: _Button,
    *,
    rename: dict[_Button, str] | None = None,
) -> _Button | None:
    """Modal question; the chosen standard button (None when closed without one)."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(info)
    box.setStandardButtons(buttons)
    box.setDefaultButton(default)
    box.setEscapeButton(_Button.Cancel)
    for button, label in (rename or {}).items():
        box.button(button).setText(label)
    try:
        result = box.exec()
    finally:
        box.deleteLater()  # a parented box would otherwise live as long as its parent
    try:
        return _Button(result)
    except ValueError:
        return None


def ask_unsaved(parent: QWidget | None, noun: str, name: str) -> str:
    """Ask Save / Discard / Cancel about unsaved ``noun`` edits of image ``name``.

    Returns :data:`SAVE`, :data:`DISCARD` or :data:`CANCEL` (also for Esc / close).
    """
    choice = _ask(
        parent,
        QMessageBox.Icon.Question,
        f"Unsaved {noun}",
        f"Save changes to the {noun} for {name}?",
        "Discarded edits cannot be recovered.",
        _Button.Save | _Button.Discard | _Button.Cancel,
        _Button.Save,
    )
    if choice == _Button.Save:
        return SAVE
    if choice == _Button.Discard:
        return DISCARD
    return CANCEL


def ask_after_failed_save(parent: QWidget | None, noun: str, name: str) -> str:
    """After a failed save of ``noun``: Discard the edits, or keep editing.

    Returns :data:`DISCARD` or :data:`KEEP` ("Keep editing", also for Esc / close).
    """
    choice = _ask(
        parent,
        QMessageBox.Icon.Warning,
        f"{noun[:1].upper()}{noun[1:]} not saved",
        f"The {noun} for {name} could not be saved.",
        "Discard the edits, or keep editing? Discarded edits are lost.",
        _Button.Discard | _Button.Cancel,
        _Button.Cancel,
        rename={_Button.Cancel: "Keep editing"},
    )
    return DISCARD if choice == _Button.Discard else KEEP


def ask_replace_unloaded(parent: QWidget | None, noun: str, label: str) -> str:
    """Before a save of ``noun`` replaces ``label``, a file the editor could not show.

    Returns :data:`REPLACE` or :data:`CANCEL` ("Keep it", also the default, Esc and close).
    """
    choice = _ask(
        parent,
        QMessageBox.Icon.Warning,
        f"Replace the saved {noun}?",
        f"{label} exists but could not be shown. Replace it?",
        "Your edits replace the whole file. Keep it leaves the file as it is.",
        _Button.Save | _Button.Cancel,
        _Button.Cancel,
        rename={_Button.Save: "Replace", _Button.Cancel: "Keep it"},
    )
    return REPLACE if choice == _Button.Save else CANCEL


def ask_changed_on_disk(parent: QWidget | None, noun: str, label: str) -> str:
    """Before a save of ``noun`` overwrites ``label``, a file that changed on disk after the
    editor opened (or last saved) it.

    Returns :data:`OVERWRITE`, :data:`RELOAD` ("Reload (lose my edits)") or :data:`CANCEL`
    (also the default, Esc and close).
    """
    choice = _ask(
        parent,
        QMessageBox.Icon.Warning,
        f"{noun[:1].upper()}{noun[1:]} changed on disk",
        f"{label} changed on disk after you opened it.",
        "Overwrite it with your edits, reload it from disk (your edits are lost), or cancel.",
        _Button.Save | _Button.Discard | _Button.Cancel,
        _Button.Cancel,
        rename={_Button.Save: "Overwrite", _Button.Discard: "Reload (lose my edits)"},
    )
    if choice == _Button.Save:
        return OVERWRITE
    if choice == _Button.Discard:
        return RELOAD
    return CANCEL

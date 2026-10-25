"""What the Correct session asks before a layer save, and what a save reports (Qt-free).

:class:`SaveGate` is the session's one hook before each layer save. A layer that loaded
less than its files hold (``DiskGuardedLayer``: a mask of another size than the photo, or
one it could not decode) names the file a save would replace without having shown it; the
gate then asks before that save (Replace / Keep it), and on ``PgDn`` it never asks: the
page stays and says how to replace the file. A user's "no" is no failure: the outcome is
``declined``, so the failed-save question is not asked, and the page still stays.

The prompts' answers, the :class:`Prompts` protocol (the ``app.gui.editor_prompts``
module in the app) and :class:`SaveReport` live here; ``app.gui.correct.session``
re-exports them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.gui.correct.layer import EditLayer, SaveOutcome

__all__ = [
    "CANCEL",
    "DISCARD",
    "KEEP",
    "REPLACE",
    "SAVE",
    "Prompts",
    "SaveGate",
    "SaveReport",
    "unloaded_label_of",
]

# The prompts' answers (the values of the constants in app.gui.editor_prompts).
SAVE, DISCARD, CANCEL, KEEP, REPLACE = "save", "discard", "cancel", "keep", "replace"


class Prompts(Protocol):
    """The session's questions (the ``app.gui.editor_prompts`` module in the app)."""

    def ask_unsaved(self, parent: object, noun: str, name: str) -> str:
        """Save / Discard / Cancel for the unsaved ``noun`` of image ``name``."""

    def ask_after_failed_save(self, parent: object, noun: str, name: str) -> str:
        """Discard / Keep editing after ``noun`` could not be saved."""

    def ask_replace_unloaded(self, parent: object, noun: str, label: str) -> str:
        """Replace / Keep it: a save of ``noun`` would replace ``label``, never shown."""


@dataclass(frozen=True)
class SaveReport:
    """What one save did: one outcome per layer save attempt, mask first."""

    outcomes: tuple[SaveOutcome, ...] = ()

    @property
    def ok(self) -> bool:
        """Every outcome ok (True when nothing was saved; False when one was declined)."""
        return all(outcome.ok for outcome in self.outcomes)

    @property
    def failed(self) -> tuple[str, ...]:
        """Ids of the layers whose save failed (a declined save did not fail)."""
        return tuple(o.layer_id for o in self.outcomes if not o.ok and not o.declined)

    @property
    def declined(self) -> tuple[str, ...]:
        """Ids of the layers whose save the user declined (nothing was written)."""
        return tuple(o.layer_id for o in self.outcomes if o.declined)


def unloaded_label_of(layer: object) -> str:
    """``layer.unloaded_label()`` (``DiskGuardedLayer``), else ``""``."""
    method = getattr(layer, "unloaded_label", None)
    return method() if callable(method) else ""


def _title(noun: str) -> str:
    return f"{noun[:1].upper()}{noun[1:]}"


class SaveGate:
    """Asks before a layer save that would replace a file the layer did not show."""

    def __init__(self, prompts: Prompts, parent: Callable[[], object]) -> None:
        self._prompts = prompts
        self._parent = parent

    def before_save(self, layer_id: str, layer: EditLayer, *, ask: bool) -> SaveOutcome | None:
        """None: go ahead and save ``layer``. Otherwise the declined outcome to report.

        ``ask`` False (``PgDn``) never asks: the save is declined with a warning.
        """
        label = unloaded_label_of(layer)
        if not label:
            return None
        if not ask:
            message = f"{label} could not be shown: save with Ctrl+S to replace it"
            return SaveOutcome(layer_id, ok=False, declined=True, message=message, kind="warn")
        answer = self._prompts.ask_replace_unloaded(self._parent(), layer.noun, label)
        if answer == REPLACE:
            return None
        message = f"{_title(layer.noun)} not saved: {label} was kept"
        return SaveOutcome(layer_id, ok=False, declined=True, message=message, kind="info")

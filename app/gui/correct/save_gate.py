"""What the Correct session asks before a layer save, and what a save reports (Qt-free).

:class:`SaveGate` is the session's one hook before each layer save. A layer that loaded
less than its files hold (``DiskGuardedLayer``: a mask of another size than the photo, or
one it could not decode) names the file a save would replace without having shown it; the
gate then asks before that save (Replace / Keep it), and on ``PgDn`` it never asks: the
page stays and says how to replace the file. A user's "no" is no failure: the outcome is
``declined``, so the failed-save question is not asked, and the page still stays.

The gate also keeps each layer's *write stamps* (the files its save writes or removes,
:func:`write_stamps_of`), taken by the session at open, reload, after each successful save
and after each Discard (:meth:`SaveGate.take`). A save whose files changed on disk since
then (another program, a rewrite) does not overwrite them unasked: files that would open
the layer's saved state again (:func:`matches_disk_of`) only renew the stamps; a layer
without edits is read again (``reopen``) and nothing is written; with edits, ``Ctrl+S``
asks (Overwrite / Reload (lose my edits) / Cancel) and ``PgDn`` stays and says why.

What the stamps cannot see: the check and the write run in one GUI call, microseconds
apart, so a write by another process (the Generate thread is one) landing in that window is
overwritten unasked; one landing between our write and the stamps taken after it is taken
as ours (absorbed: the next save does not ask). A rewrite within the file system's mtime
resolution that keeps the size (2 s on FAT, coarse on some SMB shares) changes no stamp.

The prompts' answers, the :class:`Prompts` protocol (the ``app.gui.editor_prompts``
module in the app) and :class:`SaveReport` live here; ``app.gui.correct.session``
re-exports them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.gui.correct.layer import EditLayer, SaveOutcome, project_label
from app.gui.key_text import platform_keys

__all__ = [
    "CANCEL",
    "DISCARD",
    "KEEP",
    "OVERWRITE",
    "RELOAD",
    "REPLACE",
    "SAVE",
    "Prompts",
    "SaveGate",
    "SaveReport",
    "matches_disk_of",
    "reloaded_message",
    "unloaded_label_of",
    "write_stamps_of",
]

# The prompts' answers (the values of the constants in app.gui.editor_prompts).
SAVE, DISCARD, CANCEL, KEEP, REPLACE = "save", "discard", "cancel", "keep", "replace"
OVERWRITE, RELOAD = "overwrite", "reload"


class Prompts(Protocol):
    """The session's questions (the ``app.gui.editor_prompts`` module in the app)."""

    def ask_unsaved(self, parent: object, noun: str, name: str) -> str:
        """Save / Discard / Cancel for the unsaved ``noun`` of image ``name``."""

    def ask_after_failed_save(self, parent: object, noun: str, name: str) -> str:
        """Discard / Keep editing after ``noun`` could not be saved."""

    def ask_replace_unloaded(self, parent: object, noun: str, label: str) -> str:
        """Replace / Keep it: a save of ``noun`` would replace ``label``, never shown."""

    def ask_changed_on_disk(self, parent: object, noun: str, label: str) -> str:
        """Overwrite / Reload / Cancel: ``label`` of ``noun`` changed on disk since open."""


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


def write_stamps_of(layer: object) -> tuple:
    """``layer.write_stamps()`` (``DiskGuardedLayer``), else ``layer.file_stamps()``."""
    method = getattr(layer, "write_stamps", None)
    return method() if callable(method) else layer.file_stamps()


def matches_disk_of(layer: object) -> bool:
    """``layer.matches_disk()`` (``DiskGuardedLayer``), else False (files never match)."""
    method = getattr(layer, "matches_disk", None)
    return bool(method()) if callable(method) else False


def reloaded_message(noun: str) -> str:
    """The notice of a layer read again because its file changed on disk."""
    return f"{_title(noun)} reloaded: the file changed on disk"


def _title(noun: str) -> str:
    return f"{noun[:1].upper()}{noun[1:]}"


def _declined(layer_id: str, message: str, kind: str) -> SaveOutcome:
    return SaveOutcome(layer_id, ok=False, declined=True, message=message, kind=kind)


def _changed_label(taken: tuple, now: tuple, root: str) -> str:
    """The first file of ``now`` whose stamp is not in ``taken``, named relative to ``root``
    (:func:`~app.gui.correct.layer.project_label`, as the Replace prompt names its file)."""
    path = next((stamp[0] for stamp in now if stamp not in taken), now[0][0] if now else "")
    return project_label(path, root)


class SaveGate:
    """Asks before a layer save that would replace a file the layer did not show, or one
    that changed on disk since the layer's stamps were taken.

    ``reopen(layer_id)`` reads a layer again from disk (the session's, which also takes its
    stamps again); ``root()`` is the folder the prompts' file labels are relative to.
    """

    def __init__(
        self,
        prompts: Prompts,
        parent: Callable[[], object],
        *,
        reopen: Callable[[str], None],
        root: Callable[[], str],
    ) -> None:
        self._prompts = prompts
        self._parent = parent
        self._reopen = reopen
        self._root = root
        self._taken: dict[str, tuple] = {}  # layer id -> its write stamps when last taken

    def take(self, layer_id: str, layer: EditLayer) -> None:
        """``layer``'s files hold its saved state now: remember its write stamps."""
        self._taken[layer_id] = write_stamps_of(layer)

    def forget(self) -> None:
        """Nothing is open: forget every stamp taken."""
        self._taken.clear()

    def before_save(self, layer_id: str, layer: EditLayer, *, ask: bool) -> SaveOutcome | None:
        """None: go ahead and save ``layer``. Otherwise the outcome to report (nothing is
        written; a layer read again from disk reports ok, every other one is declined).

        ``ask`` False (``PgDn``) never asks: the save is declined with a warning.
        """
        label = unloaded_label_of(layer)
        if label:
            if not ask:
                save = platform_keys("save with Ctrl+S")
                message = f"{label} could not be shown: {save} to replace it"
                return _declined(layer_id, message, "warn")
            answer = self._prompts.ask_replace_unloaded(self._parent(), layer.noun, label)
            if answer != REPLACE:
                message = f"{_title(layer.noun)} not saved: {label} was kept"
                return _declined(layer_id, message, "info")
        return self._changed_on_disk(layer_id, layer, ask=ask)

    def _changed_on_disk(self, layer_id: str, layer: EditLayer, *, ask: bool) -> SaveOutcome | None:
        """The decision of :meth:`before_save` about files changed on disk since the write
        stamps were taken (none taken, as for a layer closed then: nothing to compare)."""
        taken, now = self._taken.get(layer_id, ()), write_stamps_of(layer)
        if not taken or now == taken:
            return None
        if matches_disk_of(layer):  # rewritten as it was (same pixels): no change
            self._taken[layer_id] = now
            return None
        if not layer.is_dirty():  # nothing to lose: show the file, write nothing
            self._reopen(layer_id)
            return SaveOutcome(layer_id, ok=True, message=reloaded_message(layer.noun), kind="info")
        label = _changed_label(taken, now, self._root())
        if not ask:
            choose = platform_keys("press Ctrl+S")
            message = f"{label} changed on disk after you opened it: {choose} to choose"
            return _declined(layer_id, message, "warn")
        answer = self._prompts.ask_changed_on_disk(self._parent(), layer.noun, label)
        if answer == OVERWRITE:
            return None
        if answer == RELOAD:
            self._reopen(layer_id)
            return _declined(layer_id, reloaded_message(layer.noun), "info")
        message = f"{_title(layer.noun)} not saved: {label} changed on disk"
        return _declined(layer_id, message, "info")

"""
Chronological order of undoable actions across the layers of one image (Qt-free).

The Correct workspace edits two documents on one canvas, each with its own bounded
undo stack (the mask's patch history, the skeleton's snapshots). ``EditJournal``
records only *which* layer acted, newest last, so Ctrl+Z undoes "what I just did"
on either layer. Entries are tokens: a layer whose own stack already evicted an old
action reports that its stack is empty, and the journal drops the entry for good.

Standard library only.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable


class EditJournal:
    """Chronological order of the undoable actions of one image, across layers.

    Each layer keeps its own undo stack; the journal only records which layer acted,
    newest last, so Ctrl+Z undoes "what I just did" on either layer.
    """

    DEFAULT_MAXLEN = 256

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None:
        if maxlen < 1:
            raise ValueError(f"maxlen must be at least 1, got {maxlen}")
        self._maxlen = int(maxlen)
        self._undo: deque[str] = deque(maxlen=self._maxlen)
        # Only undo() fills it, from the bounded undo list, so it never outgrows maxlen.
        self._redo: list[str] = []

    @property
    def maxlen(self) -> int:
        """Maximum number of undo entries; past it the oldest fall off."""
        return self._maxlen

    def record(self, layer_id: str) -> None:
        """A new action of ``layer_id``: appended newest; the redo list is emptied."""
        self._undo.append(layer_id)
        self._redo.clear()

    def undo(self, step: Callable[[str], bool]) -> str | None:
        """Undo the newest action: ``step(layer_id)`` undoes one entry of that layer.

        True moves the entry to the redo list and returns its layer id. False means
        the layer's own stack is empty (it evicted that action): the entry is dropped
        for good and the next newest is tried. None when nothing is left.
        """
        return self._step(self._undo, self._redo, step)

    def redo(self, step: Callable[[str], bool]) -> str | None:
        """Redo the next undone action; mirrors ``undo`` (False drops the entry)."""
        return self._step(self._redo, self._undo, step)

    def can_undo(self, probe: Callable[[str], bool]) -> bool:
        """True when ``probe(layer_id)`` is True for the layer of some undo entry."""
        return self._any(self._undo, probe)

    def can_redo(self, probe: Callable[[str], bool]) -> bool:
        """True when ``probe(layer_id)`` is True for the layer of some redo entry."""
        return self._any(self._redo, probe)

    def forget(self, layer_id: str) -> None:
        """Drop every undo and redo entry of ``layer_id`` (its history was cleared)."""
        for entries in (self._undo, self._redo):
            if layer_id in entries:
                kept = [entry for entry in entries if entry != layer_id]
                entries.clear()
                entries.extend(kept)

    def clear(self) -> None:
        """Forget every entry (a new image was opened)."""
        self._undo.clear()
        self._redo.clear()

    @property
    def undo_entries(self) -> tuple[str, ...]:
        """Layer ids of the undo entries, oldest first."""
        return tuple(self._undo)

    @property
    def redo_entries(self) -> tuple[str, ...]:
        """Layer ids of the redo entries; the next redo is last."""
        return tuple(self._redo)

    def __len__(self) -> int:
        """Number of undo entries."""
        return len(self._undo)

    @staticmethod
    def _step(source, target, step: Callable[[str], bool]) -> str | None:
        while source:
            layer_id = source.pop()
            if step(layer_id):
                target.append(layer_id)
                return layer_id
        return None

    @staticmethod
    def _any(entries, probe: Callable[[str], bool]) -> bool:
        # The answer depends only on the layer, so each layer is asked at most once.
        asked: set[str] = set()
        for layer_id in reversed(entries):
            if layer_id not in asked:
                if probe(layer_id):
                    return True
                asked.add(layer_id)
        return False

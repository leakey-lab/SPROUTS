"""Document lifecycle of the Correct workspace (Qt-free).

``CorrectSession`` owns what the mask and skeleton layers share: which image is open,
the one undo timeline (``EditJournal``) over the layers' own stacks, the aggregate dirty
state, saving (one summary toast, one report per layer save attempt) and the one
unsaved-changes prompt. It drives the layers only through ``EditLayer`` and reaches
the UI only through the callbacks it is given, so it runs without Qt.

Gestures first: every call that saves, prompts, opens, closes or reloads first ends a
drag in progress (``finish_gestures``), so a stroke it commits is saved or prompted for
like any other edit, and nothing painted is dropped silently.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.editing.journal import EditJournal
from app.gui.correct.layer import LAYER_IDS, EditLayer, Notice, SaveOutcome

# The prompts' answers (the values of the constants in app.gui.editor_prompts).
SAVE, DISCARD, CANCEL, KEEP = "save", "discard", "cancel", "keep"


class Prompts(Protocol):
    """The unsaved-changes questions (the ``app.gui.editor_prompts`` module in the app)."""

    def ask_unsaved(self, parent: object, noun: str, name: str) -> str:
        """Save / Discard / Cancel for the unsaved ``noun`` of image ``name``."""

    def ask_after_failed_save(self, parent: object, noun: str, name: str) -> str:
        """Discard / Keep editing after ``noun`` could not be saved."""


@dataclass(frozen=True)
class SaveReport:
    """What one save did: one outcome per layer save attempt, mask first."""

    outcomes: tuple[SaveOutcome, ...] = ()

    @property
    def ok(self) -> bool:
        """Every outcome ok (True when nothing was saved)."""
        return all(outcome.ok for outcome in self.outcomes)

    @property
    def failed(self) -> tuple[str, ...]:
        """Ids of the layers whose save failed."""
        return tuple(outcome.layer_id for outcome in self.outcomes if not outcome.ok)


def _join_nouns(nouns: Sequence[str]) -> str:
    """``["mask"]`` -> "mask"; ``["mask", "skeleton"]`` -> "mask and skeleton"."""
    if len(nouns) < 2:
        return "".join(nouns)
    return f"{', '.join(nouns[:-1])} and {nouns[-1]}"


def _file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a readable file, ``(path, None)`` otherwise."""
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None)
    return (path, stat.st_mtime_ns, stat.st_size)


def _by_id(layers: Sequence[EditLayer]) -> dict[str, EditLayer]:
    """The layers by id, in ``LAYER_IDS`` order (unknown ids last, as given)."""
    rank = {layer_id: index for index, layer_id in enumerate(LAYER_IDS)}
    by_id: dict[str, EditLayer] = {}
    for layer in sorted(layers, key=lambda layer: rank.get(layer.layer_id, len(rank))):
        if layer.layer_id in by_id:
            raise ValueError(f"two layers share the id {layer.layer_id!r}")
        by_id[layer.layer_id] = layer
    return by_id


class _Batch:
    """Re-entrant scope that defers the session's UI sync to its outermost end."""

    __slots__ = ("_session",)

    def __init__(self, session: CorrectSession) -> None:
        self._session = session

    def __enter__(self) -> None:
        self._session._batch_depth += 1

    def __exit__(self, *_exc) -> None:
        session = self._session
        session._batch_depth -= 1
        if not session._batch_depth:
            session._sync()


class CorrectSession:
    """Document lifecycle of the Correct workspace: open/close, the one undo timeline,
    dirty aggregation, save and the one unsaved-changes prompt."""

    def __init__(
        self,
        layers: Sequence[EditLayer],
        *,
        prompts: Prompts,
        parent: Callable[[], object],
        on_dirty_changed: Callable[[bool], None],
        on_notice: Callable[[str, str], None],
        on_history_changed: Callable[[], None],
        on_undone: Callable[[str, str], None],
        on_saved: Callable[[SaveOutcome], None],
    ) -> None:
        self.layers: dict[str, EditLayer] = _by_id(layers)
        self.journal = EditJournal()
        self.image_path = ""
        self._prompts = prompts
        self._parent = parent
        self._on_dirty_changed = on_dirty_changed
        self._on_notice = on_notice
        self._on_history_changed = on_history_changed
        self._on_undone = on_undone
        self._on_saved = on_saved
        self._size: tuple[int, int] = (0, 0)
        self._images_base_folder: str | None = None
        self._stamps: dict[str, tuple] = {}
        self._image_stamp: tuple = ()
        self._dirty = False
        self._batch_depth = 0
        self._batch = _Batch(self)

    # ------------------------------------------------------------------ lifecycle
    def open(self, image_path: str, size: tuple[int, int], images_base_folder: str | None) -> None:
        """Open ``image_path`` (``size`` = (w, h)) in every layer, with a new timeline.

        The caller asks about unsaved edits first (``release``). The layers' notices are
        forwarded; the image's and the layers' file stamps are recorded.
        """
        with self._batch:
            self.finish_gestures()
            self.image_path = image_path
            self._size = size
            self._images_base_folder = images_base_folder
            self._image_stamp = _file_stamp(image_path)
            self._open_layers()

    def reload_documents(self) -> None:
        """Same image, files changed on disk: reopen every layer, with a new timeline.

        The image's stamp is kept: the photo is not reloaded here.
        """
        if not self.image_path:
            return
        with self._batch:
            self.finish_gestures()
            self._open_layers()

    def close(self) -> None:
        """Close every layer and forget the image and the timeline (edits are dropped)."""
        with self._batch:
            self.finish_gestures()
            for layer in self.layers.values():
                layer.close()
            self.journal.clear()
            self.image_path = ""
            self._stamps = {}
            self._image_stamp = ()

    def files_changed(self) -> bool:
        """True when any layer's ``file_stamps()`` differ from those at open (or reload)."""
        if not self.image_path:
            return False
        return any(
            layer.file_stamps() != self._stamps.get(layer_id)
            for layer_id, layer in self.layers.items()
        )

    def image_changed(self) -> bool:
        """True when the image file's stamp differs from the one at open."""
        return bool(self.image_path) and _file_stamp(self.image_path) != self._image_stamp

    def _open_layers(self) -> None:
        for layer in self.layers.values():
            self._forward(layer.open(self.image_path, self._size, self._images_base_folder))
        self.journal.clear()
        self._stamps = {layer_id: layer.file_stamps() for layer_id, layer in self.layers.items()}

    # ------------------------------------------------------------------- gestures
    def finish_gestures(self) -> None:
        """End every drag in progress as a release there would; other layers are left alone."""
        for layer in self.layers.values():
            if layer.gesture_active():
                layer.finish_gesture()

    def _gesture_in_progress(self) -> bool:
        return any(layer.gesture_active() for layer in self.layers.values())

    # --------------------------------------------------------------- layer events
    def layer_edited(self, layer_id: str) -> None:
        """A layer recorded a new undoable action: journal it; every other redo branch ends."""
        with self._batch:
            self.journal.record(layer_id)
            for other_id, layer in self.layers.items():
                if other_id != layer_id:
                    layer.drop_redo()

    def layer_changed(self, layer_id: str) -> None:
        """Something ``layer_id`` shows changed: refresh the dirty state and the history."""
        self._sync()

    def forget(self, layer_id: str) -> None:
        """Drop ``layer_id``'s timeline entries (its own history was cleared)."""
        with self._batch:
            self.journal.forget(layer_id)

    # ---------------------------------------------------------------------- dirty
    def is_dirty(self) -> bool:
        """True when any layer differs from its saved state."""
        return any(layer.is_dirty() for layer in self.layers.values())

    def dirty_layers(self) -> list[str]:
        """Ids of the dirty layers, mask first."""
        return [layer_id for layer_id, layer in self.layers.items() if layer.is_dirty()]

    def _sync(self) -> None:
        if self._batch_depth:
            return  # the outermost batch syncs once when it ends
        dirty = self.is_dirty()
        if dirty != self._dirty:
            self._dirty = dirty
            self._on_dirty_changed(dirty)
        self._on_history_changed()

    # ----------------------------------------------------------------------- save
    def save(self, active_id: str) -> SaveReport:
        """Ctrl+S: save the dirty layers, mask first; with none dirty, the active layer.

        A clean inactive layer is never rewritten. A gesture in progress is committed
        first, so its stroke is saved too.
        """
        if active_id not in self.layers:
            raise KeyError(active_id)
        if not self.image_path:
            return SaveReport()
        with self._batch:
            self.finish_gestures()
            return self._save(self.dirty_layers() or [active_id])

    def save_dirty(self) -> SaveReport:
        """PgDn / PgUp: save the dirty layers only; an empty report when none is."""
        with self._batch:
            self.finish_gestures()
            dirty = self.dirty_layers()
            return self._save(dirty) if dirty else SaveReport()

    def _save(self, layer_ids: Iterable[str]) -> SaveReport:
        """Save ``layer_ids`` mask first; report each attempt; a failure stops nothing."""
        targets = set(layer_ids)
        outcomes = []
        for layer_id, layer in self.layers.items():
            if layer_id in targets:
                layer.finish_gesture()
                outcome = layer.save()
                outcomes.append(outcome)
                self._on_saved(outcome)
        self._announce(outcomes)
        return SaveReport(tuple(outcomes))

    def _announce(self, outcomes: Sequence[SaveOutcome]) -> None:
        """One summary toast for the written layers, then every other message."""
        name = os.path.basename(self.image_path)
        written = [self.layers[o.layer_id].noun for o in outcomes if o.ok and o.path]
        if written:
            self._on_notice(f"Saved {_join_nouns(written)} · {name}", "success")
        for outcome in outcomes:
            if outcome.ok and outcome.message:
                self._on_notice(outcome.message, outcome.kind)
        for outcome in outcomes:
            if not outcome.ok:
                noun = self.layers[outcome.layer_id].noun
                message = outcome.message or f"Could not save the {noun} for {name}"
                self._on_notice(message, "danger" if outcome.kind == "success" else outcome.kind)

    # --------------------------------------------------------------------- prompt
    def release(self, layer_ids: Sequence[str] | None = None) -> bool:
        """The one unsaved-changes prompt; True when it is safe to move on.

        Asks about the dirty layers among ``layer_ids`` (all when None), naming them and
        the image. Discard reverts them and forgets their timeline entries; Cancel
        stays; Save saves them (each attempt reported) and, if one fails, asks about the
        failed layers only: Discard reverts them, Keep editing stays.
        """
        with self._batch:
            self.finish_gestures()
            dirty = [i for i in self.dirty_layers() if layer_ids is None or i in layer_ids]
            if not dirty:
                return True
            name = os.path.basename(self.image_path)
            answer = self._prompts.ask_unsaved(self._parent(), self._nouns(dirty), name)
            if answer == DISCARD:
                self._revert(dirty)
                return True
            if answer != SAVE:
                return False
            failed = self._save(dirty).failed
            if not failed:
                return True
            answer = self._prompts.ask_after_failed_save(self._parent(), self._nouns(failed), name)
            if answer == DISCARD:
                self._revert(failed)
                return True
            return False

    def reset(self) -> bool:
        """A new folder: the prompt, then close. False (nothing changed) when the user stays."""
        if not self.release():
            return False
        self.close()
        return True

    def _nouns(self, layer_ids: Sequence[str]) -> str:
        return _join_nouns([self.layers[layer_id].noun for layer_id in layer_ids])

    def _revert(self, layer_ids: Sequence[str]) -> None:
        for layer_id in layer_ids:
            self._forward(self.layers[layer_id].revert())
            self.journal.forget(layer_id)

    def _forward(self, notices: Iterable[Notice]) -> None:
        for notice in notices:
            self._on_notice(notice.message, notice.kind)

    # --------------------------------------------------------------- undo / redo
    def undo(self, active_id: str) -> str | None:
        """Ctrl+Z: the id of the layer that stepped back, or None.

        Refused while a drag is in progress. A polyline draft of the active layer loses
        its last point first (not a timeline step). Otherwise the newest action of
        either layer is undone; actions a layer already evicted are skipped.
        """
        if self._gesture_in_progress():
            return None
        with self._batch:
            if self.layers[active_id].undo_in_gesture():
                return active_id
            layer_id = self.journal.undo(self._undo_step)
            if layer_id is not None:
                self._on_undone(layer_id, "undo")
            return layer_id

    def redo(self) -> str | None:
        """Ctrl+Y: the id of the layer that stepped forward, or None (refused mid-drag)."""
        if self._gesture_in_progress():
            return None
        with self._batch:
            layer_id = self.journal.redo(self._redo_step)
            if layer_id is not None:
                self._on_undone(layer_id, "redo")
            return layer_id

    def can_undo(self) -> bool:
        """True when the timeline holds an action its layer can still undo."""
        return self.journal.can_undo(self._can_undo)

    def can_redo(self) -> bool:
        """True when the timeline holds an undone action its layer can redo."""
        return self.journal.can_redo(self._can_redo)

    def _undo_step(self, layer_id: str) -> bool:
        return self.layers[layer_id].undo()

    def _redo_step(self, layer_id: str) -> bool:
        return self.layers[layer_id].redo()

    def _can_undo(self, layer_id: str) -> bool:
        return self.layers[layer_id].can_undo()

    def _can_redo(self, layer_id: str) -> bool:
        return self.layers[layer_id].can_redo()

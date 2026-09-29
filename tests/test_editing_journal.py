"""EditJournal: the order of undoable actions across the Correct layers (C3).

Standard library only: each layer keeps its own undo stack; the journal records which
layer acted, so Ctrl+Z undoes the newest action of either layer.
"""

from __future__ import annotations

import pytest

from app.editing.journal import EditJournal


class _Stacks:
    """Per-layer undo/redo counters standing in for the layers' own histories."""

    def __init__(self, **undo_depths: int) -> None:
        self.undo_depth = dict(undo_depths)
        self.redo_depth = dict.fromkeys(undo_depths, 0)
        self.calls: list[tuple[str, str]] = []

    def undo(self, layer_id: str) -> bool:
        self.calls.append(("undo", layer_id))
        if self.undo_depth[layer_id] == 0:
            return False
        self.undo_depth[layer_id] -= 1
        self.redo_depth[layer_id] += 1
        return True

    def redo(self, layer_id: str) -> bool:
        self.calls.append(("redo", layer_id))
        if self.redo_depth[layer_id] == 0:
            return False
        self.redo_depth[layer_id] -= 1
        self.undo_depth[layer_id] += 1
        return True


def _journal(*layer_ids: str, maxlen: int = EditJournal.DEFAULT_MAXLEN) -> EditJournal:
    journal = EditJournal(maxlen)
    for layer_id in layer_ids:
        journal.record(layer_id)
    return journal


def test_record_keeps_the_order_of_actions_across_layers():
    journal = _journal("mask", "skeleton", "mask")

    assert journal.undo_entries == ("mask", "skeleton", "mask")
    assert journal.redo_entries == ()
    assert len(journal) == 3


def test_undo_steps_the_newest_action_and_moves_it_to_the_redo_list():
    journal = _journal("mask", "skeleton")
    stacks = _Stacks(mask=1, skeleton=1)

    assert journal.undo(stacks.undo) == "skeleton"
    assert stacks.calls == [("undo", "skeleton")]
    assert journal.undo_entries == ("mask",)
    assert journal.redo_entries == ("skeleton",)

    assert journal.undo(stacks.undo) == "mask"
    assert journal.undo_entries == ()
    assert journal.redo_entries == ("skeleton", "mask")  # the next redo is last
    assert len(journal) == 0


def test_redo_mirrors_undo_in_reverse_order():
    journal = _journal("mask", "skeleton")
    stacks = _Stacks(mask=1, skeleton=1)
    journal.undo(stacks.undo)
    journal.undo(stacks.undo)
    stacks.calls.clear()

    assert journal.redo(stacks.redo) == "mask"
    assert journal.redo(stacks.redo) == "skeleton"
    assert journal.redo(stacks.redo) is None

    assert stacks.calls == [("redo", "mask"), ("redo", "skeleton")]
    assert journal.undo_entries == ("mask", "skeleton")
    assert journal.redo_entries == ()


def test_nothing_to_undo_or_redo_returns_none_without_calling_the_layers():
    journal = EditJournal()
    stacks = _Stacks(mask=3)

    assert journal.undo(stacks.undo) is None
    assert journal.redo(stacks.redo) is None
    assert stacks.calls == []


def test_a_new_action_empties_the_redo_list():
    journal = _journal("mask", "skeleton")
    stacks = _Stacks(mask=1, skeleton=1)
    journal.undo(stacks.undo)

    journal.record("mask")

    assert journal.redo_entries == ()
    assert journal.undo_entries == ("mask", "mask")
    assert journal.redo(stacks.redo) is None


def test_an_evicted_action_is_dropped_and_the_next_newest_is_tried():
    # The skeleton's bounded history kept only one of its two actions.
    journal = _journal("skeleton", "mask", "skeleton", "skeleton")
    stacks = _Stacks(skeleton=1, mask=1)

    assert journal.undo(stacks.undo) == "skeleton"  # newest: still held
    assert journal.undo(stacks.undo) == "mask"  # the second skeleton entry was evicted

    assert stacks.calls == [("undo", "skeleton"), ("undo", "skeleton"), ("undo", "mask")]
    assert journal.undo_entries == ("skeleton",)
    assert journal.redo_entries == ("skeleton", "mask")  # the evicted entry is gone for good

    assert journal.undo(stacks.undo) is None  # the oldest skeleton entry is stale too
    assert journal.undo_entries == ()


def test_redo_drops_entries_whose_layer_cannot_redo():
    journal = _journal("mask", "skeleton")
    stacks = _Stacks(mask=1, skeleton=1)
    journal.undo(stacks.undo)
    journal.undo(stacks.undo)
    stacks.redo_depth["mask"] = 0  # the mask lost its redo branch

    assert journal.redo(stacks.redo) == "skeleton"
    assert journal.redo_entries == ()
    assert journal.undo_entries == ("skeleton",)


def test_can_undo_and_can_redo_ask_the_layers_of_the_entries():
    journal = _journal("mask", "skeleton")
    able = {"mask": False, "skeleton": False}
    probed = []

    def probe(layer_id: str) -> bool:
        probed.append(layer_id)
        return able[layer_id]

    assert not journal.can_undo(probe)
    assert set(probed) == {"mask", "skeleton"}
    able["mask"] = True
    assert journal.can_undo(probe)
    assert not journal.can_redo(probe)  # no redo entries: nothing to ask

    stacks = _Stacks(mask=1, skeleton=1)
    journal.undo(stacks.undo)  # skeleton -> redo list
    assert journal.can_redo(lambda layer_id: layer_id == "skeleton")
    assert not journal.can_redo(lambda layer_id: layer_id == "mask")


def test_can_undo_asks_each_layer_at_most_once():
    journal = _journal(*(["mask", "skeleton"] * 50))
    probed = []

    assert not journal.can_undo(lambda layer_id: probed.append(layer_id) or False)
    assert sorted(probed) == ["mask", "skeleton"]


def test_forget_drops_only_that_layers_entries_in_both_lists():
    journal = _journal("mask", "skeleton", "mask", "skeleton", "mask")
    stacks = _Stacks(mask=3, skeleton=2)
    journal.undo(stacks.undo)  # mask
    journal.undo(stacks.undo)  # skeleton

    journal.forget("skeleton")

    assert journal.undo_entries == ("mask", "mask")
    assert journal.redo_entries == ("mask",)


def test_clear_forgets_everything():
    journal = _journal("mask", "skeleton")
    journal.undo(_Stacks(mask=1, skeleton=1).undo)

    journal.clear()

    assert journal.undo_entries == () and journal.redo_entries == ()
    assert len(journal) == 0


def test_the_oldest_entries_fall_off_past_maxlen():
    journal = _journal("a", "b", "c", "d", maxlen=3)

    assert journal.undo_entries == ("b", "c", "d")
    assert len(journal) == 3


def test_the_default_journal_holds_256_entries():
    journal = _journal(*(["mask"] * 300))

    assert len(journal) == 256


@pytest.mark.parametrize("maxlen", [0, -1])
def test_a_journal_needs_room_for_one_entry(maxlen):
    with pytest.raises(ValueError):
        EditJournal(maxlen)


def test_the_editing_package_exports_the_journal():
    import app.editing

    assert app.editing.EditJournal is EditJournal
    assert "EditJournal" in app.editing.__all__

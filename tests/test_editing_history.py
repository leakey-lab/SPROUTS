"""SnapshotHistory: the Qt-free undo/redo stacks shared by the editors (AR-11)."""

from __future__ import annotations

import numpy as np
import pytest

from app.editing.history import SnapshotHistory


def _blob(nbytes: int) -> np.ndarray:
    return np.zeros(nbytes, dtype=np.uint8)


def test_undo_walks_back_through_pushed_states_and_redo_replays_them():
    history = SnapshotHistory(maxlen=10)
    history.push("a")  # a -> b
    history.push("b")  # b -> c (current)

    assert history.undo("c") == "b"
    assert history.undo("b") == "a"
    assert history.undo("a") is None  # nothing older: the caller keeps "a"
    assert history.redo("a") == "b"
    assert history.redo("b") == "c"
    assert history.redo("c") is None


def test_pushing_after_an_undo_discards_the_redo_branch():
    history = SnapshotHistory()
    history.push("a")
    assert history.undo("b") == "a"
    assert history.can_redo

    history.push("a")  # a new edit from "a"

    assert not history.can_redo
    assert history.redo("x") is None


def test_can_undo_and_can_redo_follow_the_stacks():
    history = SnapshotHistory()
    assert (history.can_undo, history.can_redo) == (False, False)
    history.push("a")
    assert (history.can_undo, history.can_redo) == (True, False)
    history.undo("b")
    assert (history.can_undo, history.can_redo) == (False, True)
    history.redo("a")
    assert (history.can_undo, history.can_redo) == (True, False)


def test_maxlen_drops_the_oldest_snapshot():
    history = SnapshotHistory(maxlen=3)
    for state in "abcde":
        history.push(state)
    assert list(history.undo_stack) == ["c", "d", "e"]
    assert history.maxlen == 3


def test_clear_forgets_both_directions():
    history = SnapshotHistory()
    history.push("a")
    history.push("b")
    history.undo("c")
    history.clear()
    assert (history.can_undo, history.can_redo) == (False, False)
    assert list(history.undo_stack) == [] and list(history.redo_stack) == []


def test_map_rewrites_every_snapshot_and_keeps_the_order():
    history = SnapshotHistory()
    history.push(1)
    history.push(2)
    history.push(3)
    assert history.undo(4) == 3  # undo: [1, 2], redo: [4]

    history.map(lambda s: s * 10)

    assert list(history.undo_stack) == [10, 20]
    assert list(history.redo_stack) == [40]
    assert history.undo(30) == 20
    assert history.redo(20) == 30
    assert history.redo(30) == 40


def test_map_keeps_the_stack_objects_callers_hold():
    history = SnapshotHistory(maxlen=2)
    history.push(1)
    held = history.undo_stack

    history.map(lambda s: s + 1)

    assert list(held) == [2]
    history.push(5)
    history.push(6)  # the held deque is still the bounded live stack
    assert list(held) == [5, 6]


def test_on_change_fires_only_when_the_history_changes():
    calls = []
    history = SnapshotHistory(on_change=lambda: calls.append(1))

    history.push("a")
    assert len(calls) == 1
    history.undo("b")
    assert len(calls) == 2
    history.undo("a")  # nothing left to undo
    assert len(calls) == 2
    history.redo("a")
    assert len(calls) == 3
    history.redo("b")  # nothing left to redo
    assert len(calls) == 3
    history.clear()
    assert len(calls) == 4
    history.clear()  # already empty
    history.map(str)  # nothing to rewrite
    assert len(calls) == 4


def test_on_change_can_be_attached_after_construction():
    history = SnapshotHistory()
    calls = []
    history.on_change = lambda: calls.append(1)
    history.push("a")
    assert calls == [1]


def test_byte_budget_evicts_the_oldest_snapshots_first():
    a, b, c = _blob(100), _blob(100), _blob(100)
    history = SnapshotHistory(maxlen=10, byte_budget=250)
    history.push(a)
    history.push(b)
    assert history.nbytes == 200

    history.push(c)  # 300 > 250: drop a

    assert len(history.undo_stack) == 2
    assert history.undo_stack[0] is b and history.undo_stack[1] is c
    assert history.nbytes == 200


def test_byte_budget_always_keeps_one_undo_step():
    big = _blob(100)
    history = SnapshotHistory(byte_budget=50)
    history.push(big)
    assert history.nbytes == 100  # over budget, but the last step survives
    assert history.undo(_blob(1)) is big


def test_byte_budget_trims_the_farthest_redo_states_but_keeps_the_next_one():
    a, b, d = _blob(60), _blob(60), _blob(100)
    history = SnapshotHistory(maxlen=10, byte_budget=150)
    history.push(a)
    history.push(b)

    assert history.undo(d) is b  # undo [a], redo [d]: 160, nothing trimmable
    assert history.undo(b) is a  # undo [], redo [d, b]: 160 > 150, drop d

    assert len(history.redo_stack) == 1 and history.redo_stack[0] is b
    assert history.redo(a) is b
    assert history.redo(b) is None


def test_custom_sizeof_measures_non_array_snapshots():
    history = SnapshotHistory(byte_budget=5, sizeof=len)
    history.push("abc")
    history.push("de")
    history.push("f")  # 6 > 5: drop "abc"
    assert list(history.undo_stack) == ["de", "f"]
    assert history.nbytes == 3


def test_byte_budget_without_sizeof_requires_nbytes():
    history = SnapshotHistory(byte_budget=10)
    with pytest.raises(TypeError, match="sizeof"):
        history.push("not an array")


@pytest.mark.parametrize("kwargs", [{"maxlen": 0}, {"byte_budget": 0}, {"byte_budget": -1}])
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SnapshotHistory(**kwargs)

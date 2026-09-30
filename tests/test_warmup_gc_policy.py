"""Full garbage collections are held during a warm-up import, and the heap is frozen after it
(W3-WARM W3, :mod:`app.gc_policy`).

The trade-off is pinned too: a freeze collects the young generations first, so fresh
garbage is not frozen, but garbage that already reached the oldest generation is kept for
good unless the freeze is ``full`` (what the start-up freeze does).
"""

from __future__ import annotations

import gc
import logging
import threading
import weakref

import pytest

from app import gc_policy


@pytest.fixture(autouse=True)
def unfrozen():
    """Every test leaves the thresholds as it found them, and the heap frozen as it found it
    (tests/conftest.py freezes it between test files). A frozen object that dies lowers the
    count, so only a count that grew (this test froze) or fell to 0 (it unfroze) is undone."""
    thresholds = gc.get_threshold()
    frozen = gc.get_freeze_count()
    yield
    while gc_policy.full_collections_held():
        gc_policy.release_full_collections()
    gc.set_threshold(*thresholds)
    count = gc.get_freeze_count()
    if count > frozen or (frozen and not count):
        gc.unfreeze()
        gc.collect()
        if frozen:
            gc.freeze()


class Node:
    """A reference-cycle member (weakly referenceable)."""

    def __init__(self) -> None:
        self.other = self


def test_a_hold_stops_automatic_full_collections_until_released():
    young, middle, oldest = gc.get_threshold()
    gc_policy.hold_full_collections()
    assert gc_policy.full_collections_held()
    assert gc.get_threshold()[:2] == (young, middle)  # the young ones still run
    assert gc.get_threshold()[2] > 1_000_000
    gc_policy.release_full_collections()
    assert gc.get_threshold() == (young, middle, oldest)
    assert not gc_policy.full_collections_held()


def test_holds_nest_and_a_stray_release_is_harmless():
    before = gc.get_threshold()
    gc_policy.release_full_collections()  # nothing held: no change
    assert gc.get_threshold() == before
    gc_policy.hold_full_collections()
    gc_policy.hold_full_collections()
    gc_policy.release_full_collections()
    assert gc_policy.full_collections_held()  # one hold is left
    gc_policy.release_full_collections()
    assert gc.get_threshold() == before


def test_a_hold_taken_on_a_worker_is_released_on_another_thread():
    before = gc.get_threshold()
    worker = threading.Thread(target=gc_policy.hold_full_collections)
    worker.start()
    worker.join()
    assert gc_policy.full_collections_held()
    gc_policy.release_full_collections()
    assert gc.get_threshold() == before


def test_freeze_moves_the_live_objects_out_of_later_collections(caplog):
    alive = Node()
    before = gc.get_freeze_count()
    with caplog.at_level(logging.INFO, logger="app.gc_policy"):
        frozen = gc_policy.freeze("a test")
    assert frozen > 0 and gc.get_freeze_count() > before  # an object frozen here may die since
    assert "after a test" in caplog.text
    ref = weakref.ref(alive)
    del alive
    gc.collect()
    assert ref() is not None  # frozen: the collector never looks at it again
    gc.unfreeze()
    gc.collect()
    assert ref() is None


def test_freeze_collects_fresh_garbage_first():
    young = Node()
    ref = weakref.ref(young)
    del young  # a young unreachable cycle
    gc_policy.freeze("a test")
    assert ref() is None  # collected, not frozen


def test_old_garbage_is_frozen_unless_the_freeze_is_full():
    def old_garbage():
        node = Node()
        gc.collect()  # survives twice: now in the oldest generation
        gc.collect()
        return weakref.ref(node)

    kept = old_garbage()
    gc_policy.freeze("a young-only freeze")
    assert kept() is not None  # the trade-off: kept for the rest of the session
    gc.unfreeze()
    gc.collect()
    assert kept() is None

    gone = old_garbage()
    gc_policy.freeze("a full freeze", full=True)
    assert gone() is None

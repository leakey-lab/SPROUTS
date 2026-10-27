"""A finished generation thread is freed as soon as its handler lets it go, without the cyclic GC.

The mask and skeleton handlers connected the thread's own signals to ``partial(slot, thread)``
and the success chain of Generate to a closure holding the thread: the thread's slot proxies
held the thread, a reference cycle that only the cyclic garbage collector breaks, and never
once ``gc.freeze()`` (``app/gc_policy.py``) has frozen it (C3-05). A frozen run kept its
report, about 5 MB at 46k images, for the rest of the session, as the loader thread of
``ImageManager`` did (``tests/test_library_loader_freed.py``). The slots now hold a weak
reference (``generation_base.thread_slot``); the handler still holds the thread until the next
run replaces it. These tests turn the automatic GC off, so only reference counting frees (as
for frozen objects), and watch the thread through a weak reference. ``RecordingFeedback`` keeps
every task's ``on_cancel`` (``thread.cancel``), so they use ``NullFeedback``.
"""

from __future__ import annotations

import gc
import weakref

import pytest

from app.feedback import NullFeedback
from app.handlers import generate_skeleton_handler as gsh
from app.handlers import generation_base
from app.handlers import mask_generation_handler as mgh
from app.inference.mask_origin import OverwritePolicy
from app.workers import GuardedThread
from tests.test_generation_handlers import _wait, project, qapp  # noqa: F401


@pytest.fixture
def no_gc():
    """The automatic cyclic GC off for the test: only reference counting frees."""
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    yield
    if enabled:
        gc.enable()


def _collect_the_first_imports() -> None:
    """The first run of a process imports torch and torchvision, which leaves reference
    cycles holding the importing thread's frames (and so the thread) until a collection.
    The handler still holds the thread here, so the collection cannot free it."""
    gc.collect()


def test_a_finished_mask_run_frees_its_thread_once_the_next_run_starts(
    project,  # noqa: F811
    qapp,  # noqa: F811
    no_gc,
):
    project.window.feedback = NullFeedback()
    handler = mgh.MaskGenerationHandler(
        project.window, confirm_overwrite=lambda e, t: OverwritePolicy.OVERWRITE_ALL
    )
    chained = []
    assert handler.generate_masks(on_success=lambda: chained.append(True))  # Generate's chain
    first = weakref.ref(handler.generation_thread)
    _wait(first(), qapp)
    assert chained == [True]  # its outcome and the chain were delivered
    _collect_the_first_imports()
    assert first() is not None  # the handler keeps it until the next run

    assert handler.generate_masks()
    _wait(handler.generation_thread, qapp)

    assert first() is None, "the first mask run's thread is still alive"
    assert handler.generation_thread.completed  # the run that replaced it is kept


def test_a_finished_skeleton_run_frees_its_thread_once_the_next_run_starts(
    project,  # noqa: F811
    qapp,  # noqa: F811
    no_gc,
):
    project.window.feedback = NullFeedback()
    handler = gsh.GenerateSkeletonHandler(project.window)
    assert handler.generate_skeleton()
    first = weakref.ref(handler._thread)
    _wait(first(), qapp)
    assert len(project.window.loaded) == 1  # its outcome was delivered
    _collect_the_first_imports()
    assert first() is not None

    assert handler.generate_skeleton()
    _wait(handler._thread, qapp)

    assert first() is None, "the first skeleton run's thread is still alive"
    assert len(project.window.loaded) == 2


def test_a_thread_slot_calls_with_its_thread_and_holds_no_reference_to_it(qapp):  # noqa: F811
    calls = []
    thread = GuardedThread()
    ref = weakref.ref(thread)
    slot = generation_base.thread_slot(lambda t, *args: calls.append((t is ref(), args)), thread)

    slot("out", [])
    assert calls == [(True, ("out", []))]

    del thread
    assert ref() is None  # the slot kept no reference to the thread
    slot("late")
    assert calls == [(True, ("out", []))]  # the thread is gone: nothing is called

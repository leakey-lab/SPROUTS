"""The tests' leak census: a long-lived object is freed by reference counting alone.

``gc.freeze()`` (``app/gc_policy.py``) takes the objects alive at a warm-up out of the
cyclic collector's sight for good, so an object caught in a reference cycle then stays for
the rest of the session (C3-05 generation threads, the loader thread, the canvas items, the
measurement index). A test of such an object turns the automatic collector off
(:func:`no_gc`) and watches it through a weak reference (:func:`assert_freed`).

The fixture ``no_gc`` is defined here as ``no_gc_fixture``: a test module takes it with
``from tests.leak_census import no_gc_fixture  # noqa: F401`` and its tests name ``no_gc``.
"""

from __future__ import annotations

import gc
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

__all__ = ["assert_freed", "no_gc_fixture", "refcount_only"]


@contextmanager
def refcount_only() -> Iterator[None]:
    """The automatic cyclic collector off while it lasts (one collection first): only
    reference counting frees an object."""
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


@pytest.fixture(name="no_gc")
def no_gc_fixture() -> Iterator[None]:
    """``no_gc``: the automatic cyclic collector off for the test (:func:`refcount_only`)."""
    with refcount_only():
        yield


def assert_freed(
    ref: weakref.ref, what: str = "the object", *, pump: bool = False, timeout: float = 5.0
) -> None:
    """Fail unless ``ref``'s object is gone, without a collection.

    ``pump``: first run the Qt events (queued slots, deferred deletes) until it is gone, at
    most ``timeout`` seconds (a thread is let go when Qt's own finished arrives).
    """
    if pump:
        from PyQt6.QtCore import QCoreApplication, QEvent

        end = time.monotonic() + timeout
        while ref() is not None and time.monotonic() < end:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QCoreApplication.processEvents()
            time.sleep(0.005)
    alive = ref()
    if alive is not None:
        holders = sorted({type(r).__name__ for r in gc.get_referrers(alive)} - {"frame"})
        del alive
        pytest.fail(f"{what} is still alive (referred to by: {', '.join(holders) or '?'})")

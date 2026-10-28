"""A folder scan's loader thread is freed as soon as it has ended, without the cyclic GC.

``ImageManager.load_images`` connected the scan's result signal to a ``partial`` holding
the thread itself (and a superseded scan's end to a lambda holding it): the thread's own
signal connection kept it alive in a reference cycle (thread -> slot proxy -> partial ->
thread) that only the cyclic garbage collector breaks, and never once ``gc.freeze()``
(``app/gc_policy.py``) has frozen it: the warm-up audit (W3-WARM) found a finished
``ImageLoaderThread`` in the frozen garbage after a session. The manager also kept the
last thread, with its copies of the scan results, until the next folder was opened. After
a 46,178-image folder and then another one, a full collection had 17.2 MB (46,838 objects)
of the first scan to free, a 41-51 ms pause; now it finds none of it.

No slot holds the thread now, and the manager lets it go when it ends (delivered, failed
or superseded), so reference counting frees it at once. These tests turn the automatic
GC off and watch the thread through a weak reference (``tests/leak_census.py``).
"""

from __future__ import annotations

import os
import threading
import time
import weakref

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication

from app.gui import image_manager as im
from tests.leak_census import assert_freed, no_gc_fixture  # noqa: F401
from tests.window_support import write_photos

A_STEMS = ["plot_T1_L1_2024.01.01_120000", "plot_T1_L2_2024.01.01_120000"]
B_STEMS = ["plot_T2_L1_2024.01.01_120000"]
#: How long a scan may take to end and be let go, in seconds.
WAIT_S = 10.0

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


def _project(folder, stems) -> str:
    return str(write_photos(folder, stems, size=(8, 6), suffix=".png"))


def _pump(until, timeout: float = WAIT_S) -> bool:
    """Run the event loop (queued slots, deferred deletes) until ``until()`` or timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return until()


def test_a_finished_scan_frees_its_thread(app, tmp_path, no_gc):
    manager = im.ImageManager(None)
    manager.load_images(_project(tmp_path / "a", A_STEMS))
    thread = weakref.ref(manager.loader_thread)

    assert_freed(thread, "the finished loader thread", pump=True, timeout=WAIT_S)
    assert sorted(manager.images) == sorted(A_STEMS)  # it delivered first
    assert manager.skeleton_stems == set() and manager.get_leaf_labels()
    assert manager.loader_thread is None


def test_a_failed_scan_frees_its_thread(app, tmp_path, no_gc):
    manager = im.ImageManager(None)
    manager.load_images(str(tmp_path / "gone"))
    thread = weakref.ref(manager.loader_thread)

    assert_freed(thread, "the failed loader thread", pump=True, timeout=WAIT_S)
    assert manager.images == {} and manager.loader_thread is None


def test_a_superseded_scan_frees_its_thread_when_it_ends(app, tmp_path, monkeypatch, no_gc):
    """Opening another folder while a scan runs never kills it (PG-15): the old scan is
    kept until its thread ends, then freed; the new one delivers and is freed too."""
    a, b = _project(tmp_path / "a", A_STEMS), _project(tmp_path / "b", B_STEMS)
    held, release = threading.Event(), threading.Event()
    real_scan = im.ProjectLayout.scan_image_folder

    def scan_image_folder(layout):
        if os.path.normcase(layout.root) == os.path.normcase(a):
            held.set()
            release.wait(10)  # the first scan is still running when b is opened
        return real_scan(layout)

    monkeypatch.setattr(im.ProjectLayout, "scan_image_folder", scan_image_folder)
    manager = im.ImageManager(None)
    manager.load_images(a)
    first = weakref.ref(manager.loader_thread)
    assert held.wait(10)
    manager.load_images(b)
    second = weakref.ref(manager.loader_thread)
    try:
        assert first() is not None and first().isRunning()  # kept while it runs
    finally:
        release.set()

    assert_freed(first, "the superseded loader thread", pump=True, timeout=WAIT_S)
    assert_freed(second, "the newer loader thread", pump=True, timeout=WAIT_S)
    assert sorted(manager.images) == sorted(B_STEMS)  # the superseded scan delivered nothing
    assert manager.loader_thread is None


def test_a_scan_that_ended_unread_is_kept_until_its_end_is_handled(app, tmp_path, no_gc):
    """A scan can end while its result and its end still wait in the event queue; opening
    another folder then must not free its thread under them (the reference cycle used to
    keep it): it goes when its queued end has run."""
    a, b = _project(tmp_path / "a", A_STEMS), _project(tmp_path / "b", B_STEMS)
    manager = im.ImageManager(None)
    manager.load_images(a)
    first = weakref.ref(manager.loader_thread)
    assert first().wait(10000)  # ended; nothing of it was handled yet (no event loop ran)

    manager.load_images(b)

    assert first() is not None
    assert_freed(first, "the ended scan's thread", pump=True, timeout=WAIT_S)
    assert _pump(lambda: manager.loader_thread is None)
    assert sorted(manager.images) == sorted(B_STEMS)  # a's late result was dropped

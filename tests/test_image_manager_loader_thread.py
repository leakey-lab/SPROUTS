"""The Library's folder scan (``ImageLoaderThread``) on the one worker base of the app.

It is a ``GuardedThread``: the error boundary of ``app.workers`` reports anything that ends
a scan (with the messages the scan always had, naming the folder), and the thread keeper
holds a scan's thread from ``start()`` until Qt's own ``finished``, so a superseded scan
needs no bookkeeping of the manager. A scan that is cancelled (another folder was opened)
still emits nothing.
"""

from __future__ import annotations

import logging
import os
import threading

import pytest

from app.gui import image_manager as im
from app.workers import LOGGER_NAME, GuardedThread, kept_threads
from tests.qt_wait import wait_until
from tests.window_support import write_photos

A_STEMS = ["plot_T1_L1_2024.01.01_120000", "plot_T1_L2_2024.01.01_120000"]
B_STEMS = ["plot_T2_L1_2024.01.01_120000"]

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


def _project(folder, stems) -> str:
    return str(write_photos(folder, stems, size=(8, 6), suffix=".png"))


def _record(thread) -> dict[str, list]:
    got: dict[str, list] = {"progress": [], "finished": [], "failed": [], "cancelled": []}
    thread.progress.connect(got["progress"].append)
    thread.finished.connect(lambda *args: got["finished"].append(args))
    thread.failed.connect(got["failed"].append)
    thread.cancelled.connect(lambda: got["cancelled"].append(True))
    return got


@pytest.fixture
def held_scan(monkeypatch):
    """The scan of the folder ``hold[0]`` waits inside ``scan_image_folder`` until
    ``release`` is set (set on teardown too). Returns ``(hold, held, release)``."""
    hold, held, release = [None], threading.Event(), threading.Event()
    real_scan = im.ProjectLayout.scan_image_folder

    def scan_image_folder(layout):
        if hold[0] and os.path.normcase(layout.root) == os.path.normcase(hold[0]):
            held.set()
            release.wait(10)
        return real_scan(layout)

    monkeypatch.setattr(im.ProjectLayout, "scan_image_folder", scan_image_folder)
    yield hold, held, release
    release.set()


def test_the_scan_runs_inside_the_worker_error_boundary():
    assert issubclass(im.ImageLoaderThread, GuardedThread)
    assert im.ImageLoaderThread.run is GuardedThread.run
    assert im.ImageLoaderThread.failed is GuardedThread.failed  # one failed signal


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (PermissionError(13, "Access is denied"), "Cannot read {folder}: Access is denied"),
        (OSError("no listing"), "Cannot read {folder}: no listing"),
        (ValueError("a name"), "Could not scan {folder}: ValueError: a name"),
        (SystemExit(3), "Could not scan {folder}: SystemExit: 3"),
    ],
    ids=["os-error", "os-error-without-strerror", "error", "exit"],
)
def test_a_failed_scan_reports_its_message_and_names_the_folder_in_the_log(
    app, tmp_path, monkeypatch, caplog, exc, message
):
    def fail(_layout):
        raise exc

    monkeypatch.setattr(im.ProjectLayout, "scan_image_folder", fail)
    folder = str(tmp_path)
    thread = im.ImageLoaderThread(folder)
    got = _record(thread)

    with caplog.at_level(logging.ERROR):
        thread.run()  # the thread's body, on this thread

    assert got["failed"] == [message.format(folder=folder)]
    assert got["progress"] == got["finished"] == got["cancelled"] == []
    named = [r for r in caplog.records if r.name == im.__name__]
    assert [r.getMessage() for r in named] == [f"Failed to scan image folder {folder}"]
    assert any(r.exc_info for r in caplog.records if r.name == LOGGER_NAME)  # the traceback


def test_a_missing_folder_is_reported_without_a_traceback(app, tmp_path, caplog):
    folder = str(tmp_path / "gone")
    thread = im.ImageLoaderThread(folder)
    got = _record(thread)

    with caplog.at_level(logging.WARNING):
        thread.run()

    assert got["failed"] == [f"Folder not found: {folder}"] and got["finished"] == []
    assert caplog.records == []


def test_a_cancelled_scan_emits_nothing(app, tmp_path, held_scan):
    hold, held, release = held_scan
    hold[0] = folder = _project(tmp_path / "a", A_STEMS)
    thread = im.ImageLoaderThread(folder)
    got = _record(thread)
    thread.start()
    assert held.wait(10)

    thread.cancel()
    release.set()

    assert thread.wait(10000)
    assert wait_until(lambda: thread not in kept_threads())
    assert got == {"progress": [], "finished": [], "failed": [], "cancelled": []}


def test_a_superseded_scan_is_kept_by_the_thread_keeper_until_it_ends(app, tmp_path, held_scan):
    hold, held, release = held_scan
    a, b = _project(tmp_path / "a", A_STEMS), _project(tmp_path / "b", B_STEMS)
    hold[0] = a
    manager = im.ImageManager(None)
    manager.load_images(a)
    first = manager.loader_thread
    assert held.wait(10)

    manager.load_images(b)  # the first scan is asked to stop and let go by the manager
    second = manager.loader_thread
    try:
        assert second is not first and first.isRunning() and first.is_cancelled()
        assert first in kept_threads() and second in kept_threads()
    finally:
        release.set()

    assert wait_until(lambda: manager.loader_thread is None)
    assert sorted(manager.images) == sorted(B_STEMS)  # the superseded scan delivered nothing
    assert first.wait(10000)
    assert wait_until(lambda: first not in kept_threads() and second not in kept_threads())

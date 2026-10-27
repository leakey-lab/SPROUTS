"""One decode per photo across the Library display and the Correct page (X-RUNTIME F5).

Entering Correct used to decode on the GUI thread the photo the Library display had just
shown, and the Correct read-ahead decoded again the neighbours the Library had decoded
ahead, while the Library kept its three photos for the whole Correct session. Now the
Correct page takes the Library's decodes (``DisplayController.take_photo``: handed over and
forgotten there, so the Library's cache empties while Correct is shown), and back in the
Library the photos the Correct page holds are shown and kept instead of decoded again
(``CorrectEditor.cached_photo``). A photo is shared only while its file is the one decoded.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QStatusBar, QWidget

import app.enhancement as enhancement
import app.gui.display_controller as display_module
from app.gui.display_controller import DisplayController
from app.gui.display_read_ahead import DisplayReadAhead
from app.gui.image_manager import ImageManager
from app.io_utils import file_stamp
from tests.correct_support import bump, write_image
from tests.test_correct_editor_paging import key, read_ahead_done, ring

VALUES = {"A": 90, "B": 60, "C": 30}


pytestmark = pytest.mark.usefixtures("correct_guards")


class _Window:
    """What DisplayController uses from MainWindow."""

    def __init__(self, image_manager) -> None:
        self.image_manager = image_manager
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
        self.status_bar = QStatusBar()
        self.notices: list = []

    def notify(self, message, kind="success", timeout=3200) -> None:
        self.notices.append((message, kind))


class Library:
    """A Library display over photos A, B and C (library order), on a host widget."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.paths = [
            write_image(os.path.join(folder, f"{name}.png"), value=value)
            for name, value in VALUES.items()
        ]
        manager = ImageManager(None)
        manager.original_folder = folder
        manager.images = {os.path.splitext(os.path.basename(p))[0]: p for p in self.paths}
        manager._find_processed_base_path()
        self.window = _Window(manager)
        self.controller = DisplayController(self.window)
        self.host = QWidget()
        self.controller.setup_display_area(self.host)
        self.host.resize(600, 450)
        self.host.show()
        QApplication.processEvents()
        self.order = ring(*self.paths)
        self.controller.set_neighbour_provider(self.order)

    def show(self, name: str) -> None:
        self.controller.display_selected_image_by_name(name)

    def wait_ready(self, *paths: str, timeout: float = 5.0) -> None:
        ahead, deadline = self.controller.read_ahead, time.perf_counter() + timeout
        while not all(ahead.is_ready(p) for p in paths) and time.perf_counter() < deadline:
            time.sleep(0.005)
        assert all(ahead.is_ready(p) for p in paths)

    def close(self) -> None:
        self.controller.read_ahead.close()
        self.host.close()
        self.host.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def library(app, tmp_path):
    made = Library(str(tmp_path / "project"))
    yield made
    made.close()


class PhotoDecodes:
    """Every photo decode, on any thread: ``(file name, on the GUI thread)``."""

    def __init__(self, monkeypatch) -> None:
        self.seen: list[tuple[str, bool]] = []
        gui = threading.current_thread()
        for module in (enhancement, display_module):
            real = module.imread_unicode

            def spy(path, *args, _real=real, **kwargs):
                self.seen.append((os.path.basename(str(path)), threading.current_thread() is gui))
                return _real(path, *args, **kwargs)

            monkeypatch.setattr(module, "imread_unicode", spy)


def correct_page(make_editor, library: Library):
    """A Correct page wired to the Library as MainWindow wires them."""
    editor = make_editor()
    editor.set_neighbour_provider(library.order)
    editor.set_photo_source(library.controller.take_photo)
    library.controller.set_photo_source(editor.cached_photo)
    return editor


def shown_red(editor) -> int:
    return editor.photo_item.pixmap().toImage().pixelColor(5, 5).red()


# ------------------------------------------------------------------------ into Correct
def test_entering_correct_decodes_no_photo_the_library_holds(make_editor, library, monkeypatch):
    a, b, c = library.paths
    library.show("A")
    library.wait_ready(b, c)
    library.host.hide()  # the stage switch
    editor = correct_page(make_editor, library)
    decodes = PhotoDecodes(monkeypatch)

    editor.load_image(a, library.folder)
    read_ahead_done(editor)

    assert decodes.seen == []  # neither A on the GUI thread nor B and C on the worker
    assert shown_red(editor) == VALUES["A"]
    assert editor.paging.pending() == [key(b), key(c)]
    assert library.controller.read_ahead.pending() == []  # the Library's cache emptied


def test_paging_in_correct_after_entering_decodes_only_new_photos(
    make_editor, library, monkeypatch
):
    a, b, c = library.paths
    library.show("A")
    library.wait_ready(b, c)
    library.host.hide()
    editor = correct_page(make_editor, library)
    editor.load_image(a, library.folder)
    read_ahead_done(editor)
    decodes = PhotoDecodes(monkeypatch)

    editor.load_image(b, library.folder)  # PgDn
    read_ahead_done(editor)  # b's neighbours: c (read) and a (the image left)
    editor.load_image(a, library.folder)  # PgUp

    assert decodes.seen == [] and shown_red(editor) == VALUES["A"]


# -------------------------------------------------------------------- back to the Library
def test_back_in_the_library_the_correct_photos_are_shown_without_decoding(
    make_editor, library, monkeypatch
):
    a, b, c = library.paths
    library.show("A")
    library.wait_ready(b, c)
    library.host.hide()
    editor = correct_page(make_editor, library)
    editor.load_image(a, library.folder)
    read_ahead_done(editor)
    editor.load_image(b, library.folder)  # PgDn in Correct
    read_ahead_done(editor)
    editor.hide()  # the stage switch back
    library.host.show()
    decodes = PhotoDecodes(monkeypatch)

    library.show("B")
    library.wait_ready(a, b, c)

    assert decodes.seen == []
    held = library.controller.read_ahead
    assert sorted(held.pending()) == sorted(key(p) for p in (a, b, c))
    assert held.get(b) is editor._photo  # shared, not a copy


def test_a_correct_page_left_before_its_read_ahead_takes_nothing_from_the_library(
    make_editor, library
):
    a, b, c = library.paths
    library.show("A")
    library.wait_ready(b, c)
    library.host.hide()
    editor = correct_page(make_editor, library)
    editor.load_image(a, library.folder)
    editor.hide()  # back to the Library within the read-ahead delay
    library.host.show()

    read_ahead_done(editor)  # its neighbours are read with its own decodes

    held = library.controller.read_ahead
    assert held.holds(b) and held.holds(c)


def test_a_photo_changed_since_it_was_decoded_is_not_shared(make_editor, library, monkeypatch):
    a, b, c = library.paths
    library.show("A")
    library.wait_ready(b, c)
    library.host.hide()
    write_image(a, value=200)
    bump(a)
    editor = correct_page(make_editor, library)
    decodes = PhotoDecodes(monkeypatch)

    editor.load_image(a, library.folder)

    assert decodes.seen == [("A.png", True)] and shown_red(editor) == 200


def test_a_photo_source_that_raises_is_logged_and_the_photo_decoded(
    make_editor, library, monkeypatch, caplog
):
    a, _b, _c = library.paths
    editor = correct_page(make_editor, library)

    def broken(_path):
        raise RuntimeError("library gone")

    editor.set_photo_source(broken)
    library.controller.set_photo_source(broken)

    editor.load_image(a, library.folder)
    library.show("B")

    assert shown_red(editor) == VALUES["A"] and "photo of" in caplog.text


# ------------------------------------------------------------------- DisplayReadAhead.take
class ManualExecutor:
    """Runs the jobs when told to: queued / done are the test's choice."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Future, object]] = []

    def submit(self, fn, *args):
        future: Future = Future()
        self.jobs.append((future, lambda: fn(*args)))
        return future

    def run_all(self) -> None:
        for future, job in self.jobs:
            if future.set_running_or_notify_cancel():
                future.set_result(job())
        self.jobs.clear()

    def shutdown(self, wait=True, cancel_futures=False) -> None:
        pass


def _photo(path, value: int) -> str:
    assert cv2.imwrite(str(path), np.full((12, 16, 3), value, np.uint8))
    return str(path)


def test_take_hands_a_decoded_photo_over_once(tmp_path):
    executor = ManualExecutor()
    ahead = DisplayReadAhead(lambda p: cv2.imread(p), executor=executor)
    path = _photo(tmp_path / "a.png", 50)
    ahead.request([path])
    executor.run_all()

    taken = ahead.take(path)

    assert taken is not None and taken[0] == file_stamp(path) and int(taken[1][0, 0, 0]) == 50
    assert not taken[1].flags.writeable
    assert ahead.take(path) is None and not ahead.holds(path)


def test_take_never_waits_and_forgets_a_decode_not_finished(tmp_path):
    executor = ManualExecutor()
    ahead = DisplayReadAhead(lambda p: cv2.imread(p), executor=executor)
    path = _photo(tmp_path / "a.png", 50)
    ahead.request([path])
    queued = executor.jobs[0][0]

    assert ahead.take(path) is None

    assert queued.cancelled() and ahead.pending() == []


def test_take_gives_nothing_for_a_file_changed_since_its_decode(tmp_path):
    ahead = DisplayReadAhead(lambda p: cv2.imread(p), executor=ManualExecutor())
    path = _photo(tmp_path / "a.png", 50)
    ahead.keep(path, file_stamp(path), cv2.imread(path))
    bump(path)

    assert ahead.take(path) is None and ahead.pending() == []

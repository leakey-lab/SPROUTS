"""Library navigation: neighbours in tree order and photos decoded ahead (H4).

``LibraryIndex.neighbours(path, n)`` names the images PgDn / PgUp would open, nearest
first (next, previous, next but one, ...), skipping filtered-out rows; ``position_of``
gives the 1-based place among the visible images. The Correct page reads those
neighbours ahead through them, and so does the Library display: once an image is shown,
its neighbours are decoded on a worker thread, so the next PgDn / arrow step shows a
photo without decoding it on the GUI thread (2048 px: about 6 of 10 ms of GUI time).
The display never shows a stale photo: a read is used only while the file on disk is the
one that was read.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future

import cv2
import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QTreeWidget

from app.gui import display_controller as display_module
from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.display_read_ahead import DisplayReadAhead
from app.gui.library_index import LibraryIndex, index_names
from app.io_utils import file_stamp
from tests.qt_teardown import delete_widgets
from tests.qt_wait import wait_until as _wait
from tests.window_support import build_window, open_folder, quiet_window

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in range(11, 17)]

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


# ------------------------------------------------------------ neighbours / position
def _index(app) -> tuple[QTreeWidget, LibraryIndex]:
    tree = QTreeWidget()
    index = LibraryIndex(tree, path_of=lambda name: f"D:/p/{name}.jpg")
    index.build(*index_names(STEMS))
    return tree, index


def _path(i: int) -> str:
    return f"D:/p/{STEMS[i]}.jpg"


def test_neighbours_are_next_then_previous_nearest_first(app):
    tree, index = _index(app)
    assert index.neighbours(_path(2)) == [_path(3), _path(1)]
    assert index.neighbours(_path(2), 2) == [_path(3), _path(1), _path(4), _path(0)]
    assert index.neighbours(STEMS[2]) == [_path(3), _path(1)]  # a stem works too
    tree.deleteLater()


def test_neighbours_at_the_ends_and_of_unknown_images(app):
    tree, index = _index(app)
    assert index.neighbours(_path(0)) == [_path(1)]
    assert index.neighbours(_path(5), 2) == [_path(4), _path(3)]
    assert index.neighbours("D:/p/elsewhere.jpg") == []
    assert index.neighbours("") == []
    tree.deleteLater()


def test_neighbours_skip_hidden_rows_like_paging_does(app):
    tree, index = _index(app)
    for i in (1, 3):
        index.leaves()[i].setHidden(True)
    assert index.neighbours(_path(2)) == [_path(4), _path(0)]
    assert index.step(index.leaves()[2], 1) is index.leaves()[4]  # the same rule
    assert index.position_of(_path(1)) is None  # a hidden image has no place
    tree.deleteLater()


def test_neighbours_and_position_follow_the_search(app):
    tree, index = _index(app)
    assert index.apply_filter("_L012_") == 1
    assert index.neighbours(_path(1)) == []
    assert index.position_of(_path(1)) == (1, 1)
    assert index.position_of(_path(2)) is None
    index.apply_filter("")
    assert index.position_of(_path(2)) == (3, 6)
    tree.deleteLater()


def test_position_of_is_one_based_among_the_images(app):
    tree, index = _index(app)
    assert index.position_of(_path(0)) == (1, 6)
    assert index.position_of(STEMS[5]) == (6, 6)
    assert index.position_of("D:/p/elsewhere.jpg") is None
    tree.deleteLater()


# ------------------------------------------------------------------ the read-ahead
class InlineExecutor:
    """Runs a job when told to, so a test controls queued / running / done."""

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
    image = np.full((12, 16, 3), value, np.uint8)
    assert cv2.imwrite(str(path), image)
    return str(path)


def _decode(path: str):
    return cv2.imread(path, cv2.IMREAD_COLOR)


def test_a_finished_read_is_used_until_its_file_changes(tmp_path):
    executor = InlineExecutor()
    ahead = DisplayReadAhead(_decode, executor=executor)
    path = _photo(tmp_path / "a.png", 50)
    ahead.request([path])
    assert ahead.get(path) is None  # still queued: the caller decodes it itself
    ahead.request([path])
    executor.run_all()
    image = ahead.get(path)
    assert image is not None and int(image[0, 0, 0]) == 50
    assert not image.flags.writeable  # shared with the photo item: never modified
    assert ahead.get(path) is image  # kept for PgUp, not handed over once

    time.sleep(0.02)
    _photo(tmp_path / "a.png", 90)
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    assert ahead.get(path) is None  # the file changed: never a stale photo


def test_a_running_read_is_waited_for(tmp_path):
    path = _photo(tmp_path / "b.png", 70)
    started, release = threading.Event(), threading.Event()

    def slow(p):
        started.set()
        release.wait(5)
        return _decode(p)

    ahead = DisplayReadAhead(slow)
    try:
        ahead.request([path])
        assert started.wait(5)
        threading.Timer(0.05, release.set).start()
        image = ahead.get(path)
        assert image is not None and int(image[0, 0, 0]) == 70
    finally:
        release.set()
        ahead.close()


def test_kept_images_and_reads_are_bounded(tmp_path):
    executor = InlineExecutor()
    ahead = DisplayReadAhead(_decode, max_images=3, executor=executor)
    paths = [_photo(tmp_path / f"{i}.png", 10 * i) for i in range(5)]
    for p in paths[:2]:
        ahead.keep(p, file_stamp(p), _decode(p))
    ahead.request(paths[2:4])
    executor.run_all()
    assert len(ahead.pending()) == 3
    assert ahead.get(paths[0]) is None  # the oldest went first
    ahead.retain([paths[3]])
    assert ahead.pending() == [os.path.normcase(os.path.abspath(paths[3]))]
    ahead.clear()
    assert ahead.pending() == []


def test_a_read_that_tips_the_byte_bound_is_still_handed_over(tmp_path):
    # The read asked for is the oldest entry, and its photo tips the byte bound once it
    # arrives: the trim must drop another entry, never the one being handed over.
    executor = InlineExecutor()
    first = _photo(tmp_path / "first.png", 10)
    shown = _photo(tmp_path / "shown.png", 20)
    one = _decode(first).nbytes
    ahead = DisplayReadAhead(_decode, max_images=3, max_bytes=int(one * 1.5), executor=executor)
    ahead.request([first])
    ahead.keep(shown, file_stamp(shown), _decode(shown))
    executor.run_all()

    image = ahead.get(first)

    assert image is not None and int(image[0, 0, 0]) == 10
    assert ahead.pending() == [os.path.normcase(os.path.abspath(first))]


def test_an_unreadable_file_gives_nothing(tmp_path):
    executor = InlineExecutor()
    ahead = DisplayReadAhead(_decode, executor=executor)
    missing = str(tmp_path / "missing.png")
    ahead.request([missing])
    executor.run_all()
    assert ahead.get(missing) is None


# ------------------------------------------------------------ the Library display
@pytest.fixture
def window(app, tmp_path, no_prompts):
    folder = tmp_path / "project"
    folder.mkdir()
    for i, stem in enumerate(STEMS):
        image = np.random.default_rng(i).integers(0, 255, (48, 64, 3), dtype=np.uint8)
        assert cv2.imwrite(str(folder / f"{stem}.jpg"), image)
    made = [build_window(tmp_path / "settings.ini", show=False)]
    made[0].show()
    open_folder(made[0], folder, len(STEMS))
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _shown_photo(mw) -> np.ndarray:
    items = [i for i in mw.display_controller.magnifying_view.scene().items()]
    photos = [i for i in items if isinstance(i, PhotoLayerItem)]
    assert photos
    return photos[0]._pyramid.level(0)


def test_the_next_photo_is_decoded_off_the_gui_thread(window, monkeypatch):
    mw = window
    first = mw.library_index.leaves()[0]
    mw.file_list.setCurrentItem(first)
    mw.on_image_selected(first)
    next_path = mw.image_manager.get_image_path(STEMS[1])
    ahead = mw.display_controller.read_ahead
    assert _wait(lambda: ahead.is_ready(next_path))

    gui_decodes = []
    real = display_module.imread_unicode

    def spy(path, *args):
        if threading.current_thread() is threading.main_thread():
            gui_decodes.append(path)
        return real(path, *args)

    monkeypatch.setattr(display_module, "imread_unicode", spy)
    mw.navigate_image(1)
    QApplication.processEvents()

    assert gui_decodes == []
    assert mw.display_controller.current_image == next_path
    np.testing.assert_array_equal(_shown_photo(mw), real(next_path, display_module.DECODE_FLAGS))

    mw.navigate_image(-1)  # back: the photo shown before is still kept
    QApplication.processEvents()
    assert gui_decodes == []


def test_a_photo_replaced_on_disk_is_decoded_again(window, monkeypatch):
    mw = window
    first = mw.library_index.leaves()[0]
    mw.file_list.setCurrentItem(first)
    mw.on_image_selected(first)
    next_path = mw.image_manager.get_image_path(STEMS[1])
    assert _wait(lambda: mw.display_controller.read_ahead.is_ready(next_path))

    replaced = np.full((48, 64, 3), 200, np.uint8)
    assert cv2.imwrite(next_path, replaced)
    stamp = os.stat(next_path)
    os.utime(next_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 10_000_000))
    mw.navigate_image(1)
    QApplication.processEvents()

    assert mw.display_controller.current_image == next_path
    fresh = display_module.imread_unicode(next_path, display_module.DECODE_FLAGS)
    np.testing.assert_array_equal(_shown_photo(mw), fresh)


def test_the_correct_page_gets_the_library_order(window):
    mw = window
    editor = mw.correct_editor
    if not hasattr(editor, "set_neighbour_provider"):
        pytest.skip("the Correct page has no neighbour provider in this tree")
    path = mw.image_manager.get_image_path(STEMS[2])
    provider = editor.paging._neighbours
    assert provider is not None
    assert list(provider(path)) == [
        mw.image_manager.get_image_path(STEMS[3]),
        mw.image_manager.get_image_path(STEMS[1]),
    ]
    position = getattr(editor.paging, "_position", None)
    if position is not None:
        assert position(path) == (3, len(STEMS))


def test_a_generation_run_drops_what_the_correct_page_read_ahead(window, monkeypatch):
    mw = window
    if not hasattr(mw.correct_editor, "discard_read_ahead"):
        pytest.skip("the Correct page has no read-ahead in this tree")
    calls = []
    monkeypatch.setattr(mw.correct_editor, "discard_read_ahead", lambda: calls.append(1))
    mw.on_outputs_changed()
    assert calls == [1]


def test_a_search_asks_the_correct_page_for_its_position_again(window, monkeypatch):
    mw = window
    if not hasattr(mw.correct_editor, "set_position_provider"):
        pytest.skip("the Correct page shows no position in this tree")
    offered = []
    monkeypatch.setattr(mw.correct_editor, "set_position_provider", offered.append)
    mw.library_search.setText("_L012_")
    mw.library_search_controller.apply_now()
    assert offered == [mw.library_index.position_of]
    assert mw.library_index.position_of(STEMS[1]) == (1, 1)

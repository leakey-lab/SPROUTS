"""CorrectEditor paging: the neighbours of the open image are read ahead on a worker thread
and the next open adopts them (KNOWN_ISSUES #6, background prefetch of the neighbour
images). Real page, real layers; the neighbour provider stands in for MainWindow's."""

from __future__ import annotations

import os
import threading
import time

import numpy as np
import pytest
from PyQt6.QtTest import QTest

import app.enhancement as enhancement
from app.gui.correct.layer import MASK, SKELETON
from app.gui.correct.paging import Neighbours
from app.io_utils import path_key
from tests.correct_support import bump, plane, write_image

pytestmark = pytest.mark.usefixtures("correct_guards")


def ring(*paths):
    """A provider over ``paths`` in library order: the next image, then the previous one."""

    def neighbours(path):
        keys = [os.path.normcase(os.path.abspath(p)) for p in paths]
        index = keys.index(os.path.normcase(os.path.abspath(path)))
        return [paths[(index + 1) % len(paths)], paths[index - 1]]

    return neighbours


def read_ahead_done(editor, timeout: float = 5.0) -> list[str]:
    """Wait for the read-ahead timer and its jobs; the kept entries (normalised paths)."""
    QTest.qWait(Neighbours.DELAY_MS + 30)
    deadline = time.perf_counter() + timeout
    # On a loaded machine the paging timer can fire after the fixed wait: wait it out.
    while editor.paging._timer.isActive() and time.perf_counter() < deadline:
        QTest.qWait(10)
    ahead = editor.paging._ahead
    while ahead is not None and time.perf_counter() < deadline:
        if all(job.done() for job in ahead._jobs.values()):
            break
        time.sleep(0.005)
    return editor.paging.pending()


def key(path: str) -> str:
    """The read-ahead's key of ``path`` (``io_utils.path_key``: it folds case on macOS too)."""
    return path_key(path)


class GuiDecodes:
    """Counts photo decodes made on the calling (GUI) thread."""

    def __init__(self, monkeypatch) -> None:
        self.paths: list[str] = []
        gui, real = threading.current_thread(), enhancement.imread_unicode

        def spy(path, *args, **kwargs):
            if threading.current_thread() is gui:
                self.paths.append(str(path))
            return real(path, *args, **kwargs)

        monkeypatch.setattr(enhancement, "imread_unicode", spy)


def test_without_a_neighbour_provider_nothing_is_read_ahead(make_editor, project):
    editor = make_editor(project.a, project.folder)

    assert read_ahead_done(editor) == []
    assert editor.paging._ahead is None  # no worker thread was made


def test_the_neighbours_are_read_once_the_open_image_was_shown(make_editor, project):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))

    editor.load_image(project.a, project.folder)
    assert editor.paging.pending() == []  # nothing yet: the image paints first

    assert read_ahead_done(editor) == [key(project.b)]


def test_paging_to_a_neighbour_read_ahead_decodes_nothing_on_the_gui_thread(
    make_editor, project, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    decodes = GuiDecodes(monkeypatch)

    editor.load_image(project.b, project.folder)

    assert decodes.paths == []
    assert editor.current_image_path == project.b
    shown = editor.photo_item.pixmap().toImage()
    assert shown.pixelColor(5, 5).red() == 60  # b's grey, not a's 90
    assert editor.layers[MASK].is_open() and editor.layers[SKELETON].is_open()
    assert not plane(editor).any()  # b has no mask


def test_a_photo_changed_after_it_was_read_ahead_is_decoded_again(
    make_editor, project, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    write_image(project.b, value=30)
    bump(project.b)
    decodes = GuiDecodes(monkeypatch)

    editor.load_image(project.b, project.folder)

    assert [key(path) for path in decodes.paths] == [key(project.b)]
    assert editor.photo_item.pixmap().toImage().pixelColor(5, 5).red() == 30


def test_a_layer_with_the_read_ahead_hook_opens_from_its_payload(make_editor, project):
    editor = make_editor()
    mask = editor.layers[MASK]
    read, opened = [], []
    real_open = mask.open

    def read_ahead(image_path, images_base_folder):
        def reader(size):
            read.append((threading.current_thread().name, size))
            return "mask payload"

        return reader

    def open_(image_path, size, images_base_folder, ahead=None):
        opened.append(ahead)
        return real_open(image_path, size, images_base_folder)

    mask.read_ahead, mask.open = read_ahead, open_
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)

    editor.load_image(project.b, project.folder)

    assert [(name.startswith("sprouts-read-ahead"), size) for name, size in read] == [
        (True, (200, 150))
    ]
    assert opened == [None, "mask payload"]


def test_opening_the_page_takes_the_read_only_once(make_editor, project, monkeypatch):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    editor.load_image(project.b, project.folder)
    editor.reset()
    decodes = GuiDecodes(monkeypatch)

    editor.load_image(project.b, project.folder)  # the read was handed over already

    assert [key(path) for path in decodes.paths] == [key(project.b)]


def test_paging_held_down_reads_only_the_last_images_neighbours(make_editor, tmp_path):
    folder = str(tmp_path / "many")
    paths = [write_image(os.path.join(folder, f"{name}.png")) for name in "abcde"]
    editor = make_editor()
    editor.set_neighbour_provider(ring(*paths))

    for path in paths[:4]:  # faster than the read-ahead delay
        editor.load_image(path, folder)

    assert read_ahead_done(editor) == [key(paths[4]), key(paths[2])]


def test_a_new_folder_and_a_generate_run_forget_what_was_read(make_editor, project):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    assert read_ahead_done(editor) == [key(project.b)]

    editor.discard_read_ahead()
    assert editor.paging.pending() == []

    read_ahead_done(editor)
    editor.load_image(project.b, project.folder)
    assert read_ahead_done(editor) == [key(project.a)]
    assert editor.reset() and editor.paging.pending() == []


def test_a_provider_that_raises_is_logged_and_paging_still_works(make_editor, project, caplog):
    editor = make_editor()

    def broken(_path):
        raise RuntimeError("library gone")

    editor.set_neighbour_provider(broken)
    editor.load_image(project.a, project.folder)

    assert read_ahead_done(editor) == []
    assert "neighbours of" in caplog.text
    editor.load_image(project.b, project.folder)
    assert editor.current_image_path == project.b


def test_removing_the_provider_stops_the_worker(make_editor, project):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)

    editor.set_neighbour_provider(None)

    assert editor.paging.pending() == [] and editor.paging.take(project.b) is None


def test_the_enhance_render_uses_the_photo_the_page_opened(make_editor, project, monkeypatch):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    editor.load_image(project.b, project.folder)
    decodes = GuiDecodes(monkeypatch)

    editor.apply_normalization()

    assert decodes.paths == []
    assert editor.photo_item.pixmap().toImage().pixelColor(5, 5).red() == 60
    photo = editor._photo
    assert isinstance(photo, np.ndarray) and not photo.flags.writeable


def test_a_read_ahead_image_the_user_did_not_leave_for_is_kept(make_editor, project, monkeypatch):
    from tests.correct_support import CANCEL, answer_prompts, paint

    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    paint(editor, (20, 100), (80, 110))
    answer_prompts(monkeypatch).will(CANCEL)

    editor.load_image(project.b, project.folder)  # Cancel: a stays open

    assert editor.current_image_path == project.a
    assert editor.paging.pending() == [key(project.b)]  # still read, for the next try


def test_deleting_the_page_stops_its_read_ahead_worker(app, project):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    from app.gui.correct.editor import CorrectEditor

    editor = CorrectEditor()
    editor.set_neighbour_provider(ring(project.a, project.b))
    ahead = editor.paging._ahead
    editor.layers[SKELETON].cancel_scheduled_render()
    editor.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert ahead._closed  # the worker thread ends instead of idling until collection

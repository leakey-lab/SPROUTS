"""The Correct page reads the open image's neighbours again when they may have changed.

The neighbours were read once, 60 ms after an open. Two things left the read-ahead holding
the wrong images (or none) until the next open, so the next PgDn decoded on the GUI thread:

- a library search changes the order (MainWindow hands the page its position provider
  again): the next image in the filtered order was never read;
- coming back to the open image (a stage switch, a click on its row) after the read-ahead was
  dropped (a Generate run): nothing was read again.

Now both read the neighbours again; what is still current is kept, not read twice.
"""

from __future__ import annotations

import os

import pytest

from tests.correct_support import write_image
from tests.test_correct_editor_paging import key, read_ahead_done

pytestmark = pytest.mark.usefixtures("correct_guards")


class Library:
    """The library order MainWindow's providers read (a search can hide images)."""

    def __init__(self, paths) -> None:
        self.shown = list(paths)

    def neighbours(self, path):
        keys = [key(p) for p in self.shown]
        i = keys.index(key(path))
        return [self.shown[(i + 1) % len(self.shown)], self.shown[i - 1]]

    def position(self, path):
        keys = [key(p) for p in self.shown]
        return (keys.index(key(path)) + 1, len(keys)) if key(path) in keys else None


@pytest.fixture
def library(tmp_path):
    folder = str(tmp_path / "project")
    paths = [write_image(os.path.join(folder, f"{name}.png")) for name in "abcde"]
    return folder, paths, Library(paths)


def test_a_search_that_changes_the_order_reads_the_new_neighbours(make_editor, library):
    folder, (a, b, c, _d, e), lib = library
    editor = make_editor()
    editor.set_neighbour_provider(lib.neighbours)
    editor.set_position_provider(lib.position)
    editor.load_image(a, folder)
    assert sorted(read_ahead_done(editor)) == sorted([key(b), key(e)])

    lib.shown.remove(b)  # the search hides b: PgDn from a now opens c
    editor.set_position_provider(lib.position)  # what MainWindow does on a search

    assert sorted(read_ahead_done(editor)) == sorted([key(c), key(e)])


def test_what_is_still_current_is_not_read_again(make_editor, library, monkeypatch):
    folder, (a, b, _c, _d, e), lib = library
    editor = make_editor()
    editor.set_neighbour_provider(lib.neighbours)
    editor.load_image(a, folder)
    read_ahead_done(editor)
    queued = []
    ahead = editor.paging._ahead
    real = ahead._executor.submit
    monkeypatch.setattr(ahead._executor, "submit", lambda job: queued.append(job) or real(job))

    editor.set_position_provider(lib.position)  # same order

    assert sorted(read_ahead_done(editor)) == sorted([key(b), key(e)]) and queued == []


def test_coming_back_to_the_open_image_reads_its_neighbours_again(make_editor, library):
    folder, (a, b, _c, _d, e), lib = library
    editor = make_editor()
    editor.set_neighbour_provider(lib.neighbours)
    editor.load_image(a, folder)
    read_ahead_done(editor)
    editor.discard_read_ahead()  # a Generate run rewrote the outputs
    assert editor.paging.pending() == []

    editor.load_image(a, folder)  # the stage again, or a click on its row: nothing reloads

    assert sorted(read_ahead_done(editor)) == sorted([key(b), key(e)])


def test_a_search_while_the_page_is_hidden_reads_on_the_way_back(make_editor, library):
    folder, (a, b, c, _d, e), lib = library
    editor = make_editor()
    editor.set_neighbour_provider(lib.neighbours)
    editor.load_image(a, folder)
    read_ahead_done(editor)
    editor.hide()  # another stage: the page keeps its image

    lib.shown.remove(b)
    editor.set_position_provider(lib.position)
    assert sorted(read_ahead_done(editor)) == sorted([key(b), key(e)])  # no work while hidden

    editor.show()
    editor.load_image(a, folder)  # back in Correct on the same image
    assert sorted(read_ahead_done(editor)) == sorted([key(c), key(e)])


def test_nothing_is_read_while_no_image_is_open(make_editor, library):
    _folder, _paths, lib = library
    editor = make_editor()
    editor.set_neighbour_provider(lib.neighbours)

    editor.set_position_provider(lib.position)

    assert read_ahead_done(editor) == []

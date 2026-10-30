"""The Correct page keeps the image it leaves as a finished read (X-RUNTIME F5).

Every PgDn used to read the image just left again on the worker (photo, mask and skeleton:
about 34 ms at 2048 x 1536), although its photo and its documents were in memory and clean
(the page saves or discards every edit before it moves on). The page now hands them back
to its read-ahead: PgUp opens the image left without reading anything, in exactly the state
a fresh open of the files gives. Only what is as on disk is handed back: a file changed
since, a mask file the page could not use, or a skeleton loaded from another file is read
again, as before. Real page, real layers; the neighbour provider stands in for MainWindow's.
"""

from __future__ import annotations

import os
import shutil
import threading

import numpy as np
import pytest

import app.gui.correct.paging as paging
import app.gui.correct.skeleton_document as skeleton_document
from app.gui.correct.layer import MASK, SKELETON
from app.gui.correct.mask_document import MaskDocument
from app.gui.correct.read_ahead import ReadAhead
from tests.correct_support import (
    DISCARD,
    answer_prompts,
    box_plane,
    bump,
    draw_line,
    paint,
    plane,
    raster,
    record,
    write_image,
    write_mask,
)
from tests.test_correct_editor_paging import GuiDecodes, key, read_ahead_done, ring

pytestmark = pytest.mark.usefixtures("correct_guards")


@pytest.fixture
def third(project):
    """A third photo, c.png: the library is a, b, c (PgDn from a opens b)."""
    return write_image(os.path.join(project.folder, "c.png"), value=30)


class Reads:
    """What is read: the read-ahead jobs by image, and the GUI thread's own reads."""

    def __init__(self, monkeypatch) -> None:
        self.jobs: list[str] = []
        self.gui_masks: list[str] = []
        self.gui_skeletons: list[str] = []
        self.gui_photos = GuiDecodes(monkeypatch).paths
        gui = threading.current_thread()
        real_job, real_mask = paging.read_image, MaskDocument.read
        real_skeleton = skeleton_document.imread_unicode

        def job(path, *args, **kwargs):
            self.jobs.append(os.path.basename(path))
            return real_job(path, *args, **kwargs)

        def mask(image_path, size, **kwargs):
            if threading.current_thread() is gui:
                self.gui_masks.append(os.path.basename(image_path))
            return real_mask(image_path, size, **kwargs)

        def skeleton(path, *args):
            if threading.current_thread() is gui:
                self.gui_skeletons.append(os.path.basename(path))
            return real_skeleton(path, *args)

        monkeypatch.setattr(paging, "read_image", job)
        monkeypatch.setattr(MaskDocument, "read", staticmethod(mask))
        monkeypatch.setattr(skeleton_document, "imread_unicode", skeleton)

    def on_gui(self) -> list[str]:
        return self.gui_photos + self.gui_masks + self.gui_skeletons


def state(editor) -> dict:
    """What the page shows of the open image: the documents, their sources and states."""
    mask, skeleton = editor.layers[MASK], editor.layers[SKELETON]
    return {
        "plane": plane(editor).copy(),
        "raster": raster(editor).copy(),
        "tips": skeleton.model.endpoint_array().tolist(),
        "source": skeleton.document.source,
        "save_size": skeleton.document.save_size,
        "mask_status": mask.status(),
        "skeleton_status": skeleton.status(),
        "has_saved_file": mask.document.has_saved_file,
        "dirty": editor.has_unsaved_changes(),
        "history": (mask.can_undo(), skeleton.can_undo(), editor.session.can_undo()),
    }


def fresh_state(make_editor, image, folder) -> dict:
    """The state a page that never saw the image opens it in (the files as they are)."""
    return state(make_editor(image, folder))


def assert_same_state(got: dict, want: dict) -> None:
    assert np.array_equal(got.pop("plane"), want.pop("plane"))
    assert np.array_equal(got.pop("raster"), want.pop("raster"))
    assert got == want


# ------------------------------------------------------------------------ the hand-back
def test_going_back_to_the_image_left_reads_nothing_again(make_editor, project, third, monkeypatch):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)  # b and c
    reads = Reads(monkeypatch)

    editor.load_image(project.b, project.folder)  # PgDn: a is kept as read
    assert key(project.a) in editor.paging.pending()
    read_ahead_done(editor)  # b's neighbours: c (read already) and a (handed back)
    editor.load_image(project.a, project.folder)  # PgUp

    assert reads.jobs == [] and reads.on_gui() == []
    assert_same_state(state(editor), fresh_state(make_editor, project.a, project.folder))
    assert np.array_equal(plane(editor), box_plane())
    assert raster(editor)[110, 20:180].all()


def test_an_image_saved_on_the_way_out_is_handed_back_as_saved(
    make_editor, project, third, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    moved = record(editor.navigate_requested)
    editor.navigate(1)  # PgDn saves the mask and the skeleton, then asks for b
    assert moved == [1] and os.path.exists(project.correction())
    reads = Reads(monkeypatch)

    editor.load_image(project.b, project.folder)
    read_ahead_done(editor)
    editor.load_image(project.a, project.folder)

    assert reads.jobs == [] and reads.on_gui() == []
    got = state(editor)
    assert got["source"] == "gt" and not got["dirty"]
    assert_same_state(got, fresh_state(make_editor, project.a, project.folder))


def test_an_image_whose_edits_were_discarded_is_handed_back_as_saved(
    make_editor, project, third, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    paint(editor, (20, 100), (80, 110))
    answer_prompts(monkeypatch).will(DISCARD)
    editor.load_image(project.b, project.folder)  # a tree click: Discard
    reads = Reads(monkeypatch)
    read_ahead_done(editor)

    editor.load_image(project.a, project.folder)

    assert reads.jobs == [] and reads.on_gui() == []
    assert np.array_equal(plane(editor), box_plane())  # the saved mask, not the stroke
    assert_same_state(state(editor), fresh_state(make_editor, project.a, project.folder))


def test_a_mask_file_changed_after_the_image_was_left_is_read_again(make_editor, project, third):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    editor.load_image(project.b, project.folder)  # a is kept as read
    other = np.zeros_like(box_plane())
    other[5:15, 5:15] = 1
    write_mask(project.mask(), other)
    bump(project.mask())

    editor.load_image(project.a, project.folder)

    assert np.array_equal(plane(editor), other)


def test_a_photo_changed_after_the_image_was_left_is_decoded_again(
    make_editor, project, third, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    editor.load_image(project.b, project.folder)
    write_image(project.a, value=200)
    bump(project.a)
    decodes = GuiDecodes(monkeypatch)

    editor.load_image(project.a, project.folder)

    assert [key(path) for path in decodes.paths] == [key(project.a)]
    assert editor.photo_item.pixmap().toImage().pixelColor(5, 5).red() == 200


def test_an_unusable_mask_file_is_read_again_and_warned_about_again(
    make_editor, project, third, monkeypatch
):
    with open(project.mask(), "wb") as fh:
        fh.write(b"not a png")  # a prediction that cannot be read (another size is stretched)
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    notices = record(editor.notice)
    editor.load_image(project.a, project.folder)
    assert len(notices) == 1 and notices[0][1] == "warn"
    reads = Reads(monkeypatch)

    editor.load_image(project.b, project.folder)  # a is not handed back
    read_ahead_done(editor)
    editor.load_image(project.a, project.folder)

    assert "a.png" in reads.jobs  # the worker read it again, as without a hand-back
    assert len(notices) == 2 and notices[1] == notices[0]


def test_a_stretched_prediction_is_handed_back_and_says_so_again(
    make_editor, project, third, monkeypatch
):
    small = np.zeros((60, 80), np.uint8)  # the photo is 200 x 150: stretched x 2.5
    small[20:40, 10:50] = 1
    write_mask(project.mask(), small)
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    notices = record(editor.notice)
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    stretched = (
        "Mask is 80 x 60; shown at the photo's 200 x 150. Saving stores it at the photo's size.",
        "info",
    )
    assert notices == [stretched]
    reads = Reads(monkeypatch)

    editor.load_image(project.b, project.folder)  # a is kept as read
    read_ahead_done(editor)
    editor.load_image(project.a, project.folder)

    assert reads.jobs == [] and reads.on_gui() == []  # no second read of the file
    assert notices.count(stretched) == 2
    got = state(editor)
    assert got["mask_status"].label == "Prediction" and not got["dirty"]
    assert_same_state(got, fresh_state(make_editor, project.a, project.folder))


def test_a_skeleton_loaded_from_another_file_is_read_again(
    make_editor, project, third, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    copy = os.path.join(project.folder, "elsewhere", "a_copy.png")
    os.makedirs(os.path.dirname(copy))
    shutil.copyfile(project.generated(), copy)
    skeleton = editor.layers[SKELETON]
    skeleton.load_file(copy)  # the same lines: clean, but "Loaded file"
    editor.session.forget(SKELETON)
    assert not editor.has_unsaved_changes() and skeleton.document.source == "loaded"
    reads = Reads(monkeypatch)

    editor.load_image(project.b, project.folder)
    read_ahead_done(editor)
    editor.load_image(project.a, project.folder)

    assert "a.png" in reads.jobs
    assert skeleton.document.source == "prediction"  # as a fresh open shows it


def test_without_a_neighbour_provider_nothing_is_kept(make_editor, project):
    editor = make_editor(project.a, project.folder)

    editor.load_image(project.b, project.folder)

    assert editor.paging.pending() == [] and editor.paging._ahead is None


def test_a_new_folder_forgets_the_image_left(make_editor, project, third):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.load_image(project.a, project.folder)
    editor.load_image(project.b, project.folder)

    assert editor.reset()

    assert editor.paging.pending() == [] and editor.cached_photo(project.b) is None


# --------------------------------------------------------------------- the byte budget
def test_the_next_image_stays_read_when_both_neighbours_do_not_fit(
    make_editor, project, third, monkeypatch
):
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.a, project.b, third))
    editor.paging._ahead.close()
    editor.paging._ahead = ReadAhead(max_bytes=100_000)  # one 200 x 150 photo (90 kB) fits
    editor.load_image(project.a, project.folder)

    assert read_ahead_done(editor) == [key(project.b)]  # the next image; c went
    decodes = GuiDecodes(monkeypatch)
    editor.load_image(project.b, project.folder)  # PgDn
    assert decodes.paths == []

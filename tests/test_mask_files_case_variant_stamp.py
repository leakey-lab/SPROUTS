"""A mask prediction found only by the case-folded lookup (O7) is pinned like an exact name.

``read_mask`` stamps the GT mask and the six names a prediction may have before it looks
the prediction up, then the file it found when that is none of those names (its stem or its
extension in another case, on a case-sensitive disk), before reading it; the prediction is
looked up once per read (``MaskStore.read(..., path=)``). A rewrite of that file by another
program is then seen as one of an exact name is: the save rule writes the GT instead of
removing it, Discard and the same image opened again read it again, and neither a
read-ahead nor a hand-back made before the rewrite is adopted (S01-06).

The real-folder cases need a case-sensitive folder (fsutil on Windows; the ubuntu CI) and
are skipped elsewhere. Image ``img.png``; its prediction is ``mask/img.png`` (the exact
name: the control), ``mask/IMG.png`` (the stem in another case) or ``mask/img.Png`` (the
extension in another case: not one of the six names either).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402

from app import project_layout  # noqa: E402
from app.gui.correct.layer import MASK  # noqa: E402
from app.gui.correct.mask_document import MaskDocument  # noqa: E402
from app.gui.correct.mask_files import mask_stamps, read_mask  # noqa: E402
from app.io_utils import file_stamp  # noqa: E402
from app.masks import MaskFileError, MaskStore, write_mask_file  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    DISCARD,
    H,
    W,
    answer_prompts,
    box_plane,
    bump,
    editor_factory,
    install_guards,
    paint,
    plane,
    qapp,
    record,
    write_image,
    write_mask,
)
from tests.test_correct_editor_paging import read_ahead_done, ring  # noqa: E402
from tests.test_gt_case_variant_lookups import case_sensitive_folder  # noqa: E402

EXACT = "img.png"
NAMES = [EXACT, "IMG.png", "img.Png"]


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


def _project(tmp_path, name: str) -> SimpleNamespace:
    """Photos ``img.png``, ``b.png`` and ``c.png`` on a case-sensitive folder; the prediction
    of ``img.png`` is ``mask/<name>``; ``gt`` is its GT mask path (not written)."""
    folder = case_sensitive_folder(str(tmp_path / "project"))
    image = write_image(os.path.join(folder, "img.png"))
    other = write_image(os.path.join(folder, "b.png"), value=60)
    third = write_image(os.path.join(folder, "c.png"), value=30)
    prediction = write_mask(os.path.join(folder, "mask", name), box_plane())
    assert not project_layout._ignores_case(os.path.dirname(prediction))  # the real probe
    assert os.listdir(os.path.dirname(prediction)) == [name]
    gt = os.path.join(folder, "gt", "mask", "img.png")
    return SimpleNamespace(
        folder=folder, image=image, other=other, third=third, prediction=prediction, gt=gt
    )


def _rewrite(prediction: str) -> np.ndarray:
    """Another program writes the prediction again; its new 0/1 plane."""
    newer = np.zeros((H, W), np.uint8)
    newer[100:140, 120:180] = 1
    write_mask(prediction, newer)
    bump(prediction)
    return newer


def _gt_plane(path: str) -> np.ndarray:
    return (cv2.imread(path, cv2.IMREAD_UNCHANGED)[:, :, 3] == 255).astype(np.uint8)


# ------------------------------------------------------------------ any disk
def test_mask_stamps_add_a_prediction_found_under_none_of_the_names(tmp_path):
    layout = ProjectLayout(str(tmp_path))
    image = os.path.join(str(tmp_path), "img.png")
    gt = layout.gt_mask_path(image)
    names = layout.mask_candidates(image)
    variant = os.path.join(layout.mask_dir, "IMG.png")

    exact = mask_stamps(layout, gt, image)

    assert exact == (file_stamp(gt), *map(file_stamp, names))  # 1 + 6, in lookup order
    assert mask_stamps(layout, gt, image, names[2]) == exact  # one of the names: no more
    assert mask_stamps(layout, gt, image, variant) == (*exact, file_stamp(variant))


def test_read_mask_looks_the_prediction_up_once(tmp_path, monkeypatch):
    image = write_image(os.path.join(str(tmp_path), "img.png"))
    write_mask(os.path.join(str(tmp_path), "mask", "img.png"), box_plane())
    lookups = []
    real = ProjectLayout.find_mask

    def find_mask(layout, stem):
        lookups.append(stem)
        return real(layout, stem)

    monkeypatch.setattr(ProjectLayout, "find_mask", find_mask)

    read = read_mask(image, (W, H))

    assert read.source == "prediction" and np.array_equal(read.plane, box_plane())
    assert len(lookups) == 1


def test_the_store_reads_the_file_found_without_a_second_lookup(tmp_path, monkeypatch):
    found = write_mask(os.path.join(str(tmp_path), "mask", "img.png"), box_plane())
    store = MaskStore(ProjectLayout(str(tmp_path)))
    monkeypatch.setattr(ProjectLayout, "find_mask", lambda *_args: pytest.fail("looked up"))

    assert np.array_equal(store.read("img", path=found), box_plane() > 0)
    os.remove(found)
    assert store.read("img", path=found) is None  # gone since the lookup: no prediction
    with open(found, "wb") as fh:
        fh.write(b"not a png")
    with pytest.raises(MaskFileError):
        store.read("img", path=found)


# ------------------------------------------------------------------ the document
@pytest.mark.parametrize("name", NAMES)
def test_the_prediction_shown_is_stamped_whatever_its_case(tmp_path, name):
    project = _project(tmp_path, name)
    doc = MaskDocument()
    doc.open(project.image, (W, H))
    assert doc.source == "prediction" and os.path.basename(doc.prediction_path) == name
    stamps = doc.file_stamps()
    assert len(stamps) == 1 + 6 + (name != EXACT) and file_stamp(project.prediction) in stamps

    _rewrite(project.prediction)

    assert doc.file_stamps() != stamps
    assert doc.file_stamps()[1:] != doc.prediction_stamps  # the save rule sees it


@pytest.mark.parametrize("name", NAMES)
def test_a_read_ahead_or_hand_back_of_a_rewritten_prediction_is_read_again(tmp_path, name):
    project = _project(tmp_path, name)
    ahead = MaskDocument.read(project.image, (W, H))
    doc = MaskDocument()
    doc.open(project.image, (W, H))
    back = doc.read_back(doc.file_stamps())
    assert back is not None

    newer = _rewrite(project.prediction)

    for payload in (ahead, back):
        again = MaskDocument()
        again.open(project.image, (W, H), payload)
        assert np.array_equal(again.history.plane, newer)


# ------------------------------------------------------------------ the page
@pytest.mark.parametrize("name", NAMES)
def test_save_does_not_take_a_rewritten_prediction_for_the_gt(make_editor, tmp_path, name):
    project = _project(tmp_path, name)
    block = np.zeros((H, W), bool)
    block[90:120, 20:60] = True
    write_mask_file(project.gt, block, durable=False)  # a GT mask over the prediction
    editor = make_editor(project.image, project.folder)
    document = editor.layers[MASK].document
    assert document.source == "gt" and os.path.basename(document.prediction_path) == name
    editor.load_prediction()  # the plane is the prediction as opened
    _rewrite(project.prediction)

    assert editor.save() is True

    assert os.path.exists(project.gt), "the GT file was removed against a rewritten prediction"
    assert (_gt_plane(project.gt) == box_plane()).all()  # the pixels shown and saved


@pytest.mark.parametrize("name", NAMES)
def test_discard_after_a_rewrite_shows_the_file(make_editor, tmp_path, monkeypatch, name):
    project = _project(tmp_path, name)
    editor = make_editor(project.image, project.folder)
    assert np.array_equal(plane(editor), box_plane())  # the prediction is shown
    paint(editor, (20, 100), (80, 110))
    newer = _rewrite(project.prediction)
    notices = record(editor.notice)
    answer_prompts(monkeypatch).will(DISCARD)

    assert editor.maybe_save_changes() is True

    assert np.array_equal(plane(editor), newer), "Discard went back to a stale prediction"
    assert ("Mask reloaded: the file changed on disk", "info") in notices


@pytest.mark.parametrize("name", NAMES)
def test_the_clean_image_opened_again_shows_a_rewritten_prediction(make_editor, tmp_path, name):
    project = _project(tmp_path, name)
    editor = make_editor(project.image, project.folder)
    newer = _rewrite(project.prediction)

    editor.load_image(project.image, project.folder)  # the same image again

    assert np.array_equal(plane(editor), newer), "the page kept a stale prediction"


@pytest.mark.parametrize("name", NAMES)
def test_a_rewritten_prediction_is_not_handed_back_stale(make_editor, tmp_path, name):
    project = _project(tmp_path, name)
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.image, project.other, project.third))
    editor.load_image(project.image, project.folder)
    read_ahead_done(editor)
    newer = _rewrite(project.prediction)

    editor.load_image(project.other, project.folder)  # the image left is handed back
    read_ahead_done(editor)
    editor.load_image(project.image, project.folder)

    assert np.array_equal(editor.layers[MASK].document.prediction(), newer)
    assert np.array_equal(plane(editor), newer), "a stale hand-back was adopted"


@pytest.mark.parametrize("name", NAMES)
def test_an_unreadable_prediction_still_says_so_after_a_hand_back(make_editor, tmp_path, name):
    project = _project(tmp_path, name)
    with open(project.prediction, "wb") as fh:
        fh.write(b"not a png")
    editor = make_editor()
    editor.set_neighbour_provider(ring(project.image, project.other, project.third))
    editor.load_image(project.image, project.folder)
    assert editor.layers[MASK].status().label == "Mask not shown"
    read_ahead_done(editor)

    editor.load_image(project.other, project.folder)  # handed back to the read-ahead
    read_ahead_done(editor)
    editor.load_image(project.image, project.folder)

    assert editor.layers[MASK].status().label == "Mask not shown"

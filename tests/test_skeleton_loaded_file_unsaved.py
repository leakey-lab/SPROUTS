"""A skeleton opened with "Load skeleton file..." counts as unsaved (wave 2, E1).

Before, Load reset the saved baseline, so the loaded skeleton read clean ("Loaded file") and the
next image, a stage switch or PgDn dropped it without a word. Now the baseline stays the
skeleton that was opened or last saved: a loaded file that differs from it is an unsaved edit.
The one prompt asks about it (Save writes it as the correction, Discard re-reads the start
file, Cancel stays), PgDn saves it as it saves every unsaved layer, and a file identical to
the open skeleton stays clean.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QFileDialog  # noqa: E402

from app.gui.correct.layer import SKELETON, LayerStatus  # noqa: E402
from app.gui.correct.skeleton_document import (  # noqa: E402
    CORRECTED,
    GENERATED,
    LOADED,
    SkeletonDocument,
)
from app.gui.skeleton_graph_model import SkeletonCorrectionModel  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    DISCARD,
    SAVE,
    Mouse,
    answer_prompts,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    raster,
    record,
    write_skeleton,
)

W, H = 200, 150


def _line(row: int, size: tuple[int, int] = (W, H)) -> np.ndarray:
    w, h = size
    raster_ = np.zeros((h, w), np.uint8)
    raster_[row, 20 : w - 20] = 255
    return raster_


def _file(path, row: int) -> str:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    assert cv2.imwrite(str(path), _line(row))
    return str(path)


# ------------------------------------------------------------------------------ the model
def test_a_raster_loaded_as_an_edit_keeps_the_saved_baseline():
    model = SkeletonCorrectionModel()
    model.load_from_raster(_line(30), target_size=(W, H))
    opened = model.mask.copy()

    model.load_from_raster(_line(90), target_size=(W, H), saved=False)

    assert model.has_unsaved_changes() and model.mask[90].any() and not model.can_undo
    model.load_from_raster(opened, target_size=(W, H), saved=False)  # back to the baseline
    assert not model.has_unsaved_changes()
    model.load_from_raster(_line(90), target_size=(W, H))  # an open: the new baseline
    assert not model.has_unsaved_changes()


# --------------------------------------------------------------------------- the document
@pytest.fixture
def folder(tmp_path):
    image = tmp_path / "a.png"
    assert cv2.imwrite(str(image), np.full((H, W, 3), 90, np.uint8))
    return tmp_path, str(image)


def _document(root, image) -> SkeletonDocument:
    doc = SkeletonDocument()
    doc.open(image, (W, H), str(root))
    return doc


def test_a_loaded_file_that_differs_is_unsaved_until_saved(folder):
    root, image = folder
    _file(root / "skeletons" / "a_fake.png", row=30)
    other = _file(root / "elsewhere" / "x.png", row=90)
    doc = _document(root, image)

    assert doc.load_file(other) == []

    assert doc.source == LOADED and doc.is_dirty()
    assert doc.status() == LayerStatus("dirty")
    result = doc.save()
    assert result.ok and not doc.is_dirty() and doc.source == CORRECTED
    written = cv2.imread(result.path, cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(written, doc.model.mask) and written[90].any()


def test_discard_after_a_load_reads_the_start_file_again(folder):
    root, image = folder
    _file(root / "skeletons" / "a_fake.png", row=30)
    doc = _document(root, image)
    opened = doc.model.mask.copy()
    doc.load_file(_file(root / "elsewhere" / "x.png", row=90))

    doc.reopen()  # the prompt's Discard

    assert not doc.is_dirty() and doc.source == GENERATED
    assert np.array_equal(doc.model.mask, opened)


def test_a_loaded_copy_of_the_open_skeleton_stays_clean(folder):
    root, image = folder
    generated = _file(root / "skeletons" / "a_fake.png", row=30)
    doc = _document(root, image)

    doc.load_file(_file(root / "elsewhere" / "copy.png", row=30))
    assert not doc.is_dirty() and doc.status() == LayerStatus("none", "Loaded file")
    doc.load_file(generated)
    assert not doc.is_dirty() and doc.source == GENERATED


def test_a_file_loaded_over_no_skeleton_is_unsaved(folder):
    root, image = folder
    doc = _document(root, image)  # nothing on disk: an empty skeleton
    assert doc.source is None and not doc.is_dirty()

    doc.load_file(_file(root / "elsewhere" / "x.png", row=90))

    assert doc.is_dirty()


def test_an_unreadable_file_leaves_a_clean_skeleton_clean(folder):
    root, image = folder
    _file(root / "skeletons" / "a_fake.png", row=30)
    doc = _document(root, image)
    bad = root / "bad.png"
    bad.write_bytes(b"not an image")

    doc.load_file(str(bad))

    assert not doc.last_load_ok and not doc.is_dirty() and doc.source == GENERATED


# ----------------------------------------------------------------------------- the editor
@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app, guarded):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    return new_project(tmp_path)


@pytest.fixture
def answers(monkeypatch, guarded):
    return answer_prompts(monkeypatch)


def _load(editor, monkeypatch, path: str) -> None:
    """Load skeleton file... answered with ``path``."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (path, ""))
    editor.controls.load_button.click()


def _other(project, name: str = "x.png", row: int = 20) -> str:
    return write_skeleton(os.path.join(project.folder, "elsewhere", name), row=row)


def _correction(project) -> np.ndarray | None:
    path = project.correction()
    return cv2.imread(path, cv2.IMREAD_GRAYSCALE) if os.path.exists(path) else None


def test_a_loaded_skeleton_makes_the_page_unsaved(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    dirty = record(editor.dirty_changed)

    _load(editor, monkeypatch, _other(project))

    assert raster(editor)[20].any() and editor.has_unsaved_changes()
    assert dirty == [True] and editor.layers[SKELETON].status() == LayerStatus("dirty")


def test_a_tree_click_after_a_load_asks_and_save_writes_the_loaded_skeleton(
    make_editor, project, monkeypatch, answers
):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))
    answers.will(SAVE)

    editor.load_image(project.b, project.folder)

    assert answers.seen == [("Unsaved skeleton", "Save changes to the skeleton for a.png?")]
    saved = _correction(project)
    assert saved is not None and saved[20].any() and not saved[110].any()
    assert os.path.basename(editor.current_image_path) == "b.png"


def test_a_tree_click_after_a_load_answered_discard_writes_nothing(
    make_editor, project, monkeypatch, answers
):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))
    answers.will(DISCARD)

    editor.load_image(project.b, project.folder)

    assert len(answers.seen) == 1 and _correction(project) is None
    assert os.path.basename(editor.current_image_path) == "b.png"


def test_a_tree_click_after_a_load_answered_cancel_stays_with_it(
    make_editor, project, monkeypatch, answers
):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))
    answers.will(CANCEL)

    editor.load_image(project.b, project.folder)

    assert os.path.basename(editor.current_image_path) == "a.png"
    assert raster(editor)[20].any() and editor.has_unsaved_changes()


def test_leaving_the_stage_after_a_load_asks_first(make_editor, project, monkeypatch, answers):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))

    answers.will(CANCEL)
    assert editor.maybe_save_changes() is False
    answers.will(DISCARD)
    assert editor.maybe_save_changes() is True

    assert _correction(project) is None and not editor.has_unsaved_changes()
    assert raster(editor)[110].any() and not raster(editor)[20].any()  # the generated file


def test_paging_after_a_load_saves_it_as_the_correction(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))
    moved = record(editor.navigate_requested)

    editor.navigate(1)  # PgDn saves every unsaved layer, no prompt

    assert moved == [1]
    saved = _correction(project)
    assert saved is not None and saved[20].any()


def test_load_with_a_polyline_draft_asks_first_and_discard_drops_the_draft(
    make_editor, project, monkeypatch, answers
):
    editor = make_editor(project.a, project.folder)
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in ((40, 60), (150, 70)):
        editor.router.canvas_press(QPoint(*point), Mouse())
        editor.router.canvas_release(QPoint(*point), Mouse())
    answers.will(DISCARD)

    _load(editor, monkeypatch, _other(project))

    assert answers.seen == [("Unsaved skeleton", "Save changes to the skeleton for a.png?")]
    assert editor.layers[SKELETON].draft_state().points == 0
    assert raster(editor)[20].any() and not raster(editor)[60:71, 40:151].any()


def test_loading_again_asks_about_the_loaded_skeleton(make_editor, project, monkeypatch, answers):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    _load(editor, monkeypatch, _other(project))
    answers.will(DISCARD)

    _load(editor, monkeypatch, _other(project, "y.png", row=60))

    assert answers.seen == [("Unsaved skeleton", "Save changes to the skeleton for a.png?")]
    assert raster(editor)[60].any() and not raster(editor)[20].any()
    assert editor.has_unsaved_changes()

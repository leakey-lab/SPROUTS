"""The layers' read-ahead hooks (``ReadAheadLayer``: ``read_ahead`` + ``open(ahead=)``) open
exactly the documents a plain open gives, and never a stale one.

The hooks belong to the mask and skeleton layers (cross-package requests of the neighbour
read-ahead, KNOWN_ISSUES #6); until a layer has them its cases are skipped.
"""

from __future__ import annotations

import os
import threading

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.gui.correct.layer import MASK, SKELETON, ReadAheadLayer, missing_members  # noqa: E402
from app.gui.skeleton_graph_model import SkeletonCorrectionModel  # noqa: E402
from app.masks import MaskStore  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    bump,
    editor_factory,
    install_guards,
    new_project,
    plane,
    qapp,
    raster,
    write_mask,
    write_skeleton,
)
from tests.test_correct_editor_paging import read_ahead_done, ring  # noqa: E402

W, H = 200, 150


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    project = new_project(tmp_path)
    box = np.zeros((H, W), np.uint8)
    box[60:120, 20:90] = 1
    write_mask(project.mask("b"), box)  # b gets a mask and a skeleton too
    write_skeleton(project.generated("b"), row=40)
    return project


def hooked(editor, layer_id: str):
    layer = editor.layers[layer_id]
    if missing_members(layer, ReadAheadLayer):
        pytest.skip(f"the {layer_id} layer has no read-ahead hook yet")
    return layer


def documents(editor) -> tuple:
    skeleton = editor.layers[SKELETON]
    return (
        plane(editor).copy(),
        raster(editor).copy(),
        list(skeleton.document.model.endpoints()),
        skeleton.document.source,
        editor.layers[MASK].status(),
        skeleton.status(),
    )


def opened_plainly(make_editor, project, image) -> tuple:
    editor = make_editor(image, project.folder)
    return documents(editor)


@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_a_layer_opened_from_its_read_ahead_is_the_plain_open(
    make_editor, project, layer_id, monkeypatch
):
    plain = opened_plainly(make_editor, project, project.b)
    editor = make_editor()
    hooked(editor, layer_id)
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    gui, work = threading.current_thread(), []
    readers = {MASK: (MaskStore, "read"), SKELETON: (SkeletonCorrectionModel, "load_from_raster")}
    owner, name = readers[layer_id]
    real = getattr(owner, name)

    def spy(*args, **kwargs):
        if threading.current_thread() is gui:
            work.append(name)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, spy)

    editor.load_image(project.b, project.folder)

    assert work == []  # read on the worker, not again on the GUI thread
    assert _same(documents(editor), plain)
    assert editor.session.journal.undo_entries == () and not editor.has_unsaved_changes()


def _same(got: tuple, want: tuple) -> bool:
    return (
        np.array_equal(got[0], want[0]) and np.array_equal(got[1], want[1]) and got[2:] == want[2:]
    )


@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_a_layer_file_changed_after_its_read_ahead_is_read_again(make_editor, project, layer_id):
    editor = make_editor()
    hooked(editor, layer_id)
    editor.set_neighbour_provider(ring(project.a, project.b))
    editor.load_image(project.a, project.folder)
    read_ahead_done(editor)
    other = np.zeros((H, W), np.uint8)
    other[5:25, 5:45] = 1
    if layer_id == MASK:
        write_mask(project.mask("b"), other)
        bump(project.mask("b"))
    else:
        write_skeleton(project.generated("b"), row=130)
        bump(project.generated("b"))

    editor.load_image(project.b, project.folder)

    if layer_id == MASK:
        assert np.array_equal(plane(editor), other)
    else:
        assert raster(editor)[130].any() and not raster(editor)[40].any()


def test_the_read_ahead_readers_touch_no_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    for layer_id in (MASK, SKELETON):
        layer = hooked(editor, layer_id)
        before = documents(editor)
        reader = layer.read_ahead(project.b, project.folder)
        payload = reader((W, H))
        assert payload is not None
        after = documents(editor)
        assert _same(after, before), layer_id  # the open image is untouched

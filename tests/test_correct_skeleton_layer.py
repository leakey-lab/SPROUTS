"""SkeletonLayer: the skeleton of the Correct workspace as an edit layer (plan P5b, C14).

The layer-level port of ``test_skeleton_editor_lifecycle.py`` and
``test_skeleton_editor_tools.py`` (both go with the old editor at the swap), plus the
Correct rules: one ``layer_edited`` per action and no ``layer_changed`` per drag frame,
undo / redo steps that drop a polyline draft, the mid-gesture rule, the reference-layer
view (dimmed, markers and previews hidden), the z-order under the decorations and the
item update through ``take_changed_box``. Offscreen, with a recording ``FakeHost``.
"""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPainterPath, QTransform  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QMessageBox,
    QStyleOptionGraphicsItem,
)
from skimage.morphology import skeletonize  # noqa: E402

from app.gui import skeleton_tools  # noqa: E402
from app.gui.canvas_layers.skeleton import SkeletonLayerItem  # noqa: E402
from app.gui.correct.layer import (  # noqa: E402
    LayerStatus,
    Notice,
    SaveOutcome,
    ToolSpec,
    missing_members,
)
from app.gui.correct.skeleton_layer import DraftState, SkeletonLayer  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from tests.correct_fakes import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NO_MOD = Qt.KeyboardModifier.NoModifier
W, H = 640, 480
LINE = [(100, 540, 240)]  # (x0, x1, y): today's test line, row 240, x 100..539
GAP = [(100, 200, 100), (260, 360, 100)]  # endpoints (199, 100) and (260, 100) face each other
EMPTY_HINT = (
    "No skeleton yet · Generate, Load one, or draw with Polyline (P)",
    "Generate skeletons first, open a file with Load (dock), or draw the roots with the "
    "Polyline tool.",
)


# ---------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_message_boxes(monkeypatch):
    def refuse(box):
        raise AssertionError(f"unexpected message box: {box.text()}")

    monkeypatch.setattr(QMessageBox, "exec", refuse)


@pytest.fixture()
def scene(app):
    return QGraphicsScene()


@pytest.fixture()
def host():
    return FakeHost()


@pytest.fixture()
def project(tmp_path):
    image = str(tmp_path / "a.png")
    assert cv2.imwrite(image, np.full((H, W, 3), 90, np.uint8))
    return str(tmp_path), image


def _write_raster(root, relpath, segments=()):
    raster = np.zeros((H, W), np.uint8)
    for x0, x1, y in segments:
        raster[y, x0:x1] = 255
    path = os.path.join(root, *relpath.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(path, raster)
    return path


def _open(host, scene, project, segments=(), **kwargs) -> SkeletonLayer:
    """A layer on ``scene`` with the image of ``project`` open (its generated skeleton =
    ``segments``); the host's call log starts empty."""
    root, image = project
    if segments:
        _write_raster(root, "skeletons/a_fake.png", segments)
    layer = SkeletonLayer(host, scene, **kwargs)
    layer.open(image, (W, H), root)
    host.calls.clear()
    return layer


@pytest.fixture()
def layer(host, scene, project):
    layer = _open(host, scene, project, LINE)
    yield layer
    layer.cancel_scheduled_render()


@pytest.fixture()
def gap_layer(host, scene, project):
    layer = _open(host, scene, project, GAP)
    yield layer
    layer.cancel_scheduled_render()


# ----------------------------------------------------------------------------- helpers
def _click(layer, x, y, modifiers=NO_MOD):
    layer.press(QPoint(x, y), modifiers)
    layer.release(QPoint(x, y))


def _drag(layer, points, modifiers=NO_MOD):
    first, *rest = [QPoint(x, y) for x, y in points]
    layer.press(first, modifiers)
    for point in rest:
        layer.move(point, True)
    layer.release(rest[-1] if rest else first)


def _key(key, modifiers=NO_MOD):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def _commit(layer, a, b):
    """One Polyline action from ``a`` to ``b`` (image pixels)."""
    layer.set_tool("polyline")
    _click(layer, *a)
    _click(layer, *b)
    layer.finish_polyline()


def _count(host, name):
    return sum(1 for call in host.calls if call[0] == name)


def _thin(mask):
    """scikit-image's thinning of ``mask`` as uint8 0/255 (independent of the model)."""
    return skeletonize(mask > 0).astype(np.uint8) * 255


def _wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        QTest.qWait(5)
    return condition()


def _paint(item):
    """``item`` painted 1:1 over transparency (its exact pixels at device scale 1)."""
    rect = item.boundingRect()
    image = QImage(int(rect.width()), int(rect.height()), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = rect
    item.paint(painter, option, None)
    painter.end()
    return image


def _render(scene, source, zoom):
    """``source`` (scene px) of ``scene`` rendered ``zoom`` times over the canvas colour."""
    image = QImage(
        int(source.width() * zoom), int(source.height() * zoom), QImage.Format.Format_RGB32
    )
    image.fill(QColor(tokens.BG_0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(painter, QRectF(image.rect()), source)
    painter.end()
    return image


@pytest.fixture()
def tool_calls(monkeypatch):
    """Every move / release any skeleton tool receives, as ``(tool_id, method)``."""
    calls = []
    for cls in (
        skeleton_tools.SelectTool,
        skeleton_tools.EraserTool,
        skeleton_tools.PolylineTool,
        skeleton_tools.ConnectTool,
    ):
        for name in ("move", "release"):
            real = getattr(cls, name)

            def spy(self, pos, _real=real, _name=name):
                calls.append((self.tool_id, _name))
                return _real(self, pos)

            monkeypatch.setattr(cls, name, spy)
    return calls


# ---------------------------------------------------------------------------- contract
def test_the_layer_implements_edit_layer_and_the_tool_host(layer):
    assert missing_members(layer) == []
    assert missing_members(layer, skeleton_tools.ToolHost) == []
    assert (layer.layer_id, layer.noun, layer.color) == ("skeleton", "skeleton", tokens.SKEL)


def test_the_rail_tools_are_the_skeleton_tools_and_only_the_eraser_is_sized(layer):
    assert SkeletonLayer.tools == (
        ToolSpec("select", "cursor", "Select", "V", "pick a branch, Delete removes it", False),
        ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True),
        ToolSpec("polyline", "polyline", "Polyline", "P", "Enter finish · Esc cancel", False),
        ToolSpec("connect", "link", "Connect endpoints", "C", "drag from an endpoint", False),
    )
    for spec in SkeletonLayer.tools:
        layer.set_tool(spec.tool_id)
        assert layer.current_tool == spec.tool_id
        assert (layer.size_spec() is not None) == spec.sized


def test_the_eraser_size_spec_is_the_diameter_range(layer):
    layer.set_tool("eraser")
    spec = layer.size_spec()
    assert (spec.minimum, spec.maximum) == (1, 80)


def test_the_item_sits_on_the_scene_below_every_decoration(layer, scene):
    assert isinstance(layer.item, SkeletonLayerItem)
    assert layer.item.scene() is scene
    assert layer.item.zValue() == 1.5
    deco = layer.decorations
    decorations = (
        deco.polyline_item,
        deco.handles_item,
        deco.endpoints_item,
        deco.highlight_item,
        deco.connect_item,
    )
    assert all(item.zValue() > layer.item.zValue() for item in decorations)
    assert layer.eraser_ring.inner.zValue() > max(item.zValue() for item in decorations)


# ---------------------------------------------------------------------- document glue
def test_open_shows_the_generated_skeleton_clean(host, scene, project):
    root, image = project
    _write_raster(root, "skeletons/a_fake.png", LINE)
    layer = SkeletonLayer(host, scene)

    assert layer.open(image, (W, H), root) == []

    assert layer.is_open() and not layer.is_dirty()
    assert layer.document.source == "generated"
    assert layer.document.model.mask[240, 100:540].all()
    assert layer.item.boundingRect() == QRectF(0, 0, W, H)
    assert layer.status() == LayerStatus("none", "Generated", "Not corrected yet")
    assert host.calls == [("layer_changed", "skeleton")]  # once, even for a new layer


def test_the_resolver_reaches_the_document(host, scene, project):
    root, image = project
    first = _write_raster(root, "out/a_fake.png", [(50, 150, 30)])
    layer = SkeletonLayer(host, scene, resolver=lambda _image: first)
    layer.open(image, (W, H), root)
    assert layer.document.model.mask[30, 50:150].all()

    second = _write_raster(root, "out2/a_fake.png", [(50, 150, 60)])
    layer.set_resolver(lambda _image: second)
    layer.revert()

    assert layer.document.model.mask[60, 50:150].all()
    assert not layer.document.model.mask[30].any()


def test_an_unreadable_skeleton_opens_empty_with_a_danger_notice(host, scene, project):
    root, image = project
    bad = os.path.join(root, "skeletons", "a_fake.png")
    os.makedirs(os.path.dirname(bad))
    with open(bad, "wb") as fh:
        fh.write(b"not an image")
    layer = SkeletonLayer(host, scene)

    assert layer.open(image, (W, H), root) == [
        Notice("Could not read skeleton a_fake.png", "danger")
    ]
    assert not layer.document.model.mask.any()


def test_hints_are_todays_including_the_empty_skeleton_hint(host, scene, project):
    layer = SkeletonLayer(host, scene)
    assert layer.tool_hint() == ("Select an image to correct its skeleton.", "")

    root, image = project
    layer.open(image, (W, H), root)
    assert layer.tool_hint() == EMPTY_HINT
    assert layer.status() == LayerStatus("none", "No skeleton")

    layer.set_tool("polyline")
    assert layer.tool_hint()[0] == (
        "Polyline · click to place points · Ctrl+click a segment adds a bend"
    )


def test_the_hint_follows_the_current_tool(gap_layer):
    gap_layer.set_tool("eraser")
    assert gap_layer.tool_hint()[0] == "Eraser · drag over the skeleton to erase it"

    gap_layer.set_tool("connect")
    assert gap_layer.tool_hint()[0] == "Connect · drag from an endpoint (orange dot)"
    gap_layer.press(QPoint(199, 100), NO_MOD)
    assert gap_layer.tool_hint()[0] == "Connect · release on the point to join"


def test_close_empties_the_layer(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)

    layer.close()

    assert not layer.is_open() and not layer.is_dirty()
    assert layer.item.boundingRect().isEmpty()
    assert layer.file_stamps() == ()
    assert not layer.can_undo()
    assert layer.draft.points == []
    assert layer.decorations.endpoints_item.path().isEmpty()


def test_revert_rereads_the_start_file_and_clears_the_history(layer):
    start = layer.document.model.mask.copy()
    _commit(layer, (50, 50), (200, 100))
    assert layer.is_dirty()

    assert layer.revert() == []

    assert np.array_equal(layer.document.model.mask, start)
    assert not layer.is_dirty() and not layer.can_undo()


def test_save_writes_the_correction_and_reports_its_path(layer, host, project):
    root, _image = project
    _commit(layer, (50, 50), (200, 100))
    host.calls.clear()

    outcome = layer.save()

    expected = os.path.join(root, "skeleton_corrections", "a_skeleton.png")
    assert outcome.ok and os.path.isfile(expected)
    assert os.path.normcase(os.path.abspath(outcome.path)) == os.path.normcase(expected)
    assert outcome == SaveOutcome("skeleton", True, path=outcome.path)
    assert not layer.is_dirty()
    assert layer.status().state == "clean"
    assert ("layer_changed", "skeleton") in host.calls


def test_a_failed_save_names_the_file_keeps_the_old_one_and_stays_dirty(
    layer, project, monkeypatch
):
    root, _image = project
    old = _write_raster(root, "skeleton_corrections/a_skeleton.png", [(10, 20, 5)])
    before = Path(old).read_bytes()
    _commit(layer, (50, 50), (200, 100))

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    outcome = layer.save()
    monkeypatch.undo()

    assert outcome == SaveOutcome(
        "skeleton", False, message="Could not save a_skeleton.png: disk full", kind="danger"
    )
    assert Path(old).read_bytes() == before
    assert not [f for f in os.listdir(os.path.dirname(old)) if f.endswith(".tmp")]
    assert layer.is_dirty()
    assert layer.status().state == "error"


def test_an_unwritable_corrections_folder_does_not_raise(layer, monkeypatch):
    _commit(layer, (50, 50), (200, 100))

    def deny(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", deny)
    assert layer.save().ok is False


def test_save_without_a_loaded_raster_uses_the_configured_output_size(host, scene, project):
    from app.config import SKELETON_OUTPUT_SIZE

    layer = _open(host, scene, project)  # nothing on disk: no raster size to keep
    _commit(layer, (50, 50), (200, 100))

    outcome = layer.save()

    saved = cv2.imread(outcome.path, cv2.IMREAD_GRAYSCALE)
    assert (saved.shape[1], saved.shape[0]) == tuple(SKELETON_OUTPUT_SIZE)
    assert set(np.unique(saved)) <= {0, 255}


def test_file_stamps_are_the_documents_read_afresh(layer):
    assert layer.file_stamps() == layer.document.file_stamps()
    assert layer.file_stamps()[0][1] is None  # no correction yet

    _commit(layer, (50, 50), (200, 100))
    layer.save()

    assert layer.file_stamps()[0][1] is not None  # our own save wrote the correction


def test_load_file_replaces_the_skeleton_and_forgets_its_history(layer, project):
    root, _image = project
    other = _write_raster(root, "elsewhere/x.png", [(30, 90, 400)])
    _commit(layer, (50, 50), (200, 100))

    assert layer.load_file(other) == []

    mask = layer.document.model.mask
    assert mask[400, 30:90].all() and not mask[240].any()
    assert not layer.can_undo() and not layer.is_dirty()
    assert layer.document.source == "loaded"


def test_an_unreadable_file_to_load_changes_nothing(layer, project):
    root, _image = project
    bad = os.path.join(root, "broken.png")
    Path(bad).write_bytes(b"not an image")
    before = layer.document.model.mask.copy()

    assert layer.load_file(bad) == [Notice("Could not read skeleton broken.png", "danger")]
    assert np.array_equal(layer.document.model.mask, before)


def test_dialog_start_is_the_generated_file(layer, host, scene, project):
    root, _image = project
    generated = os.path.join(root, "skeletons", "a_fake.png")
    assert os.path.normcase(os.path.abspath(layer.dialog_start())) == os.path.normcase(generated)
    assert SkeletonLayer(host, scene).dialog_start() == ""


def test_a_closed_layer_ignores_input(host, scene):
    layer = SkeletonLayer(host, scene)
    _click(layer, 10, 10)
    layer.move(QPoint(20, 20), True)
    layer.double_click(QPoint(20, 20))

    assert layer.key(_key(Qt.Key.Key_Delete)) is False
    assert layer.clear() is False
    assert not layer.can_undo() and layer.undo() is False
    assert _count(host, "layer_edited") == 0


# ------------------------------------------------------------------------------ tools
def test_set_tool_deactivates_the_old_tool_and_resets_the_draft(layer, host):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 150, 60)
    host.calls.clear()

    layer.set_tool("connect")

    assert layer.current_tool == "connect"
    assert layer.draft.points == []
    assert layer.decorations.polyline_item.path().isEmpty()
    assert _count(host, "layer_changed") == 1


def test_select_hands_the_picked_branch_to_polyline_keeping_the_draft(layer, host):
    layer.set_tool("select")
    host.calls.clear()

    _click(layer, 300, 242)

    assert layer.current_tool == "polyline"
    assert layer.draft.edit_original is not None
    assert sorted((p.x(), p.y()) for p in layer.draft.points) == [(100, 240), (539, 240)]
    assert layer.draft_state() == DraftState(True, 2, False)
    assert ("layer_changed", "skeleton") in host.calls
    path = layer.decorations.polyline_item.path()  # the click's release reached Polyline,
    assert path.elementCount() == 3  # as today: a rubber band from the branch to the pointer
    assert (path.elementAt(2).x, path.elementAt(2).y) == (300.0, 242.0)


def test_the_tool_host_geometry_follows_the_open_skeleton(layer, host, scene):
    assert layer.image_size() == (W, H)
    assert layer.clamp_to_image(QPoint(-5, 900)) == QPoint(0, H - 1)
    assert layer.clamp_to_image(QPoint(700, 20)) == QPoint(W - 1, 20)
    closed = SkeletonLayer(host, scene)
    assert closed.image_size() == (0, 0)
    assert closed.clamp_to_image(QPoint(5, 5)) == QPoint(0, 0)


def test_hover_moves_reach_the_current_tool(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)

    layer.move(QPoint(120, 90), False)

    path = layer.decorations.polyline_item.path()
    assert path.elementCount() == 2
    assert (path.elementAt(1).x, path.elementAt(1).y) == (120.0, 90.0)


# ----------------------------------------------------------------------------- eraser
def test_a_fast_eraser_drag_leaves_no_fragments(layer):
    layer.set_tool("eraser")
    layer.set_size(6)

    _drag(layer, [(120, 240), *((x, 240) for x in range(145, 521, 25))])

    assert int((layer.document.model.mask[240, 120:521] > 0).sum()) == 0


def test_eraser_drag_repaints_once_per_frame_and_its_release_thins_and_renders(
    layer, host, monkeypatch
):
    band = np.zeros((H, W), np.uint8)
    band[238:243, 100:540] = 255  # 5 px thick: the release has something to thin
    layer.document.model.mask = band
    layer.render_skeleton()
    renders = []
    real_set_mask = layer.item.set_mask

    def spy(mask, **kwargs):
        renders.append(kwargs.get("changed"))
        real_set_mask(mask, **kwargs)

    monkeypatch.setattr(layer.item, "set_mask", spy)
    markers = layer.decorations.endpoints_item
    layer.set_tool("eraser")
    layer.set_size(6)
    host.calls.clear()

    layer.press(QPoint(120, 240), NO_MOD)
    for x in range(125, 300, 5):
        layer.move(QPoint(x, 240), True)

    assert len(renders) == 1  # the press draws at once, the moves only schedule a frame
    assert not markers.isVisible()  # hidden while erasing, as today
    assert _wait_for(lambda: len(renders) == 2)  # the last moves show without a release
    assert len(renders) == 2  # one frame for all the moves
    assert _count(host, "layer_changed") == 0  # drag frames never reach the host

    layer.release(QPoint(295, 240))

    assert len(renders) == 3
    assert _count(host, "layer_changed") == 1
    mask = layer.document.model.mask
    assert np.array_equal(mask, _thin(mask))
    assert markers.isVisible()
    assert _count(host, "layer_edited") == 1


def test_the_eraser_ring_is_the_footprint_at_one_and_four_times(layer, scene):
    view = QGraphicsView(scene)
    view.resize(900, 700)
    layer.set_tool("eraser")
    layer.set_size(20)
    for zoom in (1.0, 4.0):
        view.setTransform(QTransform.fromScale(zoom, zoom))
        layer.move(QPoint(300, 200), False)
        ring = layer.eraser_ring
        assert ring.is_visible()
        assert ring.rect().width() == pytest.approx(20)
        assert ring.rect().center() == QPointF(300.5, 200.5)
        on_screen = view.mapFromScene(ring.rect()).boundingRect().width()
        assert on_screen == pytest.approx(20 * zoom, abs=2)
    assert layer.eraser_footprint_radius() == 10


def test_the_eraser_ring_hides_for_other_tools_while_panning_and_when_inactive(layer, host):
    ring = layer.eraser_ring
    layer.set_tool("eraser")
    layer.move(QPoint(300, 200), False)
    assert ring.is_visible()

    host.panning = True
    layer.hover(QPoint(310, 200))
    assert not ring.is_visible()
    host.panning = False
    layer.hover(QPoint(310, 200))
    assert ring.is_visible()

    layer.set_active(False)
    assert not ring.is_visible()
    layer.hover(QPoint(300, 200))
    assert not ring.is_visible()
    layer.set_active(True)

    layer.hover(QPoint(300, 200))
    layer.hover(None)
    assert not ring.is_visible()

    layer.set_tool("polyline")
    layer.move(QPoint(300, 200), False)
    assert not ring.is_visible()


def test_the_eraser_ring_is_dashed_in_the_skeleton_colour(layer):
    pen = layer.eraser_ring.inner.pen()
    assert pen.style() == Qt.PenStyle.DashLine
    assert pen.color() == QColor(tokens.SKEL)


def test_eraser_size_steps_by_twelve_percent_at_least_one_px_and_clamps(layer, host):
    assert layer.size() == 20
    layer.step_size(1)
    assert layer.size() == 22
    layer.set_size(20)
    layer.step_size(-1)
    assert layer.size() == 18
    layer.set_size(3)
    layer.step_size(1)
    assert layer.size() == 4
    layer.set_size(50)
    layer.step_size(1)
    assert layer.size() == 56
    layer.set_size(0)
    assert layer.size() == 1
    layer.step_size(-1)
    assert layer.size() == 1
    layer.set_size(500)
    assert layer.size() == 80
    host.calls.clear()
    layer.step_size(1)
    assert layer.size() == 80
    assert _count(host, "layer_changed") == 0  # nothing changed


def test_the_ring_and_the_footprint_follow_the_size(layer, host):
    layer.set_tool("eraser")
    layer.hover(QPoint(300, 200))
    host.calls.clear()

    layer.set_size(30)

    assert layer.eraser_ring.rect().width() == pytest.approx(30)
    assert layer.eraser_footprint_radius() == 15
    assert _count(host, "layer_changed") == 1  # the dock slider follows


# ------------------------------------------------------------------- select and delete
def test_delete_removes_the_picked_branch_and_undo_restores_it(layer, host):
    before = layer.document.model.mask.copy()
    layer.set_tool("select")
    _click(layer, 300, 242)

    assert layer.key(_key(Qt.Key.Key_Delete)) is True

    assert not layer.document.model.mask[240, 110:530].any()
    assert layer.draft.points == []
    assert ("notify", "Branch deleted · Ctrl+Z to undo", "info") in host.calls
    assert layer.undo() is True
    assert np.array_equal(layer.document.model.mask, before)


def test_delete_with_nothing_picked_is_a_no_op(layer):
    before = layer.document.model.mask.copy()

    assert layer.key(_key(Qt.Key.Key_Delete)) is False
    assert np.array_equal(layer.document.model.mask, before)


# ---------------------------------------------------------------------------- polyline
def _new_line_drawn(layer):
    return bool(layer.document.model.mask[45:66, 50:201].any())


def test_enter_commits_the_polyline(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 200, 60)
    assert not _new_line_drawn(layer)

    assert layer.key(_key(Qt.Key.Key_Return)) is True

    assert _new_line_drawn(layer)
    assert layer.draft.points == []


def test_a_double_click_commits_the_polyline(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)

    layer.double_click(QPoint(200, 60))

    assert _new_line_drawn(layer)
    assert layer.draft.points == []


def test_finish_polyline_needs_the_polyline_tool_and_two_points(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    layer.finish_polyline()
    assert not _new_line_drawn(layer) and len(layer.draft.points) == 1

    _click(layer, 200, 60)
    layer.finish_polyline()

    assert _new_line_drawn(layer) and layer.draft.points == []


@pytest.mark.parametrize("cancel", ["cancel_transient", "cancel_polyline"])
def test_escape_and_cancel_drop_the_draft(layer, cancel):
    before = layer.document.model.mask.copy()
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 200, 60)

    getattr(layer, cancel)()

    assert layer.draft.points == []
    assert layer.decorations.polyline_item.path().isEmpty()
    assert np.array_equal(layer.document.model.mask, before)


def test_s_toggles_smooth_only_in_polyline(layer):
    layer.set_tool("select")
    assert layer.key(_key(Qt.Key.Key_S)) is False
    assert not layer.draft.smooth

    layer.set_tool("polyline")
    assert layer.key(_key(Qt.Key.Key_S)) is True
    assert layer.draft.smooth and layer.draft_state().smooth
    assert layer.key(_key(Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)) is False
    assert layer.draft.smooth


def test_set_smooth_redraws_the_preview_and_reports_the_draft(layer, host):
    layer.set_tool("polyline")
    for x, y in ((50, 50), (120, 90), (200, 60)):
        _click(layer, x, y)
    host.calls.clear()

    layer.set_smooth(True)

    assert layer.draft_state() == DraftState(True, 3, True)
    path = layer.decorations.polyline_item.path()
    kinds = {path.elementAt(i).type for i in range(path.elementCount())}
    assert QPainterPath.ElementType.CurveToElement in kinds
    assert _count(host, "layer_changed") == 1


def test_undo_in_gesture_pops_draft_points_and_nothing_else(layer):
    before = layer.document.model.mask.copy()
    layer.set_tool("polyline")
    for x, y in ((50, 50), (120, 90), (200, 60)):
        _click(layer, x, y)

    assert layer.undo_in_gesture() is True
    assert [(p.x(), p.y()) for p in layer.draft.points] == [(50, 50), (120, 90)]
    assert layer.undo_in_gesture() and layer.undo_in_gesture()
    assert layer.draft.points == []
    assert layer.undo_in_gesture() is False
    assert np.array_equal(layer.document.model.mask, before)
    assert not layer.can_undo()


def test_draft_state_reports_the_tool_the_points_and_smooth(layer):
    assert layer.draft_state() == DraftState(False, 0, False)
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    assert layer.draft_state() == DraftState(True, 1, False)


# ------------------------------------------------------------------------------ connect
def test_a_connect_drag_joins_two_endpoints(gap_layer, host):
    gap_layer.set_tool("connect")

    _drag(gap_layer, [(199, 100), (230, 100), (260, 100)])

    assert gap_layer.document.model.mask[100, 200:260].all()
    assert _count(host, "layer_edited") == 1


# -------------------------------------------------------------------------------- clear
def test_clear_is_undoable_and_posts_the_cleared_notice(layer, host):
    assert layer.clear() is True

    assert not layer.document.model.mask.any()
    assert ("notify", "Skeleton cleared. Ctrl+Z to undo.", "info") in host.calls
    assert layer.undo() is True
    assert layer.document.model.mask[240, 100:540].all()


# ------------------------------------------------------------------------ host signals
@pytest.mark.parametrize("action", ["polyline", "eraser", "connect", "delete", "clear"])
def test_each_action_is_reported_once_as_layer_edited(host, scene, project, action):
    layer = _open(host, scene, project, [*LINE, *GAP])
    if action == "polyline":
        _commit(layer, (50, 300), (200, 360))
    elif action == "eraser":
        layer.set_tool("eraser")
        _drag(layer, [(120, 240), (160, 240), (200, 240)])
    elif action == "connect":
        layer.set_tool("connect")
        _drag(layer, [(199, 100), (260, 100)])
    elif action == "delete":
        layer.set_tool("select")
        _click(layer, 300, 242)
        layer.key(_key(Qt.Key.Key_Delete))
    else:
        layer.clear()

    edits = [call for call in host.calls if call[0] == "layer_edited"]
    assert edits == [("layer_edited", "skeleton")]
    layer.cancel_scheduled_render()


def test_history_steps_and_document_calls_are_not_edits(layer, host, project):
    root, image = project
    _commit(layer, (50, 50), (200, 100))
    host.calls.clear()

    layer.undo()
    layer.redo()
    layer.save()
    layer.revert()
    layer.open(image, (W, H), root)
    layer.close()

    assert _count(host, "layer_edited") == 0


@pytest.mark.parametrize(
    "call",
    [
        "set_tool",
        "add_point",
        "commit",
        "undo",
        "redo",
        "save",
        "save_again",
        "revert",
        "close",
        "drop_redo",
    ],
)
def test_ui_changes_notify_the_host_once_per_call(layer, host, call):
    _commit(layer, (50, 50), (200, 100))
    _commit(layer, (50, 400), (200, 420))
    if call in ("redo", "drop_redo"):
        layer.undo()
    if call == "commit":
        _click(layer, 300, 300)
        _click(layer, 400, 300)
    if call == "save_again":  # the source stays "corrected": only the dirty state changes
        layer.save()
        _commit(layer, (300, 300), (400, 320))
    host.calls.clear()

    {
        "set_tool": lambda: layer.set_tool("eraser"),
        "add_point": lambda: _click(layer, 300, 300),
        "commit": layer.finish_polyline,
        "undo": layer.undo,
        "redo": layer.redo,
        "save": layer.save,
        "save_again": layer.save,
        "revert": layer.revert,
        "close": layer.close,
        "drop_redo": layer.drop_redo,
    }[call]()

    assert _count(host, "layer_changed") == 1


def test_escape_during_a_connect_drag_refreshes_the_hint(gap_layer, host):
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    assert gap_layer.tool_hint()[0] == "Connect · release on the point to join"
    host.calls.clear()

    gap_layer.cancel_transient()

    assert gap_layer.tool_hint()[0] == "Connect · drag from an endpoint (orange dot)"
    assert _count(host, "layer_changed") == 1  # the chip's hint is stale otherwise


def test_a_hover_that_changes_nothing_the_ui_shows_is_silent(layer, host):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    host.calls.clear()

    for x in range(60, 200, 10):
        layer.move(QPoint(x, 80), False)

    assert host.calls == []


# ------------------------------------------------------------------------- undo / redo
def test_undo_and_redo_step_one_snapshot_and_report_it(layer):
    model = layer.document.model
    start = model.mask.copy()
    _commit(layer, (50, 50), (200, 100))
    first = model.mask.copy()
    _commit(layer, (50, 400), (200, 420))
    second = model.mask.copy()

    assert layer.undo() is True and np.array_equal(model.mask, first)
    assert layer.undo() is True and np.array_equal(model.mask, start)
    assert layer.undo() is False and np.array_equal(model.mask, start)
    assert layer.redo() is True and np.array_equal(model.mask, first)
    assert layer.redo() is True and np.array_equal(model.mask, second)
    assert layer.redo() is False


def test_undo_drops_the_whole_draft_instead_of_popping_a_point(layer, host):
    _commit(layer, (50, 50), (200, 100))
    for x, y in ((300, 300), (350, 320), (400, 300)):
        _click(layer, x, y)
    host.calls.clear()

    assert layer.undo() is True

    assert layer.draft.points == []
    assert not [call for call in host.calls if call[0] == "notify"]  # it was on screen


@pytest.mark.parametrize("step", ["undo", "redo"])
def test_a_history_step_that_drops_a_hidden_draft_says_so(layer, host, step):
    _commit(layer, (50, 50), (200, 100))
    if step == "redo":
        layer.undo()
    _click(layer, 300, 300)
    _click(layer, 400, 300)
    layer.set_active(False)
    host.calls.clear()

    assert getattr(layer, step)() is True

    assert layer.draft.points == []
    assert ("notify", "Polyline draft discarded", "info") in host.calls


def test_an_empty_stack_keeps_the_draft(layer, host):
    layer.set_tool("polyline")
    _click(layer, 300, 300)
    _click(layer, 400, 300)
    layer.set_active(False)
    host.calls.clear()

    assert layer.undo() is False
    assert layer.redo() is False

    assert len(layer.draft.points) == 2
    assert host.calls == []


def test_drop_redo_forgets_only_the_redo_branch(layer):
    _commit(layer, (50, 50), (200, 100))
    _commit(layer, (50, 400), (200, 420))
    layer.undo()
    before = layer.document.model.mask.copy()
    assert layer.can_undo() and layer.can_redo()

    layer.drop_redo()

    assert not layer.can_redo() and layer.can_undo()
    assert np.array_equal(layer.document.model.mask, before)


def test_an_undo_mid_drag_finishes_the_drag_first_then_undoes_it(gap_layer):
    start = gap_layer.document.model.mask.copy()
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    gap_layer.move(QPoint(260, 100), True)

    assert gap_layer.undo() is True  # the join was recorded, then stepped back

    assert np.array_equal(gap_layer.document.model.mask, start)
    assert not gap_layer.gesture_active()
    assert gap_layer.redo() is True
    assert gap_layer.document.model.mask[100, 200:260].all()


# ---------------------------------------------------------------- active and visibility
def test_endpoint_markers_are_device_cached_and_shown_only_while_active(layer):
    markers = layer.decorations.endpoints_item
    assert markers.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    assert markers.isVisible() and not markers.path().isEmpty()

    layer.set_active(False)
    assert not markers.isVisible()
    _commit(layer, (50, 50), (200, 100))
    layer.undo()  # a full render of the reference layer keeps them hidden
    assert not markers.isVisible()

    layer.set_active(True)
    assert markers.isVisible()


def test_going_inactive_hides_the_preview_but_keeps_the_draft(layer):
    deco = layer.decorations
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 200, 60)

    layer.set_active(False)

    assert not deco.polyline_item.isVisible() and not deco.handles_item.isVisible()
    assert len(layer.draft.points) == 2 and not layer.is_active()

    layer.set_active(True)

    assert deco.polyline_item.isVisible() and deco.handles_item.isVisible()
    assert not deco.polyline_item.path().isEmpty()
    assert layer.is_active()


def test_the_reference_layer_is_drawn_at_half_its_opacity(layer):
    assert layer.opacity() == pytest.approx(0.85)
    assert layer.item.opacity() == pytest.approx(0.85)

    layer.set_active(False)
    assert layer.item.opacity() == pytest.approx(0.425)
    assert layer.opacity() == pytest.approx(0.85)

    layer.set_opacity(0.5)
    assert layer.item.opacity() == pytest.approx(0.25)
    layer.set_active(True)
    assert layer.item.opacity() == pytest.approx(0.5)

    layer.set_opacity(2.0)
    assert layer.opacity() == 1.0


def test_hiding_the_layer_hides_the_item_and_its_decorations(layer):
    deco = layer.decorations
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 200, 60)

    layer.set_visible(False)

    assert not layer.is_visible()
    assert not layer.item.isVisible()
    assert not deco.endpoints_item.isVisible()
    assert not deco.polyline_item.isVisible() and not deco.handles_item.isVisible()

    layer.set_visible(True)

    assert layer.item.isVisible() and deco.endpoints_item.isVisible()
    assert deco.polyline_item.isVisible()


# ------------------------------------------------------------------------------ gestures
def test_gesture_active_during_an_eraser_drag(layer):
    layer.set_tool("eraser")
    assert not layer.gesture_active()
    layer.press(QPoint(300, 240), NO_MOD)
    layer.move(QPoint(320, 240), True)
    assert layer.gesture_active()
    layer.release(QPoint(320, 240))
    assert not layer.gesture_active()


def test_gesture_active_during_a_connect_drag_from_an_endpoint(gap_layer):
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(150, 300), NO_MOD)  # no endpoint here: no drag
    assert not gap_layer.gesture_active()
    gap_layer.release(QPoint(150, 300))

    gap_layer.press(QPoint(199, 100), NO_MOD)
    assert gap_layer.gesture_active()
    gap_layer.release(QPoint(260, 100))
    assert not gap_layer.gesture_active()


def test_gesture_active_while_a_polyline_point_or_line_is_dragged(layer):
    layer.set_tool("polyline")
    layer.press(QPoint(50, 50), NO_MOD)
    assert not layer.gesture_active()  # a click that adds a point drags nothing
    layer.release(QPoint(50, 50))
    _click(layer, 200, 60)

    layer.press(QPoint(200, 60), NO_MOD)  # on a point: drag it
    assert layer.gesture_active()
    layer.release(QPoint(210, 70))
    assert not layer.gesture_active()

    layer.press(QPoint(125, 55), Qt.KeyboardModifier.ShiftModifier)  # move the line
    assert layer.gesture_active()
    layer.release(QPoint(130, 60))
    assert not layer.gesture_active()


def test_finish_gesture_releases_an_eraser_drag_once_and_ignores_its_rest(layer, host, tool_calls):
    band = np.zeros((H, W), np.uint8)
    band[238:243, 100:540] = 255
    layer.document.model.mask = band
    layer.render_skeleton()
    layer.set_tool("eraser")
    layer.set_size(6)
    host.calls.clear()
    layer.press(QPoint(150, 240), NO_MOD)
    layer.move(QPoint(170, 240), True)

    layer.finish_gesture()

    mask = layer.document.model.mask
    assert np.array_equal(mask, _thin(mask))  # the release thinned it
    assert not mask[236:245, 147:174].any()
    assert not layer.gesture_active()
    assert _count(host, "layer_edited") == 1
    after = mask.copy()
    tool_calls.clear()

    layer.move(QPoint(400, 240), True)
    layer.release(QPoint(400, 240))

    assert tool_calls == []  # the rest of the drag reaches no tool
    assert np.array_equal(layer.document.model.mask, after)
    assert layer.eraser_ring.rect().center() == QPointF(400.5, 240.5)  # only the ring moved
    assert _count(host, "layer_edited") == 1


def test_finish_gesture_without_a_drag_does_nothing(layer, host):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    host.calls.clear()

    layer.finish_gesture()

    assert host.calls == [] and len(layer.draft.points) == 1


def test_a_tool_change_mid_eraser_drag_releases_it_and_the_rest_reaches_no_tool(
    layer, host, tool_calls
):
    layer.set_tool("eraser")
    layer.press(QPoint(150, 240), NO_MOD)
    layer.move(QPoint(170, 240), True)

    layer.set_tool("polyline")

    assert layer.current_tool == "polyline" and not layer.gesture_active()
    assert not layer.document.model.mask[240, 145:176].any()
    after = layer.document.model.mask.copy()
    tool_calls.clear()

    layer.move(QPoint(250, 240), True)
    layer.move(QPoint(300, 240), True)
    layer.release(QPoint(300, 240))

    assert tool_calls == []
    assert layer.draft.points == []
    assert np.array_equal(layer.document.model.mask, after)
    assert _count(host, "layer_edited") == 1


def test_finish_gesture_joins_a_connect_drag_at_the_last_position(gap_layer, host, tool_calls):
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    gap_layer.move(QPoint(230, 100), True)
    gap_layer.move(QPoint(260, 100), True)

    gap_layer.finish_gesture()

    assert gap_layer.document.model.mask[100, 200:260].all()
    assert _count(host, "layer_edited") == 1
    assert not gap_layer.decorations.connect_item.isVisible()
    tool_calls.clear()
    gap_layer.release(QPoint(300, 100))
    assert tool_calls == []
    assert _count(host, "layer_edited") == 1


def test_escape_during_a_connect_drag_drops_it(gap_layer, host, tool_calls):
    before = gap_layer.document.model.mask.copy()
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    gap_layer.move(QPoint(230, 100), True)

    gap_layer.cancel_transient()

    assert np.array_equal(gap_layer.document.model.mask, before)
    assert _count(host, "layer_edited") == 0
    assert not gap_layer.decorations.connect_item.isVisible()
    tool_calls.clear()
    gap_layer.release(QPoint(260, 100))
    assert tool_calls == [] and _count(host, "layer_edited") == 0


def test_a_dragged_polyline_point_stays_where_it_is_when_the_drag_is_finished(layer):
    layer.set_tool("polyline")
    _click(layer, 50, 50)
    _click(layer, 200, 60)
    layer.press(QPoint(200, 60), NO_MOD)
    layer.move(QPoint(220, 90), True)

    layer.finish_gesture()

    assert layer.draft.points[1] == QPoint(220, 90)
    layer.move(QPoint(300, 300), True)
    layer.release(QPoint(300, 300))
    assert layer.draft.points[1] == QPoint(220, 90)


@pytest.mark.parametrize("transition", ["set_tool", "hide", "save", "revert", "open", "close"])
def test_the_layers_own_transitions_finish_a_drag_first(gap_layer, host, project, transition):
    root, image = project
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    gap_layer.move(QPoint(260, 100), True)

    {
        "set_tool": lambda: gap_layer.set_tool("select"),
        "hide": lambda: gap_layer.set_visible(False),
        "save": gap_layer.save,
        "revert": gap_layer.revert,
        "open": lambda: gap_layer.open(image, (W, H), root),
        "close": gap_layer.close,
    }[transition]()

    assert not gap_layer.gesture_active()
    assert _count(host, "layer_edited") == 1  # the drag was joined, not dropped
    if transition == "save":
        saved = cv2.imread(os.path.join(root, "skeleton_corrections", "a_skeleton.png"), 0)
        assert saved[100, 200:260].all()


def test_a_new_press_first_finishes_a_drag_whose_release_was_lost(gap_layer, host):
    gap_layer.set_tool("connect")
    gap_layer.press(QPoint(199, 100), NO_MOD)
    gap_layer.move(QPoint(230, 100), True)

    gap_layer.press(QPoint(150, 300), NO_MOD)  # the first drag's release never came
    gap_layer.release(QPoint(150, 300))

    mask = gap_layer.document.model.mask
    assert mask[100, 200:226].all()  # joined toward where that drag last was (230, 100)
    assert not mask[250:310, 140:175].any()  # not dragged on to the second press
    assert _count(host, "layer_edited") == 1


# ------------------------------------------------------------------------------ z-order
def test_a_polyline_draft_paints_above_the_skeleton_line_at_800_percent(host, scene, project):
    layer = _open(host, scene, project, [(100, 300, 100)])
    layer.item.EXTRACT_BUDGET_MS = float("inf")  # extract every tile in the first paint
    source = QRectF(180, 80, 40, 40)  # the crossing (200, 100.5) at (160, 164) of the render
    before = QColor(_render(scene, source, 8).pixel(160, 164))
    assert layer.item.mode_painted() == "vector"
    assert before.red() > before.blue()  # the skeleton colour: the line alone

    layer.set_tool("polyline")
    _click(layer, 200, 60)
    _click(layer, 200, 140)
    image = _render(scene, source, 8)

    assert QColor(image.pixel(160, 164)) == QColor(tokens.ACCENT)  # the preview is on top
    beside = QColor(image.pixel(240, 164))  # x = 210: the line only
    assert beside.red() > beside.blue()
    layer.cancel_scheduled_render()


# ------------------------------------------------------------------------- item update
def test_only_the_skeleton_layer_calls_take_changed_box():
    callers = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "take_changed_box":
                callers.add(path.relative_to(ROOT).as_posix())

    assert callers == {"app/gui/correct/skeleton_layer.py"}


def test_the_item_is_updated_with_the_models_changed_boxes(layer, monkeypatch):
    model = layer.document.model
    taken, given = [], []
    real_take = model.take_changed_box
    real_set_mask = layer.item.set_mask

    def take():
        box = real_take()
        taken.append(box)
        return box

    def set_mask(mask, **kwargs):
        given.append((mask is model.mask, kwargs.get("changed", "missing")))
        real_set_mask(mask, **kwargs)

    monkeypatch.setattr(model, "take_changed_box", take)
    monkeypatch.setattr(layer.item, "set_mask", set_mask)
    layer.set_tool("eraser")

    layer.press(QPoint(300, 240), NO_MOD)

    assert given == [(True, taken[0])]
    x, y, w, h = taken[0]  # the erased disc of radius 10 around (300, 240)
    assert x <= 290 and y <= 230 and x + w >= 311 and y + h >= 251

    layer.release(QPoint(300, 240))
    layer.undo()  # an undo swaps the array: the model cannot tell a box

    assert given[-1] == (True, None)
    assert len(given) == len(taken)


def test_the_item_always_shows_the_models_pixels(gap_layer, project, monkeypatch):
    layer = gap_layer
    model = layer.document.model
    steps = [
        lambda: _commit(layer, (50, 300), (200, 360)),
        lambda: layer.set_tool("eraser"),
        lambda: _drag(layer, [(120, 100), (140, 100), (160, 100)]),
        lambda: layer.set_tool("connect"),
        lambda: _drag(layer, [(260, 100), (300, 200)]),
        layer.undo,
        layer.redo,
        layer.clear,
        layer.undo,
    ]
    for step in steps:
        step()
        reference = SkeletonLayerItem()
        reference.set_mask(model.mask.copy())
        assert _paint(layer.item) == _paint(reference)

    frames = []
    real_set_mask = layer.item.set_mask

    def counted(mask, **kwargs):
        frames.append(kwargs.get("changed"))
        real_set_mask(mask, **kwargs)

    monkeypatch.setattr(layer.item, "set_mask", counted)
    layer.set_tool("eraser")
    layer.press(QPoint(120, 328), NO_MOD)  # across the committed polyline
    layer.move(QPoint(140, 336), True)
    assert _wait_for(lambda: len(frames) == 2)  # the coalesced frame of the move drew
    reference = SkeletonLayerItem()
    reference.set_mask(model.mask.copy())
    assert _paint(layer.item) == _paint(reference)
    layer.release(QPoint(140, 336))

    other = _write_raster(project[0], "elsewhere/other.png", [(30, 90, 400)])
    for step in (layer.revert, lambda: layer.load_file(other)):
        step()
        reference = SkeletonLayerItem()
        reference.set_mask(model.mask.copy())
        assert _paint(layer.item) == _paint(reference)

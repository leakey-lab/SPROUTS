"""MaskLayer: the mask as an edit layer of the Correct workspace (C2, C13).

The layer composes the Qt-free ``MaskDocument`` (P4a), the ``PaintCanvas`` painting engine
(P2) and the ``RasterLayerItem`` render item (P1a), with Brush / Eraser / Fill tools and a
brush ring. The oracle for every pixel is a direct ``PaintCanvas`` replay with today's
lookup table, canvas-to-plane and fill-source rules written out here by hand
(``mask_tracing_interface.py:702-712``). A recording ``FakeHost`` stands in for the
workspace, so the signal rules of C2 are checked call by call.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.gui.canvas_layers.raster import RasterLayerItem  # noqa: E402
from app.gui.correct import mask_tools  # noqa: E402
from app.gui.correct.layer import (  # noqa: E402
    LayerStatus,
    LayerTool,
    Notice,
    SaveOutcome,
    ToolSpec,
    missing_members,
)
from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from app.gui.correct.tool_router import ToolRouter  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from app.gui.paint_canvas import PaintCanvas, pixmap_to_argb_array  # noqa: E402
from app.gui.patch_history import PatchHistory  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from app.io_utils import imread_unicode, imwrite_unicode  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from tests.correct_fakes import FakeHost, FakeLayer  # noqa: E402

STEM = "plot_T001_L001_2024.07.18_111530_001_SMM"
W, H = 160, 120
NO_MODS = Qt.KeyboardModifier.NoModifier
MASK_COLOUR = QColor(tokens.MASK)
CLEARED = "Mask cleared. Ctrl+Z to undo; Save writes an empty GT mask."
FILL_MISS = "Fill works inside a closed outline. Close the loop first."
NOT_SHOWN_SIZE_TEXT = (
    "Saved mask size does not match the image: it is not shown and is kept unless you save over it"
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


# --------------------------------------------------------------------------- the oracle
def mask_lut() -> np.ndarray:
    """Today's _plane_lut: every value above 0 opaque in the mask colour (B, G, R, A)."""
    lut = np.zeros((256, 4), np.uint8)
    lut[1:] = (MASK_COLOUR.blue(), MASK_COLOUR.green(), MASK_COLOUR.red(), 255)
    return lut


def to_plane(argb: np.ndarray, rect) -> np.ndarray:
    """Today's _argb_to_plane: painted (alpha > 0) is 1."""
    return (argb[:, :, 3] > 0).astype(np.uint8)


def fill_source(plane: np.ndarray) -> tuple[np.ndarray, int]:
    """Today's _fill_source: the mask outline, filled with 1."""
    return (plane > 0).astype(np.uint8) * 255, 1


class Replay:
    """Today's semantics straight on a PaintCanvas over an equal plane."""

    def __init__(self, plane: np.ndarray) -> None:
        self.history = PatchHistory()
        self.history.reset(plane.copy())
        self.canvas = PaintCanvas(
            self.history,
            lut=mask_lut,
            to_plane=to_plane,
            fill_source=fill_source,
            on_region=lambda rect: None,
            on_replaced=lambda: None,
        )
        self.canvas.rebuild()

    @property
    def plane(self) -> np.ndarray:
        return self.history.plane

    def stroke(self, points, size: int, *, erase: bool = False) -> None:
        self.canvas.begin(QPoint(*points[0]), MASK_COLOUR, size, erase=erase)
        for p in points[1:]:
            self.canvas.extend(QPoint(*p))
        self.canvas.commit()

    def fill(self, p) -> None:
        self.canvas.fill(QPoint(*p))


# --------------------------------------------------------------------------- helpers
def image_path(tmp_path, stem: str = STEM) -> str:
    """The photo's path: the layer never decodes it (the editor passes its size)."""
    return os.path.join(str(tmp_path), f"{stem}.jpg")


def mask_file(tmp_path, stem: str = STEM, ext: str = ".png") -> str:
    """The model's mask (the prediction): never written by the layer."""
    return os.path.join(str(tmp_path), "mask", f"{stem}{ext}")


def gt_file(tmp_path, stem: str = STEM) -> str:
    """The GT mask: the one file a save writes (WG-T2)."""
    return os.path.join(str(tmp_path), "gt", "mask", f"{stem}.png")


#: The status of a layer showing the prediction it opened.
PREDICTION = LayerStatus(
    "none",
    "Prediction",
    f"mask/{STEM}.png — the model's output. Save after an edit writes gt/mask/{STEM}.png",
)


def write_model_mask(tmp_path, plane: np.ndarray, stem: str = STEM) -> None:
    """A model mask as the generator writes it: 8-bit grey, 255 where ``plane`` is set."""
    path = mask_file(tmp_path, stem)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    imwrite_unicode(path, (plane > 0).astype(np.uint8) * 255, durable=False)


def boxes_plane() -> np.ndarray:
    """A saved mask with two filled boxes (the eraser has something to erase)."""
    plane = np.zeros((H, W), np.uint8)
    plane[40:80, 60:120] = 1
    plane[5:15, 5:25] = 1
    return plane


def open_layer(scene, tmp_path, plane: np.ndarray | None = None, host: FakeHost | None = None):
    """A MaskLayer opened on a W x H image; ``plane`` becomes its saved mask file."""
    host = host or FakeHost()
    if plane is not None:
        write_model_mask(tmp_path, plane)
    layer = MaskLayer(host, scene)
    notices = layer.open(image_path(tmp_path), (W, H), None)
    assert notices == []
    host.calls.clear()
    return layer, host


def plane_of(layer) -> np.ndarray:
    return layer.document.history.plane


def count(host: FakeHost, name: str) -> int:
    return sum(1 for call in host.calls if call[0] == name)


def stroke(layer, points) -> None:
    """Press at the first point, a dragging move per further point, release at the last."""
    layer.press(QPoint(*points[0]), NO_MODS)
    for p in points[1:]:
        layer.move(QPoint(*p), True)
    layer.release(QPoint(*points[-1]))


def click(layer, p) -> None:
    layer.press(QPoint(*p), NO_MODS)
    layer.release(QPoint(*p))


def canvas_shows_the_plane(layer) -> bool:
    return np.array_equal(pixmap_to_argb_array(layer.canvas.image), mask_lut()[plane_of(layer)])


def ring_rect(layer) -> QRectF:
    return layer.ring.rect()


def read_rgba(path: str) -> np.ndarray:
    return imread_unicode(path, cv2.IMREAD_UNCHANGED)


# --------------------------------------------------------------------------- contract
def test_the_layer_and_its_tools_implement_the_protocols(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)

    assert missing_members(layer) == []
    assert missing_members(layer, mask_tools.MaskToolHost) == []
    for cls in mask_tools.TOOL_CLASSES:
        assert missing_members(cls(layer), LayerTool) == [], cls.__name__


def test_identity_and_tools_are_the_contracts(scene):
    layer = MaskLayer(FakeHost(), scene)

    assert (layer.layer_id, layer.noun, layer.color) == ("mask", "mask", tokens.MASK)
    assert layer.tools == (
        ToolSpec("brush", "brush", "Brush", "B", "hold B + scroll to resize", True),
        ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True),
        ToolSpec(
            "fill", "fill", "Fill a closed outline", "F", "click inside a closed outline", False
        ),
    )
    assert tuple(cls.spec for cls in mask_tools.TOOL_CLASSES) == layer.tools
    assert layer.current_tool == "brush"
    assert layer.size() == 8
    assert layer.opacity() == pytest.approx(0.60)


def test_the_item_is_a_raster_layer_at_z_one_in_the_scene(scene):
    layer = MaskLayer(FakeHost(), scene)

    assert isinstance(layer.item, RasterLayerItem)
    assert layer.item.zValue() == 1.0
    assert layer.item.scene() is scene
    assert layer.item.opacity() == pytest.approx(0.60)


def test_an_unknown_tool_is_refused(scene):
    layer = MaskLayer(FakeHost(), scene)

    with pytest.raises(ValueError):
        layer.set_tool("polyline")
    assert layer.current_tool == "brush"


# --------------------------------------------------------------------------- pixels
def test_the_paint_rules_are_todays():
    # _plane_lut: 0 transparent, every other value the opaque mask colour (B, G, R, A)
    lut = mask_tools.mask_lut()
    assert lut.shape == (256, 4) and lut.dtype == np.uint8
    assert not lut[0].any()
    assert (lut[1:] == (MASK_COLOUR.blue(), MASK_COLOUR.green(), MASK_COLOUR.red(), 255)).all()
    # _argb_to_plane: any alpha is 1, colour without alpha is 0; strided input too
    argb = np.zeros((3, 256, 4), np.uint8)
    argb[0, :, 3] = np.arange(256)
    argb[1, :, :3] = 200
    argb[2] = argb[0, ::-1]
    expected = np.zeros((3, 256), np.uint8)
    expected[0, 1:] = 1
    expected[2, :-1] = 1
    assert np.array_equal(mask_tools.argb_to_plane(argb, (0, 0, 256, 3)), expected)
    assert mask_tools.argb_to_plane(argb, (0, 0, 256, 3)).dtype == np.uint8
    assert np.array_equal(mask_tools.argb_to_plane(argb[:, ::2], (0, 0, 128, 3)), expected[:, ::2])
    # _fill_source: the mask as a 0/255 outline image, filled with 1
    plane = np.array([[0, 1, 2], [0, 0, 1]], np.uint8)
    outline, value = mask_tools.fill_source(plane)
    assert value == 1
    assert np.array_equal(outline, [[0, 255, 255], [0, 0, 255]])


def test_brush_eraser_and_fill_through_the_layer_equal_a_direct_canvas_replay(scene, tmp_path):
    start = boxes_plane()
    layer, host = open_layer(scene, tmp_path, start)
    oracle = Replay(start)
    square = [(20, 60), (50, 60), (50, 100), (20, 100), (20, 60)]
    steps = [
        ("brush", 6, "stroke", [(20, 20), (60, 30), (100, 70)]),
        ("brush", 3, "stroke", square),
        ("fill", None, "click", (35, 80)),  # inside the closed square
        ("eraser", 5, "stroke", [(10, 10), (150, 110)]),
        ("fill", None, "click", (150, 5)),  # outside every closed outline
        ("brush", 1, "click", (140, 20)),
        ("eraser", 9, "click", (90, 60)),
    ]

    for tool, size, gesture, where in steps:
        layer.set_tool(tool)
        if size is not None:
            layer.set_size(size)
        if gesture == "stroke":
            stroke(layer, where)
            oracle.stroke(where, size, erase=tool == "eraser")
        elif tool == "fill":
            click(layer, where)
            oracle.fill(where)
        else:
            click(layer, where)
            oracle.stroke([where], size, erase=tool == "eraser")

        assert np.array_equal(plane_of(layer), oracle.plane), (tool, gesture, where)
        assert canvas_shows_the_plane(layer), (tool, gesture, where)
    assert np.array_equal(
        pixmap_to_argb_array(layer.canvas.image), pixmap_to_argb_array(oracle.canvas.image)
    )
    assert len(layer.document.history.undo_stack) == 6  # the missed fill recorded nothing


def test_a_fill_outside_every_outline_says_so_and_records_nothing(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path, boxes_plane())
    layer.set_tool("fill")
    host.calls.clear()

    click(layer, (150, 110))

    assert host.calls == [("notify", FILL_MISS, "info")]
    assert MaskLayer.FILL_MISS_NOTICE == FILL_MISS
    assert not layer.can_undo()
    assert np.array_equal(plane_of(layer), boxes_plane())


def test_a_fill_inside_a_closed_outline_is_one_action(scene, tmp_path):
    ring = np.zeros((H, W), np.uint8)
    ring[20:60, 30:80] = 1
    ring[22:58, 32:78] = 0
    layer, host = open_layer(scene, tmp_path, ring)
    layer.set_tool("fill")

    click(layer, (50, 40))

    expected = ring.copy()
    expected[20:60, 30:80] = 1
    assert np.array_equal(plane_of(layer), expected)
    assert count(host, "layer_edited") == 1
    assert count(host, "notify") == 0


def test_clear_is_one_undoable_action_with_todays_notice(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path, boxes_plane())

    assert layer.clear() is True

    assert not plane_of(layer).any()
    assert ("notify", CLEARED, "info") in host.calls
    assert MaskLayer.CLEARED_NOTICE == CLEARED
    assert count(host, "layer_edited") == 1
    assert canvas_shows_the_plane(layer)
    assert layer.undo() is True
    assert np.array_equal(plane_of(layer), boxes_plane())
    assert canvas_shows_the_plane(layer)


def test_clear_without_an_open_mask_does_nothing(scene):
    host = FakeHost()
    layer = MaskLayer(host, scene)

    assert layer.clear() is False
    assert host.calls == []


def test_a_double_click_paints_one_dab_and_one_history_entry(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.set_size(9)
    p = QPoint(70, 50)

    layer.press(p, NO_MODS)
    layer.release(p)
    layer.double_click(p)  # Qt sends the second press of a double-click as this
    layer.release(p)

    oracle = Replay(np.zeros((H, W), np.uint8))
    oracle.stroke([(70, 50)], 9)
    assert np.array_equal(plane_of(layer), oracle.plane)
    assert len(layer.document.history.undo_stack) == 1


def test_a_press_during_a_stroke_commits_that_stroke_first(scene, tmp_path):
    # A lost release (e.g. taken by a modal box): the next press starts a new action.
    layer, host = open_layer(scene, tmp_path)
    layer.set_size(4)

    layer.press(QPoint(10, 10), NO_MODS)
    layer.move(QPoint(40, 10), True)
    stroke(layer, [(10, 60), (40, 60)])

    oracle = Replay(np.zeros((H, W), np.uint8))
    oracle.stroke([(10, 10), (40, 10)], 4)
    oracle.stroke([(10, 60), (40, 60)], 4)
    assert np.array_equal(plane_of(layer), oracle.plane)
    assert count(host, "layer_edited") == 2
    assert len(layer.document.history.undo_stack) == 2


def test_the_item_hears_each_segment_rect_and_the_stroke_never_copies_the_canvas(
    scene, tmp_path, monkeypatch
):
    rects: list[QRect] = []
    original = RasterLayerItem.canvas_changed

    def spy(item, rect):
        rects.append(QRect(rect))
        original(item, rect)

    monkeypatch.setattr(RasterLayerItem, "canvas_changed", spy)
    layer, _ = open_layer(scene, tmp_path)
    layer.set_size(6)
    serial = layer.canvas.image.cacheKey() >> 32
    target = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)

    layer.press(QPoint(10, 10), NO_MODS)
    layer.move(QPoint(20, 10), True)
    painter = QPainter(target)  # the scene paints between segments, as a view would
    scene.render(painter, QRectF(0, 0, W, H), QRectF(0, 0, W, H))
    painter.end()
    layer.move(QPoint(20, 30), True)
    layer.release(QPoint(20, 30))

    # today's dirty rects: the segment's box padded by r + 2 = 5, aligned outwards
    assert rects == [QRect(5, 5, 11, 11), QRect(5, 5, 21, 11), QRect(15, 5, 11, 31)]
    assert layer.canvas.image.cacheKey() >> 32 == serial


# --------------------------------------------------------------------------- signals
def test_layer_edited_fires_once_per_stroke_fill_and_clear_and_never_otherwise(scene, tmp_path):
    ring = np.zeros((H, W), np.uint8)
    ring[20:60, 30:80] = 1
    ring[22:58, 32:78] = 0
    layer, host = open_layer(scene, tmp_path, ring)

    stroke(layer, [(100, 90), (140, 100)])
    assert count(host, "layer_edited") == 1
    layer.set_tool("fill")
    click(layer, (50, 40))
    assert count(host, "layer_edited") == 2
    layer.clear()
    assert count(host, "layer_edited") == 3
    assert host.calls.count(("layer_edited", "mask")) == 3

    layer.undo()
    layer.redo()
    layer.save()
    layer.revert()
    layer.open(image_path(tmp_path), (W, H), None)
    layer.close()
    assert count(host, "layer_edited") == 3


def test_layer_changed_fires_when_a_stroke_ends_never_per_segment(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path)

    layer.press(QPoint(10, 10), NO_MODS)
    for x in range(12, 60, 4):
        layer.move(QPoint(x, 20), True)
    assert count(host, "layer_changed") == 0

    layer.release(QPoint(60, 20))
    assert count(host, "layer_changed") >= 1
    assert host.calls.index(("layer_edited", "mask")) < host.calls.index(("layer_changed", "mask"))


@pytest.mark.parametrize(
    "action",
    ["undo", "redo", "save", "revert", "set_tool", "open", "close", "step_size"],
)
def test_layer_changed_fires_after_each_ui_visible_change(scene, tmp_path, action):
    layer, host = open_layer(scene, tmp_path)
    stroke(layer, [(10, 10), (60, 40)])
    if action == "redo":
        layer.undo()
    host.calls.clear()

    {
        "undo": lambda: layer.undo(),
        "redo": lambda: layer.redo(),
        "save": lambda: layer.save(),
        "revert": lambda: layer.revert(),
        "set_tool": lambda: layer.set_tool("eraser"),
        "open": lambda: layer.open(image_path(tmp_path), (W, H), None),
        "close": lambda: layer.close(),
        "step_size": lambda: layer.step_size(1),
    }[action]()

    assert count(host, "layer_changed") >= 1
    assert count(host, "layer_edited") == 0


def test_no_change_fires_nothing(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path)

    layer.set_tool("brush")  # already the tool
    layer.set_size(layer.size())
    layer.drop_redo()  # nothing to drop
    layer.finish_gesture()  # no gesture
    layer.hover(QPoint(5, 5))
    layer.move(QPoint(6, 6), False)

    assert host.calls == []


def test_drop_redo_forgets_the_redo_branch_only(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path)
    stroke(layer, [(10, 10), (40, 10)])
    stroke(layer, [(10, 50), (40, 50)])
    layer.undo()
    host.calls.clear()

    layer.drop_redo()

    assert (layer.can_undo(), layer.can_redo()) == (True, False)
    assert host.calls == [("layer_changed", "mask")]
    assert layer.redo() is False


# --------------------------------------------------------------------------- history
def test_undo_and_redo_step_one_entry_and_report_it(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    assert (layer.undo(), layer.redo()) == (False, False)
    stroke(layer, [(10, 10), (60, 40)])
    painted = plane_of(layer).copy()

    assert layer.can_undo() is True
    assert layer.undo() is True
    assert not plane_of(layer).any()
    assert canvas_shows_the_plane(layer)
    assert layer.undo() is False  # empty: the journal may drop its entry
    assert layer.redo() is True
    assert np.array_equal(plane_of(layer), painted)
    assert canvas_shows_the_plane(layer)
    assert layer.redo() is False


def test_redo_mid_stroke_commits_the_stroke_which_ends_the_redo_branch(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path)
    stroke(layer, [(10, 10), (40, 10)])
    layer.undo()
    assert layer.can_redo()
    host.calls.clear()
    layer.press(QPoint(10, 50), NO_MODS)
    layer.move(QPoint(40, 50), True)

    assert layer.redo() is False  # nothing left to redo: a new action ended the branch

    assert layer.gesture_active() is False
    assert count(host, "layer_edited") == 1
    assert (layer.can_undo(), layer.can_redo()) == (True, False)


def test_undo_back_to_the_saved_mask_reads_clean(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())
    assert layer.is_dirty() is False
    assert layer.status() == PREDICTION

    stroke(layer, [(10, 100), (60, 110)])
    assert layer.is_dirty() is True
    assert layer.status() == LayerStatus("dirty")

    layer.undo()
    assert layer.is_dirty() is False
    assert layer.status() == PREDICTION


def test_a_blank_image_says_no_mask(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)

    assert layer.status() == LayerStatus("none", "No mask")


def test_key_consumes_nothing_and_there_is_no_draft_to_pop(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, NO_MODS)

    assert layer.key(enter) is False
    assert layer.undo_in_gesture() is False


# --------------------------------------------------------------------------- gestures
def test_a_stroke_is_a_gesture_until_finish_gesture_commits_it(scene, tmp_path):
    layer, host = open_layer(scene, tmp_path)
    assert layer.gesture_active() is False

    layer.press(QPoint(10, 10), NO_MODS)
    assert layer.gesture_active() is True
    layer.move(QPoint(50, 30), True)
    layer.finish_gesture()

    assert layer.gesture_active() is False
    assert count(host, "layer_edited") == 1
    oracle = Replay(np.zeros((H, W), np.uint8))
    oracle.stroke([(10, 10), (50, 30)], 8)
    assert np.array_equal(plane_of(layer), oracle.plane)


def test_a_fill_press_is_no_gesture(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())
    layer.set_tool("fill")

    layer.press(QPoint(150, 110), NO_MODS)

    assert layer.gesture_active() is False


TRANSITIONS = {
    "esc": lambda layer: layer.cancel_transient(),
    "fill": lambda layer: layer.set_tool("fill"),
    "eraser": lambda layer: layer.set_tool("eraser"),
    "same_tool": lambda layer: layer.set_tool("brush"),  # a B tap mid-stroke
    "layer_switch": lambda layer: (layer.finish_gesture(), layer.set_active(False)),
    "made_reference": lambda layer: layer.set_active(False),
    "pgdn": lambda layer: (layer.finish_gesture(), layer.save()),
}


@pytest.mark.parametrize("transition", list(TRANSITIONS))
def test_a_transition_mid_stroke_commits_it_and_the_rest_of_the_drag_changes_nothing(
    scene, tmp_path, transition
):
    start = boxes_plane()
    layer, host = open_layer(scene, tmp_path, start)
    layer.set_size(7)
    host.calls.clear()

    layer.press(QPoint(20, 90), NO_MODS)
    layer.move(QPoint(40, 60), True)
    layer.move(QPoint(70, 50), True)  # into the first box
    TRANSITIONS[transition](layer)
    assert layer.gesture_active() is False
    calls_after_transition = list(host.calls)
    layer.move(QPoint(90, 60), True)  # across the box: an eraser would change it
    layer.move(QPoint(130, 100), True)
    layer.release(QPoint(130, 100))

    assert host.calls == calls_after_transition  # the rest of the drag did nothing
    assert count(host, "layer_edited") == 1  # the forced finish
    oracle = Replay(start)
    oracle.stroke([(20, 90), (40, 60), (70, 50)], 7)
    assert np.array_equal(plane_of(layer), oracle.plane)
    assert canvas_shows_the_plane(layer)  # the canvas holds nothing the plane lacks

    outcome = layer.save()
    assert outcome.ok
    replay_file = os.path.join(str(tmp_path), "replay.png")
    write_mask_file(replay_file, oracle.plane > 0)
    assert np.array_equal(read_rgba(gt_file(tmp_path)), read_rgba(replay_file))

    ring_follows = transition in ("esc", "eraser", "same_tool", "pgdn")
    assert layer.ring.is_visible() is ring_follows
    if ring_follows:  # only the ring moved: it sits on the pointer's last pixel
        assert ring_rect(layer).center() == QRectF(130, 100, 1, 1).center()


def _recording(cls, log):
    """``cls`` (a real mask tool) that also logs the drag input it receives."""

    class Recording(cls):
        def move(self, pos):
            log.append((self.tool_id, "move"))
            super().move(pos)

        def release(self, pos):
            log.append((self.tool_id, "release"))
            super().release(pos)

    return Recording


def test_a_drag_reaches_only_the_tool_that_began_it_and_nothing_after_a_finish(
    scene, tmp_path, monkeypatch
):
    log: list[tuple[str, str]] = []
    tools = tuple(_recording(cls, log) for cls in mask_tools.TOOL_CLASSES)
    monkeypatch.setattr(mask_tools, "TOOL_CLASSES", tools)
    layer, _ = open_layer(scene, tmp_path)

    stroke(layer, [(10, 10), (20, 10)])
    assert log == [("brush", "move"), ("brush", "release")]

    log.clear()
    layer.press(QPoint(10, 40), NO_MODS)
    layer.move(QPoint(20, 40), True)
    layer.set_tool("eraser")  # a forced finish
    layer.move(QPoint(30, 40), True)
    layer.release(QPoint(30, 40))
    assert log == [("brush", "move")]  # neither the eraser nor the brush saw the rest

    log.clear()
    stroke(layer, [(10, 70), (20, 70)])
    assert log == [("eraser", "move"), ("eraser", "release")]


@pytest.mark.parametrize("action", ["save", "revert", "close", "open", "hide", "clear", "undo"])
def test_document_actions_commit_a_stroke_in_progress_first(scene, tmp_path, action):
    start = boxes_plane()
    layer, host = open_layer(scene, tmp_path, start)
    layer.press(QPoint(10, 100), NO_MODS)
    layer.move(QPoint(60, 110), True)
    oracle = Replay(start)
    oracle.stroke([(10, 100), (60, 110)], 8)

    if action == "save":
        outcome = layer.save()
        saved = read_rgba(gt_file(tmp_path))[:, :, 3] == 255
        assert np.array_equal(saved, oracle.plane > 0)
        assert outcome == SaveOutcome("mask", ok=True, path=outcome.path)
        assert layer.is_dirty() is False
    elif action == "revert":
        assert layer.revert() == []
        assert np.array_equal(plane_of(layer), start)
        assert (layer.can_undo(), layer.can_redo()) == (False, False)
    elif action == "close":
        layer.close()
        assert layer.is_open() is False
    elif action == "open":
        other = image_path(tmp_path, "other_T001_L001_2024.07.18_111530_001_SMM")
        layer.open(other, (W, H), None)
        assert not plane_of(layer).any()
        assert (layer.can_undo(), layer.is_dirty()) == (False, False)
    elif action == "hide":
        layer.set_visible(False)
        assert np.array_equal(plane_of(layer), oracle.plane)
    elif action == "clear":
        layer.clear()
        layer.undo()  # back to the committed stroke
        assert np.array_equal(plane_of(layer), oracle.plane)
    else:  # undo: the stroke becomes an action, which is the one undone
        assert layer.undo() is True
        assert np.array_equal(plane_of(layer), start)

    assert layer.gesture_active() is False
    assert host.calls.index(("layer_edited", "mask")) == 0  # the stroke was committed first
    if layer.is_open():
        assert canvas_shows_the_plane(layer)


# --------------------------------------------------------------------------- view state
def test_the_ring_follows_the_pointer_in_the_mask_colour(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.set_size(8)

    layer.hover(QPoint(30, 40))

    assert layer.ring.is_visible()
    assert ring_rect(layer) == QRectF(26.5, 36.5, 8, 8)  # centred on the pixel centre
    assert layer.ring.inner.pen().color() == QColor(tokens.MASK)
    assert layer.ring.inner.pen().style() == Qt.PenStyle.SolidLine

    layer.move(QPoint(31, 41), False)
    assert ring_rect(layer) == QRectF(27.5, 37.5, 8, 8)
    layer.hover(None)
    assert not layer.ring.is_visible()


def test_the_eraser_ring_is_dashed_and_fill_has_none(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.hover(QPoint(30, 40))

    layer.set_tool("eraser")
    assert layer.ring.is_visible()
    assert layer.ring.inner.pen().color() == QColor(tokens.MASK)
    assert layer.ring.inner.pen().style() == Qt.PenStyle.DashLine

    layer.set_tool("fill")
    assert not layer.ring.is_visible()
    layer.move(QPoint(35, 45), False)
    assert not layer.ring.is_visible()

    layer.set_tool("brush")
    assert layer.ring.is_visible()
    assert layer.ring.inner.pen().style() == Qt.PenStyle.SolidLine


def test_the_ring_hides_while_the_view_pans(scene, tmp_path):
    host = FakeHost(panning=True)
    layer, _ = open_layer(scene, tmp_path, host=host)

    layer.hover(QPoint(30, 40))
    assert not layer.ring.is_visible()

    host.panning = False
    layer.move(QPoint(31, 40), False)
    assert layer.ring.is_visible()


def test_the_ring_diameter_is_the_brush_size(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.hover(QPoint(50, 50))

    layer.set_size(21)

    assert ring_rect(layer) == QRectF(40.0, 40.0, 21, 21)


def test_a_closed_layer_shows_no_ring_and_ignores_input(scene):
    host = FakeHost()
    layer = MaskLayer(host, scene)

    layer.hover(QPoint(3, 3))
    layer.press(QPoint(3, 3), NO_MODS)
    layer.move(QPoint(9, 9), True)
    layer.release(QPoint(9, 9))

    assert not layer.ring.is_visible()
    assert layer.gesture_active() is False
    assert (layer.is_open(), layer.file_stamps(), layer.status()) == (
        False,
        (),
        LayerStatus("none"),
    )
    assert host.calls == []
    assert layer.save().ok is False


def test_a_fill_click_on_a_closed_layer_says_nothing(scene):
    host = FakeHost()
    layer = MaskLayer(host, scene)
    layer.set_tool("fill")
    host.calls.clear()

    click(layer, (3, 3))

    assert host.calls == []


def test_a_closed_layer_hides_its_ring_even_where_the_pointer_is(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.hover(QPoint(30, 40))
    assert layer.ring.is_visible()

    layer.close()
    assert not layer.ring.is_visible()
    layer.hover(QPoint(31, 40))
    assert not layer.ring.is_visible()


def test_the_active_state_dims_the_layer_and_hides_the_ring(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.hover(QPoint(30, 40))
    assert layer.is_active() is True
    assert layer.item.opacity() == pytest.approx(0.60)

    layer.set_active(False)
    assert layer.is_active() is False
    assert layer.item.opacity() == pytest.approx(0.30)
    assert not layer.ring.is_visible()
    layer.hover(QPoint(31, 40))
    assert not layer.ring.is_visible()

    layer.set_active(True)
    assert layer.item.opacity() == pytest.approx(0.60)
    assert not layer.ring.is_visible()  # shown again where the pointer next is
    layer.hover(QPoint(32, 40))
    assert layer.ring.is_visible()


def test_the_opacity_is_the_users_and_the_reference_is_half_of_it(scene):
    layer = MaskLayer(FakeHost(), scene)

    layer.set_opacity(0.8)
    assert layer.opacity() == pytest.approx(0.8)
    assert layer.item.opacity() == pytest.approx(0.8)
    layer.set_active(False)
    assert layer.opacity() == pytest.approx(0.8)
    assert layer.item.opacity() == pytest.approx(0.4)
    layer.set_opacity(1.7)
    assert layer.opacity() == pytest.approx(1.0)
    assert layer.item.opacity() == pytest.approx(0.5)


def test_a_hidden_layer_hides_the_item_and_shows_no_hover_ring(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.hover(QPoint(30, 40))

    layer.set_visible(False)
    assert (layer.is_visible(), layer.item.isVisible(), layer.ring.is_visible()) == (
        False,
        False,
        False,
    )
    layer.hover(QPoint(31, 40))
    assert not layer.ring.is_visible()

    layer.set_visible(True)
    assert layer.item.isVisible()
    layer.hover(QPoint(32, 40))
    assert layer.ring.is_visible()


def test_a_press_shows_the_ring_where_the_stroke_starts(scene, tmp_path):
    # The router shows a hidden layer, then presses: the ring must follow the stroke.
    layer, _ = open_layer(scene, tmp_path)
    layer.set_visible(False)
    layer.set_visible(True)

    layer.press(QPoint(60, 60), NO_MODS)
    assert layer.ring.is_visible()
    layer.move(QPoint(70, 60), True)
    assert ring_rect(layer).center() == QRectF(70, 60, 1, 1).center()
    layer.release(QPoint(70, 60))


# --------------------------------------------------------------------------- sizes
@pytest.mark.parametrize(
    ("start", "direction", "expected"),
    [
        (8, 1, 9),
        (8, -1, 7),
        (50, 1, 56),
        (56, -1, 49),
        (100, 1, 100),
        (95, 1, 100),
        (1, -1, 1),
        (4, 1, 5),  # 12 % of 4 rounds to 0: the step is still 1 px
        (4, -1, 3),
        (2, 1, 3),
    ],
)
def test_step_size_moves_twelve_percent_at_least_one_pixel_within_range(
    scene, start, direction, expected
):
    layer = MaskLayer(FakeHost(), scene)
    layer.set_size(start)

    layer.step_size(direction)

    assert layer.size() == expected


@pytest.mark.parametrize(("value", "expected"), [(0, 1), (-5, 1), (1, 1), (100, 100), (500, 100)])
def test_set_size_clamps_to_one_to_a_hundred(scene, value, expected):
    layer = MaskLayer(FakeHost(), scene)

    layer.set_size(value)

    assert layer.size() == expected


def test_brush_and_eraser_share_one_size_slider_and_fill_has_none(scene):
    layer = MaskLayer(FakeHost(), scene)

    brush = layer.size_spec()
    layer.set_tool("eraser")
    eraser = layer.size_spec()
    layer.set_tool("fill")

    assert brush is not None and brush == eraser
    assert (brush.minimum, brush.maximum) == (1, 100)
    assert layer.size_spec() is None


def test_the_hint_names_the_current_tool(scene):
    layer = MaskLayer(FakeHost(), scene)
    hints = {}
    for tool, label in (("brush", "Brush"), ("eraser", "Eraser"), ("fill", "Fill")):
        layer.set_tool(tool)
        text, tooltip = layer.tool_hint()
        assert text.startswith(label), text
        assert tooltip
        hints[tool] = text

    assert len(set(hints.values())) == 3


# --------------------------------------------------------------------------- the input path
def _mouse(view, kind, scene_xy, button, buttons) -> None:
    vp = view.viewport()
    pos = QPointF(view.mapFromScene(QPointF(*scene_xy)))
    global_pos = QPointF(vp.mapToGlobal(pos.toPoint()))
    QApplication.sendEvent(vp, QMouseEvent(kind, pos, global_pos, button, buttons, NO_MODS))


def test_the_canvas_view_and_router_drive_the_layer_end_to_end(qapp, tmp_path):
    # The wiring P7b builds: EditorCanvasView -> ToolRouter (the sink) -> MaskLayer.
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    view = EditorCanvasView()
    scene = QGraphicsScene(view)
    view.setScene(scene)
    host = FakeHost()
    layer = MaskLayer(host, scene)
    layer.open(image_path(tmp_path), (W, H), None)
    router = ToolRouter(
        {"mask": layer, "skeleton": FakeLayer("skeleton")},
        canvas_size=lambda: QSize(W, H),
        on_tool_request=lambda layer_id, tool_id: None,
        on_b_hold=lambda held: None,
        on_layer_shown=lambda layer_id: None,
    )
    view.set_sink(router)
    view.set_content_rect(QRectF(0, 0, W, H))
    view.resize(400, 300)
    view.show()
    qapp.processEvents()
    view.set_zoom(2.0)
    view.centerOn(W / 2, H / 2)
    oracle = Replay(np.zeros((H, W), np.uint8))
    try:
        # a stroke: image pixel (x, y) is hit at its centre (x + 0.5, y + 0.5)
        _mouse(view, QEvent.Type.MouseButtonPress, (40.5, 30.5), left, left)
        _mouse(view, QEvent.Type.MouseMove, (80.5, 50.5), none, left)
        _mouse(view, QEvent.Type.MouseMove, (100.5, 90.5), none, left)
        _mouse(view, QEvent.Type.MouseButtonRelease, (100.5, 90.5), left, none)
        oracle.stroke([(40, 30), (80, 50), (100, 90)], 8)
        assert np.array_equal(plane_of(layer), oracle.plane)
        assert ring_rect(layer).center() == QRectF(100, 90, 1, 1).center()

        # Esc mid-drag commits the stroke; the rest of that drag paints nothing
        _mouse(view, QEvent.Type.MouseButtonPress, (40.5, 100.5), left, left)
        _mouse(view, QEvent.Type.MouseMove, (70.5, 100.5), none, left)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        _mouse(view, QEvent.Type.MouseMove, (120.5, 60.5), none, left)
        _mouse(view, QEvent.Type.MouseButtonRelease, (120.5, 60.5), left, none)
        oracle.stroke([(40, 100), (70, 100)], 8)
        assert np.array_equal(plane_of(layer), oracle.plane)
        assert count(host, "layer_edited") == 2

        # hold B + wheel: the brush grows by 12 % (at least 1 px) and the UI hears of it
        host.calls.clear()
        QTest.keyPress(view, Qt.Key.Key_B)
        vp = view.viewport()
        centre = QPointF(200, 150)
        QApplication.sendEvent(
            vp,
            QWheelEvent(
                centre,
                QPointF(vp.mapToGlobal(centre.toPoint())),
                QPoint(0, 0),
                QPoint(0, 120),
                none,
                NO_MODS,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            ),
        )
        QTest.keyRelease(view, Qt.Key.Key_B)
        assert layer.size() == 9
        assert ("layer_changed", "mask") in host.calls
    finally:
        view.close()


# --------------------------------------------------------------------------- documents
def test_open_hands_back_the_documents_notices(scene, tmp_path):
    write_mask_file(gt_file(tmp_path), np.ones((H + 1, W), bool))  # a GT mask of another size
    layer = MaskLayer(FakeHost(), scene)

    notices = layer.open(image_path(tmp_path), (W, H), None)

    assert notices == [Notice(NOT_SHOWN_SIZE_TEXT, "warn")]
    assert layer.is_open() and not plane_of(layer).any()


def test_unloaded_label_names_the_gt_file_that_was_not_shown(scene, tmp_path):
    path = gt_file(tmp_path)
    write_mask_file(path, np.ones((H // 2, W // 2), bool))  # a GT mask of another size
    layer = MaskLayer(FakeHost(), scene)
    layer.open(image_path(tmp_path), (W, H), None)
    assert layer.unloaded_label() == f"gt/mask/{STEM}.png"  # a save would replace it

    stroke(layer, [(20, 20), (40, 40)])

    assert layer.unloaded_label() == f"gt/mask/{STEM}.png"
    assert layer.status() == LayerStatus("dirty")
    os.remove(path)
    assert layer.unloaded_label() == ""  # gone since the open: nothing left to replace


def test_an_unshown_prediction_is_never_named_for_replacing(scene, tmp_path):
    os.makedirs(os.path.dirname(mask_file(tmp_path)))
    with open(mask_file(tmp_path), "wb") as fh:
        fh.write(b"not a png")  # an unreadable prediction
    layer = MaskLayer(FakeHost(), scene)
    layer.open(image_path(tmp_path), (W, H), None)

    stroke(layer, [(20, 20), (40, 40)])

    assert layer.unloaded_label() == ""  # a save writes gt/mask, never the prediction


def test_open_stretches_a_prediction_of_another_size_and_says_so(scene, tmp_path):
    write_model_mask(tmp_path, np.ones((H + 1, W), np.uint8))  # a prediction of another size
    layer = MaskLayer(FakeHost(), scene)

    notices = layer.open(image_path(tmp_path), (W, H), None)

    assert notices == [
        Notice(
            f"Mask is {W} x {H + 1}; shown at the photo's {W} x {H}."
            " Saving stores it at the photo's size.",
            "info",
        )
    ]
    assert plane_of(layer).shape == (H, W) and plane_of(layer).all()
    assert layer.status() == PREDICTION and not layer.is_dirty()


def test_a_save_with_nothing_painted_keeps_the_unshown_file(scene, tmp_path):
    path = gt_file(tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_mask_file(path, np.ones((H // 2, W // 2), bool))
    with open(path, "rb") as fh:
        before = fh.read()
    layer = MaskLayer(FakeHost(), scene)
    layer.open(image_path(tmp_path), (W, H), None)

    outcome = layer.save()

    assert outcome == SaveOutcome(
        "mask", ok=True, message="Nothing to save: the saved mask was kept", kind="info"
    )
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert layer.status() == LayerStatus("error", "Mask not shown", NOT_SHOWN_SIZE_TEXT)


def test_a_layer_with_its_mask_shown_names_no_unloaded_file(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())

    stroke(layer, [(20, 20), (40, 40)])

    assert layer.unloaded_label() == ""


def test_open_shows_the_saved_mask(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())

    assert np.array_equal(plane_of(layer), boxes_plane())
    assert canvas_shows_the_plane(layer)
    assert layer.item.boundingRect() == QRectF(0, 0, W, H)


def test_close_forgets_the_document_and_empties_the_item(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())

    layer.close()

    assert layer.is_open() is False
    assert layer.canvas.image is None
    assert layer.item.boundingRect().isEmpty()


def test_revert_goes_back_to_the_saved_mask(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path, boxes_plane())
    stroke(layer, [(10, 100), (60, 110)])

    assert layer.revert() == []

    assert np.array_equal(plane_of(layer), boxes_plane())
    assert canvas_shows_the_plane(layer)
    assert (layer.is_dirty(), layer.can_undo()) == (False, False)


def test_save_writes_the_mask_and_reports_its_path(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    stroke(layer, [(10, 10), (60, 40)])

    outcome = layer.save()

    assert outcome == SaveOutcome("mask", ok=True, path=outcome.path)
    assert os.path.normcase(os.path.normpath(outcome.path)) == os.path.normcase(
        os.path.normpath(gt_file(tmp_path))
    )
    assert np.array_equal(read_rgba(gt_file(tmp_path))[:, :, 3] == 255, plane_of(layer) > 0)
    assert layer.is_dirty() is False
    assert not os.path.exists(mask_file(tmp_path))  # no prediction is ever written


def test_saving_an_emptied_mask_writes_an_empty_gt_mask_and_says_so(scene, tmp_path):  # O9
    layer, _ = open_layer(scene, tmp_path, boxes_plane())
    layer.clear()

    outcome = layer.save()

    name = f"{STEM}.jpg"
    assert outcome == SaveOutcome(
        "mask",
        ok=True,
        path=gt_file(tmp_path),
        message=f"Empty mask saved as GT for {name}: root area 0",
        kind="info",
    )
    assert not read_rgba(gt_file(tmp_path)).any() and os.path.exists(mask_file(tmp_path))


def test_saving_the_prediction_again_removes_the_gt_file_and_says_so(scene, tmp_path):
    write_mask_file(gt_file(tmp_path), np.zeros((H, W), bool))
    layer, host = open_layer(scene, tmp_path, boxes_plane())
    assert layer.load_prediction() is True
    assert np.array_equal(plane_of(layer), boxes_plane()) and layer.can_undo()
    assert host.calls.count(("layer_edited", "mask")) == 1

    outcome = layer.save()

    message = f"Mask equals the prediction: removed gt/mask/{STEM}.png"
    assert outcome == SaveOutcome("mask", ok=True, cleared=True, message=message, kind="info")
    assert not os.path.exists(gt_file(tmp_path)) and layer.status() == PREDICTION


def test_saving_an_empty_mask_without_a_file_writes_and_says_nothing(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)

    assert layer.save() == SaveOutcome("mask", ok=True)
    assert not os.path.exists(gt_file(tmp_path))  # O2: no edits, no GT file
    assert not os.path.exists(mask_file(tmp_path))


def _raise(message: str):
    def raiser(*_args, **_kwargs):
        raise OSError(message)

    return raiser


def test_a_failed_write_is_a_danger_outcome_with_todays_text(scene, tmp_path, monkeypatch):
    layer, _ = open_layer(scene, tmp_path)
    stroke(layer, [(10, 10), (60, 40)])
    monkeypatch.setattr(os, "replace", _raise("disk full"))

    outcome = layer.save()

    target = os.path.normpath(gt_file(tmp_path))
    assert outcome == SaveOutcome(
        "mask",
        ok=False,
        path=None,  # nothing was written
        message=f"Could not save mask to {target}: disk full",
        kind="danger",
    )
    assert layer.status() == LayerStatus("error", "Not saved", "disk full")
    assert layer.is_dirty() is True


def test_a_failed_removal_is_a_danger_outcome_with_todays_text(scene, tmp_path, monkeypatch):
    write_mask_file(gt_file(tmp_path), np.zeros((H, W), bool))
    layer, _ = open_layer(scene, tmp_path, boxes_plane())
    layer.load_prediction()
    monkeypatch.setattr(os, "remove", _raise("in use"))

    outcome = layer.save()

    message = f"Could not remove gt/mask/{STEM}.png: in use"
    assert outcome == SaveOutcome("mask", ok=False, path=None, message=message, kind="danger")
    assert layer.is_dirty() is True


def test_file_stamps_are_the_documents_and_follow_a_save(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    before = layer.file_stamps()
    assert before == layer.document.file_stamps()
    assert before and all(len(entry) == 2 for entry in before)  # nothing saved yet

    stroke(layer, [(10, 10), (60, 40)])
    layer.save()

    after = layer.file_stamps()
    assert after == layer.document.file_stamps()
    assert after != before


def test_a_one_pixel_click_paints_its_pixel_as_one_undo_step(scene, tmp_path):
    layer, _ = open_layer(scene, tmp_path)
    layer.set_size(1)
    click(layer, (70, 50))
    assert plane_of(layer).sum() == 1
    assert plane_of(layer)[50, 70] == 1
    assert len(layer.document.history.undo_stack) == 1
    assert layer.document.is_dirty()
    layer.undo()
    assert plane_of(layer).sum() == 0

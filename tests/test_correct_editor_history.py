"""Undo across both layers and gestures in progress on the Correct page (plan P7b, spec 5 and 7).

One timeline: Ctrl+Z undoes the newest action of either layer without switching layers or
tools; an action of the other layer says so (a toast, its Layers row flashes) and is shown
if hidden; a new action ends every redo branch; Ctrl+Z is ignored mid-drag and first pops a
polyline draft point. The long test drives 60 mixed actions on the real layers, more than the
skeleton's 25 snapshots hold, and undoes and redoes them all exactly.

Gestures in progress (the mid-gesture rule): a key that saves, pages, switches the layer or
the tool, hides the layers or cancels, pressed in the middle of a mask stroke made of real
mouse events, commits the stroke as its release would; the rest of that drag paints nothing
and the saved file equals a replay of the committed part.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.correct.mask_tools import mask_lut  # noqa: E402
from app.gui.correct.skeleton_layer import DraftState  # noqa: E402
from app.gui.paint_canvas import pixmap_to_argb_array  # noqa: E402
from app.io_utils import imread_unicode  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    LEFT,
    NO_BUTTON,
    SAVE,
    Mouse,
    activate,
    answer_prompts,
    box_plane,
    click,
    drag,
    draw_line,
    editor_factory,
    install_guards,
    mouse,
    new_project,
    paint,
    plane,
    qapp,
    raster,
    record,
    stroke_plane,
    write_image,
    write_mask,
)

CTRL = Qt.KeyboardModifier.ControlModifier
Key = Qt.Key


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
    return new_project(tmp_path)


def press(editor, key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """A key click on the canvas, as the user types it (shortcuts first)."""
    QTest.keyClick(editor.graphics_view, key, modifiers)


# ============================================================================ one timeline
def test_ctrl_z_undoes_the_newest_action_of_either_layer_without_switching(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    start_raster = raster(editor).copy()
    paint(editor, (20, 100), (80, 110))
    stroked = plane(editor).copy()
    draw_line(editor)
    editor.set_active_layer(MASK)
    notices = record(editor.notice)

    press(editor, Key.Key_Z, CTRL)  # the skeleton line is the newest action

    assert np.array_equal(raster(editor), start_raster)
    assert np.array_equal(plane(editor), stroked)
    assert editor.active_layer_id == MASK and editor.layers[MASK].current_tool == "brush"
    assert editor.layers[SKELETON].current_tool == "polyline"
    assert notices == [("Undo · skeleton", "info")]
    assert editor.layer_panel.rows[SKELETON].is_flashing()
    assert not editor.layer_panel.rows[MASK].is_flashing()

    press(editor, Key.Key_Z, CTRL)  # then the stroke, on the edited layer: no toast

    assert np.array_equal(plane(editor), box_plane())
    assert notices == [("Undo · skeleton", "info")]
    assert editor.session.journal.redo_entries == (SKELETON, MASK)


def test_redo_of_the_other_layer_says_so_too(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    drawn = raster(editor).copy()
    editor.undo()
    editor.set_active_layer(MASK)
    notices = record(editor.notice)

    editor.redo()

    assert np.array_equal(raster(editor), drawn) and editor.active_layer_id == MASK
    assert notices == [("Redo · skeleton", "info")]
    assert editor.layer_panel.rows[SKELETON].is_flashing()


def test_undoing_an_action_of_a_hidden_layer_shows_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    editor.set_active_layer(MASK)
    editor.layer_panel.rows[SKELETON].eye_button.click()
    skeleton = editor.layers[SKELETON]
    assert not skeleton.is_visible()

    editor.undo()

    assert skeleton.is_visible() and skeleton.item.isVisible()
    assert editor.layer_panel.rows[SKELETON].eye_button.isChecked()
    assert editor.active_layer_id == MASK


def test_undo_and_redo_buttons_follow_the_journal(make_editor, project):
    editor = make_editor(project.a, project.folder)
    undo, redo = editor.undo_button, editor.redo_button
    assert not undo.isEnabled() and not redo.isEnabled()

    paint(editor, (20, 100), (80, 110))
    assert undo.isEnabled() and not redo.isEnabled()
    undo.click()
    assert not undo.isEnabled() and redo.isEnabled()
    assert np.array_equal(plane(editor), box_plane())
    redo.click()
    assert undo.isEnabled() and not redo.isEnabled() and plane(editor)[105, 50] == 1

    undo.click()
    draw_line(editor)  # a new action on the other layer ends the mask's redo branch
    assert undo.isEnabled() and not redo.isEnabled()
    assert not editor.layers[MASK].can_redo() and editor.session.journal.redo_entries == ()


def test_the_undo_keys_follow_the_same_timeline(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)

    press(editor, Key.Key_Z, CTRL)
    press(editor, Key.Key_Z, CTRL)
    assert editor.session.journal.redo_entries == (SKELETON, MASK)
    press(editor, Key.Key_Y, CTRL)
    assert editor.session.journal.undo_entries == (MASK,)
    press(editor, Key.Key_Z, CTRL | Qt.KeyboardModifier.ShiftModifier)
    assert editor.session.journal.undo_entries == (MASK, SKELETON)


def test_ctrl_z_first_pops_the_last_point_of_a_polyline_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in ((40, 20), (100, 30), (160, 20)):
        click(editor, point)
    before = raster(editor).copy()

    editor.undo()

    assert editor.layers[SKELETON].draft_state() == DraftState(True, 2, False)
    assert np.array_equal(raster(editor), before)
    assert editor.session.journal.undo_entries == (SKELETON,)


def test_an_undo_that_drops_a_hidden_polyline_draft_says_so(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    draw_line(editor)
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 20))
    click(editor, (100, 30))
    editor.set_active_layer(MASK)  # the draft is kept, out of sight
    notices = record(editor.notice)

    editor.undo()

    assert skeleton.draft_state().points == 0
    assert ("Polyline draft discarded", "info") in notices
    assert ("Undo · skeleton", "info") in notices
    assert editor.active_layer_id == MASK
    editor.set_active_layer(SKELETON)
    assert editor.controls.prompt.isHidden()


def test_ctrl_z_in_the_middle_of_a_drag_does_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    view = editor.graphics_view
    draw_line(editor)
    editor.set_active_layer(MASK)
    mouse(view, QEvent.Type.MouseButtonPress, (20, 100))
    mouse(view, QEvent.Type.MouseMove, (60, 100), NO_BUTTON, LEFT)
    drawn = raster(editor).copy()

    press(editor, Key.Key_Z, CTRL)
    press(editor, Key.Key_Y, CTRL)

    assert editor.layers[MASK].gesture_active()
    assert editor.session.journal.undo_entries == (SKELETON,)
    assert np.array_equal(raster(editor), drawn)
    mouse(view, QEvent.Type.MouseMove, (80, 100), NO_BUTTON, LEFT)
    mouse(view, QEvent.Type.MouseButtonRelease, (80, 100), LEFT, NO_BUTTON)
    assert editor.session.journal.undo_entries == (SKELETON, MASK)
    assert plane(editor)[100, 70] == 1


def test_ctrl_z_during_a_skeleton_eraser_drag_does_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    view = editor.graphics_view
    paint(editor, (20, 100), (80, 110))
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    mouse(view, QEvent.Type.MouseButtonPress, (40, 110))
    mouse(view, QEvent.Type.MouseMove, (90, 110), NO_BUTTON, LEFT)

    press(editor, Key.Key_Z, CTRL)

    assert editor.layers[SKELETON].gesture_active()
    assert editor.session.journal.undo_entries == (MASK, SKELETON)  # the drag's own entry
    mouse(view, QEvent.Type.MouseButtonRelease, (90, 110), LEFT, NO_BUTTON)
    assert not raster(editor)[110, 50:85].any()
    assert editor.session.journal.undo_entries == (MASK, SKELETON)


def test_opening_another_image_starts_a_new_timeline(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    editor.save()
    draw_line(editor)
    editor.save()

    editor.load_image(project.b, project.folder)

    assert editor.session.journal.undo_entries == () and not editor.session.can_undo()
    assert not editor.undo_button.isEnabled()


def test_the_history_of_small_strokes_on_a_big_canvas_stays_small(make_editor, tmp_path):
    photo = write_image(str(tmp_path / "big" / "plot_001.png"), w=2048, h=1536)
    editor = make_editor(photo)

    for i in range(25):
        paint(editor, (100, 40 + i * 50), (400, 45 + i * 50))

    history = editor.layers[MASK].document.history
    assert len(history.undo_stack) == 25 and len(editor.session.journal) == 25
    assert history.nbytes < 1024 * 1024


# ============================================================== 60 actions, both layers
SIZE = (320, 240)  # (w, h) of the photo of the long test


def history_project(tmp_path) -> str:
    """A 320x240 photo with a model mask and a generated skeleton of three lines."""
    w, h = SIZE
    folder = str(tmp_path / "history")
    photo = write_image(os.path.join(folder, "c.png"), w=w, h=h)
    model = np.zeros((h, w), np.uint8)
    model[60:140, 100:220] = 1
    write_mask(os.path.join(folder, "mask", "c.png"), model)
    lines = np.zeros((h, w), np.uint8)
    for row in (40, 120, 200):
        lines[row, 30 : w - 30] = 255
    os.makedirs(os.path.join(folder, "skeletons"))
    cv2.imwrite(os.path.join(folder, "skeletons", "c_fake.png"), lines)
    return photo


def far_points(rng, count: int, *, margin: int = 12, apart: int = 16) -> list[tuple[int, int]]:
    """``count`` random image points at least ``apart`` px from each other."""
    w, h = SIZE
    points: list[tuple[int, int]] = []
    while len(points) < count:
        p = (int(rng.integers(margin, w - margin)), int(rng.integers(margin, h - margin)))
        if all(abs(p[0] - q[0]) + abs(p[1] - q[1]) >= apart for q in points):
            points.append(p)
    return points


def act(editor, rng, kind: str, memo: dict) -> str:
    """One action of ``kind`` through the page's controls; the layer it belongs to.

    ``memo`` carries the centre of the last closed outline to the fill that follows it.
    """
    buttons = editor.controls.tool_buttons
    mask, skeleton = editor.layers[MASK], editor.layers[SKELETON]
    if kind == "stroke":
        buttons[(MASK, "brush" if rng.random() < 0.7 else "eraser")].click()
        mask.set_size(int(rng.integers(2, 15)))
        drag(editor, *far_points(rng, int(rng.integers(2, 5)), apart=4))
    elif kind == "outline":  # a closed outline for the next fill
        buttons[(MASK, "brush")].click()
        mask.set_size(3)
        x, y = memo["seed"] = far_points(rng, 1, margin=40)[0]
        corners = [(x - 25, y - 20), (x + 25, y - 20), (x + 25, y + 20), (x - 25, y + 20)]
        drag(editor, *corners, corners[0])
    elif kind == "fill":
        buttons[(MASK, "fill")].click()
        click(editor, memo["seed"])
    elif kind == "clear":
        editor.set_active_layer(MASK)
        editor.clear_active_layer()
    elif kind == "polyline":
        buttons[(SKELETON, "polyline")].click()
        for point in far_points(rng, int(rng.integers(2, 4))):
            click(editor, point)
        editor.controls.finish_button.click()
    elif kind == "eraser":
        buttons[(SKELETON, "eraser")].click()
        skeleton.set_size(int(rng.integers(4, 16)))
        y = int(rng.choice([40, 120, 200]) + rng.integers(-3, 4))
        x = int(rng.integers(20, 200))
        drag(editor, (x, y), (x + 30, y + 2), (x + 70, y))
    elif kind == "connect":
        buttons[(SKELETON, "connect")].click()
        ends = sorted(skeleton.document.model.endpoints())
        start = ends[int(rng.integers(0, len(ends)))]
        drag(editor, start, far_points(rng, 1)[0])
    else:
        raise ValueError(kind)
    return MASK if kind in ("stroke", "outline", "fill", "clear") else SKELETON


def action_kinds(rng) -> list[str]:
    """60 actions: 32 on the skeleton (more than its 25 snapshots), 28 on the mask."""
    skeleton = ["polyline"] * 12 + ["eraser"] * 10 + ["connect"] * 10
    mask = ["stroke"] * 16 + ["clear"] * 4 + ["outline+fill"] * 4
    order = [*skeleton, *mask]
    rng.shuffle(order)
    kinds = []
    for kind in order:
        kinds.extend(kind.split("+"))
    return kinds


def state(editor) -> dict[str, np.ndarray]:
    return {MASK: plane(editor).copy(), SKELETON: raster(editor).copy()}


def assert_state(editor, expected: dict[str, np.ndarray], where) -> None:
    assert np.array_equal(plane(editor), expected[MASK]), where
    assert np.array_equal(raster(editor), expected[SKELETON]), where


def test_sixty_mixed_actions_undo_and_redo_exactly_across_both_layers(make_editor, tmp_path):
    rng = np.random.default_rng(2026)
    editor = make_editor(history_project(tmp_path), size=(1000, 760))
    journal = editor.session.journal
    states = [state(editor)]  # states[i + 1]: after action i
    owners: list[str] = []
    memo: dict = {}
    for index, kind in enumerate(action_kinds(rng)):
        owners.append(act(editor, rng, kind, memo))
        assert len(journal) == index + 1, (index, kind)  # exactly one action each
        states.append(state(editor))
    assert len(owners) == 60 and owners.count(SKELETON) == 32
    assert journal.undo_entries == tuple(owners)
    limit = editor.layers[SKELETON].document.model.max_stack_size
    assert limit == 25 < owners.count(SKELETON)
    kept = [i for i, owner in enumerate(owners) if owner == SKELETON][-limit:]
    undoable = [i for i in reversed(range(60)) if owners[i] == MASK or i in kept]
    active = editor.active_layer_id
    tools = {layer_id: layer.current_tool for layer_id, layer in editor.layers.items()}

    expected = dict(states[-1])
    for i in undoable:  # newest first; evicted skeleton actions are skipped
        assert editor.session.can_undo()
        editor.undo()
        expected[owners[i]] = states[i][owners[i]]
        assert_state(editor, expected, ("undo", i))
    assert not editor.session.can_undo()
    editor.undo()  # nothing left: nothing changes
    assert_state(editor, expected, "undo past the start")
    assert np.array_equal(plane(editor), states[0][MASK])  # the mask as opened
    assert np.array_equal(raster(editor), states[kept[0]][SKELETON])  # oldest state kept

    for i in reversed(undoable):
        editor.redo()
        expected[owners[i]] = states[i + 1][owners[i]]
        assert_state(editor, expected, ("redo", i))
    assert not editor.session.can_redo()
    assert_state(editor, states[-1], "after redo-all")  # the final state exactly
    assert editor.active_layer_id == active
    assert {layer_id: layer.current_tool for layer_id, layer in editor.layers.items()} == tools


# ================================================================ gestures in progress
STROKE = [(20, 40), (60, 40)]  # the part of the drag before the key
REST = [(60, 100), (120, 100)]  # the rest of the drag, after the key
KEYS = {
    "Esc": (Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    "F": (Key.Key_F, Qt.KeyboardModifier.NoModifier),
    "E": (Key.Key_E, Qt.KeyboardModifier.NoModifier),
    "2": (Key.Key_2, Qt.KeyboardModifier.NoModifier),
    "X": (Key.Key_X, Qt.KeyboardModifier.NoModifier),
    "H": (Key.Key_H, Qt.KeyboardModifier.NoModifier),
    "B": (Key.Key_B, Qt.KeyboardModifier.NoModifier),
    "Ctrl+S": (Key.Key_S, CTRL),
    "PgDn": (Key.Key_PageDown, Qt.KeyboardModifier.NoModifier),
}
ENDS_THE_VIEW_DRAG = {"2", "X", "PgDn"}


def replay_file(tmp_path, plane_values: np.ndarray) -> np.ndarray:
    """The pixels of the mask file written for ``plane_values`` (the plane of a replay)."""
    path = str(tmp_path / "replay" / "b.png")
    write_mask_file(path, plane_values > 0, durable=False)
    return imread_unicode(path, cv2.IMREAD_UNCHANGED)


@pytest.mark.parametrize("name", sorted(KEYS))
def test_a_key_mid_stroke_commits_it_and_the_rest_of_the_drag_paints_nothing(
    make_editor, project, tmp_path, name
):
    editor = make_editor(project.b, project.folder)
    activate(editor)
    view, mask = editor.graphics_view, editor.layers[MASK]
    moved = record(editor.navigate_requested)
    mouse(view, QEvent.Type.MouseButtonPress, STROKE[0])
    mouse(view, QEvent.Type.MouseMove, STROKE[1], NO_BUTTON, LEFT)
    assert mask.gesture_active()

    press(editor, *KEYS[name])

    assert not mask.gesture_active()
    assert editor.session.journal.undo_entries == (MASK,)
    if name in ENDS_THE_VIEW_DRAG:
        assert view.drawing is False
    for point in REST:
        mouse(view, QEvent.Type.MouseMove, point, NO_BUTTON, LEFT)
    mouse(view, QEvent.Type.MouseButtonRelease, REST[-1], LEFT, NO_BUTTON)
    assert editor.save() is True

    replay = stroke_plane(np.zeros_like(plane(editor)), STROKE)
    assert np.array_equal(plane(editor), replay)  # the rest of the drag painted nothing
    lut = mask_lut()
    assert np.array_equal(pixmap_to_argb_array(mask.canvas.image), lut[plane(editor)])
    saved = imread_unicode(project.mask("b"), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(saved, replay_file(tmp_path, replay))
    assert editor.session.journal.undo_entries == (MASK,)
    assert moved == ([1] if name == "PgDn" else [])


def test_reopening_the_image_mid_drag_commits_the_stroke_and_ends_the_drag(make_editor, project):
    editor = make_editor(project.b, project.folder)
    view = editor.graphics_view
    mouse(view, QEvent.Type.MouseButtonPress, STROKE[0])
    mouse(view, QEvent.Type.MouseMove, STROKE[1], NO_BUTTON, LEFT)

    editor.load_image(project.b, project.folder)  # the tree re-selects the open image

    assert view.drawing is False and not editor.layers[MASK].gesture_active()
    assert editor.session.journal.undo_entries == (MASK,) and editor.has_unsaved_changes()
    assert np.array_equal(plane(editor), stroke_plane(np.zeros_like(plane(editor)), STROKE))


def test_a_press_after_a_forced_finish_starts_a_new_stroke(make_editor, project):
    editor = make_editor(project.b, project.folder)
    activate(editor)
    view = editor.graphics_view
    mouse(view, QEvent.Type.MouseButtonPress, STROKE[0])
    mouse(view, QEvent.Type.MouseMove, STROKE[1], NO_BUTTON, LEFT)
    press(editor, Key.Key_Escape)
    mouse(view, QEvent.Type.MouseButtonRelease, STROKE[1], LEFT, NO_BUTTON)

    router, event = editor.router, Mouse()
    router.canvas_press(QPoint(*REST[0]), event)
    router.canvas_move(QPoint(*REST[1]), event, True)
    router.canvas_release(QPoint(*REST[1]), event)

    first = stroke_plane(np.zeros_like(plane(editor)), STROKE)
    assert np.array_equal(plane(editor), stroke_plane(first, REST))
    assert editor.session.journal.undo_entries == (MASK, MASK)


def test_leaving_mid_drag_commits_the_stroke_before_the_prompt(make_editor, project, monkeypatch):
    editor = make_editor(project.b, project.folder)
    view = editor.graphics_view
    answers = answer_prompts(monkeypatch).will(SAVE)
    mouse(view, QEvent.Type.MouseButtonPress, STROKE[0])
    mouse(view, QEvent.Type.MouseMove, STROKE[1], NO_BUTTON, LEFT)

    assert editor.maybe_save_changes() is True

    assert [title for title, _text in answers.seen] == ["Unsaved mask"]
    assert view.drawing is False
    saved = imread_unicode(project.mask("b"), cv2.IMREAD_UNCHANGED)
    stroked = stroke_plane(np.zeros_like(plane(editor)), STROKE)
    assert np.array_equal(saved[:, :, 3] == 255, stroked > 0)

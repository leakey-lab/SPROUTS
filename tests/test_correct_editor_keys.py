"""The keys of the Correct page (plan P7b, spec section 6, registry scope ``Correct``).

Letters that act on the whole page are ``QShortcut``s installed from the registry (1 / 2 / X,
F / V / P / C / E, [ / ], H / Shift+H, Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z, Ctrl+S, the canvas zoom
keys, and PgDn / PgUp documented by the Global rows); the keys that depend on the gesture (B
held, S, Enter, Esc, Delete) go through the canvas router to the edited layer. No key is
both. Every test presses real keys on the shown, active page.
"""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut, QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui import shortcuts  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from app.gui.correct.skeleton_layer import DraftState  # noqa: E402
from tests.correct_goldens_support import png_colour_type  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    NO_MODS,
    Mouse,
    activate,
    box_plane,
    click,
    draw_line,
    editor_factory,
    install_guards,
    new_project,
    paint,
    plane,
    qapp,
    raster,
    record,
)

CTRL, SHIFT = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
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


@pytest.fixture
def page(make_editor, project):
    """The page on image a (a model mask, a generated skeleton), active, keys on the canvas."""
    editor = make_editor(project.a, project.folder)
    activate(editor)
    return editor


def press(editor, key, modifiers=NO_MODS) -> None:
    """A key click on the canvas, as the user types it (shortcuts first)."""
    QTest.keyClick(editor.graphics_view, key, modifiers)


def portable(sequence) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText)


def live_shortcuts(editor) -> list[QShortcut]:
    return [s for s in editor.findChildren(QShortcut) if s.parent() is editor]


def registry_keys(scope: str, kind: str) -> set[str]:
    return {portable(k) for s in shortcuts.specs_for(scope) if s.kind == kind for k in s.keys}


# =============================================================================== registry
def test_every_live_shortcut_is_a_correct_or_global_registry_key_bound_once(make_editor):
    live = [portable(s.key()) for s in live_shortcuts(make_editor(show=False))]
    documented = {portable(k) for s in shortcuts.specs_for(shortcuts.CORRECT) for k in s.keys}
    global_keys = {portable(k) for s in shortcuts.specs_for(shortcuts.GLOBAL) for k in s.keys}

    assert set(live) - documented - global_keys == set()
    assert {"PgDown", "PgUp"} <= set(live) <= documented | {"PgDown", "PgUp"}
    assert len(live) == len(set(live))


def test_every_correct_shortcut_row_is_bound(make_editor):
    live = {portable(s.key()) for s in live_shortcuts(make_editor(show=False))}

    assert registry_keys(shortcuts.CORRECT, shortcuts.SHORTCUT) <= live


def test_no_key_is_both_a_shortcut_and_a_canvas_key(make_editor):
    live = {portable(s.key()) for s in live_shortcuts(make_editor(show=False))}

    assert live & registry_keys(shortcuts.CORRECT, shortcuts.KEY) == set()
    assert shortcuts.find_conflicts() == []


@pytest.mark.parametrize("sequence", ["X", "H", "Shift+H"])
def test_x_h_and_shift_h_do_not_repeat_while_held(make_editor, sequence):
    bound = [s for s in live_shortcuts(make_editor(show=False)) if portable(s.key()) == sequence]

    assert len(bound) == 1 and not bound[0].autoRepeat()


# ================================================================================ layers
def test_1_2_and_x_switch_the_edited_layer(page):
    for key, active in ((Key.Key_2, SKELETON), (Key.Key_1, MASK), (Key.Key_X, SKELETON)):
        press(page, key)
        assert page.active_layer_id == active, key
    press(page, Key.Key_X)
    assert page.active_layer_id == MASK


def test_e_takes_the_eraser_of_the_edited_layer(page):
    mask, skeleton = page.layers[MASK], page.layers[SKELETON]

    press(page, Key.Key_E)
    assert (page.active_layer_id, mask.current_tool, skeleton.current_tool) == (
        MASK,
        "eraser",
        "select",
    )
    press(page, Key.Key_2)
    press(page, Key.Key_E)
    assert (page.active_layer_id, skeleton.current_tool) == (SKELETON, "eraser")
    assert page.controls.tool_buttons[(SKELETON, "eraser")].isChecked()


def test_f_and_a_tap_of_b_take_the_mask_with_that_tool(page):
    held = record(page.b_key_status_changed)
    press(page, Key.Key_2)

    press(page, Key.Key_F)
    assert (page.active_layer_id, page.layers[MASK].current_tool) == (MASK, "fill")
    press(page, Key.Key_2)
    press(page, Key.Key_B)  # a tap: pressed and released without a wheel turn

    assert (page.active_layer_id, page.layers[MASK].current_tool) == (MASK, "brush")
    assert held == [True, False]


@pytest.mark.parametrize(
    "key, tool", [(Key.Key_V, "select"), (Key.Key_P, "polyline"), (Key.Key_C, "connect")]
)
def test_v_p_and_c_take_the_skeleton_with_that_tool(page, key, tool):
    page.set_active_layer(SKELETON)
    page.layers[SKELETON].set_tool("eraser")
    page.set_active_layer(MASK)

    press(page, key)

    assert (page.active_layer_id, page.layers[SKELETON].current_tool) == (SKELETON, tool)
    assert page.controls.tool_buttons[(SKELETON, tool)].isChecked()


def test_p_back_on_the_skeleton_resumes_its_polyline_draft(page):
    press(page, Key.Key_P)
    click(page, (40, 125))
    click(page, (100, 130))
    press(page, Key.Key_1)

    press(page, Key.Key_P)

    assert page.active_layer_id == SKELETON
    assert page.layers[SKELETON].draft_state() == DraftState(True, 2, False)


def test_the_tool_keys_pick_the_skeleton_tools(page):
    press(page, Key.Key_2)
    for key, tool in (
        (Key.Key_E, "eraser"),
        (Key.Key_P, "polyline"),
        (Key.Key_C, "connect"),
        (Key.Key_V, "select"),
    ):
        press(page, key)
        assert page.layers[SKELETON].current_tool == tool, key
    assert page.controls.tool_buttons[(SKELETON, "select")].isChecked()


# ================================================================================= sizes
def test_the_bracket_keys_resize_the_tool_of_the_edited_layer(page):
    mask, skeleton = page.layers[MASK], page.layers[SKELETON]

    press(page, Key.Key_BracketRight)
    assert mask.size() == 9
    press(page, Key.Key_BracketLeft)
    assert mask.size() == 8
    press(page, Key.Key_2)
    press(page, Key.Key_E)
    press(page, Key.Key_BracketRight)
    assert (skeleton.size(), mask.size()) == (22, 8)
    press(page, Key.Key_BracketLeft)
    assert skeleton.size() < 22


def wheel(view, dy: int) -> None:
    viewport = view.viewport()
    centre = QPointF(viewport.rect().center())
    event = QWheelEvent(
        centre,
        QPointF(viewport.mapToGlobal(centre.toPoint())),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        NO_MODS,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)


def test_holding_b_and_scrolling_sizes_the_mask_brush_from_the_skeleton(page):
    press(page, Key.Key_2)
    held = record(page.b_key_status_changed)
    zoom = page.graphics_view.zoom_factor

    QTest.keyPress(page.graphics_view, Key.Key_B)
    wheel(page.graphics_view, 120)
    QTest.keyRelease(page.graphics_view, Key.Key_B)

    assert page.layers[MASK].size() == 9 and page.graphics_view.zoom_factor == zoom
    # the scroll asked for the mask brush, so its dock slider shows the new size
    assert page.active_layer_id == MASK and page.layers[MASK].current_tool == "brush"
    slider = page.controls.size_sliders[MaskLayer.SIZE_SPEC]
    assert slider.isVisibleTo(page.dock) and slider.slider.value() == 9
    assert held == [True, False]


# ============================================================================ visibility
def visibility(editor) -> tuple[bool, bool]:
    return editor.layers[MASK].is_visible(), editor.layers[SKELETON].is_visible()


def test_h_hides_both_layers_and_a_second_h_shows_them(page):
    rows = page.layer_panel.rows

    press(page, Key.Key_H)
    assert visibility(page) == (False, False)
    assert not rows[MASK].eye_button.isChecked() and not rows[SKELETON].eye_button.isChecked()
    press(page, Key.Key_H)
    assert visibility(page) == (True, True)
    assert rows[MASK].eye_button.isChecked() and rows[SKELETON].eye_button.isChecked()


def test_h_with_one_layer_hidden_hides_both(page):
    press(page, Key.Key_H, SHIFT)

    press(page, Key.Key_H)
    assert visibility(page) == (False, False)
    press(page, Key.Key_H)
    assert visibility(page) == (True, True)


def test_shift_h_hides_and_shows_only_the_other_layer(page):
    press(page, Key.Key_H, SHIFT)
    assert visibility(page) == (True, False)
    assert not page.layers[SKELETON].item.isVisible()
    press(page, Key.Key_H, SHIFT)
    assert visibility(page) == (True, True)
    press(page, Key.Key_2)
    press(page, Key.Key_H, SHIFT)
    assert visibility(page) == (False, True) and page.active_layer_id == SKELETON


# ========================================================================= gesture keys
def test_s_enter_and_delete_act_only_while_the_skeleton_is_edited(page):
    skeleton = page.layers[SKELETON]
    press(page, Key.Key_P)
    click(page, (40, 125))
    click(page, (100, 130))
    press(page, Key.Key_1)

    for key in (Key.Key_S, Key.Key_Return):
        press(page, key)
    assert skeleton.draft_state() == DraftState(True, 2, False)
    assert page.session.journal.undo_entries == ()

    press(page, Key.Key_2)
    press(page, Key.Key_S)
    assert skeleton.draft_state().smooth and page.controls.smooth_button.isChecked()
    press(page, Key.Key_Return)
    assert page.session.journal.undo_entries == (SKELETON,)

    press(page, Key.Key_V)
    click(page, (100, 112))  # pick the generated line on row 110
    before = raster(page).copy()
    press(page, Key.Key_1)
    press(page, Key.Key_Delete)
    assert (raster(page) == before).all()
    press(page, Key.Key_2)
    press(page, Key.Key_Delete)
    assert not raster(page)[110, 30:170].any()


def test_the_smooth_key_toggles_only_in_the_polyline_tool(page):
    skeleton = page.layers[SKELETON]
    press(page, Key.Key_2)

    press(page, Key.Key_S)
    assert not skeleton.draft_state().smooth
    press(page, Key.Key_P)
    press(page, Key.Key_S)
    assert skeleton.draft_state().smooth


def label_key(button) -> Qt.Key:
    """The key a button's label names in brackets: "Cancel (Esc)" -> Key_Escape."""
    match = re.search(r"\((.+)\)$", button.text())
    assert match, f"no key in the label {button.text()!r}"
    return QKeySequence(match.group(1))[0].key()


@pytest.mark.parametrize(
    "button, draft, undo",
    [
        ("finish_button", DraftState(True, 0, False), (SKELETON,)),
        ("cancel_button", DraftState(True, 0, False), ()),
        ("smooth_button", DraftState(True, 3, True), ()),
    ],
    ids=["finish", "cancel", "smooth"],
)
def test_each_prompt_button_names_a_key_that_does_its_job(page, button, draft, undo):
    """Every button of the polyline prompt shows its key in its label, and that key pressed
    on the canvas does what the button does."""
    press(page, Key.Key_P)
    for point in ((40, 125), (70, 140), (100, 130)):
        click(page, point)

    press(page, label_key(getattr(page.controls, button)))

    assert page.layers[SKELETON].draft_state() == draft
    assert page.session.journal.undo_entries == undo


def test_esc_drops_the_skeleton_draft(page):
    press(page, Key.Key_P)
    click(page, (40, 125))
    click(page, (100, 130))
    before = raster(page).copy()

    press(page, Key.Key_Escape)

    assert page.layers[SKELETON].draft_state().points == 0 and page.controls.prompt.isHidden()
    assert (raster(page) == before).all()


def test_esc_during_a_mask_stroke_keeps_the_stroke(page):
    router, event = page.router, Mouse()
    router.canvas_press(QPoint(20, 100), event)
    router.canvas_move(QPoint(60, 100), event, True)

    press(page, Key.Key_Escape)

    assert not page.layers[MASK].gesture_active()
    assert page.session.journal.undo_entries == (MASK,) and plane(page)[100, 40] == 1
    router.canvas_move(QPoint(60, 140), event, True)
    router.canvas_release(QPoint(60, 140), event)
    assert plane(page)[140, 60] == 0  # the rest of that drag paints nothing


# =============================================================================== history
@pytest.mark.parametrize("modifiers, key", [(CTRL, Key.Key_Y), (CTRL | SHIFT, Key.Key_Z)])
def test_ctrl_z_undoes_and_ctrl_y_or_ctrl_shift_z_redoes(page, modifiers, key):
    paint(page, (20, 100), (60, 100))

    press(page, Key.Key_Z, CTRL)
    assert (plane(page) == box_plane()).all()
    assert page.session.journal.redo_entries == (MASK,)
    press(page, key, modifiers)
    assert plane(page)[100, 40] == 1 and page.session.journal.redo_entries == ()


def test_the_undo_keys_do_nothing_on_an_empty_page(make_editor):
    editor = make_editor()
    activate(editor)

    press(editor, Key.Key_Z, CTRL)
    press(editor, Key.Key_Y, CTRL)

    assert editor.current_image_path == ""


# ============================================================================ save, pages
def test_ctrl_s_saves_from_the_canvas(page, project):
    draw_line(page)

    press(page, Key.Key_S, CTRL)

    assert not page.has_unsaved_changes() and os.path.isfile(project.correction())
    assert png_colour_type(project.mask()) == 0  # only the edited skeleton was written
    assert not os.path.exists(project.gt_mask())


def test_pgdn_and_pgup_save_first_then_ask_for_the_next_and_previous_image(page, project):
    moved = record(page.navigate_requested)
    paint(page, (20, 100), (60, 100))

    press(page, Key.Key_PageDown)
    assert moved == [1] and not page.has_unsaved_changes()
    assert png_colour_type(project.gt_mask()) == 6  # the edited mask was written (GT)
    assert png_colour_type(project.mask()) == 0  # the prediction was not
    press(page, Key.Key_PageUp)
    assert moved == [1, -1]


# ================================================================================== zoom
def test_the_canvas_zoom_keys_fit_show_100_percent_and_step(page):
    view = page.graphics_view
    fitted = view.zoom_factor

    press(page, Key.Key_1, CTRL)
    assert view.zoom_factor == pytest.approx(1.0) and page.active_layer_id == MASK
    press(page, Key.Key_Equal, CTRL)
    assert view.zoom_factor == pytest.approx(1.25)
    press(page, Key.Key_Minus, CTRL)
    assert view.zoom_factor == pytest.approx(1.0)
    press(page, Key.Key_Minus, CTRL)  # a 200 x 150 photo fits at about 4x: zoom-out stops at 100 %
    assert view.zoom_out_limit == 1.0 and view.zoom_factor == pytest.approx(1.0)
    press(page, Key.Key_0, CTRL)
    assert view.zoom_factor == pytest.approx(fitted)

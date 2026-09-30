"""CorrectControls and LayerPanel (plan P7a, contract C20): the Correct page's own controls on
the shared EditorChrome, and its Layers panel.

Both are pure views. The controls put two tool groups on the rail (the mask's, then the
skeleton's), one size slider per distinct SizeSpec, the opacity slider, Load / Undo / Redo /
Clear / Save, the polyline prompt and the topbar clear button over the chrome; they show the
state they are handed and emit requests, never touching a layer. The Layers panel lists the
layers, the mask on top, with their key, colour, visibility and save state.

Built as P7b builds the page: a canvas, the chrome, the real MaskLayer and SkeletonLayer on a
scene parented to the view; FakeLayers where a call log proves that nothing touched a layer.
Offscreen; colours are read from real renders.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGraphicsScene,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.enhancement import EnhanceDefaultsStore  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.correct.controls import CorrectControls  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON, LayerStatus  # noqa: E402
from app.gui.correct.layer_panel import LayerPanel  # noqa: E402
from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from app.gui.correct.skeleton_layer import DraftState, SkeletonLayer  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from app.gui.editor_chrome import EditorChrome  # noqa: E402
from app.gui.image_normalization_interface import NormalizationControls  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from app.gui.widgets.icons import load_icon  # noqa: E402
from app.gui.widgets.overlay_geometry import LayoutMetrics  # noqa: E402
from tests.correct_fakes import FakeHost, FakeLayer  # noqa: E402

M = LayoutMetrics()
UNDER_TEST = (
    os.path.join("correct", "controls.py"),
    os.path.join("correct", "layer_panel.py"),
    "test_correct_controls.py",
)
RAIL_TOOLS = [
    ((MASK, "brush"), "Brush (B) · hold B + scroll to resize"),
    ((MASK, "eraser"), "Mask eraser (E) · [ and ] resize"),
    ((MASK, "fill"), "Fill a closed outline (F)"),
    ((SKELETON, "select"), "Select (V) · pick a branch, Delete removes it"),
    ((SKELETON, "eraser"), "Skeleton eraser (E) · [ and ] resize"),
    ((SKELETON, "polyline"), "Polyline (P) · Enter finish · Esc cancel"),
    ((SKELETON, "connect"), "Connect endpoints (C)"),
]
TOOL_KEYS = [key for key, _tip in RAIL_TOOLS]
REQUESTS = (
    "tool_requested",
    "size_changed",
    "opacity_changed",
    "load_requested",
    "undo_requested",
    "redo_requested",
    "clear_requested",
    "save_requested",
    "finish_line_requested",
    "cancel_line_requested",
    "smooth_toggled",
)
LONG_NAME = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"
_APP: QApplication | None = None


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def app():
    global _APP  # one QApplication, kept referenced (icons cache QPixmaps)
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No prompt, no GL, no write to the real user config, and no exception of the code under
    test hidden in a slot (PyQt hands those to sys.excepthook)."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    errors = []

    def hook(kind, value, tb):
        files = [os.path.normcase(frame.filename) for frame in traceback.extract_tb(tb)]
        if any(f.endswith(os.path.normcase(name)) for f in files for name in UNDER_TEST):
            errors.append(value)
        else:
            sys.__excepthook__(kind, value, tb)

    monkeypatch.setattr(sys, "excepthook", hook)
    yield
    assert errors == []


class Page(QWidget):
    """The Correct page as P7b composes it: canvas, chrome, both layers, controls, panel."""

    def __init__(self, store: EnhanceDefaultsStore, *, fake: bool):
        super().__init__()
        self.view = EditorCanvasView()
        self.scene = QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        self.view.set_content_rect(QRectF(0, 0, 640, 480))
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.view)
        self.norm = NormalizationControls(store=store)
        self.chrome = EditorChrome(self, self.view, self.norm)
        self.host = FakeHost()
        if fake:
            self.layers = {MASK: FakeLayer(MASK), SKELETON: FakeLayer(SKELETON)}
        else:
            self.layers = {
                MASK: MaskLayer(self.host, self.scene),
                SKELETON: SkeletonLayer(self.host, self.scene),
            }
        self.controls = CorrectControls(self, self.chrome, self.layers)
        self.panel = LayerPanel(list(self.layers.values()), self)
        self.chrome.register(side_panel=self.panel)


@pytest.fixture
def make_page(app, tmp_path):
    made = []

    def make(size=(1000, 700), *, show=True, fake=False):
        store = EnhanceDefaultsStore(
            user_path=str(tmp_path / "user.json"), factory_path=str(tmp_path / "factory.json")
        )
        page = Page(store, fake=fake)
        page.resize(*size)
        if show:
            page.show()
            settle(app, page)
        made.append(page)
        return page

    yield make
    # Delete the pages while their wrappers are intact (page -> chrome -> OverlayLayout -> page
    # is a cycle the GC could otherwise empty while Qt still delivers teardown events).
    for page in made:
        skeleton = page.layers[SKELETON]
        if isinstance(skeleton, SkeletonLayer):
            skeleton.cancel_scheduled_render()
        page.close()
        page.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ------------------------------------------------------------------------------ helpers
def settle(app, page=None) -> None:
    for _ in range(3):
        app.processEvents()
    if page is not None:
        page.chrome.relayout()
        app.processEvents()


def show_active(app, page) -> None:
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page, 3000)
    settle(app, page)


def record(signal) -> list[tuple]:
    seen: list[tuple] = []
    signal.connect(lambda *args: seen.append(args))
    return seen


def record_requests(controls) -> list[tuple]:
    """Every request the controls emit, as ``(signal name, args)``."""
    seen: list[tuple] = []
    for name in REQUESTS:
        getattr(controls, name).connect(lambda *args, name=name: seen.append((name, args)))
    return seen


def opaque_colours(image: QImage) -> set[str]:
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return {
        QColor(image.pixel(x, y)).name()
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() == 255
    }


def icon_colours(button) -> set[str]:
    return opaque_colours(button.icon().pixmap(button.iconSize(), 1.0).toImage())


def drawn_in(button, colour: str) -> bool:
    """Every well-covered pixel (alpha >= 200) of ``button``'s icon is ``colour``, within the
    2 levels antialiasing leaves (TEXT_FAINT and TEXT_MUTED differ by 7-8 per channel); thin
    icons such as "smooth" have no fully opaque pixel at 17 px."""
    target = QColor(colour)
    image = button.icon().pixmap(button.iconSize(), 1.0).toImage()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    found = [
        QColor.fromRgba(image.pixel(x, y))
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() >= 200
    ]
    return bool(found) and all(
        max(
            abs(c.red() - target.red()),
            abs(c.green() - target.green()),
            abs(c.blue() - target.blue()),
        )
        <= 2
        for c in found
    )


def pixel(image: QImage, point: QPoint) -> str:
    return QColor(image.pixel(point)).name()


def visible_size_slider(controls, dock):
    shown = [s for s in controls.size_sliders.values() if s.isVisibleTo(dock)]
    assert len(shown) == 1, shown
    return shown[0]


def untouched(page) -> bool:
    return all(layer.calls == [] for layer in page.layers.values())


def right(widget) -> int:
    return widget.geometry().x() + widget.geometry().width()


# ============================================================================ the rail
def test_the_rail_holds_the_mask_tools_then_the_skeleton_tools_then_pan_and_enhance(make_page):
    page = make_page(show=False)
    chrome = page.chrome
    names = {button: key for key, button in page.controls.tool_buttons.items()}
    names[chrome.pan_button] = "pan"
    names[chrome.enhance_button] = "enhance"
    layout = chrome.rail.layout()
    sequence = []
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        separator = isinstance(widget, QFrame) and widget.frameShape() == QFrame.Shape.HLine
        sequence.append("|" if separator else names.get(widget, widget))

    assert sequence == [*TOOL_KEYS[:3], "|", *TOOL_KEYS[3:], "|", "pan", "|", "enhance"]


@pytest.mark.parametrize("key, tip", RAIL_TOOLS, ids=[f"{k[0]}-{k[1]}" for k in TOOL_KEYS])
def test_each_rail_tooltip_names_the_tool_its_key_and_its_gesture(make_page, key, tip):
    page = make_page(show=False)

    assert page.controls.tool_buttons[key].toolTip() == tip


def test_the_two_erasers_are_told_apart_by_their_accessible_names(make_page):
    buttons = make_page(show=False).controls.tool_buttons
    mask, skeleton = (buttons[(layer, "eraser")].accessibleName() for layer in (MASK, SKELETON))

    assert "mask" in mask.lower() and "skeleton" in skeleton.lower() and mask != skeleton


def test_the_tool_buttons_form_one_exclusive_group_checked_in_their_layer_colour(make_page):
    buttons = make_page(show=False).controls.tool_buttons

    for (layer_id, tool_id), button in buttons.items():
        button.setChecked(True)
        assert [key for key, other in buttons.items() if other.isChecked()] == [(layer_id, tool_id)]
        assert drawn_in(button, tokens.MASK if layer_id == MASK else tokens.SKEL)


@pytest.mark.parametrize("key", TOOL_KEYS, ids=[f"{k[0]}-{k[1]}" for k in TOOL_KEYS])
def test_a_rail_click_requests_its_tool_and_touches_no_layer(make_page, key):
    page = make_page(show=False, fake=True)
    requests = record_requests(page.controls)

    page.controls.tool_buttons[key].click()

    assert requests == [("tool_requested", key)]
    assert untouched(page)


def test_show_layer_fades_the_other_group_and_keeps_every_tool_clickable(make_page):
    page = make_page(show=False)
    controls = page.controls

    for shown, faded in ((SKELETON, MASK), (MASK, SKELETON), (SKELETON, MASK)):
        controls.show_layer(shown)
        for (layer_id, _tool), button in controls.tool_buttons.items():
            assert button.isEnabled()
            if not button.isChecked():
                colour = tokens.TEXT_DISABLED if layer_id == faded else tokens.TEXT_MUTED
                assert drawn_in(button, colour), (layer_id, _tool, icon_colours(button))


# ============================================================================ the dock
def test_the_dock_holds_the_size_and_opacity_then_the_actions_then_save(make_page):
    page = make_page(show=False)
    controls = page.controls
    for layer in page.layers.values():  # both size sliders are made
        layer.set_tool("eraser")
        controls.sync(layer)
    controls.show_layer(SKELETON)
    layout = page.chrome.dock.layout()
    widgets = [layout.itemAt(index).widget() for index in range(layout.count())]
    separators = [
        i
        for i, w in enumerate(widgets)
        if isinstance(w, QFrame) and w.frameShape() == QFrame.Shape.VLine
    ]
    actions = [
        controls.load_button,
        controls.undo_button,
        controls.redo_button,
        controls.clear_icon_button,
    ]

    assert len(controls.size_sliders) == 2
    assert all(widgets[0].isAncestorOf(slider) for slider in controls.size_sliders.values())
    assert widgets[1] is controls.opacity_slider
    assert separators == [2, 7]
    assert widgets[3:7] == actions
    assert widgets[8:] == [controls.save_button]


def test_load_is_offered_only_while_the_skeleton_is_shown(make_page):
    page = make_page(show=False)
    controls, dock = page.controls, page.chrome.dock

    controls.show_layer(SKELETON)
    assert controls.load_button.isVisibleTo(dock) and controls.load_button.isEnabled()
    controls.show_layer(MASK)
    assert controls.load_button.isVisibleTo(dock)  # kept in place: the dock does not shift
    assert not controls.load_button.isEnabled()


@pytest.mark.parametrize(
    "layer_id, text, tip",
    [
        (MASK, "Clear mask", "Clear the mask. Undo with Ctrl+Z; Save removes the file."),
        (SKELETON, "Clear skeleton", "Remove every skeleton line from this image (Ctrl+Z undoes)"),
    ],
)
def test_the_clear_buttons_name_the_shown_layer(make_page, layer_id, text, tip):
    controls = make_page(show=False).controls
    other = SKELETON if layer_id == MASK else MASK

    controls.show_layer(other)
    controls.show_layer(layer_id)

    assert (controls.clear_button.text(), controls.clear_button.toolTip()) == (text, tip)
    assert layer_id in controls.clear_icon_button.toolTip()
    assert other not in controls.clear_icon_button.toolTip()
    assert layer_id in controls.opacity_slider.toolTip().lower()


def test_sync_checks_the_current_tool_of_the_layer(make_page):
    page = make_page(show=False)
    controls = page.controls

    for layer_id, tool_id in [*TOOL_KEYS, *reversed(TOOL_KEYS)]:
        layer = page.layers[layer_id]
        layer.set_tool(tool_id)
        controls.show_layer(layer_id)
        controls.sync(layer)
        checked = [key for key, button in controls.tool_buttons.items() if button.isChecked()]
        assert checked == [(layer_id, tool_id)]


def test_the_brush_and_the_mask_eraser_share_a_size_slider_the_skeleton_eraser_has_its_own(
    make_page,
):
    page = make_page(show=False)
    controls, dock = page.controls, page.chrome.dock
    mask, skeleton = page.layers[MASK], page.layers[SKELETON]

    mask.set_tool("brush")
    controls.sync(mask)
    brush = visible_size_slider(controls, dock)
    mask.set_tool("eraser")
    controls.sync(mask)
    assert visible_size_slider(controls, dock) is brush

    skeleton.set_tool("eraser")
    controls.show_layer(SKELETON)
    controls.sync(skeleton)
    eraser = visible_size_slider(controls, dock)
    assert eraser is not brush
    controls.show_layer(MASK)
    controls.sync(mask)
    assert visible_size_slider(controls, dock) is brush
    assert set(controls.size_sliders.values()) == {brush, eraser}


@pytest.mark.parametrize(
    "layer_id, tool_id",
    [(MASK, "fill"), (SKELETON, "select"), (SKELETON, "polyline"), (SKELETON, "connect")],
)
def test_no_size_slider_shows_for_a_tool_without_a_size(make_page, layer_id, tool_id):
    page = make_page(show=False)
    controls, dock = page.controls, page.chrome.dock
    for sized in page.layers.values():  # both size sliders exist and one is showing
        sized.set_tool("eraser")
        controls.sync(sized)
    layer = page.layers[layer_id]

    layer.set_tool(tool_id)
    controls.show_layer(layer_id)
    controls.sync(layer)

    assert len(controls.size_sliders) == 2
    assert [s for s in controls.size_sliders.values() if s.isVisibleTo(dock)] == []
    layout = dock.layout()
    shown = [layout.itemAt(i).widget() for i in range(layout.count())]
    shown = [widget for widget in shown if widget.isVisibleTo(dock)]
    placeholder = controls.no_size_slider  # holds the slot, so the dock keeps its width
    assert shown[0].isAncestorOf(placeholder) and shown[1] is controls.opacity_slider
    assert placeholder.isVisibleTo(dock) and not placeholder.isEnabled()
    assert placeholder.toolTip() == "No size for this tool"


@pytest.mark.parametrize(
    "layer_id, size, low, high, icon, tip",
    [
        (MASK, 23, 1, 100, "brush", "Brush size ([ and ])"),
        (SKELETON, 31, 1, 80, "erase", "Eraser size ([ and ])"),
    ],
)
def test_the_size_slider_shows_the_layer_size_with_the_range_icon_and_tooltip_of_its_spec(
    make_page, layer_id, size, low, high, icon, tip
):
    page = make_page(show=False)
    controls = page.controls
    layer = page.layers[layer_id]
    layer.set_tool("eraser")
    layer.set_size(size)

    controls.show_layer(layer_id)
    controls.sync(layer)

    shown = visible_size_slider(controls, page.chrome.dock)
    assert (shown.slider.minimum(), shown.slider.maximum()) == (low, high)
    assert (shown.slider.value(), shown.value_label.text()) == (size, f"{size} px")
    assert shown.toolTip() == tip
    [icon_label] = [
        label
        for label in shown.findChildren(QLabel)
        if label.pixmap() is not None and not label.pixmap().isNull()
    ]
    expected = load_icon(icon, tokens.TEXT_MUTED, 16).pixmap(16, 16).toImage()
    assert icon_label.pixmap().toImage() == expected


# 0.29 * 100 and 0.57 * 100 fall just below 29 and 57 in floating point: rounded, not truncated.
@pytest.mark.parametrize("layer_id, opacity, percent", [(MASK, 0.29, 29), (SKELETON, 0.57, 57)])
def test_sync_shows_the_opacity_of_the_layer_in_percent(make_page, layer_id, opacity, percent):
    page = make_page(show=False)
    controls = page.controls
    layer = page.layers[layer_id]
    layer.set_opacity(opacity)

    controls.show_layer(layer_id)
    controls.sync(layer)

    slider = controls.opacity_slider
    assert (slider.slider.minimum(), slider.slider.maximum()) == (0, 100)
    assert (slider.slider.value(), slider.value_label.text()) == (percent, f"{percent}%")


def test_sync_emits_nothing_and_blocks_the_sliders_signals(make_page):
    page = make_page(show=False)
    controls, dock = page.controls, page.chrome.dock
    mask, skeleton = page.layers[MASK], page.layers[SKELETON]
    controls.sync(mask)  # the brush slider exists
    brush = controls.size_sliders[mask.size_spec()]
    requests = record_requests(controls)
    moved = record(controls.opacity_slider.slider.valueChanged)
    moved_brush = record(brush.slider.valueChanged)

    mask.set_size(40)
    mask.set_opacity(0.2)
    controls.sync(mask)
    skeleton.set_tool("eraser")
    skeleton.set_size(12)
    skeleton.set_opacity(0.5)
    controls.show_layer(SKELETON)
    controls.sync(skeleton)
    controls.set_draft(DraftState(True, 3, True))
    controls.set_history(True, False)

    assert requests == [] and moved == [] and moved_brush == []
    assert (brush.slider.value(), brush.value_label.text()) == (40, "40 px")
    assert visible_size_slider(controls, dock).slider.value() == 12
    assert controls.opacity_slider.value_label.text() == "50%"


def test_moving_a_slider_requests_that_size_or_opacity_and_touches_no_layer(make_page):
    page = make_page(show=False, fake=True)
    controls = page.controls
    controls.sync(page.layers[MASK])
    requests = record_requests(controls)

    visible_size_slider(controls, page.chrome.dock).slider.setValue(17)
    controls.opacity_slider.slider.setValue(42)

    assert requests == [("size_changed", (17,)), ("opacity_changed", (0.42,))]
    assert untouched(page)


def test_the_size_slider_sits_at_the_start_of_the_dock_even_when_made_later(app, make_page):
    page = make_page((1400, 900))
    controls, dock = page.controls, page.chrome.dock
    skeleton = page.layers[SKELETON]
    skeleton.set_tool("eraser")
    controls.show_layer(SKELETON)
    controls.sync(skeleton)  # the skeleton eraser's slider is made now, after the rest
    settle(app, page)

    def x(widget) -> int:
        return widget.mapTo(dock, QPoint(0, 0)).x()

    slider = visible_size_slider(controls, dock)
    later = [controls.opacity_slider, controls.load_button, controls.save_button]
    assert all(x(slider) < x(widget) for widget in later)


def test_a_slider_made_while_the_dock_is_compact_is_compact_at_once(make_page):
    """No relayout pass may see a full-width slider in a compact dock (a too-wide plan)."""
    page = make_page(show=False)
    controls, dock = page.controls, page.chrome.dock
    skeleton = page.layers[SKELETON]
    dock.set_compact(True)  # a narrow canvas: the layout made the dock compact

    skeleton.set_tool("eraser")
    controls.show_layer(SKELETON)
    controls.sync(skeleton)  # the skeleton eraser's slider is made now

    assert visible_size_slider(controls, dock).is_compact()


@pytest.mark.parametrize("can_undo, can_redo", [(True, False), (False, True), (True, True)])
def test_set_history_enables_undo_and_redo(make_page, can_undo, can_redo):
    controls = make_page(show=False).controls
    controls.set_history(not can_undo, not can_redo)

    controls.set_history(can_undo, can_redo)

    assert (controls.undo_button.isEnabled(), controls.redo_button.isEnabled()) == (
        can_undo,
        can_redo,
    )


BUTTON_REQUESTS = [
    ("load_button", "load_requested"),
    ("undo_button", "undo_requested"),
    ("redo_button", "redo_requested"),
    ("clear_icon_button", "clear_requested"),
    ("clear_button", "clear_requested"),
    ("save_button", "save_requested"),
    ("finish_button", "finish_line_requested"),
    ("cancel_button", "cancel_line_requested"),
]


@pytest.mark.parametrize("button, signal", BUTTON_REQUESTS, ids=[b for b, _ in BUTTON_REQUESTS])
def test_each_button_requests_its_action_and_touches_no_layer(make_page, button, signal):
    page = make_page(show=False, fake=True)
    controls = page.controls
    controls.show_layer(SKELETON)
    controls.set_history(True, True)
    controls.set_draft(DraftState(True, 2, False))
    requests = record_requests(controls)

    getattr(controls, button).click()

    assert requests == [(signal, ())]
    assert untouched(page)


@pytest.mark.parametrize(
    "button, words",
    [
        ("undo_button", ["Undo", "Ctrl+Z"]),
        ("redo_button", ["Redo", "Ctrl+Y", "Ctrl+Shift+Z"]),
        ("save_button", ["Save", "Ctrl+S"]),
        ("load_button", ["Load skeleton file"]),
        ("smooth_button", ["Smooth", "(S)"]),
    ],
)
def test_each_action_tooltip_names_its_keys_from_the_registry(make_page, button, words):
    tip = getattr(make_page(show=False).controls, button).toolTip()

    assert all(word in tip for word in words), tip


def test_a_new_page_shows_the_first_layer_with_nothing_to_undo(make_page):
    page = make_page(show=False)
    controls, panel, dock = page.controls, page.panel, page.chrome.dock

    checked = [key for key, button in controls.tool_buttons.items() if button.isChecked()]
    assert checked == [(MASK, "brush")]
    brush = controls.size_sliders[page.layers[MASK].size_spec()]
    assert visible_size_slider(controls, dock) is brush
    assert drawn_in(controls.tool_buttons[(SKELETON, "select")], tokens.TEXT_DISABLED)
    assert controls.load_button.isVisibleTo(dock) and not controls.load_button.isEnabled()
    assert controls.clear_button.text() == "Clear mask"
    assert not controls.undo_button.isEnabled() and not controls.redo_button.isEnabled()
    assert not controls.prompt.isVisibleTo(page)
    assert panel.rows[MASK].is_active() and not panel.rows[SKELETON].is_active()


# ================================================================= the polyline prompt
def test_the_prompt_reads_finish_cancel_and_smooth(make_page):
    controls = make_page(show=False).controls

    assert controls.finish_button.text() == "Finish line (Enter)"
    assert controls.cancel_button.text() == "Cancel (Esc)"
    assert drawn_in(controls.cancel_button, tokens.DANGER)
    assert controls.smooth_button.text() == "Smooth (S)" and controls.smooth_button.isCheckable()
    for button in (controls.finish_button, controls.cancel_button, controls.smooth_button):
        assert controls.prompt.isAncestorOf(button)


@pytest.mark.parametrize(
    "state, shown, finish",
    [
        (DraftState(True, 2, False), True, True),
        (DraftState(True, 5, True), True, True),
        (DraftState(True, 1, False), True, False),
        (DraftState(True, 0, False), False, None),
        (DraftState(False, 3, False), False, None),
        (None, False, None),
    ],
    ids=["2-points", "5-points", "1-point", "no-points", "other-tool", "none"],
)
def test_the_prompt_shows_only_with_draft_points_and_finish_needs_two(
    make_page, state, shown, finish
):
    page = make_page(show=False)
    controls = page.controls
    controls.show_layer(SKELETON)
    controls.set_draft(DraftState(True, 3, False))  # shown first: hiding is checked too

    controls.set_draft(state)

    assert controls.prompt.isVisibleTo(page) is shown
    if shown:
        assert controls.finish_button.isEnabled() is finish
        assert controls.cancel_button.isEnabled()


def test_the_prompt_waits_while_the_mask_is_shown_and_returns_with_the_skeleton(make_page):
    page = make_page(show=False)
    controls = page.controls

    controls.show_layer(SKELETON)
    controls.set_draft(DraftState(True, 3, False))
    assert controls.prompt.isVisibleTo(page)
    controls.show_layer(MASK)
    assert not controls.prompt.isVisibleTo(page)
    controls.set_draft(DraftState(True, 4, False))  # the draft moves on while hidden
    assert not controls.prompt.isVisibleTo(page)
    controls.show_layer(SKELETON)
    assert controls.prompt.isVisibleTo(page)


def test_smooth_shows_the_draft_and_a_click_requests_the_other_setting(make_page):
    controls = make_page(show=False).controls
    controls.show_layer(SKELETON)
    toggles = record(controls.smooth_toggled)

    controls.set_draft(DraftState(True, 3, True))
    assert controls.smooth_button.isChecked()
    assert drawn_in(controls.smooth_button, tokens.SKEL)
    controls.set_draft(DraftState(True, 3, False))
    assert not controls.smooth_button.isChecked()
    assert toggles == []

    controls.smooth_button.click()
    controls.set_draft(DraftState(True, 3, True))  # the editor applied it
    controls.smooth_button.click()

    assert toggles == [(True,), (False,)]


# ======================================================================= the Layers panel
def test_the_panel_lists_the_mask_above_the_skeleton_with_keys_names_and_colours(app, make_page):
    page = make_page((1400, 900))
    rows = page.panel.rows

    assert list(rows) == [MASK, SKELETON]
    assert rows[MASK].geometry().y() < rows[SKELETON].geometry().y()
    assert [rows[i].key_label.text() for i in (MASK, SKELETON)] == ["1", "2"]
    assert [rows[i].name_label.text() for i in (MASK, SKELETON)] == ["Mask", "Skeleton"]
    for layer_id, colour in ((MASK, tokens.MASK), (SKELETON, tokens.SKEL)):
        swatch = rows[layer_id].swatch.grab().toImage()
        assert pixel(swatch, QPoint(swatch.width() // 2, swatch.height() // 2)) == colour


@pytest.fixture
def standalone(app):
    """A LayerPanel over a plain host, placing itself (no OverlayLayout)."""
    hosts = []

    def make(layers) -> LayerPanel:
        host = QWidget()
        host.resize(600, 400)
        panel = LayerPanel(layers, host)
        host.show()
        panel.reposition()
        settle(app)
        hosts.append(host)
        return panel

    yield make
    for host in hosts:
        host.close()
        host.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_panel_is_never_narrower_than_155_px(standalone):
    """Spec 3.2: about 155 px, so the fit zoom barely changes; short names do not shrink it."""
    tiny = FakeLayer(MASK)
    tiny.noun = "m"

    panel = standalone([tiny])

    assert panel.sizeHint().width() < 155 and panel.width() == 155
    assert right(panel) == 600 - M.margin  # its own placement: the host's right edge


def test_the_mask_row_stays_on_top_and_keeps_its_key_whatever_the_order_given(standalone):
    rows = standalone([FakeLayer(SKELETON), FakeLayer(MASK)]).rows

    assert rows[MASK].geometry().y() < rows[SKELETON].geometry().y()
    assert [rows[i].key_label.text() for i in (MASK, SKELETON)] == ["1", "2"]


@pytest.mark.parametrize(
    "status, text, colour",
    [
        (LayerStatus("clean"), "Saved", tokens.OK),
        (LayerStatus("dirty"), "Unsaved", tokens.WARN),
        (LayerStatus("error"), "Not saved", tokens.DANGER),
        (LayerStatus("none", "No mask"), "No mask", tokens.TEXT_FAINT),
        (LayerStatus("none", "Generated", "Not corrected yet"), "Generated", tokens.TEXT_FAINT),
        (LayerStatus("error", "Not saved", "Disk full"), "Not saved", tokens.DANGER),
        (LayerStatus("clean", "Saved · 2/3 checked"), "Saved · 2/3 checked", tokens.OK),
    ],
    ids=["clean", "dirty", "error", "none-label", "tooltip", "error-reason", "label-wins"],
)
def test_set_status_shows_the_label_else_the_state_name_in_the_chip_colour(
    app, make_page, status, text, colour
):
    page = make_page((1400, 900))
    row = page.panel.rows[SKELETON]
    page.panel.set_status(SKELETON, LayerStatus("dirty", "something else", "old tip"))

    page.panel.set_status(SKELETON, status)
    settle(app, page)

    assert row.state_label.text() == text
    assert row.state_label.toolTip() == row.state_dot.toolTip() == status.tooltip
    assert opaque_colours(row.state_label.grab().toImage()) == {colour}
    assert opaque_colours(row.state_dot.grab().toImage()) == {colour}


def test_a_status_without_a_label_shows_nothing(app, make_page):
    page = make_page((1400, 900))
    row = page.panel.rows[MASK]
    page.panel.set_status(MASK, LayerStatus("clean"))

    page.panel.set_status(MASK, LayerStatus("none"))
    settle(app, page)

    assert row.state_label.text() == ""
    assert opaque_colours(row.state_label.grab().toImage()) == set()
    assert opaque_colours(row.state_dot.grab().toImage()) == set()


def test_a_long_status_label_is_shown_in_full(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel
    label = panel.rows[SKELETON].state_label

    panel.set_status(SKELETON, LayerStatus("none", "Generated · empty", "Not corrected yet"))
    for _ in range(3):  # no relayout from the test: the panel asks for its new width itself
        app.processEvents()

    assert label.width() >= label.fontMetrics().horizontalAdvance("Generated · empty")
    left = label.mapTo(panel, QPoint(0, 0)).x()
    assert left + label.width() <= panel.width()


def test_set_active_gives_only_that_row_the_fill_and_the_bar_in_its_colour(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel

    def bar(row) -> QPoint:
        return row.mapTo(panel, QPoint(0, row.height() // 2))

    def fill(row) -> QPoint:
        return row.mapTo(panel, QPoint(row.width() // 2, 2))

    for active, other, colour in ((SKELETON, MASK, tokens.SKEL), (MASK, SKELETON, tokens.MASK)):
        panel.set_active(active)
        settle(app, page)
        image = panel.grab().toImage()
        on, off = panel.rows[active], panel.rows[other]
        assert (pixel(image, bar(on)), pixel(image, fill(on))) == (colour, tokens.BG_2)
        width = [pixel(image, bar(on) + QPoint(dx, 0)) for dx in (1, 3)]
        assert width == [colour, tokens.BG_2]  # a 2 px bar
        assert (pixel(image, bar(off)), pixel(image, fill(off))) == (tokens.BG_1, tokens.BG_1)
        assert on.is_active() and not off.is_active()


def test_a_click_on_a_row_requests_that_layer_and_leaves_the_active_row_to_the_editor(
    app, make_page
):
    page = make_page((1400, 900))
    panel = page.panel
    panel.set_active(MASK)
    requests = record(panel.activate_requested)

    QTest.mouseClick(panel.rows[SKELETON].name_label, Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel.rows[SKELETON], Qt.MouseButton.RightButton, pos=QPoint(4, 4))
    QTest.mouseClick(panel.rows[MASK], Qt.MouseButton.LeftButton, pos=QPoint(4, 4))

    assert requests == [(SKELETON,), (MASK,)]  # the right click asked for nothing
    assert panel.rows[MASK].is_active() and not panel.rows[SKELETON].is_active()


def test_an_eye_click_requests_the_other_visibility_and_does_not_activate_the_row(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel
    toggles, activations = record(panel.visibility_toggled), record(panel.activate_requested)
    eye = panel.rows[SKELETON].eye_button

    QTest.mouseClick(eye, Qt.MouseButton.LeftButton)
    panel.set_visible_state(SKELETON, False)  # the editor hid it
    QTest.mouseClick(eye, Qt.MouseButton.LeftButton)
    panel.set_visible_state(SKELETON, False)  # the editor kept it hidden
    QTest.mouseClick(eye, Qt.MouseButton.LeftButton)

    assert toggles == [(SKELETON, False), (SKELETON, True), (SKELETON, True)]
    assert activations == []


def test_set_visible_state_changes_only_the_eye_and_emits_nothing(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel
    row = panel.rows[MASK]
    panel.set_active(MASK)
    panel.set_status(MASK, LayerStatus("dirty"))
    toggles, activations = record(panel.visibility_toggled), record(panel.activate_requested)

    def eye_image() -> QImage:
        return row.eye_button.icon().pixmap(row.eye_button.iconSize(), 1.0).toImage()

    shown_eye, shown_tip = eye_image(), row.eye_button.toolTip()
    button = row.eye_button.grab().toImage()
    assert QColor.fromRgba(button.pixel(1, button.height() // 2)).alpha() == 0  # no checked pill
    panel.set_visible_state(MASK, False)
    hidden_eye, hidden_tip = eye_image(), row.eye_button.toolTip()
    panel.set_visible_state(MASK, True)

    assert hidden_eye != shown_eye and eye_image() == shown_eye
    assert "Hide" in shown_tip and "Show" in hidden_tip and "mask" in hidden_tip
    assert all("H: both layers" in tip and "Shift+H" in tip for tip in (shown_tip, hidden_tip))
    assert toggles == [] and activations == []
    assert row.is_active() and row.state_label.text() == "Unsaved"


def test_flash_highlights_the_row_for_about_300_ms_then_restores_it(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel
    panel.set_active(MASK)
    settle(app, page)
    row = panel.rows[SKELETON]
    before = row.grab().toImage()

    start = time.perf_counter()
    panel.flash(SKELETON)
    assert row.grab().toImage() != before
    while row.grab().toImage() != before:
        assert time.perf_counter() - start < 3.0, "the flash never ends"
        QTest.qWait(10)

    assert time.perf_counter() - start >= 0.25


def test_the_panel_takes_its_side_panel_place_and_fits_a_shorter_height(app, make_page):
    page = make_page((1400, 900))
    panel = page.panel
    natural = panel.preferred_height(900)

    assert right(panel) == 1400 - M.margin and panel.height() == natural
    panel.fit_height(natural - 20)
    assert panel.height() == natural - 20
    assert panel.preferred_height(900) == natural
    panel.fit_height(natural + 50)
    assert panel.height() == natural


# ================================================================ focus and the layout
def test_clicking_any_control_leaves_the_keys_on_the_canvas(app, make_page):
    page = make_page((1400, 900))
    controls, panel = page.controls, page.panel
    skeleton = page.layers[SKELETON]
    skeleton.set_tool("eraser")
    controls.show_layer(SKELETON)
    controls.sync(skeleton)
    controls.set_history(True, True)
    controls.set_draft(DraftState(True, 3, False))
    show_active(app, page)
    page.view.setFocus()
    settle(app, page)
    assert QApplication.focusWidget() is page.view
    targets = [
        *controls.tool_buttons.values(),
        visible_size_slider(controls, page.chrome.dock).slider,
        controls.opacity_slider.slider,
        controls.load_button,
        controls.undo_button,
        controls.redo_button,
        controls.clear_icon_button,
        controls.save_button,
        controls.smooth_button,
        controls.finish_button,
        controls.cancel_button,
        panel.rows[MASK],
        panel.rows[SKELETON].name_label,
        panel.rows[MASK].eye_button,
    ]

    for widget in targets:
        assert widget.isVisible(), widget
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        assert QApplication.focusWidget() is page.view, widget
    assert controls.clear_button.focusPolicy() in (Qt.FocusPolicy.TabFocus, Qt.FocusPolicy.NoFocus)


def test_the_panel_and_its_buttons_never_take_the_focus(make_page):
    panel = make_page(show=False).panel

    assert {w.focusPolicy() for w in (panel, *panel.findChildren(QWidget))} == {
        Qt.FocusPolicy.NoFocus
    }


@pytest.mark.parametrize("shown", [MASK, SKELETON])
@pytest.mark.parametrize("size", [(1400, 900), (700, 520)], ids=["1400x900", "700x520"])
def test_the_overlays_never_overlap(app, make_page, size, shown):
    page = make_page(size)
    controls, chrome, panel = page.controls, page.chrome, page.panel
    layer = page.layers[shown]
    layer.set_tool("eraser")
    controls.show_layer(shown)
    controls.sync(layer)
    controls.set_draft(DraftState(True, 3, False) if shown == SKELETON else None)
    panel.set_active(shown)
    panel.set_status(SKELETON, LayerStatus("none", "Generated · empty", "Not corrected yet"))
    chrome.chip.set_text(LONG_NAME)
    chrome.chip.set_state("dirty")
    chrome.chip.set_hint(f"{shown.title()} · Eraser · drag to erase", dot=layer.color)
    chrome.enhance_button.click()
    settle(app, page)

    overlays = [chrome.rail, chrome.dock, chrome.chip, chrome.zoom, chrome.popover, panel]
    if shown == SKELETON:
        overlays.append(controls.prompt)
    assert all(widget.isVisible() for widget in overlays)
    for i, a in enumerate(overlays):
        assert page.rect().contains(a.geometry()), (a, a.geometry())
        for b in overlays[i + 1 :]:
            assert not a.geometry().intersects(b.geometry()), (a, b, a.geometry(), b.geometry())

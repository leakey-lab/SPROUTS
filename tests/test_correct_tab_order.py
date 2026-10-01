"""Tab through the Correct page follows the screen (WCAG 2.4.3).

Qt puts a widget in the focus chain when it is parented, so the page's Tab order was its
build order: canvas, zoom cluster (made with the chrome), rail, dock, the size slider (made
when a tool first shows it, so after everything else), then the Layers panel. Now Tab goes
from the canvas to the polyline prompt when it is shown (top centre, about the line being
drawn), the rail top to bottom (the Enhance popover right after the button that opens it),
the dock left to right, the zoom cluster (bottom right), then the Layers panel (right edge);
Shift+Tab walks back. A size slider made later takes its place in the dock. Clicks still
never take the focus from the canvas (``keep_canvas_focus``), whose keys keep working.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QAbstractButton, QApplication, QWidget  # noqa: E402

from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.skeleton_tools import ERASER, POLYLINE  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    activate,
    click,
    drag,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    settle,
)

Focus = Qt.FocusPolicy


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
def page(make_editor, tmp_path):
    """The page on an image, active, the keys on its canvas."""
    project = new_project(tmp_path)
    editor = make_editor(project.a, project.folder)
    activate(editor)
    return editor


def walk(editor, key=Qt.Key.Key_Tab, presses: int = 80) -> list[QWidget]:
    """The widgets Tab (or Shift+Tab) visits from the canvas, until it is back there."""
    editor.graphics_view.setFocus(Qt.FocusReason.TabFocusReason)
    seen = []
    for _ in range(presses):
        QTest.keyClick(QApplication.focusWidget(), key)
        widget = QApplication.focusWidget()
        if widget is editor.graphics_view:
            return seen
        seen.append(widget)
    raise AssertionError(f"Tab never came back to the canvas: {seen}")


def rail(editor, tool: tuple[str, str]) -> list[QWidget]:
    """The rail's Tab stops: the checked tool (arrow keys move in the group), Pan, Enhance."""
    return [editor.controls.tool_buttons[tool], editor.pan_button, editor.enhance_button]


def dock(editor, size_slider=None) -> list[QWidget]:
    """The dock's enabled controls, left to right."""
    controls = editor.controls
    parts = [] if size_slider is None else [size_slider.slider]
    parts.append(controls.opacity_slider.slider)
    buttons = (controls.load_button, controls.undo_button, controls.redo_button)
    parts += [button for button in buttons if button.isEnabled()]
    return parts + [controls.clear_icon_button, controls.save_button]


def zoom(editor) -> list[QWidget]:
    cluster = editor.zoom_cluster
    return [cluster.zoom_out_button, cluster.zoom_pct_label, cluster.zoom_in_button,
            cluster.fit_button]  # fmt: skip


def layers(editor) -> list[QWidget]:
    rows = editor.layer_panel.rows
    return [rows[MASK], rows[MASK].eye_button, rows[SKELETON], rows[SKELETON].eye_button]


def only_size_slider(editor):
    (slider,) = [s for s in editor.controls.size_sliders.values() if s.isVisibleTo(editor)]
    return slider


def test_tab_goes_rail_dock_zoom_then_layers(page):
    expected = [
        *rail(page, (MASK, "brush")),
        *dock(page, only_size_slider(page)),
        *zoom(page),
        *layers(page),
    ]

    assert walk(page) == expected


def test_shift_tab_walks_the_same_order_back(page):
    forward = walk(page)

    assert walk(page, Qt.Key.Key_Backtab) == forward[::-1]


def test_a_size_slider_made_later_takes_its_place_in_the_dock(page):
    """The skeleton eraser's slider is made the first time the eraser is chosen, after
    the whole page was built (it used to come after the Layers panel)."""
    page.request_tool(SKELETON, ERASER)
    settle(page)
    eraser_size = only_size_slider(page)
    drag(page, (50, 110), (90, 110))  # erases the generated line: Undo joins the dock
    settle(page)

    expected = [
        *rail(page, (SKELETON, ERASER)),
        *dock(page, eraser_size),
        *zoom(page),
        *layers(page),
    ]
    assert page.controls.undo_button.isEnabled() and page.controls.load_button.isEnabled()
    assert walk(page) == expected


@pytest.mark.parametrize(
    "tool",
    [(MASK, "brush"), (MASK, "eraser"), (MASK, "fill"), (SKELETON, "select"),
     (SKELETON, ERASER), (SKELETON, POLYLINE), (SKELETON, "connect")],
)  # fmt: skip
def test_tab_reaches_the_tools_at_the_chosen_one(page, tool):
    """The tools are a radio group: Tab stops at the checked one (Qt's own rule was the
    tool focused last, else the first), in the rail's place."""
    page.request_tool(*tool)
    settle(page)

    chain = walk(page)

    assert chain[:3] == rail(page, tool)
    stops = [w for w in chain if w in page.controls.tool_buttons.values()]
    assert stops == [page.controls.tool_buttons[tool]]


def test_the_polyline_prompt_comes_first_while_a_line_is_drawn(page):
    page.request_tool(SKELETON, POLYLINE)
    for point in ((40, 40), (90, 60)):
        click(page, point)
    settle(page)
    controls = page.controls
    assert controls.prompt.isVisible()

    chain = walk(page)

    prompt = [controls.finish_button, controls.cancel_button, controls.smooth_button]
    assert chain[:3] == prompt
    assert chain[3:6] == rail(page, (SKELETON, POLYLINE))


def test_the_enhance_popover_follows_the_button_that_opens_it(page):
    page.enhance_button.click()
    settle(page)
    popover = page.enhance_popover
    assert popover.isVisible()

    chain = walk(page)

    start = chain.index(page.enhance_button) + 1
    inside = [w for w in chain if popover.isAncestorOf(w)]
    assert inside and chain[start : start + len(inside)] == inside
    assert chain[start + len(inside)] is only_size_slider(page).slider  # then the dock


def test_clicks_still_leave_the_keys_on_the_canvas(page):
    overlays = (page.tool_rail, page.dock, page.zoom_cluster, page.layer_panel)
    for overlay in overlays:
        for widget in (overlay, *overlay.findChildren(QWidget)):
            assert not widget.focusPolicy() & Focus.ClickFocus, widget

    for button in (page.zoom_cluster.zoom_in_button, page.controls.opacity_slider.slider):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert page.graphics_view.hasFocus(), button
    buttons = [w for w in page.dock.findChildren(QAbstractButton) if w.isEnabled()]
    assert buttons


def test_the_page_keys_work_after_tabbing_back_to_the_canvas(page):
    walk(page)  # all the way round, back on the canvas
    assert page.graphics_view.hasFocus()

    QTest.keyClick(page.graphics_view, Qt.Key.Key_2)
    assert page.active_layer_id == SKELETON

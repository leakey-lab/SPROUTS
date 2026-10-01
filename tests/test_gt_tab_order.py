"""Tab through the Trace GT page follows the screen (WCAG 2.4.3), as in Correct.

Qt puts a widget in the focus chain when it is parented, so the page's Tab order was its
build order: canvas, zoom cluster (made with the chrome), rail, dock, then the class panel.
Now Tab goes from the canvas to the rail top to bottom (stopping at the chosen tool, as a
radio group; the Enhance popover right after the button that opens it), the dock left to
right, the zoom cluster (bottom right), then the class panel (right edge: the gear, then
each "checked" box in class order); Shift+Tab walks back. Classes changed in the class
editor keep that place. Clicks still never take the focus from the canvas.
"""

from __future__ import annotations

import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from app.gui.raster_editor_base import RasterEditorBase  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

H, W = 60, 80
Focus = Qt.FocusPolicy
KEYS = ("root", "nodule", "root_tip")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


def _shown(editor, tmp_path):
    """Show ``editor`` active on a fresh image, the keys on its canvas."""
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    editor.resize(1100, 720)
    editor.show()
    editor.activateWindow()
    assert QTest.qWaitForWindowActive(editor, 3000)
    editor.load_image(path)
    QApplication.processEvents()
    editor.graphics_view.setFocus()


def _delete(editor) -> None:
    editor._set_clean()  # nothing to ask about
    editor.close()
    editor.deleteLater()


@pytest.fixture
def page(app, tmp_path):
    """A shown, active Trace GT page on an image; deleted afterwards (a weak proxy:
    pytest keeps a fixture's value past its teardown, see tests/qt_teardown.py)."""
    editor = GtTracingInterface()
    _shown(editor, tmp_path)
    yield weakref.proxy(editor)
    _delete(editor)
    del editor
    flush_deleted()


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


def rail(editor, tool=None) -> list[QWidget]:
    """The rail's Tab stops: the chosen tool, Pan, Show tracing, Enhance."""
    tool = tool or editor.brush_button
    return [tool, editor.pan_button, editor.mask_visible_button, editor.enhance_button]


def dock(editor) -> list[QWidget]:
    """The dock's enabled controls, left to right."""
    parts = [editor.size_slider, editor.opacity_slider]
    parts += [b for b in (editor.undo_button, editor.redo_button) if b.isEnabled()]
    return parts + [editor.clear_icon_button, editor.save_button]


def zoom(editor) -> list[QWidget]:
    return [editor.zoom_out_button, editor.zoom_pct_label, editor.zoom_in_button,
            editor.fit_button]  # fmt: skip


def classes(editor, keys=KEYS) -> list[QWidget]:
    panel = editor.class_panel
    return [panel._gear, *(panel._rows[key].checkbox for key in keys)]


def test_tab_goes_rail_dock_zoom_then_classes(page):
    expected = [*rail(page), *dock(page), *zoom(page), *classes(page)]

    assert walk(page) == expected


def test_shift_tab_walks_the_same_order_back(page):
    forward = walk(page)

    assert walk(page, Qt.Key.Key_Backtab) == forward[::-1]


@pytest.mark.parametrize("tool", ["brush_button", "eraser_button", "fill_button"])
def test_tab_reaches_the_tools_at_the_chosen_one(page, tool):
    """The tools are a radio group: Tab stops at the checked one (Qt's own rule was the
    tool focused last, else the first), in the rail's place."""
    button = getattr(page, tool)
    button.setChecked(True)  # as B, E and F do

    chain = walk(page)

    assert chain[:4] == rail(page, button)
    tools = page.tool_button_group.buttons()
    assert [w for w in chain if w in tools] == [button]


def test_undo_and_redo_take_their_place_in_the_dock(page):
    page.begin_stroke(QPoint(20, 20))
    page.end_stroke()
    assert page.undo_button.isEnabled()
    chain = walk(page)
    assert chain[4:9] == [page.size_slider, page.opacity_slider, page.undo_button,
                          page.clear_icon_button, page.save_button]  # fmt: skip

    page.undo()
    chain = walk(page)
    assert chain[4:9] == [page.size_slider, page.opacity_slider, page.redo_button,
                          page.clear_icon_button, page.save_button]  # fmt: skip


def test_the_enhance_popover_follows_the_button_that_opens_it(page):
    page.enhance_button.click()
    QApplication.processEvents()
    popover = page.enhance_popover
    assert popover.isVisible()

    chain = walk(page)

    start = chain.index(page.enhance_button) + 1
    inside = [w for w in chain if popover.isAncestorOf(w)]
    assert inside and chain[start : start + len(inside)] == inside
    assert chain[start + len(inside)] is page.size_slider  # then the dock


def test_new_classes_keep_their_place_after_the_zoom_cluster(page):
    """The class editor's changes rebuild the rows: the new boxes stay after the gear."""
    page._set_registry(page.registry.add(key="hypha", name="Hypha", color="#3fa7ff"))
    QApplication.processEvents()  # the new rows show on the next pass

    expected = [*rail(page), *dock(page), *zoom(page), *classes(page, (*KEYS, "hypha"))]
    assert walk(page) == expected


def test_clicks_still_leave_the_keys_on_the_canvas(page):
    overlays = (page.tool_rail, page.dock, page.zoom_cluster, page.class_panel)
    for overlay in overlays:
        for widget in (overlay, *overlay.findChildren(QWidget)):
            assert not widget.focusPolicy() & Focus.ClickFocus, widget

    for widget in (page.zoom_in_button, page.opacity_slider, page.eraser_button):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        assert page.graphics_view.hasFocus(), widget


def test_the_page_keys_work_after_tabbing_back_to_the_canvas(page):
    walk(page)  # all the way round, back on the canvas
    assert page.graphics_view.hasFocus()

    QTest.keyClick(page.graphics_view, Qt.Key.Key_2)

    assert page.class_panel.active_key() == "nodule"


class _Plain(RasterEditorBase):
    """A one-value editor on the base alone (no side panel)."""

    EDITOR_NOUN = "probe"

    def _open_document(self, image_path, size):
        return np.zeros((size.height(), size.width()), np.uint8), False

    def save_mask(self, save_path=None):
        self._set_clean()
        return True

    def _plane_lut(self):
        lut = np.zeros((256, 4), np.uint8)
        lut[1:] = (0, 255, 0, 255)
        return lut

    def _argb_to_plane(self, argb, rect):
        return (argb[:, :, 3] > 0).astype(np.uint8)

    def _fill_source(self, plane):
        return (plane > 0).astype(np.uint8) * 255, 1


def test_the_base_editor_orders_its_own_chrome(app, tmp_path):
    editor = _Plain()
    try:
        _shown(editor, tmp_path)
        assert walk(editor) == [*rail(editor), *dock(editor), *zoom(editor)]
    finally:
        _delete(editor)
        del editor
        flush_deleted()

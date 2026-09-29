"""Skeleton editor tools, canvas and overlays.

UXE-05 (fit / zoom cluster / hand toggle), UXE-06 (IconButton rail, tokens),
UXE-07 (tool keys), UXE-09 (OverlayLayout), UXE-10 (eraser ring), UXE-12 (Delete
removes the picked branch), UXE-13 (overlay halo), PE-02 (gap-free eraser with a
trailing flush), AR-12 (draft / tools / no dead code), AR-13 (Enhance API).
"""

from __future__ import annotations

import os
import re

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.skeleton_correction_interface import SkeletonCorrectionInterface  # noqa: E402

SRC = os.path.join(
    os.path.dirname(__file__), os.pardir, "app", "gui", "skeleton_correction_interface.py"
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def editor(app, tmp_path):
    img = np.full((480, 640, 3), 90, np.uint8)
    path = str(tmp_path / "a.png")
    cv2.imwrite(path, img)
    iface = SkeletonCorrectionInterface()
    iface.resize(1100, 800)
    iface.show()
    app.processEvents()
    iface.load_image(path, str(tmp_path))
    app.processEvents()
    yield iface
    iface.close()


def _click_tool(iface, button):
    button.click()
    QApplication.processEvents()


# ------------------------------------------------------------------ rail (UXE-06/07)
def test_rail_uses_icon_buttons_with_key_tooltips(app):
    from app.gui.widgets import IconButton

    iface = SkeletonCorrectionInterface()
    expected = {
        iface.select_button: "(V)",
        iface.eraser_button: "(E)",
        iface.polyline_button: "(P)",
        iface.connect_button: "(C)",
        iface.smooth_polyline_toggle: "(S)",
    }
    for button, key in expected.items():
        assert isinstance(button, IconButton)
        assert key in button.toolTip()
    assert isinstance(iface.mode_toggle, IconButton)
    assert "Space" in iface.mode_toggle.toolTip()
    assert iface.tool_rail.width() <= 60


def test_no_dracula_or_emoji_styling_left():
    text = open(SRC, encoding="utf-8").read()
    for literal in ("#50fa7b", "#ff5555", "#2d2d2d", "#44475a", "#6272a4", "#282a36"):
        assert literal not in text.lower()
    assert not re.search(r"#[0-9a-fA-F]{6}\b", text), "colours must come from tokens"
    for emoji in ("🖱", "🧽", "📏", "🔗", "🔒", "✋", "〰"):
        assert emoji not in text


def test_tool_keys_switch_tools(editor):
    view = editor.graphics_view
    for key, tool in (
        (Qt.Key.Key_E, editor.TOOL_ERASER),
        (Qt.Key.Key_P, editor.TOOL_POLYLINE),
        (Qt.Key.Key_C, editor.TOOL_CONNECT),
        (Qt.Key.Key_V, editor.TOOL_SELECT),
    ):
        QTest.keyClick(view, key)
        assert editor.current_tool == tool
    assert editor.select_button.isChecked()


def test_smooth_key_toggles_only_in_polyline(editor):
    view = editor.graphics_view
    QTest.keyClick(view, Qt.Key.Key_S)
    assert not editor.smooth_polyline_toggle.isChecked()
    QTest.keyClick(view, Qt.Key.Key_P)
    QTest.keyClick(view, Qt.Key.Key_S)
    assert editor.smooth_polyline_toggle.isChecked()


def test_bracket_keys_resize_the_eraser(editor):
    view = editor.graphics_view
    QTest.keyClick(view, Qt.Key.Key_E)
    start = editor.eraser_slider.value()
    QTest.keyClick(view, Qt.Key.Key_BracketRight)
    assert editor.eraser_slider.value() > start
    QTest.keyClick(view, Qt.Key.Key_BracketLeft)
    QTest.keyClick(view, Qt.Key.Key_BracketLeft)
    assert editor.eraser_slider.value() < start


def test_live_shortcuts_are_documented_for_correct_skeleton(app):
    from PyQt6.QtGui import QShortcut

    from app.gui import shortcuts as sc

    iface = SkeletonCorrectionInterface()
    live = {s.key().toString() for s in iface.findChildren(QShortcut)}
    documented = {k for s in sc.specs_for(sc.CORRECT_SKELETON) for k in s.keys}
    assert "Ctrl+Shift+Z" in live
    assert live <= documented | {"PgDown", "PgUp"}


# ------------------------------------------------------------------ canvas (UXE-05)
def test_image_opens_fitted_with_a_zoom_readout(editor):
    from app.gui.widgets import ZoomCluster

    assert isinstance(editor.zoom_cluster, ZoomCluster)
    view = editor.graphics_view
    shown = view.mapFromScene(QPointF(0, 0)), view.mapFromScene(QPointF(640, 480))
    width = shown[1].x() - shown[0].x()
    assert width > 0.6 * view.viewport().width() or (
        shown[1].y() - shown[0].y() > 0.6 * view.viewport().height()
    )
    assert editor.zoom_cluster.zoom_pct_label.text().endswith("%")
    assert editor.zoom_cluster.zoom_pct_label.text() == f"{round(view.zoom_factor * 100)}%"


def test_next_image_opens_fitted_again(editor, tmp_path, app):
    view = editor.graphics_view
    fitted = view.zoom_factor
    view.set_zoom(7.0)
    other = str(tmp_path / "b.png")
    cv2.imwrite(other, np.full((480, 640, 3), 40, np.uint8))
    editor.load_image(other, str(tmp_path))
    app.processEvents()
    assert view.zoom_factor == pytest.approx(fitted, rel=0.05)


def test_hand_toggle_checked_means_pan(editor):
    assert not editor.mode_toggle.isChecked()
    editor.mode_toggle.click()
    assert editor.mode_toggle.isChecked()
    assert editor.pan_mode and editor.graphics_view.pan_mode
    editor.mode_toggle.click()
    assert not editor.pan_mode and not editor.graphics_view.pan_mode


def test_overlays_never_collide(editor, app):
    editor.eraser_button.click()
    for size in ((1400, 900), (1000, 700), (820, 560), (700, 520)):
        editor.resize(*size)
        app.processEvents()
        editor._reposition_overlays()
        shown = [
            w
            for w in (
                editor.tool_rail,
                editor.dock,
                editor.zoom_cluster,
                editor.hint_panel,
            )
            if w.isVisible()
        ]
        host = editor.graphics_view.rect()
        for i, a in enumerate(shown):
            assert host.contains(a.geometry()), (size, a)
            for b in shown[i + 1 :]:
                assert not a.geometry().intersects(b.geometry()), (size, a, b)


# ------------------------------------------------------------------ eraser (PE-02, UXE-10)
def _line_mask(editor):
    editor.model.push_undo()
    mask = editor.model.mask
    mask[240, 100:540] = 255
    editor.model.mask = mask
    editor.render_skeleton()


def test_fast_eraser_drag_leaves_no_fragments(editor):
    _line_mask(editor)
    editor.eraser_button.click()
    editor.eraser_slider.setValue(6)
    editor.on_tool_mouse_press(QPoint(120, 240), None)
    for x in range(145, 521, 25):
        editor.on_tool_mouse_move(QPoint(x, 240), None)
    editor.on_tool_mouse_release(QPoint(520, 240), None)
    assert int((editor.model.mask[240, 120:521] > 0).sum()) == 0


def test_eraser_display_flushes_the_last_move_without_release(editor, app):
    _line_mask(editor)
    editor.eraser_button.click()
    editor.on_tool_mouse_press(QPoint(120, 240), None)
    app.processEvents()
    editor.on_tool_mouse_move(QPoint(200, 240), None)
    key = editor.skeleton_item.pixmap().cacheKey()
    QTest.qWait(40)
    assert editor.skeleton_item.pixmap().cacheKey() != key


def test_eraser_ring_matches_the_footprint_at_any_zoom(editor):
    editor.eraser_button.click()
    editor.eraser_slider.setValue(20)
    view = editor.graphics_view
    for zoom in (1.0, 4.0):
        view.set_zoom(zoom)
        editor.on_tool_mouse_move(QPoint(300, 200), None)
        ring = editor.eraser_ring
        assert ring.is_visible()
        assert ring.rect().width() == pytest.approx(20)
        on_screen = view.mapFromScene(ring.rect()).boundingRect().width()
        assert on_screen == pytest.approx(20 * zoom, abs=2)
    assert not hasattr(editor, "brush_cursor")


def test_eraser_ring_hidden_in_other_tools_and_pan(editor):
    editor.eraser_button.click()
    editor.on_tool_mouse_move(QPoint(300, 200), None)
    assert editor.eraser_ring.is_visible()
    editor.mode_toggle.click()
    assert not editor.eraser_ring.is_visible()
    editor.mode_toggle.click()
    editor.polyline_button.click()
    editor.on_tool_mouse_move(QPoint(300, 200), None)
    assert not editor.eraser_ring.is_visible()


# ------------------------------------------------------------------ Select + Delete (UXE-12)
def test_delete_removes_the_picked_branch_and_undo_restores(editor):
    _line_mask(editor)
    editor.model.skeletonize()
    before = editor.model.mask.copy()
    notices = []
    editor.notice.connect(lambda msg, kind: notices.append(msg))
    editor.select_button.click()
    editor.on_tool_mouse_press(QPoint(300, 242), None)
    assert editor.draft.edit_original is not None
    QTest.keyClick(editor.graphics_view, Qt.Key.Key_Delete)
    assert not editor.model.mask[240, 110:530].any()
    assert editor.draft.points == []
    assert notices and "Ctrl+Z" in notices[-1]
    editor.undo()
    assert np.array_equal(editor.model.mask, before)


def test_delete_with_nothing_picked_is_a_no_op(editor):
    _line_mask(editor)
    before = editor.model.mask.copy()
    QTest.keyClick(editor.graphics_view, Qt.Key.Key_Delete)
    assert np.array_equal(editor.model.mask, before)


def test_dead_selection_code_is_gone(app):
    iface = SkeletonCorrectionInterface()
    for name in ("_nearest_skeleton_pixel", "_set_selected_point", "_loaded_skeleton_path"):
        assert not hasattr(iface, name)


# ------------------------------------------------------------------ polyline draft (AR-12)
def test_commit_polyline_draws_and_escape_resets_the_draft(editor):
    editor.polyline_button.click()
    editor.on_tool_mouse_press(QPoint(50, 50), None)
    editor.on_tool_mouse_press(QPoint(200, 60), None)
    assert len(editor.draft.points) == 2
    QTest.keyClick(editor.graphics_view, Qt.Key.Key_Escape)
    assert editor.draft.points == [] and editor.polyline_prompt.isHidden()
    editor.on_tool_mouse_press(QPoint(50, 50), None)
    editor.on_tool_mouse_press(QPoint(200, 60), None)
    QTest.keyClick(editor.graphics_view, Qt.Key.Key_Return)
    assert editor.model.mask.any()
    assert editor.draft.points == []


def test_clear_uses_the_model_and_is_undoable(editor):
    _line_mask(editor)
    editor.clear_skeleton()
    assert not editor.model.mask.any()
    editor.undo()
    assert editor.model.mask.any()


def test_connect_drag_joins_two_endpoints(editor):
    editor.model.push_undo()
    mask = editor.model.mask
    mask[100, 100:200] = 255
    mask[100, 260:360] = 255
    editor.model.mask = mask
    editor.render_skeleton()
    editor.connect_button.click()
    editor.on_tool_mouse_press(QPoint(199, 100), None)
    editor.on_tool_mouse_move(QPoint(230, 100), None)
    editor.on_tool_mouse_release(QPoint(260, 100), None)
    assert editor.model.mask[100, 200:260].all()


# ------------------------------------------------------------------ halo (UXE-13)
def test_overlay_halo_surrounds_the_line_only():
    from app.gui.skeleton_overlay import HALO_INDEX, LINE_INDEX, overlay_indices

    mask = np.zeros((9, 9), np.uint8)
    mask[4, 2:7] = 255
    idx = overlay_indices(mask)
    assert (idx == LINE_INDEX).sum() == 5 and np.array_equal(idx == LINE_INDEX, mask > 0)
    halo = idx == HALO_INDEX
    ring = cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0
    assert np.array_equal(halo, ring & (mask == 0))
    assert mask[4, 2] == 255  # input untouched


def test_halo_is_display_only(editor, tmp_path):
    _line_mask(editor)
    before = editor.model.mask.copy()
    editor.render_skeleton()
    assert np.array_equal(editor.model.mask, before)


# ------------------------------------------------------------------ enhance (AR-13)
def test_enhancement_sticks_across_images(editor, tmp_path, app):
    calls = []
    editor.norm_controls.is_active = lambda: True
    real = editor.norm_controls.display_pixmap

    def spy(path):
        calls.append(path)
        return real(path)

    editor.norm_controls.display_pixmap = spy
    other = str(tmp_path / "c.png")
    cv2.imwrite(other, np.full((480, 640, 3), 60, np.uint8))
    editor.load_image(other, str(tmp_path))
    assert calls == [other]


def test_touching_endpoint_markers_stay_filled(app):
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.skeleton_overlay import SkeletonDecorations

    deco = SkeletonDecorations(QGraphicsScene())
    deco.set_endpoints([(10, 10), (12, 10)])
    assert deco.endpoints_item.path().contains(QPointF(11, 10))

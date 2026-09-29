"""Mask raster editor: canvas item, patch undo, saving, keys, focus, layout.

Findings PE-04, PE-06, PE-07, UI-03, UXE-02/RB-05, RB-11, UXE-03, UXE-07, UXE-08/09 and
AR-13 of the UI/perf audit. The Trace GT cases (and PE-13, AR-11) live in
tests/test_raster_editor_behaviour.py, with the GT editor's own base.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QShortcut  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui import shortcuts  # noqa: E402
from app.gui.canvas_item import CanvasPixmapItem  # noqa: E402
from app.gui.mask_drawing_tools import pixmap_to_argb_array  # noqa: E402
from app.gui.mask_tracing_interface import MaskTracingInterface  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


def _image(folder, name="plot_001.png", w=80, h=60, value=90):
    path = str(folder / name)
    cv2.imwrite(path, np.full((h, w, 3), value, np.uint8))
    return path


def _editor(cls, image_path=None, size=(900, 700)):
    iface = cls()
    iface.resize(*size)
    if image_path:
        iface.load_image(image_path)
    return iface


def _stroke(iface, *points, size=None):
    if size is not None:
        iface.size_slider.setValue(size)
    iface.begin_stroke(QPoint(*points[0]))
    for p in points[1:]:
        iface.continue_stroke(QPoint(*p))
    iface.end_stroke()


def _alpha(iface):
    return pixmap_to_argb_array(iface.mask_pixmap)[:, :, 3]


def _answer(monkeypatch, *answers):
    seen, queue = [], list(answers)

    def _exec(box):
        seen.append(box.windowTitle())
        if not queue:
            pytest.fail(f"unexpected prompt {box.text()!r}")
        return queue.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return seen


def _show_active(iface):
    iface.show()
    iface.activateWindow()
    assert QTest.qWaitForWindowActive(iface, 3000)
    QApplication.processEvents()


# ------------------------------------------------------------------ PE-04
def test_strokes_paint_in_place_without_deep_copies(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path, w=640, h=480))
    assert isinstance(iface.mask_item, CanvasPixmapItem)
    serial = iface.mask_pixmap.cacheKey() >> 32
    iface.size_slider.setValue(12)
    iface.begin_stroke(QPoint(10, 10))
    for i in range(50):
        iface.continue_stroke(QPoint(10 + i * 5, 10 + i * 3))
        assert iface.mask_pixmap.cacheKey() >> 32 == serial
    iface.end_stroke()
    iface.mask_visible_button.setChecked(False)
    assert not iface.mask_item.isVisible()
    iface.opacity_slider.setValue(30)
    assert abs(iface.mask_item.opacity() - 0.3) < 1e-6


# ------------------------------------------------------------------ PE-06
def _random_ops(iface, rng, n):
    w, h = iface.mask_pixmap.width(), iface.mask_pixmap.height()
    for _ in range(n):
        op = rng.integers(0, 10)
        iface.eraser_button.setChecked(op in (6, 7))
        iface.fill_button.setChecked(op == 8)
        if op == 9:
            iface.clear_mask()
            continue
        pts = [(int(rng.integers(0, w)), int(rng.integers(0, h))) for _ in range(4)]
        _stroke(iface, *pts, size=int(rng.integers(1, 15)))
    iface.brush_button.setChecked(True)


def test_undo_redo_matches_full_snapshots(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path, w=300, h=200))
    rng = np.random.default_rng(7)
    states = [pixmap_to_argb_array(iface.mask_pixmap)]
    for _ in range(40):
        before = len(iface.undo_stack)
        _random_ops(iface, rng, 1)
        if len(iface.undo_stack) > before:  # a fill outside every outline records nothing
            states.append(pixmap_to_argb_array(iface.mask_pixmap))
        else:
            assert np.array_equal(pixmap_to_argb_array(iface.mask_pixmap), states[-1])
    depth = len(iface.undo_stack)
    assert depth == len(states) - 1 > 30
    for step in range(depth):
        iface.undo()
        assert np.array_equal(pixmap_to_argb_array(iface.mask_pixmap), states[-2 - step]), step
    for step in range(depth):
        iface.redo()
        target = states[len(states) - depth + step]
        assert np.array_equal(pixmap_to_argb_array(iface.mask_pixmap), target), step


def test_history_of_small_strokes_on_a_big_canvas_stays_small(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path, w=2048, h=1536))
    for i in range(25):
        _stroke(iface, (100, 40 + i * 50), (400, 45 + i * 50), size=8)
    assert len(iface.undo_stack) == 25
    assert iface.edits.nbytes < 1024 * 1024


# ------------------------------------------------------------------ PE-07 / RB-11
def test_saved_mask_is_compressed_and_pixel_identical(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path, w=640, h=480))
    _stroke(iface, (10, 10), (600, 400), size=9)
    expected = _alpha(iface) > 0
    assert iface.save_mask()
    path = tmp_path / "mask" / "plot_001.png"
    assert os.path.getsize(path) < 64 * 1024
    rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(rgba[:, :, 3] == 255, expected)
    assert set(np.unique(rgba)) <= {0, 255}
    assert np.array_equal(rgba[:, :, :3].max(axis=2) == 255, expected)
    assert os.listdir(tmp_path / "mask") == ["plot_001.png"]


def test_unwritable_mask_folder_is_a_notice_not_a_crash(app, tmp_path, monkeypatch):
    iface = _editor(MaskTracingInterface, _image(tmp_path))
    notices = []
    iface.notice.connect(lambda msg, kind: notices.append(kind))
    _stroke(iface, (10, 10), (40, 10), size=6)

    def _denied(*_a, **_k):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", _denied)
    assert iface.save_mask() is False
    assert notices == ["danger"] and iface.has_unsaved_changes()


# ------------------------------------------------------------------ UXE-02 / RB-05
def test_reselecting_the_open_image_keeps_unsaved_strokes(app, tmp_path):
    path = _image(tmp_path)
    iface = _editor(MaskTracingInterface, path)
    _stroke(iface, (10, 10), (40, 10), size=6)
    iface.load_image(path)  # the autouse guard fails on any prompt
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 25] > 0


def test_failed_mask_save_keeps_the_open_image(app, tmp_path, monkeypatch):
    path, other = _image(tmp_path), _image(tmp_path, "plot_002.png")
    iface = _editor(MaskTracingInterface, path)
    _stroke(iface, (10, 10), (40, 10), size=6)
    os.makedirs(tmp_path / "mask" / "plot_001.png")  # a folder where the file must go
    save, keep = QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel
    seen = _answer(monkeypatch, save, keep)
    iface.load_image(other)
    assert seen == ["Unsaved mask", "Mask not saved"]
    assert iface.current_image_path == path
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 25] > 0


def test_cancel_on_the_prompt_keeps_editing(app, tmp_path, monkeypatch):
    path, other = _image(tmp_path), _image(tmp_path, "plot_002.png")
    iface = _editor(MaskTracingInterface, path)
    _stroke(iface, (10, 10), (40, 10), size=6)
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    assert iface.maybe_save_changes() is False
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    assert iface.reset() is False
    assert iface.current_image_path == path and iface.has_unsaved_changes()
    _answer(monkeypatch, QMessageBox.StandardButton.Discard)
    iface.load_image(other)
    assert iface.current_image_path == other and not iface.has_unsaved_changes()


# ------------------------------------------------------------------ UI-03 / UXE-07
def _shortcut_keys(iface):
    keys = [s.key().toString() for s in iface.findChildren(QShortcut) if s.parent() is iface]
    return keys


def test_editor_keys_come_from_the_registry_once_each(app):
    iface = MaskTracingInterface()
    keys = _shortcut_keys(iface)
    assert len(keys) == len(set(keys)), keys
    declared = {
        k
        for s in shortcuts.specs_for(shortcuts.CORRECT_MASK)
        if s.kind == shortcuts.SHORTCUT
        for k in s.keys
    }
    assert declared <= set(keys)
    assert set(keys) - declared <= {"PgDown", "PgUp"}
    assert "Ctrl+Y" in iface.redo_button.toolTip()


@pytest.mark.parametrize("key", ["Ctrl+Y", "Ctrl+Shift+Z"])
def test_redo_keys_redo(app, tmp_path, key):
    iface = _editor(MaskTracingInterface, _image(tmp_path))
    _show_active(iface)
    _stroke(iface, (10, 10), (40, 10), size=6)
    QTest.keyClick(iface.graphics_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert not _alpha(iface).any() and len(iface.redo_stack) == 1
    mods = Qt.KeyboardModifier.ControlModifier
    if "Shift" in key:
        mods |= Qt.KeyboardModifier.ShiftModifier
    target = Qt.Key.Key_Y if key.endswith("Y") else Qt.Key.Key_Z
    QTest.keyClick(iface.graphics_view, target, mods)
    assert len(iface.redo_stack) == 0 and _alpha(iface)[10, 25] > 0
    iface._set_clean()
    iface.close()


def test_undo_redo_buttons_follow_the_history(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path))
    assert not iface.undo_button.isEnabled() and not iface.redo_button.isEnabled()
    _stroke(iface, (10, 10), (40, 10), size=6)
    assert iface.undo_button.isEnabled() and not iface.redo_button.isEnabled()
    iface.undo()
    assert not iface.undo_button.isEnabled() and iface.redo_button.isEnabled()


# ------------------------------------------------------------------ UXE-03
def test_dock_clicks_keep_keyboard_focus_on_the_canvas(app, tmp_path):
    iface = _editor(MaskTracingInterface, _image(tmp_path))
    _show_active(iface)
    iface.graphics_view.setFocus()
    saved = []
    iface.mask_saved.connect(saved.append)
    _stroke(iface, (10, 10), (40, 10), size=6)
    QTest.mouseClick(iface.save_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(iface.size_slider, Qt.MouseButton.LeftButton)
    assert QApplication.focusWidget() is iface.graphics_view
    assert len(saved) == 1
    iface.enhance_popover.toggle()
    iface.norm_controls.method_combo.setFocus()
    iface.enhance_popover.toggle()
    QApplication.processEvents()
    assert QApplication.focusWidget() is iface.graphics_view
    iface.close()


# ------------------------------------------------------------------ UXE-08 / UXE-09
def _image_rect_in(iface) -> QRect:
    view = iface.graphics_view
    poly = view.mapFromScene(QRectF(iface.image_pixmap.rect()))
    rect = poly.boundingRect()
    return QRect(view.mapTo(iface, rect.topLeft()), rect.size())


@pytest.mark.parametrize("size", [(907, 562), (1018, 662), (1658, 942)])
def test_fit_keeps_the_image_clear_of_the_overlays(app, tmp_path, size):
    iface = _editor(MaskTracingInterface, _image(tmp_path, w=640, h=480), size=size)
    iface.show()
    for _ in range(3):
        QApplication.processEvents()
    iface.graphics_view.fit_to_view()
    QApplication.processEvents()
    image = _image_rect_in(iface)
    for overlay in (iface.dock, iface.status_chip):
        assert not image.intersects(overlay.geometry()), (overlay, image, overlay.geometry())
    visible = [
        w
        for w in (iface.tool_rail, iface.dock, iface.zoom_cluster, iface.status_chip)
        if w.isVisible()
    ]
    for i, a in enumerate(visible):
        assert iface.rect().contains(a.geometry()), a
        for b in visible[i + 1 :]:
            assert not a.geometry().intersects(b.geometry()), (a, b)
    assert iface.zoom_pct_label.text() == f"{round(iface.graphics_view.zoom_factor * 100)}%"
    iface._set_clean()
    iface.close()


# ------------------------------------------------------------------ AR-13
def test_enhancement_renders_live_and_sticks_across_images(app, tmp_path):
    first = str(tmp_path / "a.png")
    gradient = np.tile(np.linspace(60, 120, 80, dtype=np.uint8), (60, 1))
    cv2.imwrite(first, np.dstack([gradient] * 3))
    second = str(tmp_path / "b.png")
    cv2.imwrite(second, np.dstack([gradient] * 3))
    iface = _editor(MaskTracingInterface, first)
    raw = iface.image_pixmap.toImage()
    controls = iface.norm_controls
    controls.method_combo.setCurrentText("Contrast Stretching")
    controls.apply_button.click()
    assert controls.is_active()
    assert iface.image_item.pixmap().toImage() != raw
    iface.load_image(second)
    assert iface.image_item.pixmap().toImage() != raw  # still enhanced

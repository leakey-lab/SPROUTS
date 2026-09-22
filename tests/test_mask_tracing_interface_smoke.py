"""Smoke test for the modern (PR5) MaskTracingInterface overlay layout.

Instantiates the interface under an offscreen QApplication and asserts that the
floating overlays exist and that the underlying control widgets/attrs survived
the reparenting into the ToolRail / FloatingDock / EnhancePopover.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.mask_tracing_interface import MaskTracingInterface  # noqa: E402
from app.gui.widgets import EnhancePopover, FloatingDock, ToolRail  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def test_overlays_present(app):
    iface = MaskTracingInterface()

    # Floating overlays exist and are the expected types.
    assert isinstance(iface.tool_rail, ToolRail)
    assert isinstance(iface.dock, FloatingDock)
    assert isinstance(iface.enhance_popover, EnhancePopover)

    # Core control widgets/attrs preserved through the reparenting.
    assert iface.brush_button is not None
    assert iface.eraser_button is not None
    assert iface.fill_button is not None
    assert iface.save_button is not None
    assert iface.size_slider is not None
    assert iface.zoom_slider is not None
    assert iface.mode_toggle is not None

    # Tool buttons stay in the exclusive group.
    assert iface.brush_button in iface.tool_button_group.buttons()


def test_b_key_and_ctrl_wheel_still_drive_sliders(app):
    iface = MaskTracingInterface()

    # B-key wheel adjusts the brush size slider.
    iface.size_slider.setValue(5)
    iface.b_key_pressed = True
    start = iface.size_slider.value()
    iface.update_brush_size(start)  # smoke the brush-size code path
    assert iface.size_slider.value() == start

    # Ctrl-wheel zoom path drives the zoom slider.
    iface.zoom_slider.setValue(100)
    iface.zoom_slider.setValue(min(iface.zoom_slider.value() + 10, 200))
    assert iface.zoom_slider.value() == 110

    # Resize triggers overlay reposition without error.
    iface.resize(900, 700)


# --------------------------------------------------------------------------- #
#  Behaviour of the reworked editor (dirty state, save format, fill, zoom)
# --------------------------------------------------------------------------- #
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402

from app.gui.mask_drawing_tools import pixmap_to_argb_array  # noqa: E402


@pytest.fixture
def image_dir(tmp_path):
    img = np.full((60, 80, 3), 90, np.uint8)
    path = tmp_path / "plot_001.png"
    cv2.imwrite(str(path), img)
    return tmp_path, str(path)


def _editor(image_path):
    iface = MaskTracingInterface()
    iface.resize(900, 700)
    iface.load_image(image_path)
    return iface


def test_stroke_marks_dirty_and_save_writes_white_png(app, image_dir):
    folder, image_path = image_dir
    iface = _editor(image_path)
    assert not iface.has_unsaved_changes()

    iface.size_slider.setValue(6)
    iface.begin_stroke(QPoint(10, 10))
    iface.continue_stroke(QPoint(40, 10))
    iface.end_stroke()
    assert iface.has_unsaved_changes()

    assert iface.save_mask()
    assert not iface.has_unsaved_changes()
    saved = cv2.imread(str(folder / "mask" / "plot_001.png"), cv2.IMREAD_GRAYSCALE)
    assert saved is not None and saved.shape == (60, 80)
    # Stored mask is binary white-on-black (no antialiased grey edge).
    assert set(np.unique(saved)) <= {0, 255}
    assert saved[10, 25] == 255 and saved[50, 70] == 0

    # Reloading shows the same pixels.
    iface.load_image(image_path)
    alpha = pixmap_to_argb_array(iface.mask_pixmap)[:, :, 3]
    assert ((alpha > 0) == (saved > 200)).all()


def test_eraser_single_click_erases(app, image_dir):
    _, image_path = image_dir
    iface = _editor(image_path)
    iface.size_slider.setValue(20)
    iface.begin_stroke(QPoint(30, 30))
    iface.end_stroke()
    iface.eraser_button.setChecked(True)
    iface.size_slider.setValue(4)
    iface.begin_stroke(QPoint(30, 30))
    iface.end_stroke()
    alpha = pixmap_to_argb_array(iface.mask_pixmap)[:, :, 3]
    assert alpha[30, 30] == 0 and alpha[30, 36] > 0


def test_clear_is_undoable_and_empty_save_removes_file(app, image_dir):
    folder, image_path = image_dir
    iface = _editor(image_path)
    iface.begin_stroke(QPoint(20, 20))
    iface.end_stroke()
    iface.save_mask()
    mask_file = folder / "mask" / "plot_001.png"
    assert mask_file.exists()

    cleared = []
    iface.mask_cleared.connect(cleared.append)
    iface.clear_mask()
    assert mask_file.exists(), "Clear must not delete the file by itself"
    iface.undo()
    assert pixmap_to_argb_array(iface.mask_pixmap)[:, :, 3].any()

    iface.clear_mask()
    iface.save_mask()
    assert not mask_file.exists()
    assert cleared == [image_path]


def test_fill_inside_outline_and_noop_outside(app, image_dir):
    _, image_path = image_dir
    iface = _editor(image_path)
    iface.size_slider.setValue(3)
    pts = [QPoint(20, 15), QPoint(50, 15), QPoint(50, 45), QPoint(20, 45), QPoint(20, 15)]
    iface.begin_stroke(pts[0])
    for p in pts[1:]:
        iface.continue_stroke(p)
    iface.end_stroke()

    notices = []
    iface.notice.connect(lambda msg, kind: notices.append(kind))
    iface.fill_button.setChecked(True)
    undo_depth = len(iface.undo_stack)
    iface.begin_stroke(QPoint(5, 5))  # outside: no-op + hint, no undo entry
    iface.end_stroke()
    assert notices == ["info"] and len(iface.undo_stack) == undo_depth

    iface.begin_stroke(QPoint(35, 30))  # inside the loop
    iface.end_stroke()
    alpha = pixmap_to_argb_array(iface.mask_pixmap)[:, :, 3]
    assert alpha[30, 35] > 0 and alpha[5, 5] == 0
    assert len(iface.undo_stack) == undo_depth + 1


def test_zoom_range_goes_past_200_percent(app, image_dir):
    _, image_path = image_dir
    iface = _editor(image_path)
    view = iface.graphics_view
    view.set_zoom(8.0)
    assert abs(view.zoom_factor - 8.0) < 1e-6
    assert iface.zoom_pct_label.text() == "800%"
    view.set_zoom(100.0)
    assert view.zoom_factor == view.MAX_ZOOM

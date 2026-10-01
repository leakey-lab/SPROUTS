"""Trace GT's raster editor base (``app/gui/raster_editor_base.py``) and the GT editor on it.

(a) GT cases: moved from ``tests/test_mask_editor_behaviour.py`` (GT parameters only), GT
    versions of the behaviours that file checked for the mask editor alone, the override
    guard (GT's hooks win over the new base's) and the names MainWindow, the shell and the
    tests read on the GT editor.
(b) Base cases through a test-local ``ProbeEditor(RasterEditorBase)``: the shared render
    items, one decode per open, the canvas view and its sink, zoom keys on the ladder,
    B hold / tap, the brush ring, unsaved = differs from the saved plane, and the prompt.

Findings PE-04, PE-06, PE-13, UI-03, UXE-02/RB-05, UXE-03, UXE-07, UXE-08/09, AR-11 and AR-13
of the UI/perf audit; plan package P3 of the unified Correct editor.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt  # noqa: E402
from PyQt6.QtGui import QEnterEvent, QImage, QMouseEvent, QShortcut, QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app.gt import Tracing, present_keys  # noqa: E402
from app.gui import canvas_gl, shortcuts  # noqa: E402
from app.gui.canvas_layers.photo import PhotoLayerItem  # noqa: E402
from app.gui.canvas_layers.raster import RasterLayerItem  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from app.gui.paint_canvas import pixmap_to_argb_array  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

_SAVE = QMessageBox.StandardButton.Save
_DISCARD = QMessageBox.StandardButton.Discard
_CANCEL = QMessageBox.StandardButton.Cancel
_LEFT = Qt.MouseButton.LeftButton
_NO_BUTTON = Qt.MouseButton.NoButton
_CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def make(app):
    """Build editors that are closed and deleted deterministically after the test."""
    made = []

    def _make(cls, image_path=None, size=(900, 700)):
        iface = cls()
        made.append(iface)
        iface.resize(*size)
        if image_path:
            iface.load_image(image_path)
        return iface

    yield _make
    # Dead wrappers left behind can make stale sip entries that crash a later test inside
    # findChildren (see tests/qt_teardown.py).
    delete_widgets(made)


@pytest.fixture(scope="module")
def probe_cls():
    """A one-class editor on the new base (imported here: its absence fails the tests)."""
    from app.gui.raster_editor_base import RasterEditorBase

    class ProbeEditor(RasterEditorBase):
        """Plane 0/1 drawn in the brush colour; opens empty, saves to a list."""

        EDITOR_NOUN = "probe"

        def __init__(self, parent=None):
            super().__init__(parent)
            self.opened: list[tuple[str, QSize]] = []
            self.saved: list[np.ndarray] = []

        def _open_document(self, image_path, size):
            self.opened.append((image_path, QSize(size)))
            return np.zeros((size.height(), size.width()), np.uint8), False

        def save_mask(self, save_path=None):
            self._finish_stroke()
            self.saved.append(self.edits.plane.copy())
            self._set_clean()
            return True

        # MaskTracingInterface's plane hooks (mask_tracing_interface.py:702-712), one class.
        def _plane_lut(self):
            c = self.brush_color
            lut = np.zeros((256, 4), np.uint8)
            lut[1:] = (c.blue(), c.green(), c.red(), 255)
            return lut

        def _argb_to_plane(self, argb, rect):
            return (argb[:, :, 3] > 0).astype(np.uint8)

        def _fill_source(self, plane):
            return (plane > 0).astype(np.uint8) * 255, 1

    return ProbeEditor


# ------------------------------------------------------------------ helpers
def _image(folder, name="plot_001.png", w=80, h=60, value=90):
    path = str(folder / name)
    cv2.imwrite(path, np.full((h, w, 3), value, np.uint8))
    return path


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
    """Answer the next prompts in order; returns the window titles seen."""
    seen, queue = [], list(answers)

    def _exec(box):
        seen.append(box.windowTitle())
        if not queue:
            pytest.fail(f"unexpected prompt {box.text()!r}")
        return queue.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return seen


def _show_active(iface):
    """Shortcuts (WidgetWithChildrenShortcut) need a shown, active window."""
    iface.show()
    iface.activateWindow()
    assert QTest.qWaitForWindowActive(iface, 3000)
    QApplication.processEvents()
    iface.graphics_view.setFocus()


def _shortcut_keys(iface):
    return [s.key().toString() for s in iface.findChildren(QShortcut) if s.parent() is iface]


def _mouse(view, kind, scene_xy, *, buttons=_LEFT):
    """Send a left-button mouse event at scene point ``scene_xy`` to ``view``'s viewport."""
    local = QPointF(view.mapFromScene(QPointF(*scene_xy)))
    glob = QPointF(view.viewport().mapToGlobal(local.toPoint()))
    button = _NO_BUTTON if kind == QEvent.Type.MouseMove else _LEFT
    event = QMouseEvent(kind, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def _wheel(view, dy):
    local = QPointF(view.viewport().rect().center())
    glob = QPointF(view.viewport().mapToGlobal(local.toPoint()))
    event = QWheelEvent(
        local,
        glob,
        QPoint(),
        QPoint(0, dy),
        _NO_BUTTON,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view.viewport(), event)


def _image_rect_in(iface) -> QRect:
    view = iface.graphics_view
    rect = view.mapFromScene(iface.image_item.sceneBoundingRect()).boundingRect()
    return QRect(view.mapTo(iface, rect.topLeft()), rect.size())


# ======================================================== (a) GT on the new base
def test_gt_runs_on_the_new_base_and_its_hooks_win():
    from app.gui.raster_editor_base import RasterEditorBase

    assert issubclass(GtTracingInterface, RasterEditorBase)
    for name in ("_install_shortcuts", "_open_document", "save_mask", "_status", "reset"):
        assert getattr(GtTracingInterface, name) is not getattr(RasterEditorBase, name), name
    # The base comes before QWidget in the MRO: its methods are never shadowed by Qt's.
    mro = GtTracingInterface.__mro__
    assert mro.index(RasterEditorBase) < mro.index(QWidget)


# Read on the GT editor by MainWindow, shell_chrome, export_dataset_dialog and the tests.
_GT_NAMES = (
    "b_key_pressed b_key_status_changed begin_stroke brush_button brush_color brush_preview "
    "brush_size class_panel clear_button clear_icon_button clear_mask continue_stroke "
    "current_image_path dirty dirty_changed dock drawing edit_classes edits end_stroke "
    "enhance_button enhance_popover eraser_button file_label fill_button fit_button flood_fill "
    "graphics_view gt_saved has_unsaved_changes image_item image_pixmap load_image mask_item "
    "mask_pixmap mask_visible_button maybe_save_changes mode_toggle navigate navigate_requested "
    "norm_controls notice opacity_container opacity_label opacity_slider overlay_layout "
    "pan_button redo redo_button redo_shortcut redo_stack registry reset save_button save_mask "
    "scene set_active_class size_container size_slider state_label status_chip tool_button_group "
    "tool_rail undo undo_button undo_shortcut undo_stack update_brush_preview update_brush_size "
    "zoom_cluster zoom_in_button zoom_out_button zoom_pct_label DEFAULT_BRUSH _active_key "
    "_navigate _pixmap_to_ids _present _project_dir _registry_error _reviewed _set_clean "
    "_shortcuts _status"
).split()


def test_gt_keeps_every_name_its_callers_read(make):
    iface = make(GtTracingInterface)
    assert [name for name in _GT_NAMES if not hasattr(iface, name)] == []


def test_gt_editor_has_no_mask_storage():
    for name in ("find_mask_path", "mask_store", "mask_saved", "mask_cleared", "_mask_save_path"):
        assert not hasattr(GtTracingInterface, name), name


# ------------------------------------------------------------------ PE-04
def test_gt_strokes_paint_in_place_without_deep_copies(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path, w=640, h=480))
    assert isinstance(iface.mask_item, RasterLayerItem)
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
def _random_ops(iface, rng, n, classes):
    w, h = iface.mask_pixmap.width(), iface.mask_pixmap.height()
    for _ in range(n):
        op = rng.integers(0, 10)
        iface.set_active_class(classes[int(rng.integers(0, len(classes)))])
        iface.eraser_button.setChecked(op in (6, 7))
        iface.fill_button.setChecked(op == 8)
        if op == 9:
            iface.clear_mask()
            continue
        pts = [(int(rng.integers(0, w)), int(rng.integers(0, h))) for _ in range(4)]
        _stroke(iface, *pts, size=int(rng.integers(1, 15)))
    iface.brush_button.setChecked(True)


def test_gt_undo_redo_matches_full_snapshots(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path, w=300, h=200))
    rng = np.random.default_rng(7)
    states = [pixmap_to_argb_array(iface.mask_pixmap)]
    for _ in range(40):
        before = len(iface.undo_stack)
        _random_ops(iface, rng, 1, ["root", "nodule"])
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


def test_gt_history_of_small_strokes_on_a_big_canvas_stays_small(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path, w=2048, h=1536))
    for i in range(25):
        _stroke(iface, (100, 40 + i * 50), (400, 45 + i * 50), size=8)
    assert len(iface.undo_stack) == 25
    assert iface.edits.nbytes < 1024 * 1024


# ------------------------------------------------------------------ PE-13
def test_gt_class_presence_tracks_every_edit(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path, w=120, h=90))
    rng = np.random.default_rng(3)
    classes = [c.key for c in iface.registry.active()]
    for _ in range(60):
        op = rng.integers(0, 4)
        if op == 0 and iface.undo_stack:
            iface.undo()
        elif op == 1 and iface.redo_stack:
            iface.redo()
        else:
            _random_ops(iface, rng, 1, classes)
        expected = present_keys(Tracing(iface._pixmap_to_ids()), iface.registry)
        assert iface._present == expected


# ------------------------------------------------------------------ UXE-02 / RB-05
def test_gt_failed_save_keeps_the_open_image(make, tmp_path, monkeypatch):
    path, other = _image(tmp_path), _image(tmp_path, "plot_002.png")
    iface = make(GtTracingInterface, path)
    _stroke(iface, (10, 10), (40, 10), size=6)
    os.makedirs(tmp_path / "gt" / "plot_001.png")  # a folder where the file must go
    seen = _answer(monkeypatch, _SAVE, _CANCEL)
    iface.load_image(other)
    assert seen == ["Unsaved GT", "GT not saved"]
    assert iface.current_image_path == path
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 25] > 0


def test_gt_cancel_on_the_prompt_keeps_editing(make, tmp_path, monkeypatch):
    path, other = _image(tmp_path), _image(tmp_path, "plot_002.png")
    iface = make(GtTracingInterface, path)
    _stroke(iface, (10, 10), (40, 10), size=6)
    _answer(monkeypatch, _CANCEL)
    assert iface.maybe_save_changes() is False
    _answer(monkeypatch, _CANCEL)
    assert iface.reset() is False
    assert iface.current_image_path == path and iface.has_unsaved_changes()
    _answer(monkeypatch, _DISCARD)
    iface.load_image(other)
    assert iface.current_image_path == other and not iface.has_unsaved_changes()


# ------------------------------------------------------------------ UI-03 / UXE-07
def test_gt_editor_keys_come_from_the_registry_once_each(make):
    iface = make(GtTracingInterface)
    keys = _shortcut_keys(iface)
    assert len(keys) == len(set(keys)), keys
    declared = {
        k
        for s in shortcuts.specs_for(shortcuts.TRACE_GT)
        if s.kind == shortcuts.SHORTCUT
        for k in s.keys
    }
    assert declared <= set(keys)
    assert set(keys) - declared <= {"PgDown", "PgUp"}  # GT pages itself (saves first)
    assert "Ctrl+Y" in iface.redo_button.toolTip()


@pytest.mark.parametrize("key", ["Ctrl+Y", "Ctrl+Shift+Z"])
def test_gt_redo_keys_redo(make, tmp_path, key):
    iface = make(GtTracingInterface, _image(tmp_path))
    _show_active(iface)
    _stroke(iface, (10, 10), (40, 10), size=6)
    QTest.keyClick(iface.graphics_view, Qt.Key.Key_Z, _CTRL)
    assert not _alpha(iface).any() and len(iface.redo_stack) == 1
    mods = _CTRL | Qt.KeyboardModifier.ShiftModifier if "Shift" in key else _CTRL
    target = Qt.Key.Key_Y if key.endswith("Y") else Qt.Key.Key_Z
    QTest.keyClick(iface.graphics_view, target, mods)
    assert len(iface.redo_stack) == 0 and _alpha(iface)[10, 25] > 0
    iface._set_clean()


def test_gt_undo_redo_buttons_follow_the_history(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path))
    assert not iface.undo_button.isEnabled() and not iface.redo_button.isEnabled()
    _stroke(iface, (10, 10), (40, 10), size=6)
    assert iface.undo_button.isEnabled() and not iface.redo_button.isEnabled()
    iface.undo()
    assert not iface.undo_button.isEnabled() and iface.redo_button.isEnabled()


# ------------------------------------------------------------------ UXE-03
def test_gt_dock_clicks_keep_keyboard_focus_on_the_canvas(make, tmp_path):
    iface = make(GtTracingInterface, _image(tmp_path))
    _show_active(iface)
    saved = []
    iface.gt_saved.connect(saved.append)
    _stroke(iface, (10, 10), (40, 10), size=6)
    QTest.mouseClick(iface.save_button, _LEFT)
    QTest.mouseClick(iface.size_slider, _LEFT)
    assert QApplication.focusWidget() is iface.graphics_view
    assert len(saved) == 1
    iface.enhance_popover.toggle()
    iface.norm_controls.method_combo.setFocus()
    iface.enhance_popover.toggle()
    QApplication.processEvents()
    assert QApplication.focusWidget() is iface.graphics_view


# ------------------------------------------------------------------ UXE-08 / UXE-09
@pytest.mark.parametrize("size", [(907, 562), (1018, 662), (1658, 942)])
def test_gt_fit_keeps_the_image_clear_of_the_overlays(make, tmp_path, size):
    iface = make(GtTracingInterface, _image(tmp_path, w=640, h=480), size=size)
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


# ------------------------------------------------------------------ AR-13
def test_gt_enhancement_renders_live_and_sticks_across_images(make, tmp_path):
    gradient = np.tile(np.linspace(60, 120, 80, dtype=np.uint8), (60, 1))
    first, second = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    cv2.imwrite(first, np.dstack([gradient] * 3))
    cv2.imwrite(second, np.dstack([gradient] * 3))
    iface = make(GtTracingInterface, first)
    raw = iface.image_pixmap.toImage()
    controls = iface.norm_controls
    controls.method_combo.setCurrentText("Contrast Stretching")
    controls.apply_button.click()
    assert controls.is_active()
    assert iface.image_item.pixmap().toImage() != raw
    iface.load_image(second)
    assert iface.image_item.pixmap().toImage() != raw  # still enhanced


# ======================================================== (b) the base, through a probe
def test_base_shows_the_photo_and_the_canvas_with_the_shared_render_items(
    make, tmp_path, probe_cls
):
    iface = make(probe_cls, _image(tmp_path, w=64, h=48))
    assert isinstance(iface.image_item, PhotoLayerItem) and iface.image_item.zValue() == 0
    assert iface.image_item.image_size() == (64, 48)
    assert isinstance(iface.mask_item, RasterLayerItem) and iface.mask_item.zValue() == 1
    assert iface.mask_item.opacity() == pytest.approx(0.6)
    assert isinstance(iface.mask_pixmap, QImage) and iface.mask_pixmap.size() == QSize(64, 48)
    assert iface.brush_size == 8 and iface.size_slider.value() == 8


def test_base_stroke_paints_the_canvas_in_place_and_commits_one_entry(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path, w=640, h=480))
    serial = iface.mask_pixmap.cacheKey() >> 32
    iface.size_slider.setValue(5)
    iface.begin_stroke(QPoint(20, 20))
    for x in range(30, 130, 10):
        iface.continue_stroke(QPoint(x, 20))
        assert iface.mask_pixmap.cacheKey() >> 32 == serial
    iface.end_stroke()
    plane = iface.edits.plane
    assert len(iface.undo_stack) == 1
    assert plane[18:23, 20:121].all() and plane[25, 60] == 0 and plane[20, 125] == 0
    assert np.array_equal(pixmap_to_argb_array(iface.mask_pixmap), iface._plane_lut()[plane])
    assert iface.has_unsaved_changes()


def test_base_open_document_gets_the_image_path_and_size(make, tmp_path, probe_cls):
    path = _image(tmp_path, w=64, h=48)
    iface = make(probe_cls, path)
    assert iface.opened == [(path, QSize(64, 48))]


def test_base_opening_decodes_the_photo_once(make, tmp_path, probe_cls, monkeypatch):
    import app.enhancement as enhancement

    path = _image(tmp_path, w=64, h=48)
    iface = make(probe_cls)
    shown, decoded = [], []
    display_image = iface.norm_controls.display_image
    imread = enhancement.imread_unicode
    monkeypatch.setattr(
        iface.norm_controls, "display_image", lambda p: shown.append(p) or display_image(p)
    )
    monkeypatch.setattr(
        enhancement, "imread_unicode", lambda *a: decoded.append(a[0]) or imread(*a)
    )
    iface.load_image(path)
    assert shown == [path] and len(decoded) == 1
    assert iface.image_item.image_size() == (64, 48)


def test_base_undecodable_image_is_a_danger_notice_and_changes_nothing(make, tmp_path, probe_cls):
    path = _image(tmp_path, w=64, h=48)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    iface = make(probe_cls, path)
    _stroke(iface, (5, 5), (30, 5), size=4)
    iface._set_clean()  # GT semantics: clean, the strokes stay on the canvas
    canvas = pixmap_to_argb_array(iface.mask_pixmap)
    notices = []
    iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    iface.load_image(str(broken))
    assert notices == [("Could not open broken.jpg", "danger")]
    assert iface.current_image_path == path and iface.opened == [(path, QSize(64, 48))]
    assert np.array_equal(pixmap_to_argb_array(iface.mask_pixmap), canvas)
    assert iface.image_item.image_size() == (64, 48) and len(iface.undo_stack) == 1


def test_base_zoom_keys_step_along_the_zoom_ladder(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path, w=640, h=480))
    _show_active(iface)
    view = iface.graphics_view
    for key, expected in ((Qt.Key.Key_Equal, 2.0), (Qt.Key.Key_Plus, 2.0), (Qt.Key.Key_Minus, 1.5)):
        view.set_zoom(1.63)
        QTest.keyClick(view, key, _CTRL)
        assert view.zoom_factor == pytest.approx(expected), key


def test_base_keys_are_the_trace_gt_scope(make, probe_cls):
    from app.gui.raster_editor_base import RasterEditorBase

    assert RasterEditorBase.SHORTCUT_SCOPE == shortcuts.TRACE_GT
    iface = make(probe_cls)
    keys = _shortcut_keys(iface)
    declared = {
        k
        for s in shortcuts.specs_for(shortcuts.TRACE_GT)
        if s.kind == shortcuts.SHORTCUT and s.action != "select_class"
        for k in s.keys
    }
    assert sorted(keys) == sorted(declared)


def test_base_canvas_sends_pointer_input_to_the_editor(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path, w=64, h=48))
    _show_active(iface)
    view = iface.graphics_view
    assert isinstance(view, EditorCanvasView)
    view.set_zoom(4.0)
    view.centerOn(32, 24)
    iface.size_slider.setValue(1)
    _mouse(view, QEvent.Type.MouseButtonPress, (10.5, 20.5))
    for x in range(14, 51, 4):  # ... 46, 50: the release itself paints nothing, as today
        _mouse(view, QEvent.Type.MouseMove, (x + 0.5, 20.5))
    _mouse(view, QEvent.Type.MouseButtonRelease, (50.5, 20.5), buttons=_NO_BUTTON)
    assert len(iface.undo_stack) == 1
    assert iface.edits.plane[20, 10:51].all() and iface.edits.plane.sum() == 41


def test_base_double_click_paints_one_dab(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path, w=64, h=48))
    _show_active(iface)
    view = iface.graphics_view
    view.set_zoom(4.0)
    view.centerOn(32, 24)
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, _LEFT),
        (QEvent.Type.MouseButtonRelease, _NO_BUTTON),
        (QEvent.Type.MouseButtonDblClick, _LEFT),
        (QEvent.Type.MouseButtonRelease, _NO_BUTTON),
    ):
        _mouse(view, kind, (30.5, 20.5), buttons=buttons)
    assert len(iface.undo_stack) == 1
    assert iface.edits.plane[20, 30] == 1


def test_base_b_hold_resizes_the_brush_and_a_tap_picks_it(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path))
    _show_active(iface)
    view = iface.graphics_view
    zoom = view.zoom_factor
    held = []
    iface.b_key_status_changed.connect(held.append)
    iface.eraser_button.setChecked(True)
    QTest.keyPress(view, Qt.Key.Key_B)
    assert iface.b_key_pressed and held == [True]
    _wheel(view, 120)
    assert iface.size_slider.value() == 9 and iface.brush_size == 9
    assert view.zoom_factor == zoom  # the wheel sized the brush, not the view
    QTest.keyRelease(view, Qt.Key.Key_B)
    assert held == [True, False] and iface.eraser_button.isChecked()  # not a tap
    _wheel(view, 120)
    assert iface.size_slider.value() == 9  # B is up: the wheel is the view's again
    QTest.keyClick(view, Qt.Key.Key_B)
    assert iface.brush_button.isChecked()


def test_base_brush_ring_follows_the_pointer_and_hides_off_canvas(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path, w=64, h=48))
    _show_active(iface)
    view, ring = iface.graphics_view, iface.brush_preview
    view.set_zoom(4.0)
    view.centerOn(32, 24)
    _mouse(view, QEvent.Type.MouseMove, (10.5, 20.5), buttons=_NO_BUTTON)
    assert ring.is_visible() and ring.rect().center() == QPointF(10.5, 20.5)
    assert ring.rect().width() == pytest.approx(8)
    iface.pan_button.setChecked(True)
    assert not ring.is_visible()
    iface.pan_button.setChecked(False)
    assert ring.is_visible()
    QApplication.sendEvent(view, QEvent(QEvent.Type.Leave))
    assert not ring.is_visible()
    inside = QPointF(view.mapFromScene(QPointF(12.5, 20.5)))
    QApplication.sendEvent(view, QEnterEvent(inside, inside, view.mapToGlobal(inside)))
    _mouse(view, QEvent.Type.MouseMove, (12.5, 20.5), buttons=_NO_BUTTON)
    assert ring.is_visible() and ring.rect().center() == QPointF(12.5, 20.5)
    iface.fill_button.setChecked(True)
    assert not ring.is_visible()


def test_base_discard_reverts_to_the_saved_plane(make, tmp_path, probe_cls, monkeypatch):
    """Discard goes back to the plane as last saved (or opened), like Correct (G1)."""
    iface = make(probe_cls, _image(tmp_path))
    _stroke(iface, (10, 10), (40, 10), size=6)
    seen = _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert seen == ["Unsaved probe"]
    assert not iface.has_unsaved_changes() and iface.saved == []
    assert not _alpha(iface).any() and not iface.edits.plane.any()  # back to the opened state
    assert len(iface.undo_stack) == 0 and len(iface.redo_stack) == 0

    _stroke(iface, (10, 10), (40, 10), size=6)
    assert iface.save_mask()
    _stroke(iface, (10, 40), (40, 40), size=6)
    _answer(monkeypatch, _DISCARD)
    assert iface.maybe_save_changes() is True
    assert _alpha(iface)[10, 25] > 0 and not _alpha(iface)[40, 25]  # the saved stroke stays
    assert np.array_equal(iface.edits.plane, iface.saved[-1])


def test_base_dirty_follows_every_edit_undo_and_redo(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path))
    changes = []
    iface.dirty_changed.connect(changes.append)
    _stroke(iface, (10, 10), (40, 10), size=6)
    assert iface.save_mask() and not iface.has_unsaved_changes()
    iface.undo()
    assert iface.has_unsaved_changes()  # the opened state differs from the saved one
    iface._set_clean()
    iface.redo()
    assert iface.has_unsaved_changes()
    assert changes == [True, False, True, False, True]


def test_base_reset_drops_the_image_and_its_history(make, tmp_path, probe_cls):
    iface = make(probe_cls, _image(tmp_path))
    _stroke(iface, (10, 10), (40, 10), size=6)
    iface._set_clean()
    assert iface.reset() is True
    assert iface.current_image_path == "" and not iface.has_unsaved_changes()
    assert iface.mask_pixmap is None and iface.image_pixmap is None
    assert iface.image_item.image_size() == (0, 0) and len(iface.undo_stack) == 0
    assert iface.canvas_size().isEmpty()

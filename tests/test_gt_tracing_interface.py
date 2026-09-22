"""Behaviour of the "Trace GT" editor (GtTracingInterface).

Runs under an offscreen QApplication. The fixture project holds an 80x60
image and a full-white ``mask/plot_001.png``: the GT editor must never read
(or write) ``mask/``, so every test starts from an empty tracing.
"""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox  # noqa: E402

from app.gt import (  # noqa: E402
    Tracing,
    default_registry,
    gt_path,
    read_tracing,
    registry_path,
    save_registry,
    write_tracing,
)
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from app.gui.mask_drawing_tools import pixmap_to_argb_array  # noqa: E402
from app.gui.mask_tracing_interface import MaskTracingInterface  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture(autouse=True)
def no_modal_prompts(monkeypatch):
    """Fail loudly instead of blocking if any code path opens a modal box."""

    def _fail(self, *args, **kwargs):
        pytest.fail(f"unexpected modal dialog: {self.text()!r}")

    monkeypatch.setattr(QMessageBox, "exec", _fail)


@pytest.fixture
def project(tmp_path):
    cv2.imwrite(str(tmp_path / "plot_001.png"), np.full((H, W, 3), 90, np.uint8))
    (tmp_path / "mask").mkdir()
    cv2.imwrite(str(tmp_path / "mask" / "plot_001.png"), np.full((H, W), 255, np.uint8))
    return tmp_path, str(tmp_path / "plot_001.png")


def _editor(app, image_path=None, notices=None):
    iface = GtTracingInterface()
    iface.resize(900, 700)
    if notices is not None:
        iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
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


def _show_active(iface):
    """Shortcuts (WidgetWithChildrenShortcut) need a shown, active window."""
    iface.show()
    iface.activateWindow()
    assert QTest.qWaitForWindowActive(iface, 3000)
    iface.graphics_view.setFocus()


def _checkbox(panel, key) -> QCheckBox:
    """The 'checked' box of ``key``'s row in the class panel.

    Looked up through the live row map: ``set_registry`` rebuilds rows and
    the old ones linger (deleteLater) until the event loop runs, so a plain
    ``findChildren`` could return a stale box.
    """
    box = panel._rows[key].checkbox
    assert isinstance(box, QCheckBox)
    return box


# --------------------------------------------------------------------------- #
def test_opens_empty_ignoring_mask(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    alpha = _alpha(iface)
    assert alpha.shape == (H, W)
    assert not alpha.any(), "GT editor must not start from mask/"
    assert not iface.has_unsaved_changes()
    assert iface.save_button.text() == "Save GT"
    assert "No GT" in iface.state_label.text()


def test_paint_classes_and_save_ids(app, project):
    folder, image_path = project
    mask_file = folder / "mask" / "plot_001.png"
    mask_before = mask_file.read_bytes()

    iface = _editor(app, image_path)
    saved, mask_signals = [], []
    iface.gt_saved.connect(saved.append)
    iface.mask_saved.connect(mask_signals.append)
    iface.mask_cleared.connect(mask_signals.append)

    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30), size=6)
    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55), size=6)
    assert iface.has_unsaved_changes()
    assert iface.save_mask()
    assert not iface.has_unsaved_changes()

    tracing = read_tracing(gt_path(image_path))
    assert tracing is not None and tracing.ids.shape == (H, W)
    assert tracing.ids[30, 15] == 1 and tracing.ids[30, 65] == 1  # root only
    assert tracing.ids[30, 40] == 2 and tracing.ids[10, 40] == 2  # crossing + nodule only
    assert tracing.ids[50, 10] == 0
    assert set(np.unique(tracing.ids).tolist()) == {0, 1, 2}
    assert tracing.reviewed == {"root", "nodule"}
    assert os.path.exists(registry_path(str(folder)))

    assert saved == [image_path]
    assert mask_signals == []
    assert mask_file.read_bytes() == mask_before
    assert sorted(os.listdir(folder / "mask")) == ["plot_001.png"]
    assert "Saved" in iface.state_label.text() and "2/3 checked" in iface.state_label.text()


def test_fill_limited_to_active_class(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    iface.set_active_class("root")
    _stroke(iface, (15, 10), (45, 10), (45, 40), (15, 40), (15, 10), size=3)
    iface.set_active_class("nodule")
    _stroke(iface, (65, 50), size=5)  # a dot outside the square

    kinds = []
    iface.notice.connect(lambda msg, kind: kinds.append(kind))
    before = iface._pixmap_to_ids()
    iface.fill_button.setChecked(True)

    # Nodule active: the nodule has no outline around the click -> no fill.
    assert iface.flood_fill(QPoint(30, 25)) is False
    undo_depth = len(iface.undo_stack)
    iface.begin_stroke(QPoint(30, 25))
    iface.end_stroke()
    assert kinds == ["info"]
    assert len(iface.undo_stack) == undo_depth
    assert np.array_equal(iface._pixmap_to_ids(), before)

    # Root active: the root square is a closed outline -> interior becomes root.
    iface.set_active_class("root")
    iface.begin_stroke(QPoint(30, 25))
    iface.end_stroke()
    ids = iface._pixmap_to_ids()
    assert ids[25, 30] == 1
    assert ids[50, 65] == 2
    assert ids[55, 5] == 0
    assert len(iface.undo_stack) == undo_depth + 1


def test_empty_save_writes_file(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    iface.class_panel.reviewed_changed.emit("nodule", True)
    assert iface.has_unsaved_changes()
    assert iface.save_mask()

    tracing = read_tracing(gt_path(image_path))
    assert tracing is not None
    assert tracing.ids.shape == (H, W) and not tracing.ids.any()
    assert tracing.reviewed == {"nodule"}


def test_checkbox_locked_while_pixels(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30), size=6)

    root_box = _checkbox(iface.class_panel, "root")
    nodule_box = _checkbox(iface.class_panel, "nodule")
    assert root_box.isChecked() and not root_box.isEnabled()
    assert not nodule_box.isChecked() and nodule_box.isEnabled()

    # Clearing removes the pixels, so the box unlocks; undo brings them back.
    iface.clear_mask()
    root_box = _checkbox(iface.class_panel, "root")
    assert not root_box.isChecked() and root_box.isEnabled()
    iface.undo()
    root_box = _checkbox(iface.class_panel, "root")
    assert root_box.isChecked() and not root_box.isEnabled()


def test_digit_keys_select_classes(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    _show_active(iface)
    try:
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_2)
        assert iface.class_panel.active_key() == "nodule"
        assert iface.brush_color.name() == "#ff7eb6"
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_0)
        assert iface.class_panel.active_key() == "ignore"
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_1)
        assert iface.class_panel.active_key() == "root"
        assert iface.brush_color.name() == "#5fd6a0"
    finally:
        iface.close()


def test_pgdn_saves_and_navigates(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    _show_active(iface)
    try:
        _stroke(iface, (10, 10), (30, 10))
        assert iface.has_unsaved_changes()
        moves = []
        iface.navigate_requested.connect(moves.append)

        QTest.keyClick(iface.graphics_view, Qt.Key.Key_PageDown)
        assert moves == [1]
        assert os.path.exists(gt_path(image_path))
        assert not iface.has_unsaved_changes()

        QTest.keyClick(iface.graphics_view, Qt.Key.Key_PageUp)
        assert moves == [1, -1]
    finally:
        iface.close()


def test_per_class_brush_size(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    iface.set_active_class("root")
    iface.size_slider.setValue(20)
    iface.set_active_class("root_tip")
    assert iface.size_slider.value() == iface.DEFAULT_BRUSH
    iface.size_slider.setValue(3)
    iface.set_active_class("root")
    assert iface.size_slider.value() == 20 and iface.brush_size == 20
    iface.set_active_class("root_tip")
    assert iface.size_slider.value() == 3


def test_retired_class_pixels_survive(app, project):
    folder, image_path = project
    registry = (
        default_registry()
        .add(key="dead_root", name="Dead root", color="#7f5af0")
        .retire("dead_root")
    )
    assert registry.by_key("dead_root").id == 4
    save_registry(str(folder), registry)
    ids = np.zeros((H, W), np.uint8)
    ids[5:15, 5:15] = 4
    write_tracing(gt_path(image_path), Tracing(ids, frozenset({"nodule"})), registry)

    iface = _editor(app, image_path)
    assert not iface.has_unsaved_changes()
    iface.set_active_class("root")
    _stroke(iface, (50, 40), (70, 40), size=4)
    assert iface.save_mask()

    tracing = read_tracing(gt_path(image_path))
    assert (tracing.ids[5:15, 5:15] == 4).all()
    assert tracing.ids[40, 60] == 1
    assert set(np.unique(tracing.ids).tolist()) == {0, 1, 4}
    assert {"root", "nodule"} <= tracing.reviewed


def test_size_mismatch_warns_and_keeps_file(app, project):
    _, image_path = project
    path = gt_path(image_path)
    write_tracing(path, Tracing(np.ones((10, 10), np.uint8), frozenset()), default_registry())
    with open(path, "rb") as fh:
        before = fh.read()

    notices = []
    iface = _editor(app, image_path, notices)
    assert any(kind == "danger" for _, kind in notices)
    assert iface.image_pixmap is not None
    assert not _alpha(iface).any()
    assert not iface.has_unsaved_changes()
    with open(path, "rb") as fh:
        assert fh.read() == before


def test_invalid_registry_blocks_save(app, project):
    folder, image_path = project
    (folder / "gt").mkdir()
    reg_file = folder / "gt" / "classes.json"
    reg_file.write_text("{", encoding="utf-8")

    notices = []
    iface = _editor(app, image_path, notices)
    assert any(kind == "danger" for _, kind in notices)
    assert iface.image_pixmap is not None  # the image still shows

    _stroke(iface, (10, 10), (30, 10))
    notices.clear()
    assert iface.save_mask() is False
    assert any(kind == "danger" for _, kind in notices)
    assert reg_file.read_text(encoding="utf-8") == "{"
    assert not os.path.exists(gt_path(image_path))
    assert iface.has_unsaved_changes()


def test_reselecting_open_image_keeps_unsaved_edits(app, project):
    """Clicking the already-open image again must not reload it from disk."""
    _, image_path = project
    iface = _editor(app, image_path)
    _stroke(iface, (10, 10), (30, 10), size=6)
    iface.load_image(image_path)  # no prompt (autouse guard), no reload
    assert iface.has_unsaved_changes()
    assert _alpha(iface)[10, 20] > 0


def test_prompt_names_the_gt(app, project, monkeypatch):
    _, image_path = project
    iface = _editor(app, image_path)
    _stroke(iface, (10, 10), (30, 10))
    texts = []

    def _discard(box):
        texts.append((box.windowTitle(), box.text()))
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "exec", _discard)
    iface.maybe_save_changes()
    assert texts == [("Unsaved GT", "Save changes to the GT for plot_001.png?")]
    assert not iface.has_unsaved_changes()
    assert not os.path.exists(gt_path(image_path))


def test_edit_classes_recolours_without_changing_ids(app, project, monkeypatch):
    folder, image_path = project
    iface = _editor(app, image_path)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30), size=6)
    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55), size=6)
    ids_before = iface._pixmap_to_ids()
    new_registry = iface.registry.update("root", color="#123456", name="Main root")

    class _FakeDialog:
        def __init__(self, registry, parent=None):
            assert registry == iface.registry

        def exec(self):
            return 1

        def result_registry(self):
            return new_registry

    fake = types.ModuleType("app.gui.class_editor_dialog")
    fake.ClassEditorDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.class_editor_dialog", fake)

    iface.edit_classes()
    assert iface.registry == new_registry
    assert np.array_equal(iface._pixmap_to_ids(), ids_before)
    arr = pixmap_to_argb_array(iface.mask_pixmap)
    assert tuple(arr[30, 15, :3]) == (0x56, 0x34, 0x12)  # BGR of #123456
    assert os.path.exists(registry_path(str(folder)))

    # Undo history was recoloured too: undoing the nodule stroke keeps root ids.
    iface.undo()
    ids = iface._pixmap_to_ids()
    assert ids[30, 15] == 1 and ids[30, 40] == 1 and ids[10, 40] == 0


def test_mask_mode_unchanged(app, tmp_path, monkeypatch):
    folder = tmp_path / "plain"
    folder.mkdir()
    image_path = str(folder / "plot_001.png")
    cv2.imwrite(image_path, np.full((H, W, 3), 90, np.uint8))

    iface = MaskTracingInterface()
    iface.resize(900, 700)
    iface.load_image(image_path)
    assert iface.save_button.text() == "Save mask"
    assert iface.clear_button.text() == "Clear mask"
    _stroke(iface, (10, 10), (40, 10), size=6)
    assert iface.save_mask()

    saved = cv2.imread(str(folder / "mask" / "plot_001.png"), cv2.IMREAD_GRAYSCALE)
    assert saved is not None and saved.shape == (H, W)
    assert set(np.unique(saved)) <= {0, 255}
    assert saved[10, 25] == 255 and saved[50, 70] == 0
    assert not (folder / "gt").exists()

    texts = []

    def _discard(box):
        texts.append((box.windowTitle(), box.text()))
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "exec", _discard)
    _stroke(iface, (10, 50), (40, 50))
    iface.maybe_save_changes()
    assert texts == [("Unsaved mask", "Save changes to the mask for plot_001.png?")]

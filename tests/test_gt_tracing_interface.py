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
from app.gui.paint_canvas import pixmap_to_argb_array  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402

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
    """The 'checked' box of ``key``'s row in the class panel (live row map)."""
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
    saved = []
    iface.gt_saved.connect(saved.append)
    # The GT editor has no mask storage at all (it is not a MaskTracingInterface).
    assert not hasattr(iface, "mask_saved") and not hasattr(iface, "mask_cleared")

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


@pytest.mark.parametrize("bad_json", ["{", "[]", '{"version": 1, "classes": [7]}'])
def test_invalid_registry_blocks_save(app, project, bad_json):
    folder, image_path = project
    (folder / "gt").mkdir()
    reg_file = folder / "gt" / "classes.json"
    reg_file.write_text(bad_json, encoding="utf-8")

    notices = []
    iface = _editor(app, image_path, notices)
    assert any(kind == "danger" for _, kind in notices)
    assert iface.image_pixmap is not None  # the image still shows

    _stroke(iface, (10, 10), (30, 10))  # refused: nothing could be saved
    assert not _alpha(iface).any()
    notices.clear()
    assert iface.save_mask() is False
    assert any(kind == "danger" for _, kind in notices)
    assert reg_file.read_text(encoding="utf-8") == bad_json
    assert not os.path.exists(gt_path(image_path))
    assert not iface.has_unsaved_changes()


def test_invalid_registry_blocks_every_edit(app, project):
    """While classes.json is invalid nothing can be saved, so nothing can be edited."""
    folder, image_path = project
    (folder / "gt").mkdir()
    (folder / "gt" / "classes.json").write_text("{", encoding="utf-8")
    notices = []
    iface = _editor(app, image_path, notices)

    def _refused(first=False):
        # The first refusal on an image says why in a toast; from then on the chip says
        # "Read-only" (each refused stroke used to stack another toast).
        danger = [msg for msg, kind in notices if kind == "danger"]
        notices.clear()
        return len(danger) == 1 and "classes.json" in danger[0] if first else danger == []

    notices.clear()
    assert "Read-only" in iface.state_label.text()
    _stroke(iface, (10, 10), (30, 10), size=6)  # brush
    assert _refused(first=True)
    iface.eraser_button.setChecked(True)
    _stroke(iface, (10, 10), (30, 10))  # eraser
    assert _refused()
    iface.fill_button.setChecked(True)
    iface.begin_stroke(QPoint(20, 20))  # fill
    iface.end_stroke()
    assert _refused()
    iface.clear_mask()
    assert _refused()
    box = _checkbox(iface.class_panel, "nodule")
    box.setChecked(True)  # a user tick on the 'checked' box
    assert _refused()
    assert not box.isChecked()
    assert iface._reviewed == set()

    assert not _alpha(iface).any()
    assert len(iface.undo_stack) == 0
    assert not iface.has_unsaved_changes()


def test_invalid_registry_never_leaves_unsaveable_edits(app, project):
    """A GT file whose ids would be remapped still opens clean while classes.json
    is invalid: nothing could be saved, so leaving the image must not prompt."""
    folder, image_path = project
    _write_with_unknown_key(image_path)
    (folder / "gt" / "classes.json").write_text("{", encoding="utf-8")
    second = str(folder / "plot_002.png")
    cv2.imwrite(second, np.full((H, W, 3), 60, np.uint8))

    iface = _editor(app, image_path)
    assert iface._registry_error
    assert not iface.has_unsaved_changes()
    assert "remapped" not in iface.state_label.text()
    iface.load_image(second)  # the autouse guard fails on any prompt
    assert iface.current_image_path == second


def _answer_prompts(monkeypatch, *answers):
    """Answer the next QMessageBox prompts in order; returns the (title, text) seen."""
    seen, queue = [], list(answers)

    def _exec(box):
        seen.append((box.windowTitle(), box.text()))
        if not queue:
            pytest.fail(f"unexpected modal dialog: {box.text()!r}")
        return queue.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return seen


def _failing_writes(monkeypatch):
    import app.gui.gt_tracing_interface as gti

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gti, "write_tracing", _boom)


def test_failed_save_on_prompt_asks_again(app, project, monkeypatch):
    folder, image_path = project
    second = str(folder / "plot_002.png")
    cv2.imwrite(second, np.full((H, W, 3), 60, np.uint8))
    iface = _editor(app, image_path)
    _stroke(iface, (10, 10), (30, 10), size=6)
    _failing_writes(monkeypatch)
    save, discard, keep = (
        QMessageBox.StandardButton.Save,
        QMessageBox.StandardButton.Discard,
        QMessageBox.StandardButton.Cancel,
    )

    # Save fails -> "Keep editing": the caller must stay, edits intact.
    seen = _answer_prompts(monkeypatch, save, keep)
    assert iface.maybe_save_changes() is False
    assert [title for title, _ in seen] == ["Unsaved GT", "GT not saved"]
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 20] > 0

    # Opening another image then keeps the open one.
    _answer_prompts(monkeypatch, save, keep)
    iface.load_image(second)
    assert iface.current_image_path == image_path
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 20] > 0

    # Save fails -> "Discard": safe to move on, nothing written.
    _answer_prompts(monkeypatch, save, discard)
    iface.load_image(second)
    assert iface.current_image_path == second
    assert not iface.has_unsaved_changes()
    assert not os.path.exists(gt_path(image_path))

    # A clean editor and a plain Discard both say "go ahead".
    assert iface.maybe_save_changes() is True
    _stroke(iface, (10, 10), (30, 10))
    _answer_prompts(monkeypatch, discard)
    assert iface.maybe_save_changes() is True


def test_reset_keeps_edits_the_user_kept(app, project, monkeypatch):
    _, image_path = project
    iface = _editor(app, image_path)
    _stroke(iface, (10, 10), (30, 10), size=6)
    _failing_writes(monkeypatch)
    _answer_prompts(monkeypatch, QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel)
    iface.reset()
    assert iface.current_image_path == image_path
    assert iface.has_unsaved_changes() and _alpha(iface)[10, 20] > 0


def test_panel_clicks_keep_canvas_focus_for_space(app, project):
    """Space after ticking a class must pan, never toggle the box (spec §3.4 safety)."""
    _, image_path = project
    iface = _editor(app, image_path)
    _show_active(iface)
    try:
        panel = iface.class_panel
        box = _checkbox(panel, "nodule")
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        assert box.isChecked() and iface._reviewed == {"nodule"}
        QTest.mouseClick(panel._rows["root_tip"], Qt.MouseButton.LeftButton)
        assert iface.class_panel.active_key() == "root_tip"

        focus = QApplication.focusWidget()
        assert focus is iface.graphics_view, focus
        QTest.keyPress(focus, Qt.Key.Key_Space)
        assert iface.graphics_view.space_pan
        QTest.keyRelease(focus, Qt.Key.Key_Space)
        assert not iface.graphics_view.space_pan
        assert box.isChecked() and iface._reviewed == {"nodule"}
        for w in [box, panel._rows["nodule"], panel._gear, panel._scroll, panel]:
            assert w.focusPolicy() == Qt.FocusPolicy.NoFocus, w
    finally:
        iface.close()


def test_keypad_digits_select_classes(app, project):
    _, image_path = project
    iface = _editor(app, image_path)
    _show_active(iface)
    try:
        keypad = Qt.KeyboardModifier.KeypadModifier
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_2, keypad)
        assert iface.class_panel.active_key() == "nodule"
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_0, keypad)
        assert iface.class_panel.active_key() == "ignore"
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_3, keypad)
        assert iface.class_panel.active_key() == "root_tip"
    finally:
        iface.close()


def _write_with_unknown_key(image_path):
    """A GT file from another project: id 4 = 'hypha', unknown to the defaults."""
    other = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    ids = np.zeros((H, W), np.uint8)
    ids[5:15, 5:15] = 4
    ids[30:40, 30:40] = 1
    path = gt_path(image_path)
    write_tracing(path, Tracing(ids, frozenset({"root"})), other)
    with open(path, "rb") as fh:
        return path, fh.read()


def test_pgdn_never_saves_a_lossy_remap_silently(app, project, monkeypatch):
    _, image_path = project
    path, before = _write_with_unknown_key(image_path)
    notices = []
    iface = _editor(app, image_path, notices)
    assert any(kind == "warn" and "hypha" in msg for msg, kind in notices)
    assert iface.has_unsaved_changes()  # 4 -> Ignore changed the ids
    assert "remapped" in iface.state_label.text()

    _show_active(iface)
    try:
        moves = []
        iface.navigate_requested.connect(moves.append)
        seen = _answer_prompts(monkeypatch, QMessageBox.StandardButton.Discard)
        QTest.keyClick(iface.graphics_view, Qt.Key.Key_PageDown)
        assert [title for title, _ in seen] == ["Unsaved GT"]  # the normal prompt
        assert moves == [1]
        with open(path, "rb") as fh:
            assert fh.read() == before
    finally:
        iface.close()


def test_pgdn_lossy_remap_saved_when_user_says_save(app, project, monkeypatch):
    _, image_path = project
    path, before = _write_with_unknown_key(image_path)
    iface = _editor(app, image_path)
    moves = []
    iface.navigate_requested.connect(moves.append)
    seen = _answer_prompts(monkeypatch, QMessageBox.StandardButton.Save)
    iface._navigate(1)
    assert [title for title, _ in seen] == ["Unsaved GT"]
    assert moves == [1]
    tracing = read_tracing(path)
    assert (tracing.ids[5:15, 5:15] == 255).all()
    assert "remapped" not in iface.state_label.text()


def test_pgdn_saves_a_lossless_remap_silently(app, project):
    """Ids that differ only by number (same keys) are remapped and saved without a prompt."""
    from app.gt import Registry

    _, image_path = project
    swapped = default_registry().to_dict()
    swapped["classes"][0]["id"], swapped["classes"][1]["id"] = 2, 1  # root=2, nodule=1
    other = Registry.from_dict(swapped)
    ids = np.zeros((H, W), np.uint8)
    ids[5:15, 5:15] = 2  # root in the other project's ids
    write_tracing(gt_path(image_path), Tracing(ids, frozenset({"root"})), other)

    notices = []
    iface = _editor(app, image_path, notices)
    assert not [n for n in notices if n[1] == "warn"]
    assert iface.has_unsaved_changes() and "remapped" not in iface.state_label.text()
    moves = []
    iface.navigate_requested.connect(moves.append)
    iface._navigate(1)  # the autouse guard fails on any prompt
    assert moves == [1]
    assert (read_tracing(gt_path(image_path)).ids[5:15, 5:15] == 1).all()


@pytest.mark.parametrize("kind", ["corrupt", "wrong_size"])
def test_status_chip_flags_an_unreadable_gt_file(app, project, kind):
    _, image_path = project
    path = gt_path(image_path)
    if kind == "corrupt":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"not a png")
    else:
        write_tracing(path, Tracing(np.ones((10, 10), np.uint8)), default_registry())
    iface = _editor(app, image_path)
    text = iface.state_label.text()
    assert "GT unreadable" in text and "No GT" not in text
    assert tokens.DANGER.lower() in text.lower()

    _stroke(iface, (10, 10), (30, 10))
    assert "Unsaved" in iface.state_label.text()
    assert iface.save_mask()
    assert "Saved" in iface.state_label.text()


def test_class_editor_dialog_is_deleted_after_use(app, project, monkeypatch):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent

    from app.gui.class_editor_dialog import ClassEditorDialog

    _, image_path = project
    iface = _editor(app, image_path)
    opened = []

    def _exec(dialog):
        opened.append(dialog)
        return 0  # cancelled

    monkeypatch.setattr(ClassEditorDialog, "exec", _exec)
    iface.edit_classes()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(opened) == 1 and sip.isdeleted(opened[0])


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


def _fake_class_editor(monkeypatch, result):
    """Replace ClassEditorDialog with a stub that 'accepts' with ``result``."""

    class _FakeDialog:
        def __init__(self, registry, parent=None):
            pass

        def exec(self):
            return 1

        def result_registry(self):
            return result

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

    fake = types.ModuleType("app.gui.class_editor_dialog")
    fake.ClassEditorDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.class_editor_dialog", fake)


def test_class_panel_resizes_after_classes_added(app, project, monkeypatch):
    _, image_path = project
    iface = _editor(app, image_path)
    iface.show()
    QApplication.processEvents()
    try:
        bigger = iface.registry.add(key="dead_root", name="Dead root", color="#7f5af0").add(
            key="hypha", name="Hypha", color="#3fa7ff"
        )
        _fake_class_editor(monkeypatch, bigger)
        iface.edit_classes()
        QApplication.processEvents()

        panel = iface.class_panel
        assert set(panel._rows) == {c.key for c in bigger.active()}
        content = panel._rows_container.sizeHint().height()
        assert panel._scroll.height() >= content, (panel._scroll.height(), content)
        # Still pinned to the right edge and vertically centred.
        assert panel.geometry().right() < iface.width()
        assert abs(panel.geometry().center().y() - iface.height() // 2) <= 2
    finally:
        iface.close()


def test_fixed_registry_unblocks_save(app, project):
    """An invalid classes.json blocks saving only until the file is fixed."""
    folder, image_path = project
    (folder / "gt").mkdir()
    reg_file = folder / "gt" / "classes.json"
    reg_file.write_text("{", encoding="utf-8")
    second = folder / "plot_002.png"
    cv2.imwrite(str(second), np.full((H, W, 3), 60, np.uint8))

    iface = _editor(app, image_path)
    _stroke(iface, (10, 10), (30, 10))
    assert iface.save_mask() is False

    save_registry(str(folder), default_registry())  # the user fixes the file
    iface._set_clean()  # discard the unsaveable edit (no prompt on the next load)
    iface.load_image(str(second))
    _stroke(iface, (10, 10), (30, 10))
    assert iface.save_mask() is True
    assert os.path.exists(gt_path(str(second)))

    # Reopening the same image after the fix works too.
    reg_file.write_text("{", encoding="utf-8")
    other = _editor(app, image_path)
    assert other.save_mask() is False
    save_registry(str(folder), default_registry())
    other.load_image(image_path)
    assert other.save_mask() is True
    assert os.path.exists(gt_path(image_path))


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

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

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

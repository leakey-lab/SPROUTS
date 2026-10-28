"""The class labels on the Correct page (WG-T5, Design 3.2 - 3.6).

The page edits three layers of an image on one canvas: the mask, the skeleton and the class
labels (``ClassesLayer``, GT for training). The class panel (``ClassPanel``) sits under the
Layers panel in one side card (``SidePanels``) and shows while the class labels are edited;
M / L / G pick a layer, X goes back to the previous one, the digits pick a class (only on
the class labels), and B / F take the brush / fill of the edited raster layer. The page's
GT actions live in ``PageActions``: Load skeleton file, Load prediction, Classes… (the
class editor) and Remove GT… (the topbar Correct page holds Clear, Classes…, Export
dataset… and Remove GT…).

Ported here (the Trace GT files were deleted in WG-T11): the page cases of
``test_gt_tracing_interface.py`` (keys, PgDn, the class panel, the class editor),
``test_gt_class_panel_keyboard.py``, four cases each of ``test_gt_editor_discard.py`` and
``test_gt_editor_open.py``, and ``test_gt_editor_ux.py`` (the class panel, the class editor
and the export dialog as they are; the chip and notices for the merged page).
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QImage, QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QMessageBox,
    QPushButton,
    QWidget,
)

import app.gt as gt_package
from app.gt import (
    Registry,
    Tracing,
    default_registry,
    load_registry,
    read_tracing,
    registry_path,
    save_registry,
    write_tracing,
)
from app.gui import editor_prompts, shortcuts
from app.gui.class_editor_dialog import ClassEditorDialog
from app.gui.class_panel import ClassPanel
from app.gui.correct.editor import CorrectEditor
from app.gui.correct.layer import CLASSES, MASK, SKELETON
from app.gui.correct.side_panels import SidePanels
from app.gui.editor_prompts import CANCEL, DISCARD, SAVE
from app.gui.export_dataset_dialog import ExportDatasetDialog
from app.gui.paint_canvas import pixmap_to_argb_array
from app.project_layout import ProjectLayout
from tests.correct_support import (
    NO_MODS,
    H,
    W,
    activate,
    drag,
    draw_line,
    editor_factory,
    new_project,
    record,
    settle,
)
from tests.qt_teardown import delete_widgets
from tests.qt_wait import wait_until as _spin_until
from tests.window_support import build_window, open_folder, quiet_window

ROOT = Path(__file__).resolve().parents[1]
APP_QSS = ROOT / "resources" / "themes" / "dark_theme.qss"
KEYS = ("root", "nodule", "root_tip")  # the default registry's labelled classes
Focus = Qt.FocusPolicy
REMOVE_DETAIL = (
    "Deletes gt/mask/a.png, gt/skeletons/a.png and gt/a.png where they exist. The predictions"
    " in mask/ and skeletons/ are kept. This cannot be undone."
)
PAGE_SIGNALS = ("gt_saved", "gt_removed", "mask_saved", "mask_cleared", "skeleton_cleared")


# ============================================================================== helpers
def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def tree(folder: str) -> dict[str, tuple[int, int]]:
    """Every file under ``folder``: normalised path -> (mtime_ns, size)."""
    files = {}
    for root, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            stat = os.stat(path)
            files[norm(path)] = (stat.st_mtime_ns, stat.st_size)
    return files


def changed(before: dict, after: dict) -> set[str]:
    """The files written, made or removed between two :func:`tree` listings."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def press(editor, key: str, modifiers=NO_MODS) -> None:
    """A key click on the canvas, as the user types it (``"G"``, ``"2"``)."""
    QTest.keyClick(editor.graphics_view, QKeySequence(key)[0].key(), modifiers)


def tap_b(editor) -> None:
    """B pressed and released on the canvas without a wheel turn."""
    QTest.keyClick(editor.graphics_view, Qt.Key.Key_B)


def paint(editor, *points) -> None:
    """A stroke of the edited layer's current tool (a drag on the canvas input)."""
    drag(editor, *points)


def ids(editor) -> np.ndarray:
    """The class-id plane of the open image."""
    return editor.layers[CLASSES].document.history.plane


def ticked(editor) -> frozenset[str]:
    return editor.layers[CLASSES].document.ticked()


def box(editor, key: str) -> QCheckBox:
    """The "checked" box of class ``key`` on the page's class panel."""
    widget = editor.class_panel._rows[key].checkbox
    assert isinstance(widget, QCheckBox)
    return widget


def row_text(editor, layer_id: str) -> str:
    return editor.layer_panel.rows[layer_id].state_label.text()


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


def pixels(widget) -> np.ndarray:
    QApplication.processEvents()
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    width = image.width()
    return rows.reshape(image.height(), image.bytesPerLine())[:, : width * 4].reshape(
        image.height(), width, 4
    )


def write_unknown_class(image: str) -> bytes:
    """A labels file from another project: id 4 = 'hypha', unknown here (becomes Ignore)."""
    other = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    plane = np.zeros((H, W), np.uint8)
    plane[5:15, 5:15] = 4
    plane[30:40, 30:40] = 1
    path = ProjectLayout.for_image(image).gt_path("a")
    write_tracing(path, Tracing(plane, frozenset({"root"})), other)
    with open(path, "rb") as fh:
        return fh.read()


def fake_class_editor(monkeypatch, result: Registry | None, seen: list | None = None) -> None:
    """Replace the class editor with a stub that accepts with ``result``."""

    class _FakeDialog:
        def __init__(self, registry, parent=None):
            if seen is not None:
                seen.append(registry)

        def exec(self):
            return 1

        def result_registry(self):
            return result

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

        def testAttribute(self, _attribute):  # noqa: N802 - Qt API (run_modal asks)
            return False

    fake = types.ModuleType("app.gui.class_editor_dialog")
    fake.ClassEditorDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.class_editor_dialog", fake)


class Prompts:
    """The unsaved-changes box, answered with ``answer``; ``calls`` records each question."""

    def __init__(self) -> None:
        self.answer = CANCEL
        self.calls: list[tuple[str, str, str]] = []

    def ask_unsaved(self, parent, noun, name):
        self.calls.append(("ask_unsaved", noun, name))
        return self.answer


class Confirm:
    """The Remove GT box, answered with ``answer``; ``calls`` records ``(name, detail)``."""

    def __init__(self) -> None:
        self.answer = False
        self.calls: list[tuple[str, str]] = []

    def __call__(self, parent, name, detail) -> bool:
        self.calls.append((name, detail))
        return self.answer


# ============================================================================= fixtures
pytestmark = pytest.mark.usefixtures("correct_guards")


@pytest.fixture
def signals() -> dict[str, list]:
    """The page signals of the last page made, recorded (filled by ``make_editor``)."""
    return {}


@pytest.fixture
def notices() -> list[tuple[str, str]]:
    """Every notice of the last page made, from its build on (filled by ``make_editor``)."""
    return []


@pytest.fixture
def make_editor(app, signals, notices):
    """``make(image=None, base=None, *, size=(1100, 800), show=True)``: a CorrectEditor whose
    signals and notices are recorded before it opens ``image``."""
    factory = editor_factory()
    build = next(factory)

    def make(image=None, base=None, *, size=(1100, 800), show=True) -> CorrectEditor:
        editor = build(size=size, show=show)
        notices.clear()
        editor.notice.connect(lambda message, kind: notices.append((message, kind)))
        for name in PAGE_SIGNALS:
            signals[name] = record(getattr(editor, name))
        if image is not None:
            editor.load_image(image, base)
            settle(editor)
        return editor

    yield make
    for _ in factory:  # its teardown deletes every page made
        pass


@pytest.fixture
def project_lossy(project):
    """``project`` with a ``gt/a.png`` that holds a class unknown here (a lossy remap)."""
    write_unknown_class(project.a)
    return project


@pytest.fixture
def project_bad_registry(project):
    """``project`` with an invalid ``gt/classes.json``."""
    os.makedirs(os.path.join(project.folder, "gt"), exist_ok=True)
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")
    return project


@pytest.fixture
def layout(project) -> ProjectLayout:
    return ProjectLayout.for_image(project.a)


@pytest.fixture
def prompts(monkeypatch) -> Prompts:
    fake = Prompts()
    monkeypatch.setattr(editor_prompts, "ask_unsaved", fake.ask_unsaved)
    return fake


@pytest.fixture
def page(make_editor, project):
    """The page on image a, the class labels edited, active, the keys on its canvas."""
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(CLASSES)
    activate(editor)
    return editor


@pytest.fixture
def themed(app):
    """The app stylesheet: a focus ring under test is the theme's."""
    previous = app.styleSheet()
    app.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield app
    app.setStyleSheet(previous)


@pytest.fixture
def reads(monkeypatch) -> list[str]:
    """The labels files read (``gt/<stem>.png``)."""
    calls: list[str] = []
    real = gt_package.read_tracing

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(gt_package, "read_tracing", spy)
    return calls


@pytest.fixture
def made(app):
    """Widgets closed and deleted deterministically after the test."""
    widgets: list = []
    yield widgets
    delete_widgets(widgets)


# ------------------------------------------------------------------- the main window
@pytest.fixture
def confirm() -> Confirm:
    return Confirm()


@pytest.fixture
def window_with_project(app, tmp_path, no_temp_sweep, confirm):
    """A shown MainWindow on ``project`` with image a open in the Correct stage; the
    Remove GT box is ``confirm``."""
    project = new_project(tmp_path)
    window = build_window(tmp_path / "settings.ini")
    made = [window]
    open_folder(window, project.folder, 2)
    window.file_list.expandAll()
    item = window.library_index.item_for("a")
    window.file_list.setCurrentItem(item)
    window.on_tree_item_clicked(item, 0)
    settle()
    window.ribbon_buttons["Correct"].click()
    settle()
    window.correct_editor.actions.confirm_remove = confirm
    yield SimpleNamespace(window=window, project=project, editor=window.correct_editor)
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


# ============================================================================ the page
def test_the_page_has_three_layers_in_panel_order_and_the_class_panel_shows_only_when_active(
    make_editor, project
):
    editor = make_editor(project.a, project.folder)

    assert list(editor.layers) == [MASK, SKELETON, CLASSES]
    assert list(editor.layer_panel.rows) == [MASK, SKELETON, CLASSES]
    assert list(editor.session.layers) == [MASK, SKELETON, CLASSES]
    assert isinstance(editor.side_panels, SidePanels) and isinstance(editor.class_panel, ClassPanel)
    assert editor.side_panels.isAncestorOf(editor.layer_panel)
    assert editor.side_panels.isAncestorOf(editor.class_panel)
    assert not editor.class_panel.isVisible()
    editor.set_active_layer(CLASSES)
    settle(editor)
    assert editor.class_panel.isVisible()
    assert editor.layer_panel.rows[CLASSES].is_active()
    editor.set_active_layer(SKELETON)
    settle(editor)
    assert not editor.class_panel.isVisible()


def test_the_classes_row_names_its_key_and_layer(make_editor, project):
    row = make_editor(project.a, project.folder).layer_panel.rows[CLASSES]

    assert row.name_label.text() == "Classes" and row.key_label.text() == "G"
    assert row.toolTip() == "Edit the class labels (G)"
    assert "Hide the class labels" in row.eye_button.toolTip()
    assert "H: every layer" in row.eye_button.toolTip()
    assert "Shift+H: the others" in row.eye_button.toolTip()


def test_the_side_card_grows_with_the_class_panel_and_stays_on_the_right(make_editor, project):
    editor = make_editor(project.a, project.folder)
    card = editor.side_panels
    settle(editor)
    short = card.height()

    editor.set_active_layer(CLASSES)
    settle(editor)

    assert card.height() > short + editor.class_panel._header.sizeHint().height()
    assert card.geometry().right() < editor.width()
    assert editor.class_panel.geometry().bottom() <= card.height()
    assert editor.layer_panel.graphicsEffect() is None  # one card, one shadow
    assert editor.class_panel.graphicsEffect() is None and card.graphicsEffect() is not None


def test_keys_m_l_g_switch_layers_and_x_returns_to_the_previous_one(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)

    press(editor, "L")
    assert editor.active_layer_id == SKELETON
    press(editor, "G")
    assert editor.active_layer_id == CLASSES
    press(editor, "X")
    assert editor.active_layer_id == SKELETON
    press(editor, "M")
    press(editor, "X")
    assert editor.active_layer_id == SKELETON and editor.previous_layer_id == MASK
    assert shortcuts.find_conflicts() == []


def test_x_on_a_fresh_page_switches_between_the_mask_and_the_skeleton(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    assert editor.previous_layer_id is None

    press(editor, "X")
    assert editor.active_layer_id == SKELETON
    press(editor, "X")
    assert editor.active_layer_id == MASK


def test_the_correct_rows_name_the_layer_keys():
    rows = {spec.action: spec for spec in shortcuts.specs_for(shortcuts.CORRECT)}

    assert [rows[a].keys for a in ("mask_layer", "skeleton_layer", "classes_layer")] == [
        ("M",),
        ("L",),
        ("G",),
    ]
    assert rows["classes_layer"].description == "Edit the class labels (GT for training)"
    assert rows["swap_layer"].description == "Switch to the previous layer"
    assert rows["select_class"].keys == ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0")
    assert rows["select_class"].label == "1–9, 0"
    assert rows["select_class"].description == (
        "Pick the n-th class; 0 = Ignore (class labels layer only)"
    )
    assert rows["toggle_layers"].description == "Show or hide every layer (see the photo)"
    assert rows["toggle_reference"].description == "Show or hide the other layers"


def test_digits_pick_classes_only_while_the_classes_layer_is_active(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)

    press(editor, "2")
    assert editor.active_layer_id == MASK and editor.layers[CLASSES].active_class() == "root"
    press(editor, "G")
    press(editor, "2")
    assert editor.layers[CLASSES].active_class() == "nodule"
    assert editor.class_panel.active_key() == "nodule"
    press(editor, "0")
    assert editor.layers[CLASSES].active_class() == "ignore"
    press(editor, "9")  # no ninth class: nothing changes
    assert editor.layers[CLASSES].active_class() == "ignore"


def test_keypad_digits_pick_classes_too(page):
    keypad = Qt.KeyboardModifier.KeypadModifier
    press(page, "2", keypad)
    assert page.class_panel.active_key() == "nodule"
    press(page, "0", keypad)
    assert page.class_panel.active_key() == "ignore"
    press(page, "3", keypad)
    assert page.class_panel.active_key() == "root_tip"


def test_the_brush_paints_in_the_active_class_colour(page):
    press(page, "2")  # nodule
    assert page.layers[CLASSES].brush_color.name() == "#ff7eb6"
    press(page, "1")
    assert page.layers[CLASSES].brush_color.name() == "#5fd6a0"


def test_b_tap_requests_the_brush_of_the_active_raster_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    editor.set_active_layer(CLASSES)
    editor.request_tool(CLASSES, "eraser")

    tap_b(editor)
    assert (editor.active_layer_id, editor.layers[CLASSES].current_tool) == (CLASSES, "brush")
    editor.set_active_layer(SKELETON)
    tap_b(editor)
    assert editor.active_layer_id == MASK and editor.layers[MASK].current_tool == "brush"


def test_f_takes_the_fill_of_the_classes_layer_and_the_masks_from_the_skeleton(
    make_editor, project
):
    editor = make_editor(project.a, project.folder)
    activate(editor)

    press(editor, "G")
    press(editor, "F")
    assert (editor.active_layer_id, editor.layers[CLASSES].current_tool) == (CLASSES, "fill")
    press(editor, "L")
    press(editor, "F")
    assert (editor.active_layer_id, editor.layers[MASK].current_tool) == (MASK, "fill")


def test_b_and_the_wheel_size_the_brush_of_the_classes_layer(page):
    from tests.test_correct_editor_keys import wheel

    mask_size = page.layers[MASK].size()
    QTest.keyPress(page.graphics_view, Qt.Key.Key_B)
    wheel(page.graphics_view, 120)
    QTest.keyRelease(page.graphics_view, Qt.Key.Key_B)

    assert page.active_layer_id == CLASSES and page.layers[CLASSES].size() == 9
    assert page.layers[MASK].size() == mask_size


def test_each_class_keeps_its_own_brush_size_on_the_dock(page):
    page.controls.size_changed.emit(20)  # root
    press(page, "2")
    assert page.layers[CLASSES].size() == 8
    page.controls.size_changed.emit(30)
    press(page, "1")
    slider = next(s for s in page.controls.size_sliders.values() if s.isVisibleTo(page.dock))
    assert page.layers[CLASSES].size() == 20 and slider.slider.value() == 20


def test_shift_h_hides_and_shows_every_layer_but_the_edited_one(page):
    page.toggle_reference()
    assert [page.layers[i].is_visible() for i in (MASK, SKELETON, CLASSES)] == [False, False, True]
    page.toggle_reference()
    assert all(layer.is_visible() for layer in page.layers.values())
    page.set_layer_visible(MASK, False)
    page.toggle_reference()  # one other layer is shown: hide the others
    assert not page.layers[SKELETON].is_visible() and page.layers[CLASSES].is_visible()


def test_the_controls_on_the_classes_layer(page):
    controls = page.controls

    assert not controls.load_button.isEnabled()
    assert not controls.prediction_button.isEnabled()
    assert controls.prediction_button.toolTip() == "Load prediction (not for the class labels)"
    assert controls.clear_icon_button.toolTip() == (
        "Clear class labels (undoable) · Save then writes an empty file"
    )
    assert controls.tool_buttons[(CLASSES, "fill")].toolTip() == (
        "Fill a closed outline in the class labels (F)"
    )
    assert controls.tool_buttons[(MASK, "brush")].toolTip() == (
        "Mask brush (B) · hold B + scroll to resize"
    )
    assert controls.tool_buttons[(CLASSES, "brush")].toolTip() == (
        "Class labels brush (B) · hold B + scroll to resize"
    )


def test_the_class_labels_tools_show_on_the_rail_only_while_edited(make_editor, project):
    """As the class panel: three more rail tools for every user did not fit beside the
    polyline prompt in a 700 x 520 canvas (``test_the_overlays_never_collide``)."""
    editor = make_editor(project.a, project.folder)
    buttons = editor.controls.tool_buttons
    classes = [buttons[(CLASSES, tool)] for tool in ("brush", "eraser", "fill")]
    assert not any(button.isVisible() for button in classes)
    assert buttons[(SKELETON, "select")].isVisible()  # another layer's tools stay, dimmed

    editor.set_active_layer(CLASSES)
    settle(editor)
    assert all(button.isVisible() for button in classes)
    assert buttons[(CLASSES, "brush")].isChecked()

    editor.request_tool(MASK, "fill")
    settle(editor)
    assert not any(button.isVisible() for button in classes)
    assert editor.tool_rail.geometry().bottom() < editor.height()


def test_the_tool_hints_name_the_edited_layer(page):
    """The Brush and Eraser hints said "the mask" on every raster layer (WG-T4 review)."""
    chip = page.status_chip
    assert chip.hint_label.text() == "Class labels · Brush · drag to paint"
    assert (
        page.layers[CLASSES].tool_hint()[1].startswith("Click and drag to paint the class labels")
    )
    page.request_tool(CLASSES, "eraser")
    assert (
        page.layers[CLASSES].tool_hint()[1].startswith("Click and drag to erase the class labels")
    )
    page.set_active_layer(MASK)
    assert page.layers[MASK].tool_hint()[1].startswith("Click and drag to paint the mask")


def test_a_one_pixel_click_paints_its_class_pixel(page):
    page.controls.size_changed.emit(1)
    drag(page, (30, 20))

    assert np.count_nonzero(ids(page)) == 1
    assert ids(page)[20, 30] == default_registry().by_key("root").id


def test_the_classes_row_says_what_is_saved(page):
    assert row_text(page, CLASSES) == "No class labels"
    paint(page, (20, 100), (80, 110))
    assert row_text(page, CLASSES) == "Unsaved"
    assert page.save()
    assert row_text(page, CLASSES) == "GT · 1/3 checked"


# ============================================================================== saving
def test_a_classes_save_writes_only_gt_stem_png_and_emits_gt_saved(
    make_editor, project, layout, signals, notices
):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    before = tree(project.folder)

    assert editor.save() is True

    expected = {norm(layout.gt_path("a")), norm(registry_path(project.folder))}
    assert changed(before, tree(project.folder)) == expected
    assert signals["gt_saved"] == [project.a] and signals["mask_saved"] == []
    assert ("Saved class labels · a.png", "success") in notices
    assert read_tracing(layout.gt_path("a")).ids[105, 50] == 1


def test_each_layer_writes_only_its_own_file(make_editor, project, layout):
    """The guard of Design 3.7: one layer, one file."""
    save_registry(project.folder, default_registry())  # so the labels save writes one file
    editor = make_editor(project.a, project.folder)
    edits = {
        MASK: lambda: paint(editor, (20, 100), (60, 100)),
        SKELETON: lambda: draw_line(editor),
        CLASSES: lambda: paint(editor, (20, 100), (80, 110)),
    }
    files = {
        MASK: layout.gt_mask_path("a"),
        SKELETON: layout.gt_skeleton_path("a"),
        CLASSES: layout.gt_path("a"),
    }
    for layer_id in (MASK, SKELETON, CLASSES):
        editor.set_active_layer(layer_id)
        edits[layer_id]()
        before = tree(project.folder)
        assert editor.save(), layer_id
        assert changed(before, tree(project.folder)) == {norm(files[layer_id])}, layer_id


def test_prompts_name_the_class_labels(make_editor, project, prompts):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))  # the mask
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 120), (80, 120))
    prompts.answer = DISCARD

    assert editor.maybe_save_changes() is True

    assert prompts.calls == [("ask_unsaved", "mask and class labels", "a.png")]
    assert not editor.has_unsaved_changes() and not ids(editor).any()


def test_an_untouched_image_writes_no_class_labels(make_editor, project, layout, notices):
    """O2: Save writes only the unsaved layers (Trace GT wrote an empty file here)."""
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(CLASSES)

    assert editor.save() is False
    assert not os.path.exists(layout.gt_path("a")) and ("Nothing unsaved", "info") in notices


def test_a_tick_alone_is_saved(page, layout):
    page.class_panel.reviewed_changed.emit("nodule", True)
    assert page.has_unsaved_changes()

    assert page.save()
    tracing = read_tracing(layout.gt_path("a"))
    assert tracing.reviewed == frozenset({"nodule"}) and not tracing.ids.any()


# =============================================================================== PgDn
def test_pgdn_prompts_after_a_lossy_remap_and_saves_silently_otherwise(
    make_editor, project_lossy, layout, prompts, notices
):
    a = project_lossy.a
    original = (Path(layout.gt_path("a"))).read_bytes()
    editor = make_editor(a, project_lossy.folder)
    moves = record(editor.navigate_requested)
    assert editor.layers[CLASSES].is_dirty()
    assert any(kind == "warn" and "hypha" in message for message, kind in notices)

    editor.navigate(1)  # lossy: the prompt, answered Cancel
    assert prompts.calls == [("ask_unsaved", "class labels", "a.png")] and moves == []
    assert Path(layout.gt_path("a")).read_bytes() == original

    prompts.answer = SAVE
    editor.navigate(1)
    assert moves == [1] and (read_tracing(layout.gt_path("a")).ids[5:15, 5:15] == 255).all()

    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))  # no remap pending now: a silent save
    prompts.calls.clear()
    editor.navigate(1)
    assert prompts.calls == [] and moves == [1, 1]
    assert read_tracing(layout.gt_path("a")).ids[105, 50] == 1


def test_pgdn_saves_class_labels_and_asks_for_the_next_image(page, layout):
    moves = record(page.navigate_requested)
    paint(page, (20, 100), (80, 110))

    press(page, "PgDown")

    assert moves == [1] and os.path.isfile(layout.gt_path("a"))
    assert not page.has_unsaved_changes()


def test_pgdn_saves_a_lossless_remap_silently(make_editor, project, layout, notices):
    """Ids that differ only by number (same keys) are remapped and saved without a prompt."""
    swapped = default_registry().to_dict()
    swapped["classes"][0]["id"], swapped["classes"][1]["id"] = 2, 1  # root = 2, nodule = 1
    plane = np.zeros((H, W), np.uint8)
    plane[5:15, 5:15] = 2  # root in the other project's ids
    write_tracing(
        layout.gt_path("a"), Tracing(plane, frozenset({"root"})), Registry.from_dict(swapped)
    )
    editor = make_editor(project.a, project.folder)
    moves = record(editor.navigate_requested)
    assert editor.layers[CLASSES].is_dirty() and not [n for n in notices if n[1] == "warn"]

    editor.navigate(1)  # the guard fails on any prompt

    assert moves == [1] and (read_tracing(layout.gt_path("a")).ids[5:15, 5:15] == 1).all()


# ============================================================= invalid gt/classes.json
def test_an_invalid_registry_locks_only_the_classes_layer(
    make_editor, project_bad_registry, notices
):
    folder, a = project_bad_registry.folder, project_bad_registry.a
    editor = make_editor(a, folder)
    paint(editor, (20, 100), (60, 100))  # the mask
    assert editor.save() is True

    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    paint(editor, (20, 120), (80, 120))
    editor.clear_active_layer()
    editor.class_panel.reviewed_changed.emit("nodule", True)
    editor.edit_classes()  # refused too, without a dialog (the guard fails on one)

    classes = editor.layers[CLASSES]
    assert classes.status().label == "Read-only" and not classes.is_dirty()
    assert not ids(editor).any() and not box(editor, "nodule").isChecked()
    refusals = [n for n in notices if n[0].startswith("Editing is off: gt/classes.json is invalid")]
    assert len(refusals) == 1 and refusals[0][1] == "danger"
    # One danger notice per image: the open's read-only notice is not shown as well.
    assert [kind for _message, kind in notices].count("danger") == 1
    assert not [m for m, _k in notices if m.startswith("gt/classes.json is invalid")]


def test_an_invalid_registry_refuses_once_more_on_the_next_image(
    make_editor, project_bad_registry, notices
):
    editor = make_editor(project_bad_registry.a, project_bad_registry.folder)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    editor.load_image(project_bad_registry.b, project_bad_registry.folder)
    paint(editor, (20, 100), (80, 110))

    refusals = [m for m, _k in notices if m.startswith("Editing is off")]
    assert len(refusals) == 2 and not editor.has_unsaved_changes()


def test_an_invalid_registry_shows_read_only_on_the_row_and_on_the_chip_while_edited(
    make_editor, project_bad_registry
):
    """The lock is the class labels' own (``LayerStatus.local``): the chip says "Read-only"
    only while they are edited; the other layers' states always show (review WG-T5 r1)."""
    editor = make_editor(project_bad_registry.a, project_bad_registry.folder)
    row, chip = editor.layer_panel.rows[CLASSES], editor.status_chip
    assert row.state_label.text() == "Read-only"
    assert "Expecting property name" in row.state_label.toolTip()
    assert str(project_bad_registry.folder) not in row.state_label.toolTip()
    assert editor.layers[CLASSES].status().local
    assert chip.state() == "none" and "Read-only" not in chip.state_label.text()

    editor.set_active_layer(CLASSES)
    assert chip.state() == "error" and "Read-only" in chip.state_label.text()
    assert "gt/classes.json is invalid" in chip.state_label.toolTip()

    editor.set_active_layer(MASK)
    assert chip.state() == "none"


def test_a_read_only_class_labels_layer_never_hides_another_layers_unsaved_edits(
    make_editor, project_bad_registry
):
    editor = make_editor(project_bad_registry.a, project_bad_registry.folder)
    chip = editor.status_chip

    paint(editor, (20, 100), (60, 100))  # the mask
    assert chip.state() == "dirty" and editor.layer_panel.rows[MASK].state_label.text() == "Unsaved"
    editor.set_active_layer(SKELETON)
    assert chip.state() == "dirty"  # another layer's unsaved edits still count
    editor.set_active_layer(CLASSES)
    assert chip.state() == "error" and "Read-only" in chip.state_label.text()
    assert editor.layer_panel.rows[CLASSES].state_label.text() == "Read-only"


# ======================================================================== Classes…
def test_edit_classes_saves_the_registry_and_recolours(page, project, monkeypatch, notices):
    paint(page, (10, 30), (70, 30))  # root
    press(page, "2")
    paint(page, (40, 5), (40, 55))  # nodule
    before = ids(page).copy()
    layer = page.layers[CLASSES]
    new_registry = layer.registry().update("root", color="#123456", name="Main root")
    seen: list = []
    fake_class_editor(monkeypatch, new_registry, seen)

    page.edit_classes()

    assert seen == [default_registry()] and layer.registry() == new_registry
    assert np.array_equal(ids(page), before)
    argb = pixmap_to_argb_array(layer.canvas.image)
    assert tuple(argb[30, 15, :3]) == (0x56, 0x34, 0x12)  # BGR of #123456
    assert (
        os.path.exists(registry_path(project.folder)) and ("Classes updated", "success") in notices
    )
    assert page.class_panel._rows["root"]._name_label.text() == "Main root"
    page.undo()  # the history holds ids: undoing the nodule stroke keeps the root ids
    assert ids(page)[30, 15] == 1 and ids(page)[30, 40] == 1 and ids(page)[10, 40] == 0
    assert page.save()
    assert read_tracing(page.layers[CLASSES].document.gt_path).ids[30, 15] == 1


def test_the_gear_and_the_topbar_open_the_same_class_editor(page, monkeypatch):
    seen: list = []
    fake_class_editor(monkeypatch, None, seen)

    page.class_panel.edit_requested.emit()

    assert len(seen) == 1


def test_classes_saved_by_the_page_are_no_change_on_disk(page, project, monkeypatch, reads):
    """The registry write re-stamps the class labels: re-selecting the image keeps them,
    their history and asks nothing (WG-T4 review, carried concern 2)."""
    paint(page, (10, 30), (70, 30))
    assert page.save()
    paint(page, (10, 40), (70, 40))
    page.undo()  # back to the saved labels, with one undo and one redo
    bigger = page.layers[CLASSES].registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    fake_class_editor(monkeypatch, bigger)
    page.edit_classes()
    reads.clear()

    page.load_image(project.a, project.folder)  # the guard fails on any prompt

    assert reads == [] and not page.session.files_changed()
    assert page.layers[CLASSES].can_undo() and page.layers[CLASSES].can_redo()


def test_a_restored_class_is_applied_saved_and_no_change_on_disk(
    make_editor, project, monkeypatch, notices
):
    """WG-T9: Restore in the class editor ends in the page's usual Classes… path."""
    retired = default_registry().retire("nodule")
    save_registry(project.folder, retired)
    editor = make_editor(project.a, project.folder)
    layer = editor.layers[CLASSES]
    assert layer.registry().by_key("nodule").retired
    fake_class_editor(monkeypatch, retired.restore("nodule"))

    editor.edit_classes()

    assert layer.registry().by_key("nodule").retired is False
    assert load_registry(project.folder).by_key("nodule").retired is False
    assert not editor.session.files_changed()
    assert ("Classes updated", "success") in notices
    assert "nodule" in editor.class_panel._rows


def test_classes_without_an_image_says_where_classes_are_kept(make_editor, notices):
    editor = make_editor()

    editor.edit_classes()  # the guard fails on any dialog

    assert notices == [("Open an image first; classes are saved with its folder.", "info")]


def test_the_class_editor_dialog_is_deleted_after_use(page, monkeypatch):
    from PyQt6 import sip

    opened = []
    monkeypatch.setattr(ClassEditorDialog, "exec", lambda dialog: opened.append(dialog) or 0)

    page.edit_classes()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert len(opened) == 1 and sip.isdeleted(opened[0])


def test_the_class_panel_resizes_after_classes_are_added(page, monkeypatch):
    bigger = (
        page.layers[CLASSES]
        .registry()
        .add(key="dead_root", name="Dead root", color="#7f5af0")
        .add(key="hypha", name="Hypha", color="#3fa7ff")
    )
    fake_class_editor(monkeypatch, bigger)

    page.edit_classes()
    settle(page)

    panel = page.class_panel
    assert set(panel._rows) == {c.key for c in bigger.active()}
    assert panel._scroll.height() >= panel._rows_container.sizeHint().height()
    assert page.side_panels.geometry().right() < page.width()


# ============================================================================ Remove GT…
def _all_gt(editor, layout, folder) -> None:
    """Every layer edited and saved: the three GT files exist."""
    save_registry(folder, default_registry())
    paint(editor, (20, 100), (60, 100))
    editor.set_active_layer(SKELETON)
    draw_line(editor)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 120), (80, 120))
    assert editor.save()
    for path in (layout.gt_mask_path("a"), layout.gt_skeleton_path("a"), layout.gt_path("a")):
        assert os.path.isfile(path), path


def test_remove_gt_is_disabled_while_edits_are_unsaved_and_asks_before_it_deletes(
    window_with_project, confirm
):
    window, editor, project = (
        window_with_project.window,
        window_with_project.editor,
        window_with_project.project,
    )
    layout = ProjectLayout.for_image(project.a)
    gt_mask, gt_skel, gt_labels = (
        layout.gt_mask_path("a"),
        layout.gt_skeleton_path("a"),
        layout.gt_path("a"),
    )
    removed = record(editor.gt_removed)
    button = window.gt_remove_button
    save_registry(project.folder, default_registry())
    paint(editor, (20, 100), (60, 100))
    assert not button.isEnabled() and "Save or discard" in button.toolTip()
    editor.set_active_layer(SKELETON)
    draw_line(editor)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 120), (80, 120))
    assert editor.save()
    assert button.isEnabled() and window.image_manager.has_gt_mask("a")

    confirm.answer = False
    button.click()
    assert os.path.isfile(gt_mask) and removed == []

    confirm.answer = True
    button.click()

    assert not any(map(os.path.exists, (gt_mask, gt_skel, gt_labels)))
    assert os.path.isfile(project.mask()) and os.path.isfile(project.generated())
    assert removed == [project.a] and editor.layers[MASK].status().label == "Prediction"
    assert editor.layers[SKELETON].status().label == "Prediction"
    assert editor.layers[CLASSES].status().label == "No class labels"
    assert not window.image_manager.has_gt_mask("a")
    assert not window.image_manager.has_gt_skeleton("a")
    assert not window.image_manager.has_gt_labels("a")
    assert os.path.isfile(registry_path(project.folder))  # the project's classes stay
    assert confirm.calls[-1] == ("a.png", REMOVE_DETAIL)


def test_remove_gt_needs_an_open_image(make_editor):
    editor = make_editor()
    button = editor.actions.remove_button

    assert not button.isEnabled() and button.text() == "Remove GT…"


def test_remove_gt_toasts_what_it_removed(make_editor, project, layout, confirm, notices):
    editor = make_editor(project.a, project.folder)
    editor.actions.confirm_remove = confirm
    _all_gt(editor, layout, project.folder)
    confirm.answer = True
    notices.clear()

    editor.remove_gt()

    assert ("Removed GT · a.png", "success") in notices
    assert not editor.has_unsaved_changes() and not ids(editor).any()


def test_remove_gt_with_no_gt_asks_nothing(make_editor, project, confirm, notices):
    editor = make_editor(project.a, project.folder)
    editor.actions.confirm_remove = confirm

    editor.remove_gt()

    assert confirm.calls == [] and notices == [("No GT to remove for a.png", "info")]


def test_remove_gt_forgets_the_read_ahead_of_that_image(make_editor, project, layout, confirm):
    editor = make_editor(project.a, project.folder)
    editor.actions.confirm_remove = confirm
    _all_gt(editor, layout, project.folder)
    editor.set_neighbour_provider(lambda path: [project.b])
    editor.load_image(project.b, project.folder)
    editor.load_image(project.a, project.folder)  # b is kept as read; a was read back
    confirm.answer = True
    forgotten = []
    real = editor.paging.forget
    editor.paging.forget = lambda path=None: forgotten.append(path) or real(path)

    editor.remove_gt()

    assert forgotten and all(path == project.a for path in forgotten)
    assert norm(project.a) not in {norm(p) for p in editor.paging.pending()}
    cached = editor.paging.cached_photo(project.a)
    assert cached is not None  # the photo stays shared; the documents were read again
    assert editor.layers[MASK].status().label == "Prediction"


def test_remove_gt_reports_what_it_could_not_remove(
    make_editor, project, layout, confirm, notices, monkeypatch
):
    """Must-do (d): a file that stays is named, the rest is removed and the image reopens."""
    editor = make_editor(project.a, project.folder)
    editor.actions.confirm_remove = confirm
    _all_gt(editor, layout, project.folder)
    removed = record(editor.gt_removed)
    stuck = layout.gt_skeleton_path("a")
    real_remove = os.remove

    def remove(path):
        if norm(path) == norm(stuck):
            raise PermissionError(13, "Permission denied")
        real_remove(path)

    monkeypatch.setattr(os, "remove", remove)
    confirm.answer = True
    notices.clear()

    editor.remove_gt()

    assert os.path.isfile(stuck) and not os.path.exists(layout.gt_mask_path("a"))
    assert removed == [project.a]
    assert editor.layers[MASK].status().label == "Prediction"  # reopened from what is left
    assert editor.layers[SKELETON].status().label == "GT"
    assert notices == [
        (
            "Remove GT · a.png: removed gt/mask/a.png, gt/a.png; could not remove"
            " gt/skeletons/a.png (Permission denied)",
            "danger",
        )
    ]


# ====================================================================== the main window
def test_the_correct_page_holds_the_gt_actions(window_with_project):
    window = window_with_project.window
    topbar = window.ribbon
    page = window.action_bar_stack.widget(topbar._pages["Correct"])

    assert [b.text() for b in page.findChildren(QPushButton)] == [
        # O18: no Clear here, the page's one Clear is in the dock
        "Classes…",
        "Export dataset…",
        "Remove GT…",
    ]
    assert window.gt_remove_button is window.correct_editor.actions.remove_button
    assert "Trace GT" not in topbar._pages  # the stage and its page are gone (WG-T11)


def test_the_topbar_classes_button_opens_the_page_class_editor(window_with_project, monkeypatch):
    seen: list = []
    fake_class_editor(monkeypatch, None, seen)

    window_with_project.window.gt_classes_button.click()

    assert len(seen) == 1


def test_export_gt_dataset_saves_the_correct_editor_first(window_with_project, monkeypatch):
    import app.gui.export_dataset_dialog as edd

    window, editor = window_with_project.window, window_with_project.editor
    layout = ProjectLayout.for_image(window_with_project.project.a)
    opened = []
    monkeypatch.setattr(edd.ExportDatasetDialog, "exec", lambda dialog: opened.append(dialog) or 0)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    asked = []
    monkeypatch.setattr(editor_prompts, "ask_unsaved", lambda *args: asked.append(args) or SAVE)

    window.export_gt_dataset()

    assert len(asked) == 1 and os.path.isfile(layout.gt_path("a")) and len(opened) == 1

    paint(editor, (20, 120), (80, 120))
    monkeypatch.setattr(editor_prompts, "ask_unsaved", lambda *args: CANCEL)
    window.export_gt_dataset()
    assert len(opened) == 1  # Cancel: no export without the edits


def test_the_window_handlers_follow_the_gt_files(window_with_project, monkeypatch):
    """``on_gt_saved`` marks the class labels; ``on_gt_removed`` sets every GT mark from
    the disk (a file that could not be deleted keeps its mark: must-do d);
    ``on_skeleton_cleared`` drops the skeleton's mark. Each repaints the row."""
    from app.gui.window import editors as window_editors

    window, project = window_with_project.window, window_with_project.project
    layout = ProjectLayout.for_image(project.a)
    manager = window.image_manager
    repainted: list[str] = []
    real = window.library_index.refresh_status
    monkeypatch.setattr(
        window.library_index,
        "refresh_status",
        lambda name=None: repainted.append(name) or real(name),
    )

    window_editors.on_gt_saved(window, project.a)
    assert manager.has_gt_labels("a") and repainted == ["a"]

    os.makedirs(os.path.dirname(layout.gt_skeleton_path("a")), exist_ok=True)
    Path(layout.gt_skeleton_path("a")).write_bytes(b"stays")  # the file that stayed
    manager.set_gt("a", "mask", True)
    window_editors.on_gt_removed(window, project.a)
    assert (manager.has_gt_mask("a"), manager.has_gt_skeleton("a")) == (False, True)
    assert not manager.has_gt_labels("a") and repainted == ["a", "a"]

    window_editors.on_skeleton_cleared(window, project.a)
    assert not manager.has_gt_skeleton("a") and repainted == ["a", "a", "a"]


def test_a_class_label_save_marks_the_library_row(window_with_project):
    window, editor = window_with_project.window, window_with_project.editor
    assert not window.image_manager.has_gt_labels("a")
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))

    assert editor.save()

    assert window.image_manager.has_gt_labels("a")
    assert window.metrics_bar._status_value.text().endswith("Classes: traced")


def test_a_class_label_save_shows_one_toast_the_pages_summary(window_with_project, monkeypatch):
    window, editor = window_with_project.window, window_with_project.editor
    toasts: list[str] = []
    real = window.notify
    monkeypatch.setattr(
        window, "notify", lambda message, *a, **k: toasts.append(message) or real(message, *a, **k)
    )
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))

    assert editor.save()

    assert toasts == [f"Saved class labels · {os.path.basename(window_with_project.project.a)}"]


# ==================================================================== the class panel
def test_tab_reaches_the_gear_then_each_box_in_class_order(page):
    panel = page.class_panel
    wanted = [panel._gear, *(box(page, key) for key in KEYS)]

    chain = walk(page)

    assert all(widget in chain for widget in wanted), chain
    start = chain.index(panel._gear)
    assert chain[start : start + len(wanted)] == wanted


def test_a_click_on_the_class_panel_never_takes_the_focus(page):
    panel = page.class_panel
    for widget in (panel, *panel.findChildren(QWidget)):
        assert not widget.focusPolicy() & Focus.ClickFocus, widget
    assert panel._gear.focusPolicy() == Focus.TabFocus
    for key in KEYS:
        assert box(page, key).focusPolicy() == Focus.TabFocus
        assert panel._rows[key].focusPolicy() == Focus.NoFocus


def test_space_ticks_the_focused_box_and_the_focus_stays_for_the_next_one(page):
    nodule, root_tip = box(page, "nodule"), box(page, "root_tip")
    nodule.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(nodule, Qt.Key.Key_Space)

    assert nodule.isChecked() and ticked(page) == {"nodule"}
    assert page.has_unsaved_changes() and nodule.hasFocus()
    QTest.keyClick(nodule, Qt.Key.Key_Tab)
    assert root_tip.hasFocus()
    QTest.keyClick(root_tip, Qt.Key.Key_Space)
    assert ticked(page) == {"nodule", "root_tip"}
    QTest.keyClick(root_tip, Qt.Key.Key_Space)
    assert ticked(page) == {"nodule"} and not root_tip.isChecked()


def test_a_click_on_a_box_hands_the_keys_back_to_the_canvas(page):
    view = page.graphics_view
    box(page, "root_tip").setFocus(Qt.FocusReason.TabFocusReason)
    nodule = box(page, "nodule")

    QTest.mouseClick(nodule, Qt.MouseButton.LeftButton)

    assert nodule.isChecked() and ticked(page) == {"nodule"}
    assert QApplication.focusWidget() is view
    QTest.keyPress(view, Qt.Key.Key_Space)  # Space pans; it never ticks a box
    assert view.space_pan
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert ticked(page) == {"nodule"} and not box(page, "root_tip").isChecked()


def test_panel_clicks_keep_the_canvas_focus_for_space(page):
    """Space after ticking a class must pan, never toggle the box (spec 3.4 safety)."""
    panel = page.class_panel
    nodule = box(page, "nodule")
    QTest.mouseClick(nodule, Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel._rows["root_tip"], Qt.MouseButton.LeftButton)
    assert page.layers[CLASSES].active_class() == "root_tip"

    focus = QApplication.focusWidget()
    assert focus is page.graphics_view, focus
    QTest.keyPress(focus, Qt.Key.Key_Space)
    assert page.graphics_view.space_pan
    QTest.keyRelease(focus, Qt.Key.Key_Space)
    assert nodule.isChecked() and ticked(page) == {"nodule"}


@pytest.mark.parametrize("checked", [False, True], ids=["unchecked", "checked"])
def test_the_focused_box_draws_a_focus_ring(themed, page, checked):
    nodule = box(page, "nodule")
    if checked:
        QTest.mouseClick(nodule, Qt.MouseButton.LeftButton)
        assert nodule.isChecked()
    page.graphics_view.setFocus()
    before = pixels(nodule)

    nodule.setFocus(Qt.FocusReason.TabFocusReason)
    assert nodule.hasFocus()
    after = pixels(nodule)

    assert (before != after).any(axis=2).mean() > 0.1


def test_a_box_that_locks_while_it_has_the_focus_hands_the_keys_to_the_canvas(page):
    drag(page, (20, 20))  # a root dab
    root = box(page, "root")
    assert not root.isEnabled() and root.isChecked()  # the class has pixels: locked
    page.undo()
    assert root.isEnabled() and not root.isChecked()
    root.setFocus(Qt.FocusReason.TabFocusReason)

    page.redo()  # the pixels are back: the box locks under the focus

    assert not root.isEnabled() and root.isChecked()
    assert QApplication.focusWidget() is page.graphics_view


def test_the_class_keys_still_work_from_a_focused_box(page):
    nodule = box(page, "nodule")
    nodule.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(nodule, Qt.Key.Key_3)

    assert page.class_panel.active_key() == "root_tip" and not nodule.isChecked()


def test_the_boxes_follow_the_gear_after_the_classes_change(page, monkeypatch):
    bigger = (
        page.layers[CLASSES]
        .registry()
        .add(key="dead_root", name="Dead root", color="#7f5af0")
        .add(key="hypha", name="Hypha", color="#3fa7ff")
    )
    fake_class_editor(monkeypatch, bigger)
    page.edit_classes()
    QApplication.processEvents()
    panel = page.class_panel
    wanted = [panel._gear, *(box(page, c.key) for c in bigger.labelled())]

    chain = walk(page)

    start = chain.index(panel._gear)
    assert chain[start : start + len(wanted)] == wanted


def test_a_refused_tick_goes_back(make_editor, project_bad_registry):
    editor = make_editor(project_bad_registry.a, project_bad_registry.folder)
    editor.set_active_layer(CLASSES)

    box(editor, "nodule").setChecked(True)

    assert not box(editor, "nodule").isChecked() and ticked(editor) == frozenset()


# ============================================================================ Discard
def test_discard_restores_the_saved_labels_and_their_checks(page, prompts):
    paint(page, (10, 30), (70, 30))  # root
    assert page.save()
    saved = ids(page).copy()
    press(page, "2")
    paint(page, (40, 5), (40, 55))  # nodule
    page.class_panel.reviewed_changed.emit("root_tip", True)
    prompts.answer = DISCARD

    assert page.maybe_save_changes() is True

    assert prompts.calls == [("ask_unsaved", "class labels", "a.png")]
    assert not page.has_unsaved_changes() and np.array_equal(ids(page), saved)
    argb = pixmap_to_argb_array(page.layers[CLASSES].canvas.image)
    assert np.array_equal(argb, page.layers[CLASSES].document.lut()[saved])
    assert ticked(page) == {"root"} and not box(page, "root_tip").isChecked()
    assert not box(page, "nodule").isChecked()
    assert not page.layers[CLASSES].can_undo() and not page.undo_button.isEnabled()
    assert row_text(page, CLASSES) == "GT · 1/3 checked"


def test_an_edit_after_discard_never_saves_the_discarded_strokes(page, prompts, layout):
    paint(page, (10, 30), (70, 30))
    assert page.save()
    press(page, "2")
    paint(page, (40, 5), (40, 55))  # to be discarded
    prompts.answer = DISCARD
    assert page.maybe_save_changes() is True
    paint(page, (5, 50), (20, 50))  # a new nodule stroke, kept

    assert page.save()

    saved = read_tracing(layout.gt_path("a")).ids
    assert saved[50, 10] == 2 and saved[10, 40] == 0 and saved[30, 40] == 1


def test_discard_without_a_labels_file_empties_the_canvas(page, prompts, layout):
    paint(page, (10, 10), (40, 10))
    page.class_panel.reviewed_changed.emit("nodule", True)
    prompts.answer = DISCARD

    assert page.maybe_save_changes() is True

    assert not ids(page).any() and ticked(page) == frozenset()
    assert row_text(page, CLASSES) == "No class labels"
    assert not os.path.exists(layout.gt_path("a"))


def test_discard_of_the_class_checks_alone(page, prompts):
    """A tick is an edit of the labels too: Discard unticks it."""
    page.class_panel.reviewed_changed.emit("nodule", True)
    assert page.has_unsaved_changes() and ticked(page) == {"nodule"}
    prompts.answer = DISCARD

    assert page.maybe_save_changes() is True

    assert ticked(page) == frozenset() and not box(page, "nodule").isChecked()


# ============================================================================== opening
def test_reselecting_the_clean_open_image_keeps_labels_history_and_view(page, project, reads):
    paint(page, (10, 10), (40, 10))
    assert page.save()
    paint(page, (10, 40), (40, 40))
    page.undo()  # back at the saved labels; one undo and one redo left
    view = page.graphics_view
    view.set_zoom(4.0)
    view.centerOn(20, 20)
    zoom, center = view.zoom_factor, view.mapToScene(view.viewport().rect().center())
    reads.clear()

    page.load_image(project.a, project.folder)

    assert reads == []  # the labels file was not read again
    assert page.layers[CLASSES].can_undo() and page.layers[CLASSES].can_redo()
    assert view.zoom_factor == zoom
    assert view.mapToScene(view.viewport().rect().center()) == center
    assert not page.has_unsaved_changes()


def test_a_labels_file_changed_on_disk_reloads_in_the_same_view(page, project, layout, reads):
    from tests.correct_support import bump

    paint(page, (10, 10), (40, 10))
    assert page.save()
    view = page.graphics_view
    view.set_zoom(3.0)
    zoom = view.zoom_factor
    other = np.zeros((H, W), np.uint8)
    other[30:40, 30:40] = 2  # someone else's labels
    write_tracing(layout.gt_path("a"), Tracing(other, frozenset({"nodule"})), default_registry())
    bump(layout.gt_path("a"))
    reads.clear()

    page.load_image(project.a, project.folder)

    assert [norm(path) for path in reads] == [norm(layout.gt_path("a"))]
    assert np.array_equal(ids(page), other) and ticked(page) == {"nodule"}
    assert not page.layers[CLASSES].can_undo() and view.zoom_factor == zoom
    assert not page.has_unsaved_changes()
    os.remove(layout.gt_path("a"))  # gone: empty labels
    page.load_image(project.a, project.folder)
    assert not ids(page).any() and row_text(page, CLASSES) == "No class labels"


def test_an_invalid_registry_is_read_again_on_reselect(make_editor, project_bad_registry):
    folder, a = project_bad_registry.folder, project_bad_registry.a
    editor = make_editor(a, folder)
    assert editor.layers[CLASSES].is_read_only()

    save_registry(folder, default_registry())  # fixed
    editor.load_image(a, folder)

    assert not editor.layers[CLASSES].is_read_only()
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    assert editor.save()


def test_saving_does_not_count_as_a_change_on_disk(page, project, reads):
    paint(page, (10, 10), (40, 10))
    assert page.save()
    reads.clear()

    page.load_image(project.a, project.folder)

    assert reads == [] and page.layers[CLASSES].can_undo()


# ============================================================ undo, read-ahead, the box
def test_one_undo_timeline_runs_through_the_three_layers(make_editor, project, notices):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))  # the mask
    editor.set_active_layer(SKELETON)
    draw_line(editor)
    editor.set_active_layer(CLASSES)
    paint(editor, (20, 120), (80, 120))
    editor.set_active_layer(MASK)
    assert editor.session.journal.undo_entries == (MASK, SKELETON, CLASSES)
    notices.clear()

    editor.undo()  # the newest action: the class labels, without switching layers
    assert ("Undo · class labels", "info") in notices and editor.active_layer_id == MASK
    assert not editor.layers[CLASSES].is_dirty() and editor.layers[MASK].is_dirty()
    editor.undo()
    assert ("Undo · skeleton", "info") in notices and not editor.layers[SKELETON].is_dirty()
    editor.undo()
    assert not editor.has_unsaved_changes()
    editor.redo()
    assert editor.layers[MASK].is_dirty() and not editor.layers[CLASSES].is_dirty()


def test_the_class_labels_are_read_ahead_and_handed_back(make_editor, project, monkeypatch):
    """PgDn opens the neighbour's class labels from what the worker read; going back takes
    the image left as it is in memory (also after a class-label save): no read either way."""
    from app.gui.correct.classes_layer import ClassesLayer

    opened: list[bool] = []
    real_open = ClassesLayer.open

    def spy(layer, *args, ahead=None, **kwargs):
        opened.append(ahead is not None)
        return real_open(layer, *args, ahead=ahead, **kwargs)

    monkeypatch.setattr(ClassesLayer, "open", spy)
    editor = make_editor(project.a, project.folder)
    other = {norm(project.a): project.b, norm(project.b): project.a}
    editor.set_neighbour_provider(lambda path: [other[norm(path)]])
    editor.load_image(project.a, project.folder)  # read b ahead
    assert _spin_until(lambda: editor.paging.cached_photo(project.b) is not None)
    opened.clear()

    editor.load_image(project.b, project.folder)
    assert opened == [True]  # b from the worker's read
    editor.load_image(project.a, project.folder)
    assert opened == [True, True]  # a handed back

    editor.set_active_layer(CLASSES)
    paint(editor, (20, 100), (80, 110))
    assert editor.save()
    editor.load_image(project.b, project.folder)
    opened.clear()
    editor.load_image(project.a, project.folder)
    assert opened == [True] and ids(editor)[105, 50] == 1  # the saved labels, handed back


def test_the_remove_gt_box_defaults_to_cancel(monkeypatch):
    """Enter and Esc both keep the GT: Cancel is the default and the Esc button."""
    from app.gui.correct.actions import ask_remove_gt

    seen: list[tuple] = []

    def exec_(box):
        cancel = box.button(QMessageBox.StandardButton.Cancel)
        assert box.defaultButton() is cancel and box.escapeButton() is cancel
        seen.append((box.windowTitle(), box.text(), box.informativeText()))
        answer = seen_answers.pop(0)
        if answer is not None:
            next(b for b in box.buttons() if b.text() == answer).click()
        return 0

    seen_answers: list[str | None] = [None, "Cancel", "Remove GT"]
    monkeypatch.setattr(QMessageBox, "exec", exec_)

    assert ask_remove_gt(None, "a.png", "detail") is False  # closed: Esc
    assert ask_remove_gt(None, "a.png", "detail") is False  # Cancel
    assert ask_remove_gt(None, "a.png", "detail") is True
    assert seen[0] == ("Remove GT", "Remove the GT of a.png?", "detail")


# ===================================================================== prefs and size
def test_opacity_and_visibility_of_the_classes_layer_persist(app, tmp_path):
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    made = []
    try:
        first = CorrectEditor(settings=settings)
        made.append(first)
        first.show()
        first.layers[CLASSES].set_opacity(0.3)
        first.set_layer_visible(CLASSES, False)
        first.hide()  # the page stores its layers' prefs when it is hidden
        assert float(settings.value("correct/classes/opacity")) == pytest.approx(0.3)

        second = CorrectEditor(settings=settings)
        made.append(second)

        assert second.layers[CLASSES].opacity() == pytest.approx(0.3)
        assert not second.layers[CLASSES].is_visible()
        assert not second.layer_panel.rows[CLASSES].eye_button.isChecked()
    finally:
        delete_widgets(made, prepare=lambda e: e.layers[SKELETON].cancel_scheduled_render())


def test_the_page_stays_within_440_lines():
    lines = (ROOT / "app" / "gui" / "correct" / "editor.py").read_text(encoding="utf-8")
    assert len(lines.splitlines()) <= 440


# ===================================================================== the class panel
def _long_named_registry(n=12):
    reg = default_registry()
    for i in range(n - len(reg.labelled())):
        reg = reg.add(
            key=f"extra_{i}", name=f"Extra class number {i}", color=f"#{0x40A0C0 + 7 * i:06x}"
        )
    return reg


@pytest.mark.parametrize("n", [4, 12, 25])
def test_long_class_names_elide_and_every_box_shows(made, n):
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    reg = _long_named_registry(n)
    panel.set_registry(reg)
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()

    scroll = panel._scroll
    assert not scroll.horizontalScrollBar().isVisible()
    viewport = scroll.viewport()
    for key, class_row in panel._rows.items():
        assert class_row._name_label.text() == reg.by_key(key).name
        if class_row.checkbox is None:
            continue
        right = class_row.mapTo(viewport, class_row.checkbox.geometry().topRight()).x()
        assert right < viewport.width(), f"{key}: box at x={right}, viewport {viewport.width()}"
    assert panel.width() <= 280
    for class_row in panel._rows.values():
        label = class_row._name_label
        assert label.toolTip() == (label.text() if label.is_elided() else "")


def test_a_forty_character_name_elides_with_the_full_name_as_tooltip(made):
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    name = "A very long class name that goes on and"  # 40 characters, the most allowed
    panel.set_registry(default_registry().add(key="long_one", name=name, color="#123456"))
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()
    label = panel._rows["long_one"]._name_label
    assert label.is_elided() and label.toolTip() == name and label.text() == name
    checkbox = panel._rows["long_one"].checkbox
    viewport = panel._scroll.viewport()
    right = panel._rows["long_one"].mapTo(viewport, checkbox.geometry().topRight()).x()
    assert right < viewport.width()


def test_the_panel_widens_for_longer_names_after_a_registry_change(made):
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    panel.set_registry(default_registry())
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()
    narrow = panel.width()

    def swap(registry):
        panel.set_registry(registry)
        panel.reposition()
        QApplication.processEvents()
        panel.reposition()
        QApplication.processEvents()

    swap(_long_named_registry(12))
    assert panel.width() > narrow
    label = panel._rows["extra_0"]._name_label
    assert label.width() == label.sizeHint().width()

    swap(default_registry())
    assert panel.width() == narrow


def test_a_locked_box_says_why_it_is_locked(made):
    host = QWidget()
    made.append(host)
    panel = ClassPanel(host)
    panel.set_registry(default_registry())
    panel.set_reviewed({"nodule"}, {"root"})
    root, nodule = panel._rows["root"].checkbox, panel._rows["nodule"].checkbox
    assert not root.isEnabled() and root.isChecked()
    assert nodule.isEnabled() and nodule.isChecked()
    assert "pixels" in root.toolTip() and root.toolTip() != nodule.toolTip()
    panel.set_reviewed(set(), set())
    assert root.isEnabled() and root.toolTip() == nodule.toolTip()


# ===================================================================== the class editor
def test_enter_in_the_add_form_adds_the_class_and_keeps_the_dialog_open(made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    dlg._add_name_edit.setText("Dead root")
    dlg._add_name_edit.setFocus()
    QTest.keyClick(dlg._add_name_edit, Qt.Key.Key_Return)
    assert dlg.isVisible() and dlg.result() == 0  # not accepted
    assert "dead_root" in dlg._rows and dlg._add_name_edit.text() == ""

    dlg._add_name_edit.setText("Hypha")
    dlg._add_key_edit.setFocus()
    QTest.keyClick(dlg._add_key_edit, Qt.Key.Key_Enter)
    assert "hypha" in dlg._rows and dlg.isVisible()

    dlg._button_box.button(dlg._button_box.StandardButton.Ok).click()
    assert not dlg.isVisible()
    assert {"dead_root", "hypha"} <= {c.key for c in dlg.result_registry().classes}


def test_class_editor_keyboard_flow(made):
    """It opens in the new-class field; Tab cycles through the rows, the form, OK and Cancel
    in drawing order; Enter with nothing typed is OK."""
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    dlg.activateWindow()
    QTest.qWaitForWindowActive(dlg, 2000)
    assert dlg.focusWidget() is dlg._add_name_edit

    expected = []
    for class_row in (dlg._rows[k] for k in ("root", "nodule", "root_tip", "ignore")):
        expected += [class_row.swatch_button, class_row.name_edit]
        if class_row.retire_button is not None:
            expected.append(class_row.retire_button)
    ok = dlg._button_box.button(dlg._button_box.StandardButton.Ok)
    cancel = dlg._button_box.button(dlg._button_box.StandardButton.Cancel)
    expected += [
        dlg._add_name_edit,
        dlg._add_key_edit,
        dlg._add_swatch,
        dlg._add_button,
        ok,
        cancel,
    ]
    chain, widget = [], expected[0]
    while True:
        if widget.focusPolicy() & Focus.TabFocus and widget.isVisible() and widget.isEnabled():
            chain.append(widget)
        widget = widget.nextInFocusChain()
        if widget is expected[0] or len(chain) > 40:
            break
    assert chain == expected

    QTest.keyClick(dlg._add_name_edit, Qt.Key.Key_Return)  # nothing typed: Enter = OK
    assert not dlg.isVisible() and dlg.result() == 1


def test_ok_adds_a_typed_class_that_was_not_added_yet(made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg._add_name_edit.setText("Dead root")
    dlg._on_accept()
    assert dlg.result_registry() is not None
    assert "dead_root" in {c.key for c in dlg.result_registry().classes}


def test_ok_with_an_invalid_typed_class_stays_open_with_its_error(made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    dlg._add_name_edit.setText("Second root")
    dlg._add_key_edit.setText("root")  # key taken
    dlg._on_accept()
    assert dlg.isVisible() and dlg.result_registry() is None
    assert dlg._error_label.isVisible() and "Duplicate" in dlg._error_label.text()


def test_a_blank_name_says_a_class_needs_a_name(made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg._rows["root"].name_edit.setText("   ")
    dlg._on_name_committed("root")
    assert dlg._error_label.text() == "A class needs a name."
    dlg._rows["root"].name_edit.setText("Root")
    dlg._on_name_committed("root")
    dlg._add_name_edit.setText("  ")
    dlg._on_add_clicked()
    assert dlg._error_label.text() == "A class needs a name."


# =================================================================== the export dialog
def _photo(folder: Path, name: str) -> str:
    path = str(folder / name)
    cv2.imwrite(path, np.full((60, 80, 3), 90, np.uint8))
    return path


def _traced_project(folder: Path, n_images=3, n_traced=2) -> dict[str, str]:
    images = {}
    for i in range(n_images):
        path = _photo(folder, f"plot_{i:03d}.png")
        images[f"plot_{i:03d}"] = path
        if i < n_traced:
            plane = np.zeros((60, 80), np.uint8)
            plane[10:20, 10:20] = 1
            checked = frozenset({"root", "nodule", "root_tip"})  # fully checked
            write_tracing(
                ProjectLayout.for_image(path).gt_path(f"plot_{i:03d}"),
                Tracing(plane, checked),
                default_registry(),
            )
    return images


def _run_export(dlg):
    dlg.start_export()
    assert dlg.export_thread is not None, dlg.status_label.text()
    dlg.export_thread.wait(20000)
    QApplication.processEvents()
    return dlg.result_summary


def test_enter_in_the_folder_field_exports_instead_of_browsing(made, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    images = _traced_project(tmp_path)
    browsed = []
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: browsed.append(1) or ""
    )
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    defaults = [b.text() for b in dlg.findChildren(QPushButton) if b.isDefault()]
    assert defaults == ["Export"]
    assert not dlg.browse_button.autoDefault() and not dlg.cancel_button.autoDefault()

    dlg.output_edit.setText(str(tmp_path / "out"))
    dlg.output_edit.setFocus()
    QTest.keyClick(dlg.output_edit, Qt.Key.Key_Return)
    assert browsed == [] and dlg.export_thread is not None
    dlg.export_thread.wait(20000)
    QApplication.processEvents()
    assert dlg.result_summary is not None and dlg.result_summary.exported == 2


def test_export_counts_the_traced_images(made, tmp_path):
    images = _traced_project(tmp_path, n_images=5, n_traced=2)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    assert "2 of 5 images" in dlg.traced_label.text()
    dlg.output_edit.setText(str(tmp_path / "out"))
    assert dlg.export_button.isEnabled()


def test_export_with_no_traced_image_explains_and_stays_off(made, tmp_path):
    images = _traced_project(tmp_path, n_images=3, n_traced=0)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    assert dlg.traced_label.text() == (
        "No image in this folder has class labels yet. Paint some on the Correct page (G) first."
    )  # the Trace GT stage is gone (WG-T11): the label names where to paint them
    assert "Trace GT" not in dlg.only_complete_check.toolTip()
    dlg.output_edit.setText(str(tmp_path / "out"))
    assert not dlg.export_button.isEnabled()
    dlg.start_export()
    assert dlg.export_thread is None


def test_a_relative_output_folder_goes_next_to_the_images(made, tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    images = _traced_project(project_dir)
    dlg = ExportDatasetDialog(str(project_dir), images, default_registry())
    made.append(dlg)
    dlg.output_edit.setText("out")
    result = _run_export(dlg)
    assert result is not None and result.exported == 2
    assert os.path.isdir(project_dir / "out" / "images")
    assert not os.path.exists(elsewhere / "out")
    assert dlg.output_edit.text() == str(project_dir / "out")


def test_a_pasted_quoted_path_is_unquoted(made, tmp_path):
    """Explorer's "Copy as path" puts the path in double quotes."""
    images = _traced_project(tmp_path)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    dlg.output_edit.setText(f'  "{tmp_path / "quoted_out"}"  ')
    result = _run_export(dlg)
    assert result is not None and result.exported == 2
    assert os.path.isdir(tmp_path / "quoted_out" / "masks")

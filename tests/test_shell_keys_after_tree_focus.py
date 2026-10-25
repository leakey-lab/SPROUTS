"""Editor and canvas keys work while the library tree has the keyboard (W1, W7).

The library tree keeps the keyboard while the arrow keys browse (an editor takes it only
when a click opens an image), so page-scoped shortcuts (``WidgetWithChildrenShortcut`` on
the Correct / Trace GT page) were dead there: plain keys went to the tree's type-ahead
("2" jumped the selection to a date row) and Ctrl+S / Ctrl+Z / the zoom keys did nothing.
The pages' shortcuts are now window-wide while their page is on screen (Qt ignores the
shortcuts of a hidden widget), the Library's zoom keys are shortcuts of its display
canvas, and the keys a canvas handles itself (hold B, hold Space, Correct's S / Enter /
Esc / Delete) are handed from the tree to the editor canvas on screen. A text field keeps
its typing (it takes the keys first through ShortcutOverride), PgDn / PgUp stay
unambiguous, and a hidden page never reacts.

Offscreen; every project is a tmp_path folder of four 200x150 photos, every window has its
own INI settings.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import canvas_gl, shortcuts  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import paint, plane, qapp  # noqa: E402
from tests.test_correct_main_window import (  # noqa: E402
    STEMS,
    activate,
    enter,
    leaf,
    new_project,
    open_project,
    same_path,
    settle,
    wait,
)

CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    """No prompt or file dialog, no GL, the user's config untouched, no delayed sweeps."""
    from app.gui import image_manager

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: pytest.fail("dialog"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def window(app, tmp_path):
    """A shown 1280x800 window with the project open and image 0 shown in the Library."""
    from app.gui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    made = MainWindow(settings=settings)
    made.resize(1280, 800)
    made.show()
    settle()
    project = new_project(tmp_path)
    open_project(made, project)
    made.project = project
    click_row(made, 0)
    yield made
    for editor in made._editors().values():
        editor.maybe_save_changes = lambda: True
    made.correct_editor.layers[SKELETON].cancel_scheduled_render()
    made.close()
    made.deleteLater()
    del made, editor
    flush_deleted()
    QApplication.processEvents()


def click_row(window, i: int) -> None:
    """A mouse click on the library row of image ``i`` (the tree takes the keyboard)."""
    tree = window.file_list
    item = leaf(window, i)
    tree.scrollToItem(item)
    settle()
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(item).center()
    )
    settle()


def key(widget, k, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, k, modifiers)
    settle()


def in_stage(window, stage: str, image: int = 1):
    """Enter ``stage``, click the image before ``image`` in the library, then step to
    ``image`` with the Down arrow: it opens, and the tree keeps the keyboard (by design,
    so the arrows keep browsing)."""
    enter(window, stage)
    activate(window)
    click_row(window, image - 1)
    window.file_list.setFocus()
    settle()
    QTest.keyClick(window.file_list, Qt.Key.Key_Down)
    editor = window._editors().get(window.right_panel.currentIndex())
    if editor is not None:
        path = window.project.image(image)
        assert wait(lambda: same_path(editor.current_image_path, path), 2.0)
    else:
        assert wait(lambda: window.display_controller.current_name == STEMS[image], 2.0)
    settle()
    assert window.file_list.hasFocus()
    assert window.file_list.currentItem() is leaf(window, image)
    return editor


def ambiguous_log(window) -> list[str]:
    """Every ambiguous activation of any shortcut of ``window`` from now on."""
    seen: list[str] = []
    for shortcut in window.findChildren(QShortcut):
        shortcut.activatedAmbiguously.connect(
            lambda s=shortcut: seen.append(
                s.key().toString(QKeySequence.SequenceFormat.PortableText)
            )
        )
    return seen


# ================================================================================ Correct
def test_correct_layer_keys_work_after_a_tree_click(window):
    editor = in_stage(window, "Correct")
    tree = window.file_list
    assert same_path(editor.current_image_path, window.project.image(1))
    assert editor.active_layer_id == MASK

    key(tree, Qt.Key.Key_2)
    assert editor.active_layer_id == SKELETON
    assert tree.currentItem() is leaf(window, 1)  # no type-ahead jump to a date row
    key(tree, Qt.Key.Key_1)
    assert editor.active_layer_id == MASK
    key(tree, Qt.Key.Key_X)
    assert editor.active_layer_id == SKELETON
    key(tree, Qt.Key.Key_E)
    assert editor.layers[SKELETON].current_tool == "eraser"
    key(tree, Qt.Key.Key_P)
    assert editor.layers[SKELETON].current_tool == "polyline"
    key(tree, Qt.Key.Key_F)
    assert editor.active_layer_id == MASK and editor.layers[MASK].current_tool == "fill"
    assert tree.hasFocus()  # the keyboard stays with the tree (arrows keep browsing)


def test_correct_undo_redo_save_and_zoom_keys_work_after_a_tree_click(window):
    editor = in_stage(window, "Correct")
    tree = window.file_list
    before = plane(editor).copy()
    paint(editor, (20, 100), (60, 100))
    painted = plane(editor).copy()
    assert (painted != before).any()
    tree.setFocus()

    key(tree, Qt.Key.Key_Z, CTRL)
    assert (plane(editor) == before).all()
    key(tree, Qt.Key.Key_Y, CTRL)
    assert (plane(editor) == painted).all()

    view = editor.graphics_view
    key(tree, Qt.Key.Key_1, CTRL)
    assert view.target_zoom == pytest.approx(1.0)
    key(tree, Qt.Key.Key_0, CTRL)
    assert view.target_zoom != pytest.approx(1.0)  # fitted (a 200x150 photo fits larger)

    saved = []
    window.correct_editor.mask_saved.connect(saved.append)
    key(tree, Qt.Key.Key_S, CTRL)
    assert saved and same_path(saved[0], window.project.image(1))
    assert os.path.isfile(window.project.gt_mask(1)) and not editor.has_unsaved_changes()


def test_the_search_field_keeps_its_typing_in_correct(window):
    editor = in_stage(window, "Correct")
    search = window.library_search
    search.setFocus()
    settle()

    for k in (Qt.Key.Key_2, Qt.Key.Key_X, Qt.Key.Key_E):
        key(search, k)
    assert search.text() == "2xe"
    assert editor.active_layer_id == MASK and editor.layers[MASK].current_tool != "eraser"
    search.clear()


def test_pgdn_after_a_tree_click_in_correct_opens_the_next_image_once(window):
    editor = in_stage(window, "Correct")
    ambiguous = ambiguous_log(window)

    key(window.file_list, Qt.Key.Key_PageDown)

    assert same_path(editor.current_image_path, window.project.image(2))
    assert ambiguous == []


# ================================================================================ Trace GT
def test_trace_gt_tool_and_class_keys_work_after_a_tree_click(window):
    gt = in_stage(window, "Trace GT")
    tree = window.file_list
    assert gt is window.gt_tracing_interface
    assert same_path(gt.current_image_path, window.project.image(1))

    key(tree, Qt.Key.Key_E)
    assert gt.eraser_button.isChecked()
    key(tree, Qt.Key.Key_F)
    assert gt.fill_button.isChecked()
    first_key = gt.class_panel.key_for_shortcut(1)
    second_key = gt.class_panel.key_for_shortcut(2)
    if second_key is not None:
        key(tree, Qt.Key.Key_2)
        assert gt._active_key == second_key
    key(tree, Qt.Key.Key_1)
    assert gt._active_key == first_key
    assert tree.currentItem() is leaf(window, 1)  # no type-ahead jump

    view = gt.graphics_view
    key(tree, Qt.Key.Key_1, CTRL)
    assert view.target_zoom == pytest.approx(1.0)


# ================================================================================ Library
def test_library_zoom_keys_work_after_a_tree_click(window):
    in_stage(window, "Library", image=2)
    tree = window.file_list
    view = window.display_controller.magnifying_view
    fitted = view.target_zoom
    assert fitted != pytest.approx(1.0)

    key(tree, Qt.Key.Key_1, CTRL)
    assert view.target_zoom == pytest.approx(1.0)
    key(tree, Qt.Key.Key_Equal, CTRL)
    zoomed_in = view.target_zoom
    assert zoomed_in > 1.0
    key(tree, Qt.Key.Key_Minus, CTRL)
    assert view.target_zoom < zoomed_in
    key(tree, Qt.Key.Key_0, CTRL)
    assert view.target_zoom == pytest.approx(fitted)
    assert tree.hasFocus()


def test_library_zoom_keys_are_on_the_f1_sheet():
    keys = {k for spec in shortcuts.specs_for(shortcuts.LIBRARY) for k in spec.keys}
    assert {"Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl+-"} <= keys
    assert shortcuts.find_conflicts() == []


def test_the_library_keeps_tree_type_ahead_and_a_hidden_editor_never_reacts(window):
    in_stage(window, "Library", image=1)
    editor = window.correct_editor
    layer, tool = editor.active_layer_id, editor.layers[MASK].current_tool

    for k in (Qt.Key.Key_2, Qt.Key.Key_X, Qt.Key.Key_E, Qt.Key.Key_F):
        key(window.file_list, k)

    assert editor.active_layer_id == layer and editor.layers[MASK].current_tool == tool
    assert window.gt_tracing_interface.eraser_button.isChecked() is False


def test_a_modal_dialog_keeps_the_page_keys_to_itself(window):
    """A dialog over the window (the class editor, a prompt) blocks the page's shortcuts:
    keys typed there never edit the image behind it."""
    from PyQt6.QtWidgets import QDialog, QLineEdit, QVBoxLayout

    editor = in_stage(window, "Correct")
    dialog = QDialog(window)
    field = QLineEdit(dialog)
    QVBoxLayout(dialog).addWidget(field)
    dialog.setModal(True)
    dialog.show()
    dialog.activateWindow()
    QTest.qWaitForWindowActive(dialog, 3000)
    field.setFocus()
    settle()
    try:
        for k in (Qt.Key.Key_2, Qt.Key.Key_X):
            QTest.keyClick(field, k)
        QTest.keyClick(field, Qt.Key.Key_1, CTRL)
        settle()
        assert editor.active_layer_id == MASK
        assert field.text() == "2x"
    finally:
        dialog.close()
        dialog.deleteLater()
        settle()


# ============================================================ canvas keys from the tree
def test_b_tap_and_space_hold_reach_the_correct_canvas_from_the_tree(window):
    editor = in_stage(window, "Correct")
    tree, view = window.file_list, editor.graphics_view
    key(tree, Qt.Key.Key_E)
    assert editor.layers[MASK].current_tool == "eraser"

    key(tree, Qt.Key.Key_B)  # a tap: the mask brush
    assert editor.layers[MASK].current_tool == "brush"

    QTest.keyPress(tree, Qt.Key.Key_Space)
    assert view.space_pan  # a left drag on the canvas now pans instead of painting
    QTest.keyRelease(tree, Qt.Key.Key_Space)
    assert not view.space_pan
    assert tree.hasFocus() and tree.currentItem() is leaf(window, 1)


def test_a_space_pan_held_from_the_tree_survives_the_click_on_the_canvas(window):
    editor = in_stage(window, "Correct")
    tree, view = window.file_list, editor.graphics_view

    QTest.keyPress(tree, Qt.Key.Key_Space)
    view.setFocus()  # the press on the canvas takes the keyboard mid-pan
    settle()
    assert view.space_pan
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert not view.space_pan


def test_a_hold_ends_when_the_tree_loses_the_keyboard_elsewhere(window):
    editor = in_stage(window, "Correct")
    tree, view = window.file_list, editor.graphics_view
    key(tree, Qt.Key.Key_E)
    holds = []
    editor.b_key_status_changed.connect(holds.append)

    QTest.keyPress(tree, Qt.Key.Key_Space)
    QTest.keyPress(tree, Qt.Key.Key_B)
    assert view.space_pan and holds == [True]
    window.library_search.setFocus()  # the releases will never reach the canvas
    settle()

    assert not view.space_pan
    assert holds == [True, False]
    assert editor.layers[MASK].current_tool == "eraser"  # a lost hold is not a B tap


def test_trace_gt_b_tap_reaches_its_canvas_from_the_tree(window):
    gt = in_stage(window, "Trace GT")
    tree = window.file_list
    key(tree, Qt.Key.Key_E)
    assert gt.eraser_button.isChecked()

    key(tree, Qt.Key.Key_B)

    assert gt.brush_button.isChecked()
    assert tree.hasFocus()


def test_the_library_tree_keeps_space_and_letters_in_the_library(window):
    in_stage(window, "Library")
    view = window.display_controller.magnifying_view
    QTest.keyPress(window.file_list, Qt.Key.Key_Space)
    assert not view.space_pan  # nothing is relayed outside the editors
    QTest.keyRelease(window.file_list, Qt.Key.Key_Space)


# ======================================================================= no ambiguous keys
@pytest.mark.parametrize("stage", ["Library", "Correct", "Trace GT", "Measure"])
def test_no_shortcut_key_is_ambiguous_in_any_stage_or_focus(window, stage):
    """Every registry shortcut key, pressed with the keyboard in the tree, on the canvas and
    in the search field, never fires ``activatedAmbiguously`` (an ambiguous key does
    nothing)."""
    editor = in_stage(window, stage)
    canvas = editor.graphics_view if editor is not None else window.display_area
    ambiguous = ambiguous_log(window)
    scopes = {shortcuts.GLOBAL, shortcuts.scope_for_stage(stage)}
    if editor is None:  # the display page (Library, Generate, Measure) has the Library keys
        scopes.add(shortcuts.LIBRARY)
    presses = {
        (spec.scope, k)
        for spec in shortcuts.REGISTRY
        if spec.kind == shortcuts.SHORTCUT
        and spec.action not in ("help", "open_folder", "go_to_stage", "save")
        and spec.scope in scopes
        for k in spec.keys
    }
    assert presses
    for focus in (window.file_list, canvas, window.library_search):
        for _scope, k in sorted(presses):
            focus.setFocus()
            settle()
            combo = QKeySequence(k)[0]
            target = QApplication.focusWidget() or focus
            QTest.keyClick(target, combo.key(), combo.keyboardModifiers())
            settle()
            if window.right_panel.currentIndex() != (
                Panel.DISPLAY if editor is None else window.right_panel.indexOf(editor)
            ):
                enter(window, stage)  # a stage key left the page: come back
        window.library_search.clear()
    assert ambiguous == []

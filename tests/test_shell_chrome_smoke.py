"""Smoke + wiring assertions for the PR4 SPROUTS guided shell.

Runs under an offscreen QApplication. Covers the Welcome screen, the loading
overlay, the shell chrome, and (T4.15) the MainWindow connect-map contract.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# QtWebEngine (imported transitively by the visualization modules that
# MainWindow pulls in) requires either an early QtWebEngineWidgets import or
# AA_ShareOpenGLContexts set before the QApplication exists. Do that here so the
# MainWindow contract test (T4.15) can construct the window offscreen.
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.gui.panels import Panel  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def test_welcome_widget_imports(app):
    from app.gui.welcome_screen import WelcomeWidget

    calls = []
    opened = []
    w = WelcomeWidget(
        on_get_started=lambda: calls.append(1),
        on_open_recent=lambda p: opened.append(p),
    )
    assert w is not None
    # The open-recent forwarder routes to the supplied callback.
    w._handle_open_recent("C:/some/dir")
    assert opened == ["C:/some/dir"]


def test_welcome_get_started_callback(app):
    from app.gui.welcome_screen import WelcomeWidget

    calls = []
    w = WelcomeWidget(on_get_started=lambda: calls.append(1))
    # Find the primary Browse button and click it.
    browse = w.findChild(QPushButton, "primaryButton")
    assert browse is not None
    browse.click()
    assert calls == [1]


def test_filter_file_list(app):
    """FIX5: case-insensitive contains filter, empty query shows all; parents of
    matching leaves stay visible."""
    from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

    from app.gui.ui_panels import filter_file_list

    class _MW:
        pass

    mw = _MW()
    tree = QTreeWidget()
    parent = QTreeWidgetItem(["FieldA"])
    leaf1 = QTreeWidgetItem(["root_001.png"])
    leaf2 = QTreeWidgetItem(["other_002.png"])
    parent.addChild(leaf1)
    parent.addChild(leaf2)
    tree.addTopLevelItem(parent)
    mw.file_list = tree

    filter_file_list(mw, "ROOT")
    assert not leaf1.isHidden()
    assert leaf2.isHidden()
    assert not parent.isHidden()  # kept for the matching descendant

    filter_file_list(mw, "")
    assert not leaf1.isHidden()
    assert not leaf2.isHidden()
    assert not parent.isHidden()


def test_loading_overlay_imports(app):
    from app.gui.loading_overlay import LoadingOverlay

    ov = LoadingOverlay()
    ov.set_filename("foo.png")
    ov.set_count(3, 10)
    ov.set_progress(50)
    ov.start()
    ov.hide()


# --------------------------------------------------------------------------- #
#  T4.15 — MainWindow connect-map contract
# --------------------------------------------------------------------------- #
_CONNECT_MAP_ATTRS = [
    "load_images_button",
    "generate_mask_button",
    "generate_button",
    "calculate_length_button",
    "calculate_area_button",
    "visualize_root_length_button",
    "visualize_root_area_button",
    "view_mode_combo",
    "expand_all_button",
    "collapse_all_button",
    "file_list",
]


def test_main_window_contract(app):
    from app.gui.main_window import MainWindow

    if not hasattr(MainWindow, "_pr4_shell_ready"):
        pytest.skip("shell (app_stack) not wired yet — enabled at T4.5/T4.15")

    mw = MainWindow()
    try:
        for attr in _CONNECT_MAP_ATTRS:
            assert hasattr(mw, attr), f"missing {attr}"
        assert mw.view_mode_combo.count() == 3
        assert mw.right_panel.count() == len(Panel)  # display, Correct, viz, Trace GT
        # Six-stage pipeline, in order: Trace GT (hand-traced ground truth)
        # right after Library; mask+skeleton generation is one stage, and so is
        # their correction (one canvas, two layers).
        assert list(mw.ribbon_buttons.keys()) == [
            "Library",
            "Trace GT",
            "Generate",
            "Correct",
            "Measure",
            "Visualize",
        ]
        assert mw.right_panel.widget(Panel.CORRECT) is mw.correct_editor
        assert mw.right_panel.widget(Panel.GT_TRACING) is mw.gt_tracing_interface
        assert mw.gt_classes_button.text() == "Classes…"
        assert mw.gt_export_button.text() == "Export dataset…"
        assert mw.gt_remove_button.text() == "Remove GT…"
        # WG-T5: the GT actions are on the Correct page (Trace GT's page is empty).
        correct_page = mw.action_bar_stack.widget(mw._action_bar_pages["Correct"])
        for button in (mw.gt_classes_button, mw.gt_export_button, mw.gt_remove_button):
            assert button.parentWidget() is correct_page
        assert mw.app_stack.count() >= 2
        # FIX2: the dialog-free loader entry point exists for recent rows.
        assert callable(getattr(mw, "open_path", None))
    finally:
        mw.close()
        mw.deleteLater()
        flush_deleted()


# --------------------------------------------------------------------------- #
#  Trace GT stage wiring
# --------------------------------------------------------------------------- #
@pytest.fixture
def main_window(app, monkeypatch):
    from app.gui.main_window import MainWindow

    mw = MainWindow()
    # No editor prompt may block a headless run.
    monkeypatch.setattr(mw.correct_editor, "maybe_save_changes", lambda: None)
    yield mw
    mw.close()
    mw.deleteLater()
    del mw
    flush_deleted()


def test_lifecycle_hooks_call_gt_prompt(main_window, monkeypatch):
    mw = main_window
    calls = []
    # maybe_save_changes returns True when it is safe to move on.
    monkeypatch.setattr(
        mw.gt_tracing_interface, "maybe_save_changes", lambda: calls.append(1) or True
    )

    mw.open_path("")  # no-op: no folder
    assert calls == []

    mw.toggle_gt_tracing()
    assert mw.right_panel.currentIndex() == Panel.GT_TRACING
    mw.switch_right_panel("display")  # leaving the stage prompts
    assert calls == [1]
    assert mw.right_panel.currentIndex() == Panel.DISPLAY

    mw.close()  # closing the window prompts
    assert calls == [1, 1]


def test_open_path_resets_gt_editor(main_window, monkeypatch, tmp_path):
    mw = main_window
    resets = []
    monkeypatch.setattr(mw.gt_tracing_interface, "reset", lambda: resets.append(1))
    monkeypatch.setattr(mw.image_manager, "load_images", lambda _d: None)
    mw.open_path(str(tmp_path))
    assert resets == [1]


def test_trace_gt_stage_button_enters_editor(main_window):
    mw = main_window
    mw.ribbon_buttons["Trace GT"].click()
    assert mw.right_panel.currentWidget() is mw.gt_tracing_interface
    # Clicking the active stage again must not toggle it off.
    mw.ribbon_buttons["Trace GT"].click()
    assert mw.right_panel.currentIndex() == Panel.GT_TRACING
    mw.ribbon_buttons["Correct"].click()
    assert mw.right_panel.currentIndex() == Panel.CORRECT
    mw.ribbon_buttons["Trace GT"].click()
    assert mw.right_panel.currentIndex() == Panel.GT_TRACING


def test_navigate_image_moves_tree_selection(main_window, monkeypatch):
    from PyQt6.QtWidgets import QTreeWidgetItem

    mw = main_window
    mw.file_list.clear()
    folder = QTreeWidgetItem(["Field"])
    leaves = {}
    for name in ("a", "b", "c"):
        leaf = QTreeWidgetItem([f"{name}.png"])
        leaf.setData(0, Qt.ItemDataRole.UserRole, name)
        folder.addChild(leaf)
        leaves[name] = leaf
    mw.file_list.addTopLevelItem(folder)
    leaves["c"].setHidden(True)  # filtered out of the library
    mw.image_manager.images = {n: f"/project/{n}.png" for n in leaves}

    loaded = []
    monkeypatch.setattr(mw.gt_tracing_interface, "load_image", loaded.append)
    mw.switch_right_panel("gt_tracing")
    mw.file_list.setCurrentItem(leaves["a"])

    mw.navigate_image(1)
    assert mw.file_list.currentItem() is leaves["b"]
    assert loaded == ["/project/b.png"]
    mw.navigate_image(1)  # "c" is hidden: stay on the last visible image
    assert mw.file_list.currentItem() is leaves["b"]
    assert loaded == ["/project/b.png"]
    mw.navigate_image(-1)
    assert mw.file_list.currentItem() is leaves["a"]
    assert loaded == ["/project/b.png", "/project/a.png"]
    mw.navigate_image(-1)  # already first
    assert mw.file_list.currentItem() is leaves["a"]

    # PgDn/PgUp in the editor arrive as navigate_requested(+-1).
    mw.gt_tracing_interface.navigate_requested.emit(1)
    assert mw.file_list.currentItem() is leaves["b"]


def test_export_gt_dataset_opens_dialog(main_window, monkeypatch, tmp_path):
    import sys
    import types

    mw = main_window
    toasts = []
    monkeypatch.setattr(mw, "notify", lambda msg, kind="success", **_k: toasts.append(kind))

    opened = []

    class _FakeDialog:
        def __init__(self, project_dir, images, registry, parent=None):
            opened.append((project_dir, images, registry, parent))

        def exec(self):
            return 0

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

    fake = types.ModuleType("app.gui.export_dataset_dialog")
    fake.ExportDatasetDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.export_dataset_dialog", fake)

    mw.image_manager.original_folder = None
    mw.export_gt_dataset()
    assert opened == [] and toasts == ["warn"]

    mw.image_manager.original_folder = str(tmp_path)
    mw.image_manager.images = {"a": str(tmp_path / "a.png")}
    mw.export_gt_dataset()
    assert len(opened) == 1
    project_dir, images, registry, parent = opened[0]
    assert project_dir == str(tmp_path)
    assert images == {"a": str(tmp_path / "a.png")}
    assert images is not mw.image_manager.images  # a copy
    assert registry == mw.gt_tracing_interface.registry
    assert parent is mw


def test_export_uses_the_folders_registry(main_window, monkeypatch, tmp_path):
    """Export must use the folder's gt/classes.json even if Trace GT never opened
    an image there (the editor's registry is still the default then)."""
    import sys
    import types

    from app.gt import default_registry, save_registry

    mw = main_window
    toasts = []
    monkeypatch.setattr(mw, "notify", lambda msg, kind="success", **_k: toasts.append(kind))
    opened = []

    class _FakeDialog:
        def __init__(self, project_dir, images, registry, parent=None):
            opened.append(registry)

        def exec(self):
            return 0

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

    fake = types.ModuleType("app.gui.export_dataset_dialog")
    fake.ExportDatasetDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.export_dataset_dialog", fake)

    custom = default_registry().add(key="dead_root", name="Dead root", color="#7f5af0")
    save_registry(str(tmp_path), custom)
    mw.image_manager.original_folder = str(tmp_path)
    mw.image_manager.images = {}
    mw.export_gt_dataset()
    assert opened == [custom]

    # An invalid registry blocks the export instead of silently using defaults.
    (tmp_path / "gt" / "classes.json").write_text("{", encoding="utf-8")
    mw.export_gt_dataset()
    assert opened == [custom] and toasts == ["danger"]


# --------------------------------------------------------------------------- #
#  Unsaved GT the user keeps (a failed save) blocks every way out
# --------------------------------------------------------------------------- #
def _gt_tree(mw, tmp_path, names=("a", "b")):
    """Real images ``<name>.png`` in ``tmp_path`` plus a library tree of them."""
    import cv2
    import numpy as np
    from PyQt6.QtWidgets import QTreeWidgetItem

    paths, leaves = {}, {}
    mw.file_list.clear()
    folder = QTreeWidgetItem(["Field"])
    for name in names:
        path = tmp_path / f"{name}.png"
        cv2.imwrite(str(path), np.full((40, 50, 3), 90, np.uint8))
        paths[name] = str(path)
        leaf = QTreeWidgetItem([f"{name}.png"])
        leaf.setData(0, Qt.ItemDataRole.UserRole, name)
        folder.addChild(leaf)
        leaves[name] = leaf
    mw.file_list.addTopLevelItem(folder)
    mw.image_manager.images = dict(paths)
    mw.image_manager.original_folder = str(tmp_path)
    return paths, leaves


def _open_dirty_gt_with_failing_save(mw, monkeypatch, tmp_path):
    """Trace GT open on ``a`` with a stroke; every GT write fails; prompts answer
    Save, then "Keep editing" (Cancel) on the "GT not saved" follow-up."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QMessageBox

    import app.gui.gt_tracing_interface as gti

    paths, leaves = _gt_tree(mw, tmp_path)
    gt = mw.gt_tracing_interface
    mw.toggle_gt_tracing()
    mw.file_list.setCurrentItem(leaves["a"])
    mw.on_image_selected(leaves["a"])
    assert gt.current_image_path == paths["a"]
    gt.begin_stroke(QPoint(10, 10))
    gt.continue_stroke(QPoint(30, 10))
    gt.end_stroke()
    assert gt.has_unsaved_changes()

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(gti, "write_tracing", _boom)
    titles = []

    def _exec(box):
        titles.append(box.windowTitle())
        if box.windowTitle() == "Unsaved GT":
            return QMessageBox.StandardButton.Save
        return QMessageBox.StandardButton.Cancel  # "Keep editing"

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return paths, leaves, titles


def _drop_edits(mw):
    """Let the fixture teardown close the window without a prompt."""
    mw.gt_tracing_interface._set_clean()


def test_failed_gt_save_keeps_the_open_image_on_tree_click(main_window, monkeypatch, tmp_path):
    mw = main_window
    gt = mw.gt_tracing_interface
    paths, leaves, titles = _open_dirty_gt_with_failing_save(mw, monkeypatch, tmp_path)

    mw.file_list.setCurrentItem(leaves["b"])  # the tree click selects b first
    mw.on_image_selected(leaves["b"])

    assert titles == ["Unsaved GT", "GT not saved"]
    assert gt.current_image_path == paths["a"]
    assert gt.has_unsaved_changes()
    assert mw.file_list.currentItem() is leaves["a"]  # the tree points back at a
    _drop_edits(mw)


def test_failed_gt_save_cancels_window_close(main_window, monkeypatch, tmp_path):
    from PyQt6.QtGui import QCloseEvent

    mw = main_window
    _open_dirty_gt_with_failing_save(mw, monkeypatch, tmp_path)
    event = QCloseEvent()
    mw.closeEvent(event)
    assert not event.isAccepted()
    assert mw.gt_tracing_interface.has_unsaved_changes()
    _drop_edits(mw)


def test_failed_gt_save_blocks_leaving_the_stage(main_window, monkeypatch, tmp_path):
    mw = main_window
    gt = mw.gt_tracing_interface
    paths, _leaves, titles = _open_dirty_gt_with_failing_save(mw, monkeypatch, tmp_path)

    assert mw.switch_right_panel("display") is False
    assert mw.right_panel.currentIndex() == Panel.GT_TRACING
    for stage in ("Library", "Generate", "Correct", "Measure"):
        mw.ribbon_buttons[stage].click()
        assert mw.right_panel.currentIndex() == Panel.GT_TRACING, stage
        assert mw.ribbon_buttons["Trace GT"].isChecked(), stage
    assert gt.current_image_path == paths["a"] and gt.has_unsaved_changes()
    assert set(titles) == {"Unsaved GT", "GT not saved"}
    _drop_edits(mw)


def test_failed_gt_save_cancels_open_folder_and_export(main_window, monkeypatch, tmp_path):
    import app.gui.export_dataset_dialog as edd

    mw = main_window
    gt = mw.gt_tracing_interface
    paths, _leaves, _titles = _open_dirty_gt_with_failing_save(mw, monkeypatch, tmp_path)
    loads, dialogs = [], []
    monkeypatch.setattr(mw.image_manager, "load_images", loads.append)
    monkeypatch.setattr(edd.ExportDatasetDialog, "exec", lambda d: dialogs.append(d) or 0)

    other = tmp_path / "other"
    other.mkdir()
    mw.open_path(str(other))
    assert loads == []
    assert gt.current_image_path == paths["a"] and gt.has_unsaved_changes()
    mw.export_gt_dataset()
    assert dialogs == []
    _drop_edits(mw)


def test_navigate_starts_from_the_open_gt_image(main_window, monkeypatch):
    """Arrow keys move the tree selection without loading; PgDn must still step
    from the image that is open in the editor."""
    from PyQt6.QtWidgets import QTreeWidgetItem

    mw = main_window
    gt = mw.gt_tracing_interface
    mw.file_list.clear()
    folder = QTreeWidgetItem(["Field"])
    leaves = {}
    for name in ("a", "b", "c", "d"):
        leaf = QTreeWidgetItem([f"{name}.png"])
        leaf.setData(0, Qt.ItemDataRole.UserRole, name)
        folder.addChild(leaf)
        leaves[name] = leaf
    mw.file_list.addTopLevelItem(folder)
    mw.image_manager.images = {n: f"/project/{n}.png" for n in leaves}

    loaded = []

    def _load(path):
        loaded.append(path)
        gt.current_image_path = path

    monkeypatch.setattr(gt, "load_image", _load)
    mw.switch_right_panel("gt_tracing")
    gt.current_image_path = "/project/a.png"
    mw.file_list.setCurrentItem(leaves["c"])  # moved by arrow keys, nothing loaded

    mw.navigate_image(1)
    assert loaded == ["/project/b.png"]
    assert mw.file_list.currentItem() is leaves["b"]

    # Nothing open in the editor: fall back to the tree selection.
    gt.current_image_path = ""
    mw.file_list.setCurrentItem(leaves["c"])
    mw.navigate_image(1)
    assert loaded[-1] == "/project/d.png"
    gt.current_image_path = ""


def test_export_dialog_is_deleted_after_use(main_window, monkeypatch, tmp_path):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent

    import app.gui.export_dataset_dialog as edd

    mw = main_window
    mw.image_manager.original_folder = str(tmp_path)
    mw.image_manager.images = {}
    opened = []
    monkeypatch.setattr(edd.ExportDatasetDialog, "exec", lambda d: opened.append(d) or 0)
    mw.export_gt_dataset()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(opened) == 1 and sip.isdeleted(opened[0])

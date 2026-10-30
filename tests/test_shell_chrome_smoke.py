"""Smoke + wiring assertions for the PR4 SPROUTS guided shell.

Runs under an offscreen QApplication. Covers the Welcome screen, the loading
overlay, the shell chrome, and (T4.15) the MainWindow connect-map contract.
"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from app.gui.panels import Panel
from tests.qt_teardown import delete_widgets


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
        assert mw.right_panel.count() == len(Panel)  # display, Correct, viz
        # Five-stage pipeline, in order: mask+skeleton generation is one stage, and so
        # is their correction with the class labels (one canvas, three layers).
        assert list(mw.ribbon_buttons.keys()) == [
            "Library",
            "Generate",
            "Correct",
            "Measure",
            "Visualize",
        ]
        assert mw.right_panel.widget(Panel.CORRECT) is mw.correct_editor
        assert mw.gt_classes_button.text() == "Classes…"
        assert mw.gt_export_button.text() == "Export dataset…"
        assert mw.gt_remove_button.text() == "Remove GT…"
        # WG-T5: the GT actions are on the Correct page.
        correct_page = mw.action_bar_stack.widget(mw._action_bar_pages["Correct"])
        for button in (mw.gt_classes_button, mw.gt_export_button, mw.gt_remove_button):
            assert button.parentWidget() is correct_page
        assert mw.app_stack.count() >= 2
        # FIX2: the dialog-free loader entry point exists for recent rows.
        assert callable(getattr(mw, "open_path", None))
    finally:
        delete_widgets([mw])


# --------------------------------------------------------------------------- #
#  Window wiring: library navigation and the dataset export
# --------------------------------------------------------------------------- #
@pytest.fixture
def main_window(app, monkeypatch):
    from app.gui.main_window import MainWindow

    made = [MainWindow()]
    # No editor prompt may block a headless run.
    monkeypatch.setattr(made[0].correct_editor, "maybe_save_changes", lambda: None)
    yield made[0]
    delete_widgets(made)


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
    monkeypatch.setattr(mw.correct_editor, "load_image", lambda path, **_k: loaded.append(path))
    mw.switch_right_panel("correct")
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
    mw.correct_editor.navigate_requested.emit(1)
    assert mw.file_list.currentItem() is leaves["b"]


def test_export_gt_dataset_opens_dialog(main_window, monkeypatch, tmp_path):
    import sys
    import types

    from app.gt import default_registry

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

        def testAttribute(self, _attribute):  # noqa: N802 - Qt API (run_modal asks)
            return False

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
    assert registry == default_registry()  # the folder has no gt/classes.json yet
    assert parent is mw


def test_export_uses_the_folders_registry(main_window, monkeypatch, tmp_path):
    """Export must use the folder's gt/classes.json even if Correct never opened
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

        def testAttribute(self, _attribute):  # noqa: N802 - Qt API (run_modal asks)
            return False

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


def test_navigate_starts_from_the_image_open_in_correct(main_window, monkeypatch):
    """Arrow keys move the tree selection without loading; PgDn must still step
    from the image that is open in the editor."""
    from PyQt6.QtWidgets import QTreeWidgetItem

    mw = main_window
    editor = mw.correct_editor
    gt = SimpleNamespace(current_image_path="")  # what the editor has open
    monkeypatch.setattr(
        type(editor), "current_image_path", property(lambda _self: gt.current_image_path)
    )
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

    def _load(path, **_kwargs):
        loaded.append(path)
        gt.current_image_path = path

    monkeypatch.setattr(editor, "load_image", _load)
    mw.switch_right_panel("correct")
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

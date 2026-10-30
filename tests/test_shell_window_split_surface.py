"""The main window's names, frozen before it was split into parts (review CQ-07).

``MainWindow`` is built from the parts in ``app/gui/window/``. Every name that code and
tests used on the unsplit window keeps working with the same signature: the parts'
functions are bound as its methods under their old names (so a call or a slot reaches the
part without an extra call), and the metrics controller's state stays reachable through
properties. The lists were captured from the unsplit window (``fcef800``); change them
deliberately, never to make a refactor pass.
"""

from __future__ import annotations

import inspect
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, QThreadPool, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui import image_manager, main_window  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from app.gui.window import (  # noqa: E402
    body,
    editors,
    job_buttons,
    lifecycle,
    navigator,
    project_loader,
    stages,
)
from tests.qt_teardown import delete_widgets  # noqa: E402

#: Bound signature of every method of the unsplit window (``inspect.signature``).
SIGNATURES = {
    "_bottom_chrome": "() -> list[tuple]",
    "_build_body": "() -> PyQt6.QtWidgets.QWidget",
    "_build_shell": "() -> PyQt6.QtWidgets.QWidget",
    "_check_freshness": "() -> None",
    "_current_image_name": "() -> str | None",
    "_current_stage": "() -> str | None",
    "_current_widget_is_viz": "() -> bool",
    "_editor_canvas": "()",
    "_editors": "() -> dict",
    "_end_failed_load": "(message, kind)",
    "_generated_skeleton_for": "(image_path: str) -> str | None",
    "_go_to_stage": "(key: str) -> None",
    "_image_has_gt_skeleton": "(stored_name) -> bool",
    "_image_has_mask": "(stored_name)",
    "_image_has_skeleton": "(stored_name)",
    "_image_leaves": "(*, visible_only: bool = False) -> list",
    "_install_global_shortcuts": "() -> None",
    "_install_image_paging_shortcuts": "() -> None",
    "_install_library_shortcuts": "() -> None",
    "_install_warmup_intents": "() -> None",
    "_kept_other_image": "(editor, image_path: str) -> bool",
    "_leaf_for_image": "(image_path: str, leaves: list | None = None)",
    "_load_current_tree_item": "() -> None",
    "_missing_dashboard_data": "(kind: str) -> tuple[str, str, str, str | None] | None",
    "_on_editor_dirty_changed": "(*_args) -> None",
    "_on_jobs_busy_changed": "(busy: bool) -> None",
    "_on_measurements_ready": "(index) -> None",
    "_on_warmup_finished": "(key: str, error: str) -> None",
    "_page_image": "(delta: int) -> None",
    "_prewarm_measurements": "(*, locate: bool = False) -> None",
    "_record_recent_project": "(count)",
    "_refresh_idle_text": "() -> None",
    "_refresh_metrics_bar": "(stored_name=None)",
    "_refresh_metrics_if_current": "(image_name)",
    "_refresh_recents": "() -> None",
    "_request_warmup": "(keys: tuple[str, ...] | None) -> None",
    "_save_window_state": "() -> None",
    "_select_tree_image": "(image_path: str) -> None",
    "_show_selection_on_display": "(index: int) -> None",
    "_side_chrome": "() -> list[tuple]",
    "_stop_jobs_for_close": "() -> bool",
    "_sync_library_for_panel": "(*_args)",
    "_sync_open_correction": "() -> None",
    "_sync_window_modified": "(*_args) -> None",
    "_toast_bottom_offset": "() -> int",
    "_toggle_editor": "(panel: app.gui.panels.Panel) -> None",
    "_warmup_blocked": "() -> str | None",
    "_wire_library_order": "() -> None",
    "calculate_root_area": "()",
    "calculate_root_length": "()",
    "close_root_area_visualization": "()",
    "close_root_length_visualization": "()",
    "export_gt_dataset": "() -> None",
    "find_test_latest_dir": "(start_path, max_depth=3)",
    "find_tree_item_by_image_name": "(image_name)",
    "generate_all": "()",
    "highlight_saved_mask": "(image_path)",
    "init_ui": "()",
    "load_images": "()",
    "load_results": "(results_dir)",
    "mask_exists": "(image_name)",
    "natural_sort_key": "(text)",
    "navigate_image": "(delta: int) -> None",
    "notify": (
        "(message: str, kind: str = 'success', timeout: int = 3200, *, "
        "key: str | None = None) -> None"
    ),
    "on_area_visualization_server_closed": "()",
    "on_b_key_status_changed": "(is_pressed)",
    "on_image_selected": "(item)",
    "on_library_filtered": "() -> None",
    "on_loading_empty": "(folder)",
    "on_loading_failed": "(folder, message)",
    "on_loading_finished": "(images, fake_images, masks, has_fake_real_pairs)",
    "on_mask_cleared": "(image_path)",
    "on_mask_saved": "(image_path)",
    "on_outputs_changed": "()",
    "on_stage_changed": "(key: str) -> None",
    "on_tree_current_changed": "(current, _previous=None) -> None",
    "on_tree_item_clicked": "(item, column)",
    "on_visualization_server_closed": "()",
    "open_path": "(dir_name)",
    "populate_file_list": "()",
    "set_opengl_viewports_enabled": "(enabled: bool) -> None",
    "show_about": "() -> None",
    "show_dashboard": "(kind: str) -> None",
    "show_root_area_visualization": "()",
    "show_root_length_visualization": "()",
    "show_shortcuts": "() -> None",
    "stage_summary": "() -> str | None",
    "switch_right_panel": "(panel) -> bool",
    "toggle_correct": "() -> None",
    "toggle_root_area_visualization": "()",
    "toggle_root_length_visualization": "()",
    "unhighlight_cleared_mask": "(image_path)",
    "update_display": "()",
    "update_file_list_mask_status": "()",
    "update_loading_progress": "(value)",
}

#: The instance attributes of the unsplit window once built (``vars(window)``).
INSTANCE_ATTRIBUTES = (
    "_action_bar_pages _activate_action_stage _canvas_key_relay _global_shortcuts "
    "_help_shortcut_f1 _help_shortcut_q _hover_intent _job_button_tooltips _library_shortcuts "
    "_loading_project _measurement_signals _measurements _measurements_loading "
    "_paging_shortcuts _persist_window_state _populate_action_bar _pre_viz_splitter_sizes "
    "_shown_leaf_name _tree_load_timer _warm_ups _warmup_settle_until action_bar_stack "
    "action_bar_view_seg action_bar_viz_seg app_stack body_splitter calculate_area_button "
    "calculate_length_button collapse_all_button correct_editor device_check display_area "
    "display_controller display_page_stack empty_state expand_all_button feedback file_list "
    "generate_all_button generate_button generate_mask_button gt_classes_button "
    "gt_export_button gt_remove_button image_manager jobs left_panel "
    "library_index "
    "library_search library_search_controller library_search_status load_images_button "
    "loading_overlay mask_generation_handler metrics_bar recent_projects ribbon "
    "ribbon_buttons right_panel root_area_viz root_length_viz skeleton_handler stage_status "
    "status_bar statusline statusline_info statusline_message task_progress toast_clearance "
    "toasts topbar_load_button view_mode_combo visualization_widget "
    "visualize_root_area_button visualize_root_length_button warmups welcome window_state"
).split()

#: Module-level names the unsplit module defined, with their values where they have one.
MODULE_VALUES = {
    "WARMUP_SETTLE_MS": 2500,
    "TREE_LOAD_DELAY_MS": 40,
    "JOB_BUTTONS": (
        "generate_all_button",
        "generate_button",
        "calculate_length_button",
        "calculate_area_button",
    ),
    "_LOAD_TOAST": "project-load",
    "_EDITOR_CHROME": ("dock", "zoom_cluster"),
    "_EDITOR_SIDE_CHROME": ("layer_panel", "class_panel"),
    "_CLOSE_WAIT_MS": 5000,
    "_CLOSE_EXTRA_WAIT_MS": 120_000,
}
MODULE_CALLABLES = (
    "MainWindow",
    "ShortcutsDialog",
    "_MeasurementSignals",
    "_prewarm_skeletonize_logged",
    "_released",
    "_same_path",
)

#: Window method -> the part's function it is (a call reaches the part with no extra call).
BOUND = {
    "_stop_jobs_for_close": lifecycle.stop_jobs_for_close,
    "_save_window_state": lifecycle.save_window_state,
    "init_ui": body.init_ui,
    "_build_shell": body.build_shell,
    "_build_body": body.build_body,
    "_current_widget_is_viz": body.current_widget_is_viz,
    "_sync_library_for_panel": body.sync_library_for_panel,
    "_toast_bottom_offset": body.toast_bottom_offset,
    "_bottom_chrome": body.bottom_chrome,
    "_side_chrome": body.side_chrome,
    "_go_to_stage": stages.go_to_stage,
    "_current_stage": stages.current_stage,
    "on_stage_changed": stages.on_stage_changed,
    "stage_summary": stages.stage_summary,
    "_refresh_idle_text": stages.refresh_idle_text,
    "show_dashboard": stages.show_dashboard,
    "_missing_dashboard_data": stages.missing_dashboard_data,
    "_on_jobs_busy_changed": job_buttons.on_jobs_busy_changed,
    "load_images": project_loader.load_images,
    "open_path": project_loader.open_path,
    "update_loading_progress": project_loader.update_loading_progress,
    "on_loading_empty": project_loader.on_loading_empty,
    "on_loading_failed": project_loader.on_loading_failed,
    "_end_failed_load": project_loader.end_failed_load,
    "_record_recent_project": project_loader.record_recent_project,
    "_refresh_recents": project_loader.refresh_recents,
    "_image_leaves": navigator.image_leaves,
    "_leaf_for_image": navigator.leaf_for_image,
    "_select_tree_image": navigator.select_tree_image,
    "navigate_image": navigator.navigate_image,
    "on_tree_current_changed": navigator.on_tree_current_changed,
    "_load_current_tree_item": navigator.load_current_tree_item,
    "_install_image_paging_shortcuts": navigator.install_image_paging_shortcuts,
    "_page_image": navigator.page_image,
    "on_tree_item_clicked": navigator.on_tree_item_clicked,
    "on_image_selected": navigator.on_image_selected,
    "_kept_other_image": navigator.kept_other_image,
    "_show_selection_on_display": navigator.show_selection_on_display,
    "_editors": editors.editors,
    "_editor_canvas": editors.editor_canvas,
    "_sync_window_modified": editors.sync_window_modified,
    "_on_editor_dirty_changed": editors.on_editor_dirty_changed,
    "_sync_open_correction": editors.sync_open_correction,
    "_generated_skeleton_for": editors.generated_skeleton_for,
    "switch_right_panel": editors.switch_right_panel,
    "toggle_correct": editors.toggle_correct,
    "_toggle_editor": editors.toggle_editor,
    "on_b_key_status_changed": editors.on_b_key_status_changed,
    "on_mask_saved": editors.on_mask_saved,
    "on_mask_cleared": editors.on_mask_cleared,
    # WG-T5: the Correct page's class labels, Remove GT… and a GT skeleton removed.
    "_on_gt_saved": editors.on_gt_saved,
    "_on_gt_removed": editors.on_gt_removed,
    "_on_skeleton_cleared": editors.on_skeleton_cleared,
    # WG-T11: a GT skeleton saved in Correct reaches the Library and the metrics at once.
    "_on_skeleton_saved": editors.on_skeleton_saved,
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(app, tmp_path_factory):
    settings = QSettings(
        str(tmp_path_factory.mktemp("surface") / "s.ini"), QSettings.Format.IniFormat
    )
    made = [main_window.MainWindow(settings=settings)]
    yield made[0]
    QThreadPool.globalInstance().waitForDone(10_000)
    delete_widgets(made)


# ------------------------------------------------------------------ the frozen names
@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_every_method_of_the_unsplit_window_keeps_its_signature(window, name):
    assert str(inspect.signature(getattr(window, name))) == SIGNATURES[name]


def test_the_signals_and_the_shell_sentinel_stay():
    assert isinstance(main_window.MainWindow.__dict__["mask_saved"], pyqtSignal)
    assert isinstance(main_window.MainWindow.__dict__["mask_cleared"], pyqtSignal)
    assert main_window.MainWindow._pr4_shell_ready is True


def test_every_instance_attribute_of_the_unsplit_window_is_still_reachable(window):
    missing = [name for name in INSTANCE_ATTRIBUTES if not hasattr(window, name)]
    assert missing == []


def test_the_module_still_defines_its_names():
    for name, value in MODULE_VALUES.items():
        assert getattr(main_window, name) == value, name
    for name in MODULE_CALLABLES:
        assert callable(getattr(main_window, name)), name
    assert main_window.USER_ROLE == main_window.Qt.ItemDataRole.UserRole
    # WG-T8 (O1): GT is measured first; these two replace CORRECTION_NOT_MEASURED and
    # CORRECTED_STATUS_TIP (a corrected skeleton was not measured, FR-03).
    assert main_window.GT_NOT_USED.startswith("A GT {noun} exists")
    assert main_window.GT_STATUS_TIP.startswith("Overlay and Side by side draw the GT skeleton")


# ------------------------------------------------------------------ no extra call
@pytest.mark.parametrize("name", sorted(BOUND))
def test_the_windows_method_is_the_parts_function(name):
    """The window binds the part's function itself: calling ``window.<name>`` (or a signal
    connected to it) runs the part with no forwarding call in between."""
    assert main_window.MainWindow.__dict__[name] is BOUND[name]


APP_DIR = os.path.normcase(os.path.dirname(os.path.dirname(main_window.__file__)))


def _callers(action, function) -> list[str]:
    """Who called ``function`` while ``action`` ran: ``"Qt"`` when no code of ``app/`` sits
    between the signal (or the test) and it, else that caller's ``module:function``."""
    callers: list[str] = []

    def hook(frame, event, _arg):
        if event == "call" and frame.f_code is function.__code__:
            back = frame.f_back
            if back is None or not os.path.normcase(back.f_code.co_filename).startswith(APP_DIR):
                callers.append("Qt")
            else:
                callers.append(f"{back.f_globals.get('__name__')}:{back.f_code.co_name}")

    sys.setprofile(hook)
    try:
        action()
    finally:
        sys.setprofile(None)
    return callers


def test_the_hot_paths_reach_the_parts_without_a_forwarding_call(window):
    """Arrow-key browse (the tree's current item, the debounce), PgDn in an editor, a stage
    switch and a finished job: the signal runs the part's function itself, with no code of
    the app (such as a forwarding method of the window) in between."""
    tree = window.file_list
    emit = tree.currentItemChanged.emit
    assert _callers(lambda: emit(None, None), navigator.on_tree_current_changed) == ["Qt"]
    timer = window._tree_load_timer
    timer.setInterval(0)
    try:
        timer.start()

        def until_fired():
            for _ in range(100):
                QApplication.processEvents()
                if not timer.isActive():
                    return

        assert _callers(until_fired, navigator.load_current_tree_item) == ["Qt"]
    finally:
        timer.setInterval(main_window.TREE_LOAD_DELAY_MS)
    emit = window.correct_editor.navigate_requested.emit
    assert _callers(lambda: emit(1), navigator.navigate_image) == ["Qt"]
    emit = window.right_panel.currentChanged.emit
    assert _callers(lambda: emit(Panel.DISPLAY), body.sync_library_for_panel) == ["Qt"]
    assert _callers(lambda: emit(Panel.DISPLAY), navigator.show_selection_on_display) == ["Qt"]
    emit = window.jobs.busy_changed.emit
    assert _callers(lambda: emit(False), job_buttons.on_jobs_busy_changed) == ["Qt"]


#: The window's forwarding methods (and properties) to the metrics controller: for the
#: code and tests that use the old names, never on the window's own paths.
DELEGATORS = (
    "_refresh_metrics_bar",
    "_refresh_metrics_if_current",
    "_image_has_mask",
    "_image_has_skeleton",
    "_image_has_gt_skeleton",
    "_current_image_name",
    "_prewarm_measurements",
    "_check_freshness",
)
DELEGATOR_PROPERTIES = ("_measurements", "_measurements_loading", "_measurement_signals")


def test_the_hot_paths_never_run_a_forwarding_method(app, tmp_path, monkeypatch):
    """Arrow-key browse, PgDn from the library and in Correct, and every stage switch call
    the metrics controller directly, never through the window's old names."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    cls = main_window.MainWindow
    codes = {cls.__dict__[name].__code__: name for name in DELEGATORS}
    for name in DELEGATOR_PROPERTIES:
        prop = cls.__dict__[name]
        codes.update({f.__code__: name for f in (prop.fget, prop.fset) if f is not None})
    folder = tmp_path / "project"
    folder.mkdir()
    image = QImage(160, 120, QImage.Format.Format_RGB32)
    for d in (11, 12, 13):
        image.fill(QColor(40 + 30 * (d - 10), 60, 30))
        assert image.save(str(folder / f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM.jpg"))
    made = [
        main_window.MainWindow(
            settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
        )
    ]
    window = made[0]
    try:
        window.resize(1280, 800)
        window.show()
        window.open_path(str(folder))
        end = time.monotonic() + 10
        while len(window.library_index.leaves()) < 3 and time.monotonic() < end:
            QTest.qWait(10)
        leaves = window.library_index.leaves()
        ran: list[str] = []

        def hook(frame, event, _arg):
            if event == "call" and frame.f_code in codes:
                ran.append(codes[frame.f_code])

        sys.setprofile(hook)
        try:
            window.file_list.setCurrentItem(leaves[1])  # an arrow key: the debounce loads it
            QTest.qWait(3 * main_window.TREE_LOAD_DELAY_MS)
            window._page_image(1)  # PgDn from the library
            for key in ("Generate", "Correct", "Measure", "Visualize", "Library"):
                if key == "Correct":
                    window.ribbon_buttons[key].click()
                    window.correct_editor.navigate_requested.emit(-1)  # PgUp in Correct
                elif key == "Visualize":
                    window._go_to_stage(key)  # no CSV: the page names the next step
                else:
                    window.ribbon_buttons[key].click()
                QTest.qWait(20)
        finally:
            sys.setprofile(None)
        assert window._shown_leaf_name is not None
        assert ran == []
    finally:
        QThreadPool.globalInstance().waitForDone(10_000)
        for editor in window._editors().values():
            editor.maybe_save_changes = lambda: True
        del window
        delete_widgets(made)


# ------------------------------------------------------------------ the metrics state
def test_the_measurement_state_names_are_the_controllers(window):
    controller = window.metrics
    assert window._measurement_signals is controller.signals
    marker = object()
    window._measurements = marker
    try:
        assert controller.index is marker and window._measurements is marker
    finally:
        window._measurements = None
    window._measurements_loading = True
    try:
        assert controller.loading is True and window._measurements_loading is True
    finally:
        window._measurements_loading = False

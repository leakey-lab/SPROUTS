"""
Main window of SPROUTS.
Orchestrates the GUI components and handles core functionality.

The window is built from parts in :mod:`app.gui.window` (review CQ-07), one module per
responsibility: the library navigator, the project loader, the metrics controller, the
editor pages, the stages, the job buttons, closing the window and its widgets. The parts
without state of their own are functions of the window, bound below as its methods under
their old names (a call or a slot costs what it did before, a slot stays a method of the
window); the metrics controller owns its state and the window holds it as ``metrics``.
"""

import functools
import logging
import os
import time

from PyQt6.QtCore import QSettings, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

from app import APP_NAME
from app.gt import RegistryError, load_registry
from app.handlers.job_registry import JobRegistry

from . import (
    file_tree_manager,
    shell_chrome,
    shortcuts,
    skeleton_graph_model,
    visualization_manager,
    warmup,
)
from .canvas_key_relay import CanvasKeyRelay
from .correct.editor import CorrectEditor
from .device_check import DeviceCheck
from .display_controller import DisplayController
from .empty_state import ShortcutsDialog
from .gt_tracing_interface import GtTracingInterface
from .image_manager import ImageManager, project_layout_of
from .recent_projects import RecentProjectsStore
from .stage_status import StageStatus
from .window import (
    _common,
    body,
    editors,
    job_buttons,
    lifecycle,
    metrics,
    navigator,
    project_loader,
    stages,
)
from .window.metrics import MetricsController
from .window_state import WindowStateStore

logger = logging.getLogger(__name__)

#: No warm-up import starts until this long after a project has loaded: the library tree,
#: the first image, the CSV parse and the stale-temp sweep (at +2 s) go first.
WARMUP_SETTLE_MS = 2500

# Defined here before the window was split into parts (CQ-07); still importable.
USER_ROLE = _common.USER_ROLE
_same_path = _common.same_path
_released = _common.released
TREE_LOAD_DELAY_MS = navigator.TREE_LOAD_DELAY_MS
JOB_BUTTONS = job_buttons.JOB_BUTTONS
CORRECTION_NOT_MEASURED = metrics.CORRECTION_NOT_MEASURED
CORRECTED_STATUS_TIP = metrics.CORRECTED_STATUS_TIP
_MeasurementSignals = metrics._MeasurementSignals
_LOAD_TOAST = project_loader._LOAD_TOAST
_EDITOR_CHROME = body._EDITOR_CHROME
_EDITOR_SIDE_CHROME = body._EDITOR_SIDE_CHROME
_CLOSE_WAIT_MS = lifecycle._CLOSE_WAIT_MS
_CLOSE_EXTRA_WAIT_MS = lifecycle._CLOSE_EXTRA_WAIT_MS


def _prewarm_skeletonize_logged() -> None:
    """Import scikit-image's skeletonize (a thread-pool job); a failure is only logged.

    The first skeleton thinning imports it anyway, so a failure here costs nothing but
    the head start; an exception must never leave the pool thread.
    """
    try:
        skeleton_graph_model.prewarm_skeletonize()
    except Exception:
        logger.exception("Could not prewarm scikit-image")


class MainWindow(QMainWindow):
    mask_saved = pyqtSignal(str)
    mask_cleared = pyqtSignal(str)

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        detect_device: bool | None = None,
        warm_up: bool | None = None,
    ):
        """Build the window.

        ``settings`` (optional) is where window geometry, the last folder and the
        recent projects persist; by default the app's own QSettings. Headless
        (``offscreen``) runs without explicit settings neither restore nor save
        the window geometry / last folder, so test runs never touch the user's.

        ``detect_device``: name the inference device in the thread pool once the
        window first shows, for the status line's device chip (see
        :class:`~app.gui.device_check.DeviceCheck`). Default: on, except headless.

        ``warm_up``: import torch / the dashboards' libraries when the user heads for
        Generate / Visualize, and freeze the long-lived objects after start-up and after
        each such import (see :mod:`app.gui.warmup`). Default: on, except headless, so
        tests never import them behind a test's back. scikit-image is warmed up at
        start-up either way.
        """
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}[*]")  # "<project> — SPROUTS" once a folder loads

        self.window_state = WindowStateStore(settings)
        self.recent_projects = RecentProjectsStore(settings)
        self._persist_window_state = (
            settings is not None or QApplication.platformName() != "offscreen"
        )
        # The metrics bar and the measurement CSVs behind it. Its pool job reports to the
        # window's slot: on the GUI thread, and to nothing once the window is destroyed.
        self.metrics = MetricsController(self)
        self.metrics.signals.ready.connect(self._on_measurements_ready)
        # What Generate / Measure would work on (the status line's idle text, W12).
        self.stage_status = StageStatus(self)
        self.stage_status.changed.connect(self._refresh_idle_text)
        self._job_button_tooltips: dict[str, str] = {}
        if detect_device is None:
            detect_device = QApplication.platformName() != "offscreen"
        if warm_up is None:
            warm_up = QApplication.platformName() != "offscreen"
        self._warm_ups = bool(warm_up)
        # Warm-ups wait while a folder loads and a little after (see _warmup_blocked).
        self._loading_project = False
        self._warmup_settle_until = 0.0

        # Handlers are imported lazily (they import app.gui modules themselves).
        from app.handlers.mask_generation_handler import MaskGenerationHandler
        from app.handlers.skeleton_handler import SkeletonHandler

        # One background job at a time (generation / measurement); handlers register
        # their threads here, so it must exist before they are built.
        self.jobs = JobRegistry(self)
        self.jobs.busy_changed.connect(self._on_jobs_busy_changed)
        self.image_manager = ImageManager(self)
        self.display_controller = DisplayController(self)
        self.skeleton_handler = SkeletonHandler(self)
        self.mask_generation_handler = MaskGenerationHandler(self)
        # The Correct stage: the mask (root area) and the skeleton (root length) of an
        # image on one canvas.
        self.correct_editor = CorrectEditor(settings=settings)
        # Multi-class ground-truth editor (Trace GT stage). Independent of the
        # ML pipeline: it reads/writes only gt/, never mask/ or skeletons/.
        self.gt_tracing_interface = GtTracingInterface()
        self.root_length_viz = None
        self.root_area_viz = None

        # A saved / removed mask updates the Library badge (image_manager).
        self.mask_saved.connect(self.highlight_saved_mask)
        self.mask_cleared.connect(self.unhighlight_cleared_mask)

        # Transient editor feedback (fill hints, clear/undo notes, saves) -> toasts, a
        # save's in place; unsaved edits -> the title's modified marker; PgDn / PgUp after
        # a save.
        for editor in self._editors().values():
            editor.notice.connect(self._on_editor_notice)
            editor.dirty_changed.connect(self._on_editor_dirty_changed)
            editor.navigate_requested.connect(self.navigate_image)
        # The Correct page reports every mask it writes or removes, on every save path
        # (Ctrl+S, PgDn, the prompt's Save). Connected once, here. Its skeleton_saved
        # stays unconnected: a corrected skeleton is not measured (FR-03).
        correct = self.correct_editor
        correct.mask_saved.connect(self.on_mask_saved)
        correct.mask_cleared.connect(self.on_mask_cleared)
        correct.b_key_status_changed.connect(self.on_b_key_status_changed)
        correct.set_generated_skeleton_resolver(self._generated_skeleton_for)
        self.gt_tracing_interface.gt_saved.connect(self._on_gt_saved)

        # Keyboard browsing: the image whose row was last loaded, and the debounce
        # that loads the tree's current row once the arrow keys come to rest.
        self._shown_leaf_name = None
        self._tree_load_timer = QTimer(self)
        self._tree_load_timer.setSingleShot(True)
        self._tree_load_timer.setInterval(TREE_LOAD_DELAY_MS)
        self._tree_load_timer.timeout.connect(self._load_current_tree_item)

        self.init_ui()
        self.display_area.zoom_changed.connect(self.metrics_bar.set_zoom)  # the ZOOM cell
        self._install_image_paging_shortcuts()
        self._install_library_shortcuts()
        self._install_global_shortcuts()
        # Hold B / Space (and Correct's S, Enter, Esc, Delete) reach the editor canvas
        # while the library tree keeps the keyboard for arrow browsing.
        self._canvas_key_relay = CanvasKeyRelay(self.file_list, self._editor_canvas)
        self._wire_library_order()

        if self._persist_window_state:
            self.window_state.restore(self, self.body_splitter)
        else:
            self.window_state.apply_default_geometry(self)

        # The heavy imports, one at a time off the GUI thread (app.gui.warmup). scikit-image
        # (the skeleton's thinning, about 0.2 s from a warm disk cache, 1.8 to 2.5 s in the
        # logged launches, in the thread pool) starts once the event loop runs, i.e. after
        # main.py showed the window: neither the start-up nor the first Correct open waits
        # for it. torch and the dashboards' libraries (each on a thread of its own) wait
        # for the user to head for Generate / Visualize (staying in a stage for a second,
        # resting on its buttons).
        self.warmups = warmup.WarmupQueue(
            self,
            jobs={
                warmup.SKIMAGE: lambda: _prewarm_skeletonize_logged(),
                warmup.TORCH: warmup.warm_inference,
                warmup.MODELS: warmup.warm_models,
                warmup.DASHBOARDS: warmup.warm_dashboards,
            },
            blocked=self._warmup_blocked,
            ungated=(warmup.SKIMAGE,),
            pooled=(warmup.SKIMAGE,),
            freeze=self._warm_ups,
        )
        self.warmups.finished.connect(self._on_warmup_finished)
        self.warmups.request(warmup.SKIMAGE)
        self._install_warmup_intents()
        # The device chip: named in the pool once the window shows (no torch: the driver).
        self.device_check = (
            DeviceCheck(self, self.statusline, settings=settings) if detect_device else None
        )

    # ==================== Warm-ups (app.gui.warmup) ====================

    def _install_warmup_intents(self) -> None:
        """Resting on a run button (or the Visualize stage button) asks for its warm-up,
        and so does staying in Generate or Measure for a second (on_stage_changed)."""
        self._hover_intent = warmup.HoverIntent(self, self._request_warmup)
        for attr, keys in warmup.BUTTON_WARMUPS.items():
            self._hover_intent.watch(getattr(self, attr, None), keys)
        visualize = (getattr(self, "ribbon_buttons", None) or {}).get("Visualize")
        self._hover_intent.watch(visualize, warmup.VISUALIZE)
        self._stage_intent = warmup.StageIntent(self, self._request_warmup)

    def _request_warmup(self, keys: tuple[str, ...] | None) -> None:
        """Queue the ``keys`` warm-ups (torch and the models / the dashboards), in order,
        when warm-ups are on."""
        queue = getattr(self, "warmups", None)
        if not keys or not self._warm_ups or queue is None:
            return
        for key in keys:
            queue.request(key)

    def _warmup_blocked(self) -> str | None:
        """Why a warm-up import must wait now (None: it may start).

        An import holds the interpreter lock for tens of milliseconds at a time, so it
        never runs beside a folder scan, the library build and first image of a project
        (:data:`WARMUP_SETTLE_MS`), a Measure CSV parse, a batch job or another pool job.
        """
        if self._loading_project:
            return "a folder is loading"
        if time.perf_counter() < self._warmup_settle_until:
            return "the project is settling"
        if self.metrics.loading:
            return "the measurement CSVs are being read"
        if self.jobs.is_busy():
            return f"{self.jobs.running_name() or 'a job'} is running"
        if QThreadPool.globalInstance().activeThreadCount() > 0:
            return "a background job is running"
        return None

    def _on_warmup_finished(self, key: str, error: str) -> None:
        """torch is loaded: the chip shows the full runtime line (or why it failed)."""
        if key != warmup.TORCH:
            return
        line = self.statusline
        if error:
            line.set_device_check(shell_chrome.FAILED, error)
        else:
            line.refresh_runtime()

    def _wire_library_order(self) -> None:
        """The Library display and the Correct page read the next images ahead in library
        order, and the Correct page names the open image's place ("Image 12 / 40"). They
        share their decoded photos: the Correct page takes the Library's (the Library's
        cache empties while Correct is shown), the Library shows the Correct page's."""
        index, display = self.library_index, self.display_controller
        display.set_neighbour_provider(index.neighbours)
        for setter, provider in (
            ("set_neighbour_provider", index.neighbours),
            ("set_position_provider", index.position_of),
            ("set_photo_source", display.take_photo),
        ):
            method = getattr(self.correct_editor, setter, None)
            if callable(method):  # a Correct page without read-ahead keeps working
                method(provider)
        shared = getattr(self.correct_editor, "cached_photo", None)
        if callable(shared):
            display.set_photo_source(shared)

    def on_library_filtered(self) -> None:
        """The library search changed which images show: the Correct page's "Image i / n"
        counts the shown ones, so it asks again."""
        offer = getattr(self.correct_editor, "set_position_provider", None)
        if callable(offer):
            offer(self.library_index.position_of)

    # ==================== Closing (window/lifecycle.py) ====================

    def closeEvent(self, event: QCloseEvent) -> None:
        """Offer to save editor changes, stop a running job, then clean up and persist.

        An editor that keeps unsaved edits (the user cancelled, or the save
        failed and they kept editing) or a job the user keeps running cancels the close.
        """
        if lifecycle.close_event(self, event):
            super().closeEvent(event)

    _stop_jobs_for_close = lifecycle.stop_jobs_for_close
    _save_window_state = lifecycle.save_window_state

    # ==================== Widgets (window/body.py) ====================

    # Sentinel: presence signals the PR4 guided shell (app_stack) is wired, used
    # by the shell smoke test to gate the MainWindow contract assertions.
    _pr4_shell_ready = True

    init_ui = body.init_ui
    _build_shell = body.build_shell
    _build_body = body.build_body
    _current_widget_is_viz = body.current_widget_is_viz
    _sync_library_for_panel = body.sync_library_for_panel
    _toast_bottom_offset = body.toast_bottom_offset
    _bottom_chrome = body.bottom_chrome
    _side_chrome = body.side_chrome

    def notify(
        self, message: str, kind: str = "success", timeout: int = 3200, *, key: str | None = None
    ) -> None:
        """Show a transient toast. ``kind`` in success/info/warn/danger; a ``key`` updates the
        live toast of that key instead of stacking another."""
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.show(message, kind=kind, timeout=timeout, key=key)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.reposition()
        overlay = getattr(self, "loading_overlay", None)
        if overlay is not None:
            overlay.reposition()

    def set_opengl_viewports_enabled(self, enabled: bool) -> None:
        """Enable/disable QOpenGLWidget-based viewports across the app.

        Qt6 limitation: QtWebEngine (used for Dash visualizations via QWebEngineView)
        cannot render in a top-level window that contains any QOpenGLWidget.
        We disable OpenGL viewports only while visualizations are shown, and
        re-enable them afterwards for performance elsewhere.
        """
        enabled = bool(enabled)
        views = (
            ("display", getattr(self.display_controller, "magnifying_view", None)),
            ("Correct", getattr(self.correct_editor, "graphics_view", None)),
            ("Trace GT", getattr(self.gt_tracing_interface, "graphics_view", None)),
        )
        for label, view in views:
            if view is None or not hasattr(view, "set_opengl_viewport_enabled"):
                continue
            try:
                view.set_opengl_viewport_enabled(enabled)
            except RuntimeError as e:
                logger.warning("Failed to toggle OpenGL on %s view: %s", label, e)

    # ==================== Shortcuts / help ====================

    def _install_global_shortcuts(self) -> None:
        """F1 / ?, Ctrl+O and Alt+1…6 from the shortcut registry (window-wide)."""
        stage_slots = [lambda k=k: self._go_to_stage(k) for k in self.ribbon_buttons]
        self._global_shortcuts = shortcuts.install_shortcuts(
            self,
            shortcuts.GLOBAL,
            {
                "help": self.show_shortcuts,
                "open_folder": self.load_images,
                "go_to_stage": stage_slots,
            },
            context=Qt.ShortcutContext.WindowShortcut,
        )
        # Legacy attribute names (F1 / ?).
        self._help_shortcut_f1, self._help_shortcut_q = self._global_shortcuts["help"]

    def _install_library_shortcuts(self) -> None:
        """The Library canvas' zoom keys (registry: Library), live while the display canvas
        is on screen wherever the keyboard is: the tree keeps it while the arrows browse."""
        view = self.display_area

        def on_image(zoom):  # a message ("Select an image …") is never zoomed
            return lambda: None if view.content_rect().isEmpty() else zoom()

        installed = shortcuts.install_shortcuts(
            view,
            shortcuts.LIBRARY,
            {
                "fit": on_image(lambda: view.fit_to_view()),
                "zoom_100": on_image(lambda: view.set_zoom(1.0)),
                "zoom_in": on_image(lambda: view.zoom_step(1)),
                "zoom_out": on_image(lambda: view.zoom_step(-1)),
            },
        )
        self._library_shortcuts = [s for group in installed.values() for s in group]

    def show_shortcuts(self) -> None:
        """The F1 keyboard-shortcut sheet, opened on the current stage's keys."""
        dialog = ShortcutsDialog(
            self,
            current_scope=shortcuts.scope_for_stage(self._current_stage()),
            on_about=self.show_about,
        )
        # WA_DeleteOnClose: exec() deletes the dialog before it returns, so it must not
        # be touched afterwards (a deleteLater() here raised on every close, RB-16).
        dialog.exec()

    def show_about(self) -> None:
        """About SPROUTS: version, calibration, runtime, models and how to cite."""
        from app.gui.about_dialog import AboutDialog

        AboutDialog(self).exec()  # WA_DeleteOnClose: exec() deletes it (RB-16)

    # ==================== Stages (window/stages.py) ====================

    _go_to_stage = stages.go_to_stage
    _current_stage = stages.current_stage
    on_stage_changed = stages.on_stage_changed
    stage_summary = stages.stage_summary
    _refresh_idle_text = stages.refresh_idle_text
    show_dashboard = stages.show_dashboard
    _missing_dashboard_data = stages.missing_dashboard_data

    # ==================== Background jobs (window/job_buttons.py) ====================

    _on_jobs_busy_changed = job_buttons.on_jobs_busy_changed

    # ==================== File Tree Methods (delegated) ====================

    def find_tree_item_by_image_name(self, image_name):
        """The library row of ``image_name`` (O(1)), or None."""
        return file_tree_manager.find_tree_item_by_image_name(self.file_list, image_name)

    def highlight_saved_mask(self, image_path):
        file_tree_manager.highlight_saved_mask(self, image_path)

    def unhighlight_cleared_mask(self, image_path):
        file_tree_manager.unhighlight_cleared_mask(self, image_path)

    def populate_file_list(self):
        """Populate the file tree with hierarchical structure"""
        file_tree_manager.populate_file_list(self)

    def update_file_list_mask_status(self):
        """Repaint the per-image status badges of the library tree."""
        file_tree_manager.update_file_list_mask_status(self)

    def natural_sort_key(self, text):
        """Create a sort key that handles numbers naturally"""
        return file_tree_manager.natural_sort_key(text)

    # ==================== Visualization Methods (delegated) ====================

    def toggle_root_length_visualization(self):
        """Toggle between showing and hiding the root length visualization."""
        visualization_manager.toggle_root_length_visualization(self)

    def show_root_length_visualization(self):
        """Show the root length visualization."""
        visualization_manager.show_root_length_visualization(self)

    def close_root_length_visualization(self):
        """Safely close and cleanup the root length visualization."""
        visualization_manager.close_root_length_visualization(self)

    def toggle_root_area_visualization(self):
        """Toggle between showing and hiding the root area visualization."""
        visualization_manager.toggle_root_area_visualization(self)

    def show_root_area_visualization(self):
        """Show the root area visualization."""
        visualization_manager.show_root_area_visualization(self)

    def close_root_area_visualization(self):
        """Safely close and cleanup the root area visualization."""
        visualization_manager.close_root_area_visualization(self)

    def find_test_latest_dir(self, start_path, max_depth=3):
        """Efficiently search for 'test_latest' directory."""
        return visualization_manager.find_test_latest_dir(start_path, max_depth)

    def on_visualization_server_closed(self):
        """Handle root length visualization server closed event."""
        if self.root_length_viz:
            visualization_manager.close_root_length_visualization(self)

    def on_area_visualization_server_closed(self):
        """Handle root area visualization server closed event."""
        if self.root_area_viz:
            visualization_manager.close_root_area_visualization(self)

    # ==================== Image Loading (window/project_loader.py) ====================

    load_images = project_loader.load_images
    open_path = project_loader.open_path
    update_loading_progress = project_loader.update_loading_progress
    on_loading_empty = project_loader.on_loading_empty
    on_loading_failed = project_loader.on_loading_failed
    _end_failed_load = project_loader.end_failed_load
    _record_recent_project = project_loader.record_recent_project
    _refresh_recents = project_loader.refresh_recents

    def on_loading_finished(self, images, fake_images, masks, has_fake_real_pairs):
        """Show a freshly scanned folder: build the library, then drop the overlay."""
        project_loader.on_loading_finished(self, images, fake_images, masks, has_fake_real_pairs)
        # No warm-up import starts while the new project settles (see _warmup_blocked).
        self._warmup_settle_until = time.perf_counter() + WARMUP_SETTLE_MS / 1000.0

    # ==================== Metrics (window/metrics.py: MetricsController) ====================

    @property
    def _measurements(self):
        """The project's measurement index (``metrics.index``)."""
        return self.metrics.index

    @_measurements.setter
    def _measurements(self, index) -> None:
        self.metrics.index = index

    @property
    def _measurements_loading(self) -> bool:
        """True while a pool job parses the measurement CSVs (``metrics.loading``)."""
        return self.metrics.loading

    @_measurements_loading.setter
    def _measurements_loading(self, loading: bool) -> None:
        self.metrics.loading = loading

    @property
    def _measurement_signals(self):
        """The object the CSV parse reports on (``metrics.signals``)."""
        return self.metrics.signals

    def mask_exists(self, image_name):
        """True when ``image_name`` has a saved mask (kept in ``image_manager``, no stat)."""
        return self.image_manager.has_mask(image_name)

    def _image_has_mask(self, stored_name):
        """Return True if the image has a saved mask (``metrics.image_has_mask``)."""
        return self.metrics.image_has_mask(stored_name)

    def _image_has_skeleton(self, stored_name):
        """Return True if a processed skeleton is known for the image (``metrics``)."""
        return self.metrics.image_has_skeleton(stored_name)

    def _image_has_correction(self, stored_name) -> bool:
        """True when a skeleton correction of the image is known (``metrics``)."""
        return self.metrics.image_has_correction(stored_name)

    def _current_image_name(self) -> str | None:
        """Stem of the image selected in the library (``metrics.current_image_name``)."""
        return self.metrics.current_image_name()

    def _refresh_metrics_bar(self, stored_name=None):
        """Update the read-only metrics strip for the selected image (``metrics.refresh``)."""
        self.metrics.refresh(stored_name)

    def _refresh_metrics_if_current(self, image_name):
        """Refresh the metrics bar when ``image_name`` is selected (``metrics``)."""
        self.metrics.refresh_if_current(image_name)

    def _prewarm_measurements(self, *, locate: bool = False) -> None:
        """Parse new / changed measurement CSVs in the thread pool (``metrics.prewarm``)."""
        self.metrics.prewarm(locate=locate)

    def _on_measurements_ready(self, index) -> None:
        """The CSVs of ``index`` are parsed (``metrics.on_ready``): the slot of the pool
        job's signal, a method of the window so a late job reaches no destroyed window."""
        self.metrics.on_ready(index)

    def _check_freshness(self) -> None:
        """Count Measure's inputs written after each CSV, in the thread pool (``metrics``)."""
        self.metrics.check_freshness()

    # ==================== Runs and their outputs ====================

    def generate_all(self):
        """Generate masks, then skeletons, as one user-visible operation.

        The skeleton pass starts only after a completed mask run (never after
        Cancel or a failure); a second click while a job runs is refused by the
        handler / job registry, so the chain is never doubled. Both passes run on the
        folder open when Generate was pressed: the skeleton pass is bound to it here,
        not read again once the masks are done (R7).
        """
        folder = self.mask_generation_handler.project_folder()
        return self.mask_generation_handler.generate_masks(
            on_success=functools.partial(self.skeleton_handler.generate_skeleton, input_dir=folder)
        )

    def calculate_root_length(self):
        self.skeleton_handler.calculate_root_length()

    def calculate_root_area(self):
        self.skeleton_handler.calculate_root_area()

    def update_display(self):
        self.display_controller.update_display()

    def load_results(self, results_dir):
        """Show freshly generated skeletons without reloading the folder.

        ``results_dir`` is the folder the skeleton run wrote (``<project>/skeletons``);
        an old ``<results>/images`` layout is accepted too.
        """
        if not os.path.isdir(results_dir):
            QMessageBox.critical(self, "Error", f"Results folder not found at: {results_dir}")
            return
        self.on_outputs_changed()
        self.notify("Results loaded")

    def on_outputs_changed(self):
        """Masks or skeletons changed on disk (a generation run finished): re-read them
        and refresh the canvas, the status badges and the metrics of the current image."""
        self.image_manager.refresh_outputs()
        controller = self.display_controller
        controller.current_fake_image = None
        controller.update_display()
        file_tree_manager.update_file_list_mask_status(self)
        self.metrics.refresh(self.metrics.current_image_name())
        self.statusline.refresh_idle()  # the Generate / Measure counts changed
        # The Correct page's read-ahead holds masks / skeletons the run may have rewritten.
        discard = getattr(self.correct_editor, "discard_read_ahead", None)
        if callable(discard):
            discard()

    # ==================== The library (window/navigator.py) ====================

    _image_leaves = navigator.image_leaves
    _leaf_for_image = navigator.leaf_for_image
    _select_tree_image = navigator.select_tree_image
    navigate_image = navigator.navigate_image
    on_tree_current_changed = navigator.on_tree_current_changed
    _load_current_tree_item = navigator.load_current_tree_item
    _install_image_paging_shortcuts = navigator.install_image_paging_shortcuts
    _page_image = navigator.page_image
    on_tree_item_clicked = navigator.on_tree_item_clicked
    on_image_selected = navigator.on_image_selected
    _kept_other_image = navigator.kept_other_image
    _show_selection_on_display = navigator.show_selection_on_display

    # ==================== The editors, Correct and Trace GT (window/editors.py) ============

    _editors = editors.editors
    _editor_canvas = editors.editor_canvas
    _on_editor_notice = editors.on_editor_notice
    _on_gt_saved = editors.on_gt_saved
    _sync_window_modified = editors.sync_window_modified
    _on_editor_dirty_changed = editors.on_editor_dirty_changed
    _sync_open_correction = editors.sync_open_correction
    _generated_skeleton_for = editors.generated_skeleton_for
    switch_right_panel = editors.switch_right_panel
    toggle_correct = editors.toggle_correct
    toggle_gt_tracing = editors.toggle_gt_tracing
    _toggle_editor = editors.toggle_editor
    on_b_key_status_changed = editors.on_b_key_status_changed
    on_mask_saved = editors.on_mask_saved
    on_mask_cleared = editors.on_mask_cleared

    def export_gt_dataset(self) -> None:
        """Open the dataset-export dialog for the loaded folder's GT tracings."""
        layout = project_layout_of(self.image_manager)
        if layout is None:
            self.notify("Load an image folder first", "warn")
            return
        folder = layout.root
        # Pending edits on the open image belong in the export; if they could
        # not be saved and the user kept editing, do not export without them.
        if not self.gt_tracing_interface.maybe_save_changes():
            return
        # The folder's own registry: the editor's copy is only in sync once Trace
        # GT has opened an image from this folder (it holds the defaults before).
        try:
            registry = load_registry(folder)
        except RegistryError as exc:
            self.notify(f"Cannot export: gt/classes.json is invalid ({exc})", "danger")
            return
        from .export_dataset_dialog import ExportDatasetDialog

        dialog = ExportDatasetDialog(folder, dict(self.image_manager.images), registry, self)
        try:
            dialog.exec()  # cannot close while its export thread runs
        finally:
            dialog.deleteLater()  # parented to the window: free it

"""
Main window for the Root Viewer application.
Orchestrates the GUI components and handles core functionality.
"""

import logging
import os

from PyQt6.QtCore import QObject, QPoint, QSettings, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.gt import RegistryError, load_registry
from app.handlers.job_registry import JobRegistry
from app.naming import image_stem

from . import (
    file_tree_manager,
    shortcuts,
    skeleton_graph_model,
    ui_panels,
    visualization_manager,
)
from .correct.editor import CorrectEditor
from .display_controller import DisplayController
from .empty_state import ShortcutsDialog
from .gt_tracing_interface import GtTracingInterface
from .image_manager import ImageManager, project_layout_of
from .library_index import count_label
from .panels import Panel
from .recent_projects import RecentProjectsStore
from .task_progress import TaskProgressWidget
from .ui_feedback import QtUiFeedback
from .window_state import WindowStateStore

logger = logging.getLogger(__name__)

USER_ROLE = Qt.ItemDataRole.UserRole

#: Keyboard browsing in the library loads the image this long after the last move,
#: so holding an arrow key decodes only the image it stops on.
TREE_LOAD_DELAY_MS = 40

#: Buttons that start a batch job; disabled (with a tooltip saying why) while one runs.
JOB_BUTTONS = (
    "generate_all_button",
    "generate_button",
    "calculate_length_button",
    "calculate_area_button",
)

#: Gap kept between the toast stack and an editor dock sitting under it.
_TOAST_DOCK_GAP = 8
# Closing with a running job: first wait (window visible, "Stopping…"), then a
# longer wait with the window hidden; a job still running after both is logged.
_CLOSE_WAIT_MS = 5000
_CLOSE_EXTRA_WAIT_MS = 120_000


def _same_path(a: str | None, b: str | None) -> bool:
    """True when both are set and name the same file (case/separator-insensitive on Windows)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _released(editor) -> bool:
    """Ask ``editor`` to save or discard pending edits; False only when it refuses.

    Editors return False when the user cancelled or kept editing after a failed
    save; anything else (True, or ``None`` from a legacy stub) lets the caller move on.
    """
    return editor.maybe_save_changes() is not False


def _prewarm_skeletonize_logged() -> None:
    """Import scikit-image's skeletonize (a thread-pool job); a failure is only logged.

    The first skeleton thinning imports it anyway, so a failure here costs nothing but
    the head start; an exception must never leave the pool thread.
    """
    try:
        skeleton_graph_model.prewarm_skeletonize()
    except Exception:
        logger.exception("Could not prewarm scikit-image")


class _MeasurementSignals(QObject):
    """Carries 'CSV parsed' from a pool thread back to the GUI thread.

    Unparented and kept alive by the worker's closure, so a worker that ends
    after the window was destroyed emits into nothing instead of a dead object.
    """

    ready = pyqtSignal(object)


class MainWindow(QMainWindow):
    mask_saved = pyqtSignal(str)
    mask_cleared = pyqtSignal(str)

    def __init__(self, *, settings: QSettings | None = None):
        """Build the window.

        ``settings`` (optional) is where window geometry, the last folder and the
        recent projects persist; by default the app's own QSettings. Headless
        (``offscreen``) runs without explicit settings neither restore nor save
        the window geometry / last folder, so test runs never touch the user's.
        """
        super().__init__()
        self.setWindowTitle("Root Viewer[*]")

        self.window_state = WindowStateStore(settings)
        self.recent_projects = RecentProjectsStore(settings)
        self._persist_window_state = (
            settings is not None or QApplication.platformName() != "offscreen"
        )
        self._measurements: MeasurementIndex | None = None
        self._measurements_loading = False
        self._measurement_signals = _MeasurementSignals()
        self._measurement_signals.ready.connect(self._on_measurements_ready)
        self._job_button_tooltips: dict[str, str] = {}

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
        self.correct_editor = CorrectEditor()
        # Multi-class ground-truth editor (Trace GT stage). Independent of the
        # ML pipeline: it reads/writes only gt/, never mask/ or skeletons/.
        self.gt_tracing_interface = GtTracingInterface()
        self.root_length_viz = None
        self.root_area_viz = None

        # A saved / removed mask updates the Library badge (image_manager).
        self.mask_saved.connect(self.highlight_saved_mask)
        self.mask_cleared.connect(self.unhighlight_cleared_mask)

        # Transient editor feedback (fill hints, clear/undo notes, saves) -> toasts;
        # unsaved edits -> the title's modified marker; PgDn / PgUp after a save.
        for editor in self._editors().values():
            editor.notice.connect(self.notify)
            editor.dirty_changed.connect(self._sync_window_modified)
            editor.navigate_requested.connect(self.navigate_image)
        # The Correct page reports every mask it writes or removes, on every save path
        # (Ctrl+S, PgDn, the prompt's Save). Connected once, here. Its skeleton_saved
        # stays unconnected: a corrected skeleton is not measured (FR-03).
        correct = self.correct_editor
        correct.mask_saved.connect(self.on_mask_saved)
        correct.mask_cleared.connect(self.on_mask_cleared)
        correct.b_key_status_changed.connect(self.on_b_key_status_changed)
        correct.set_generated_skeleton_resolver(self._generated_skeleton_for)
        self.gt_tracing_interface.gt_saved.connect(
            lambda p: self.toasts.show(f"GT saved for {os.path.basename(p)}", key="gt-saved")
        )

        # Keyboard browsing: the image whose row was last loaded, and the debounce
        # that loads the tree's current row once the arrow keys come to rest.
        self._shown_leaf_name = None
        self._tree_load_timer = QTimer(self)
        self._tree_load_timer.setSingleShot(True)
        self._tree_load_timer.setInterval(TREE_LOAD_DELAY_MS)
        self._tree_load_timer.timeout.connect(self._load_current_tree_item)

        self.init_ui()
        self._install_image_paging_shortcuts()
        self._install_global_shortcuts()

        if self._persist_window_state:
            self.window_state.restore(self, self.body_splitter)
        else:
            self.window_state.apply_default_geometry(self)

        # scikit-image (the skeleton's thinning, ~0.2 s to import) loads in the thread pool
        # once the event loop runs, i.e. after main.py showed the window: neither the
        # start-up nor the first Correct open waits for it.
        QTimer.singleShot(0, self._prewarm_skeletonize)

    def _prewarm_skeletonize(self) -> None:
        """Start the scikit-image import in the thread pool (never on the GUI thread)."""
        QThreadPool.globalInstance().start(_prewarm_skeletonize_logged)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Offer to save editor changes, stop a running job, then clean up and persist.

        An editor that keeps unsaved edits (the user cancelled, or the save
        failed and they kept editing) or a job the user keeps running cancels the close.
        """
        for editor in self._editors().values():
            if not _released(editor):
                event.ignore()
                return
        if not self._stop_jobs_for_close():
            event.ignore()
            return
        try:
            if self.root_length_viz is not None:
                self.close_root_length_visualization()
            if self.root_area_viz is not None:
                self.close_root_area_visualization()
        except Exception:
            logger.exception("Error cleaning up visualizations on close")
        self._save_window_state()
        QApplication.processEvents()
        super().closeEvent(event)

    def _stop_jobs_for_close(self) -> bool:
        """True when no job runs any more (asks before stopping one).

        The registry is shut down first, so a job chained to the stopped one
        (Generate: masks, then skeletons) can never start during or after the
        close. A job that outlives the first wait (a CPU batch in flight) gets
        the window hidden and a longer wait, instead of the process exiting
        under a running worker.
        """
        if not self.jobs.is_busy():
            self.jobs.shutdown()
            return True
        name = self.jobs.running_name() or "A task"
        answer = QMessageBox.question(self, "Quit", f"{name} is running. Stop it and quit?")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.jobs.shutdown()
        self.feedback.status(f"Stopping: {name}…", 0)
        QApplication.processEvents()
        if self.jobs.stop_all(timeout_ms=_CLOSE_WAIT_MS):
            return True
        self.hide()
        if not self.jobs.stop_all(timeout_ms=_CLOSE_EXTRA_WAIT_MS):
            logger.warning("Job %r did not stop in time; quitting anyway", self.jobs.running_name())
        return True

    def _save_window_state(self) -> None:
        if not self._persist_window_state:
            return
        try:
            if self._pre_viz_splitter_sizes:
                # The library is collapsed for a dashboard: store the user's layout.
                self.left_panel.setVisible(True)
                self.body_splitter.setSizes(self._pre_viz_splitter_sizes)
            self.window_state.save(self, self.body_splitter)
        except Exception:
            logger.exception("Failed to save the window state")

    def _editors(self) -> dict:
        """The editor pages that hold unsaved work, by right-panel index."""
        return {
            Panel.CORRECT: self.correct_editor,
            Panel.GT_TRACING: self.gt_tracing_interface,
        }

    def _sync_window_modified(self, *_args) -> None:
        """Title marker ``*`` while any editor holds unsaved edits."""
        self.setWindowModified(any(e.has_unsaved_changes() for e in self._editors().values()))

    def _generated_skeleton_for(self, image_path: str) -> str | None:
        """The skeleton the last generation run wrote for ``image_path`` (None: editor default)."""
        base = getattr(self.image_manager, "processed_base_path", None)
        if not base or not image_path:
            return None
        return os.path.join(base, f"{image_stem(image_path)}_fake.png")

    def _build_body(self) -> QWidget:
        """Build the left/right splitter body (presentation extract of init_ui).

        Returns a container widget holding the QSplitter. The ``right_panel``
        pages are added in the fixed order of :class:`Panel`: 0 display,
        1 Correct, 2 visualization placeholder, 3 Trace GT (dashboards are
        appended after them).
        """
        from app.gui.widgets import tokens

        body = QWidget()
        main_layout = QHBoxLayout(body)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # User-resizable library panel. The handle colour lifts to the accent on
        # hover so the drag target is discoverable; names in the tree middle-
        # elide to whatever width the user drags to (see create_left_panel).
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: {tokens.BORDER}; }}"
            f"QSplitter::handle:hover {{ background-color: {tokens.ACCENT_LINE}; }}"
        )

        # Left Panel - use extracted module
        left_panel = ui_panels.create_left_panel(self)
        left_panel.setMinimumWidth(170)  # names still legible at the floor
        self.left_panel = left_panel
        splitter.addWidget(left_panel)

        # Right Panel: the display, the editors and the dashboards (one page each).
        self.right_panel = QStackedWidget()
        self.right_panel.addWidget(ui_panels.create_right_panel(self))  # Panel.DISPLAY
        self.right_panel.addWidget(self.correct_editor)  # Panel.CORRECT
        self.visualization_widget = QWidget()  # Panel.VISUALIZATION (placeholder)
        self.right_panel.addWidget(self.visualization_widget)
        self.right_panel.addWidget(self.gt_tracing_interface)  # Panel.GT_TRACING
        splitter.addWidget(self.right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 960])
        self.body_splitter = splitter

        # When a visualization becomes the current right-panel widget, collapse
        # the library panel so the dashboard gets full width; restore it (and its
        # prior width) on the way out. Driven off currentChanged so every path
        # (ribbon switch, toggle, close) is covered.
        self._pre_viz_splitter_sizes = None
        self.right_panel.currentChanged.connect(self._sync_library_for_panel)
        return body

    def _current_widget_is_viz(self) -> bool:
        cur = self.right_panel.currentWidget()
        return cur is not None and cur in (
            getattr(self, "root_length_viz", None),
            getattr(self, "root_area_viz", None),
        )

    def _sync_library_for_panel(self, *_args):
        """Collapse the library panel while a visualization is shown; restore it
        (and its previous width) otherwise. Idempotent and safe to call before
        any viz exists."""
        left = getattr(self, "left_panel", None)
        splitter = getattr(self, "body_splitter", None)
        if left is None or splitter is None:
            return
        if self._current_widget_is_viz():
            if left.isVisible():
                self._pre_viz_splitter_sizes = splitter.sizes()
                left.setVisible(False)
        else:
            if not left.isVisible():
                left.setVisible(True)
                if self._pre_viz_splitter_sizes:
                    splitter.setSizes(self._pre_viz_splitter_sizes)
                    self._pre_viz_splitter_sizes = None
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.reposition()  # the editor dock under the toasts may have changed

    def _build_shell(self) -> QWidget:
        """Build the guided shell: one chrome row over the body (stretch) over
        the statusline. Returns the shell container.

        Presentation wrapper only — the body (and its four right_panel indices)
        is unchanged; the chrome row forwards to existing handlers.
        """
        from . import shell_chrome

        shell = QWidget()
        col = QVBoxLayout(shell)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        col.addWidget(shell_chrome.build_topbar(self))
        col.addWidget(self._build_body(), 1)
        col.addWidget(shell_chrome.build_statusline(self))

        # Populate the stage-aware action bar now that the body (and the real
        # stage buttons created in create_left_panel) exists; then show the
        # default Library stage.
        self._populate_action_bar()
        self._activate_action_stage("Library")
        return shell

    # Sentinel: presence signals the PR4 guided shell (app_stack) is wired, used
    # by the shell smoke test to gate the MainWindow contract assertions.
    _pr4_shell_ready = True

    def init_ui(self):
        # Guided app shell: a QStackedWidget that flips between the Welcome
        # screen (index 0) and the working shell/body (index 1). The window
        # starts on Welcome; load_images flips to the shell (T4.6).
        from app.gui import shell_chrome
        from app.gui.loading_overlay import LoadingOverlay
        from app.gui.welcome_screen import WelcomeWidget

        self.app_stack = QStackedWidget()
        self.welcome = WelcomeWidget(
            on_get_started=self.load_images,
            on_open_recent=self.open_path,
            recent_store=self.recent_projects,
            on_about=self.show_about,
        )
        self.app_stack.addWidget(self.welcome)  # index 0

        shell = self._build_shell()
        self.app_stack.addWidget(shell)  # index 1

        self.setCentralWidget(self.app_stack)
        self.app_stack.setCurrentIndex(0)

        # Full-window loading overlay (parented to the window), hidden until a
        # directory is chosen.
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()

        # One status surface: the statusline shows the QStatusBar's messages and
        # hosts the task progress inline; the QStatusBar itself stays (API kept,
        # hidden) so every existing showMessage() caller keeps working.
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.task_progress = TaskProgressWidget()
        self.status_bar.addPermanentWidget(self.task_progress)

        # Transient success/feedback toasts (bottom-right, above the statusline
        # and clear of an editor dock). Decisions keep using QMessageBox.
        from app.gui.widgets import ToastManager

        self.toasts = ToastManager(self)
        self.toasts.set_bottom_offset(self._toast_bottom_offset)

        shell_chrome.install_status_surface(self)
        # Handlers report progress / results / failures through this (AR-07).
        self.feedback = QtUiFeedback(
            self.task_progress,
            status_bar=self.status_bar,
            notifier=lambda message, kind: self.notify(message, kind),
            on_task_end=self.statusline.refresh_runtime,
        )

    def notify(self, message: str, kind: str = "success", timeout: int = 3200) -> None:
        """Show a transient toast. ``kind`` in success/info/warn/danger."""
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.show(message, kind=kind, timeout=timeout)

    def _toast_bottom_offset(self) -> int:
        """Pixels the toast stack keeps above the window's bottom edge: the
        statusline, or the current editor's dock when it sits under the toasts."""
        offset = 0
        line = getattr(self, "statusline", None)
        if line is not None and line.isVisible():
            offset = line.height()
        right_panel = getattr(self, "right_panel", None)
        if right_panel is None:
            return offset
        editor = self._editors().get(right_panel.currentIndex())
        dock = getattr(editor, "dock", None)
        if dock is None or not dock.isVisible():
            return offset
        from app.gui.widgets.toasts import MAX_TOAST_WIDTH

        top_left = dock.mapTo(self, QPoint(0, 0))
        toast_left = self.width() - min(MAX_TOAST_WIDTH, self.width()) - 16
        if top_left.x() + dock.width() > toast_left:
            offset = max(offset, self.height() - top_left.y() + _TOAST_DOCK_GAP)
        return offset

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

    def _go_to_stage(self, key: str) -> None:
        button = self.ribbon_buttons.get(key)
        if button is None or not button.isEnabled() or self.app_stack.currentIndex() != 1:
            return  # nothing loaded yet: the stages are not on screen
        button.click()

    def _current_stage(self) -> str | None:
        buttons = getattr(self, "ribbon_buttons", {}) or {}
        return next((k for k, b in buttons.items() if b.isChecked()), None)

    def show_shortcuts(self) -> None:
        """The F1 keyboard-shortcut sheet, opened on the current stage's keys."""
        dialog = ShortcutsDialog(
            self,
            current_scope=shortcuts.scope_for_stage(self._current_stage()),
            on_about=self.show_about,
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def show_about(self) -> None:
        """About SPROUTS: version, calibration, runtime, models and how to cite."""
        from app.gui.about_dialog import AboutDialog

        dialog = AboutDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    # ==================== Background jobs ====================

    def _on_jobs_busy_changed(self, busy: bool) -> None:
        """Disable the job buttons while a job runs; refresh measured values after one."""
        name = self.jobs.running_name() or "A task"
        for attr in JOB_BUTTONS:
            button = getattr(self, attr, None)
            if button is None:
                continue
            if busy:
                self._job_button_tooltips.setdefault(attr, button.toolTip())
                button.setEnabled(False)
                button.setToolTip(f"{name} is running. Wait for it, or press Cancel.")
            else:
                button.setEnabled(True)
                if attr in self._job_button_tooltips:
                    button.setToolTip(self._job_button_tooltips.pop(attr))
        if not busy and self._measurements is not None:
            # A Measure run may have rewritten the CSVs: re-locate and re-read them.
            self._measurements.invalidate()
            self._refresh_metrics_bar(self._current_image_name())

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

    # ==================== Image Loading ====================

    def load_images(self):
        """Open the directory dialog (at the last folder), then load the chosen folder."""
        start = self.window_state.last_folder() if self._persist_window_state else ""
        dir_name = QFileDialog.getExistingDirectory(self, "Select Image Directory", start)
        if dir_name:
            self.open_path(dir_name)

    def open_path(self, dir_name):
        """Load a directory of images (no dialog).

        Shared by ``load_images`` (after the dialog) and the Welcome screen's
        recent-projects rows. A missing folder is reported and changes nothing,
        and so does an editor that keeps unsaved edits. Otherwise flips to the
        working shell, shows the loading overlay, and kicks off
        ``image_manager.load_images``; the current library stays until the scan
        delivers images.
        """
        if not dir_name:
            return
        if not os.path.isdir(dir_name):
            self.notify(f"Folder not found: {dir_name}", "danger")
            self._refresh_recents()
            return
        # Pending edits: save or discard first; an editor the user keeps editing
        # (cancel, or a failed save) cancels the open. Asked before the overlay.
        for editor in self._editors().values():
            if not _released(editor):
                return
        # Stash for on_loading_finished so it can persist a recent entry.
        self._last_loaded_dir = dir_name

        # Leave the Welcome screen for the working shell and show the
        # full-window loading overlay (PR4 T4.6).
        self.app_stack.setCurrentIndex(1)
        self.loading_overlay.start("Loading images")

        # Show progress before loading.
        self.task_progress.start("Loading images")

        # The editors are clean now: drop their image and history (no prompt).
        for editor in self._editors().values():
            editor.reset()

        # Load images through image manager
        self.image_manager.load_images(dir_name)
        if self._persist_window_state:
            self.window_state.set_last_folder(dir_name)
        reset_viz = getattr(self.ribbon, "reset_visualize_selection", None)
        if callable(reset_viz):
            reset_viz()

    def update_loading_progress(self, value):
        """Update the loading progress bar"""
        self.task_progress.set_progress(value)
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.set_progress(value)

    def on_loading_finished(self, images, fake_images, masks, has_fake_real_pairs):
        """Show a freshly scanned folder: build the library, then drop the overlay."""
        self._shown_leaf_name = None
        layout = project_layout_of(self.image_manager)
        folder = layout.root if layout is not None else getattr(self, "_last_loaded_dir", None)
        self._measurements = MeasurementIndex(folder) if folder else None
        self.populate_file_list()
        reset = getattr(self.display_controller, "reset", None)
        if callable(reset):
            reset()  # never keep showing the previous folder's image
        self._refresh_metrics_bar(None)
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.hide()
        message = f"Loaded {count_label(len(images))}"
        self.task_progress.finish(message)
        self.notify(message)

        # Swap the index-0 display page from the empty state to the real display
        # area now that images exist.
        if images and getattr(self, "display_page_stack", None) is not None:
            self.display_page_stack.setCurrentIndex(0)

        # Persist a recent-projects entry for the Welcome screen (FIX2).
        if images:
            self._record_recent_project(len(images))
        self._prewarm_measurements()  # parse the CSVs before the first selection

    def on_loading_empty(self, folder):
        """The scanned folder has no images: warn and keep whatever was loaded."""
        name = os.path.basename(os.path.normpath(folder or "")) or folder
        self._end_failed_load(f"No .jpg/.png images in {name} — nothing loaded", "warn")

    def on_loading_failed(self, folder, message):
        """The folder could not be scanned: report it and keep whatever was loaded."""
        self._end_failed_load(message, "danger")

    def _end_failed_load(self, message, kind):
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.hide()
        if kind == "danger":
            self.task_progress.fail(message)
        else:
            self.task_progress.finish(message)
        self.notify(message, kind)
        if not self.image_manager.images and getattr(self, "app_stack", None) is not None:
            self.app_stack.setCurrentIndex(0)  # nothing to show: back to Welcome

    def _record_recent_project(self, count):
        """Record the just-loaded dir in the recent projects (MRU, capped)."""
        dir_name = getattr(self, "_last_loaded_dir", None)
        if not dir_name:
            return
        try:
            self.recent_projects.add(dir_name, count)
        except Exception:
            logger.exception("Failed to record recent project")
        self._refresh_recents()

    def _refresh_recents(self) -> None:
        welcome = getattr(self, "welcome", None)
        if welcome is None:
            return
        try:
            welcome.refresh_recents()
        except Exception:
            logger.exception("Failed to refresh the recent projects")

    # ==================== Tree Item Selection ====================

    def mask_exists(self, image_name):
        """True when ``image_name`` has a saved mask (kept in ``image_manager``, no stat)."""
        return self.image_manager.has_mask(image_name)

    def _image_has_mask(self, stored_name):
        """Return True if the image has a saved mask.

        ``image_manager`` is the single source of truth: filled by the folder scan,
        updated on ``mask_saved`` / ``mask_cleared`` and after generation runs.
        """
        return self.image_manager.has_mask(stored_name)

    def _image_has_skeleton(self, stored_name):
        """Return True if a processed ``<name>_fake.png`` skeleton is known for the image."""
        return self.image_manager.has_skeleton(stored_name)

    def _current_image_name(self) -> str | None:
        """Stem of the image selected in the library (else the one on the display)."""
        file_list = getattr(self, "file_list", None)
        item = file_list.currentItem() if file_list is not None else None
        name = item.data(0, USER_ROLE) if item is not None else None
        return name or getattr(self.display_controller, "current_name", None)

    def _refresh_metrics_bar(self, stored_name=None):
        """Update the read-only metrics strip for the selected image.

        Status comes from the mask / skeleton state in ``image_manager``. Root
        length / area are the values the Measure run wrote to its CSVs, read
        through :class:`MeasurementIndex` (never computed here, never parsed on
        the GUI thread: an unparsed CSV is parsed in the pool, then this runs again).
        """
        bar = getattr(self, "metrics_bar", None)
        if bar is None:
            return

        status = None
        if stored_name:
            if self._image_has_skeleton(stored_name):
                status = "Skeleton ready"
            elif self._image_has_mask(stored_name):
                status = "Mask ready"
            else:
                status = "No mask"

        measured, stale, source = Measurement(None, None, None), False, None
        index = self._measurements
        if stored_name and index is not None:
            try:
                measured = index.lookup(stored_name, load=False)
                if index.needs_load():
                    self._prewarm_measurements()
                stale = index.is_outdated(stored_name)
                sources = index.sources()
                source = sources.get("length") or sources.get("area")
            except Exception:
                logger.exception("Could not read the measured values of %s", stored_name)

        bar.set_metrics(
            length=measured.length_mm,
            area=measured.area_mm2,
            status=status,
            name=stored_name,
            error=measured.error,
            stale=stale,
            source=source,
        )

    def _prewarm_measurements(self) -> None:
        """Parse new / changed measurement CSVs in the thread pool (once at a time)."""
        index = self._measurements
        if index is None or self._measurements_loading or not index.needs_load():
            return
        self._measurements_loading = True
        signals = self._measurement_signals

        def work(index=index, signals=signals):
            try:
                index.prewarm()
            except Exception:
                logger.exception("Could not read the measurement CSVs")
            try:
                signals.ready.emit(index)
            except RuntimeError:
                pass  # the window is gone

        QThreadPool.globalInstance().start(work)

    def _on_measurements_ready(self, index) -> None:
        self._measurements_loading = False
        if index is self._measurements:
            self._refresh_metrics_bar(self._current_image_name())

    def generate_all(self):
        """Generate masks, then skeletons, as one user-visible operation.

        The skeleton pass starts only after a completed mask run (never after
        Cancel or a failure); a second click while a job runs is refused by the
        handler / job registry, so the chain is never doubled.
        """
        return self.mask_generation_handler.generate_masks(
            on_success=self.skeleton_handler.generate_skeleton
        )

    def on_tree_item_clicked(self, item, column):
        """Handle tree item clicks - expand/collapse folders, load images"""
        # Get the stored image name from the item's user data
        image_name = item.data(0, Qt.ItemDataRole.UserRole)

        # If item is not an image (it's a folder node), toggle expansion
        if image_name is None:
            item.setExpanded(not item.isExpanded())
            return

        # If it's an image, load it
        self.on_image_selected(item)

    def on_image_selected(self, item):
        """Load and display the selected image"""
        # Get the stored image name from the item's user data
        image_name = item.data(0, Qt.ItemDataRole.UserRole)

        # If item is not an image (it's a folder node), do nothing
        if image_name is None:
            return

        image_path = self.image_manager.get_image_path(image_name)
        if image_path is None:
            return
        self._shown_leaf_name = image_name

        current = self.right_panel.currentWidget()
        if current is self.correct_editor:
            self.correct_editor.load_image(
                image_path, images_base_folder=self.image_manager.original_folder
            )
            if self._kept_other_image(self.correct_editor, image_path):
                return
        elif current is self.gt_tracing_interface:
            self.gt_tracing_interface.load_image(image_path)
            if self._kept_other_image(self.gt_tracing_interface, image_path):
                return
        else:
            self.display_controller.display_selected_image_by_name(image_name)

        # Read-only: refresh the metrics strip for the now-selected image.
        self._refresh_metrics_bar(image_name)

    def _kept_other_image(self, editor, image_path: str) -> bool:
        """True when ``editor`` kept its open image (the user cancelled or its save
        failed): the library is pointed back at that image."""
        kept = getattr(editor, "current_image_path", None)
        if kept and not _same_path(kept, image_path):
            self._select_tree_image(kept)
            return True
        return False

    # ==================== Calculation Methods ====================

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
        self._refresh_metrics_bar(self._current_image_name())

    # ==================== The editors (Correct, Trace GT) ====================

    def toggle_correct(self) -> None:
        """Enter / leave the Correct stage (``Panel.CORRECT``), then show the selected image
        there (or in the Library). A leave the editor refuses (unsaved edits the user keeps)
        stays in it."""
        self._toggle_editor(Panel.CORRECT)

    def toggle_gt_tracing(self) -> None:
        """Enter / leave the multi-class GT editor (``Panel.GT_TRACING``), like Correct."""
        self._toggle_editor(Panel.GT_TRACING)

    def _toggle_editor(self, panel: Panel) -> None:
        # Status badges read image_manager at paint time: no recolour pass here
        # (a saved mask keeps its badge in every stage, F-016).
        target = panel if self.right_panel.currentIndex() != panel else Panel.DISPLAY
        if not self.switch_right_panel(target):
            return  # the editor being left kept unsaved edits: stay there
        current_item = self.file_list.currentItem()
        if current_item:
            self.on_image_selected(current_item)

    def on_b_key_status_changed(self, is_pressed):
        """B held on the Correct canvas: the status line says how to resize the brush."""
        if is_pressed:
            self.status_bar.showMessage("Scroll to resize the brush; release B when done", 3000)
        else:
            self.status_bar.clearMessage()

    def on_mask_saved(self, image_path):
        """A mask was written: record it for the Library badge and refresh the metrics.

        The editor's own summary toast says so; no second toast here.
        """
        # mask_saved -> highlight_saved_mask records the mask in image_manager.
        self.mask_saved.emit(image_path)
        self._refresh_metrics_if_current(image_stem(image_path))

    def on_mask_cleared(self, image_path):
        """An emptied mask removed the saved file: the badge and the metrics follow."""
        # mask_cleared -> unhighlight_cleared_mask forgets the mask in image_manager.
        self.mask_cleared.emit(image_path)
        self._refresh_metrics_if_current(image_stem(image_path))

    def _refresh_metrics_if_current(self, image_name):
        item = self.file_list.currentItem()
        if item is not None and item.data(0, USER_ROLE) == image_name:
            self._refresh_metrics_bar(image_name)

    def _image_leaves(self, *, visible_only: bool = False) -> list:
        """Image leaves of the library tree, in display order."""
        return self.library_index.leaves(visible_only=visible_only)

    def _leaf_for_image(self, image_path: str, leaves: list | None = None):
        """The leaf whose image is ``image_path`` (O(1)), or None.

        With ``leaves``, only a leaf among them counts.
        """
        if not image_path:
            return None
        name = image_stem(image_path)
        item = self.library_index.item_for(name)
        if item is None or not _same_path(self.image_manager.get_image_path(name), image_path):
            return None
        if leaves is not None and item not in leaves:
            return None
        return item

    def _select_tree_image(self, image_path: str) -> None:
        """Make ``image_path``'s leaf the tree's current item (without loading it)."""
        item = self._leaf_for_image(image_path)
        if item is not None:
            self._shown_leaf_name = item.data(0, USER_ROLE)  # it is already open
            self.file_list.setCurrentItem(item)
            self.file_list.scrollToItem(item)

    def navigate_image(self, delta: int) -> None:
        """Select and open the next/previous visible image leaf in the library tree.

        In an editor (Correct, Trace GT), steps from the image open in it; otherwise
        (or when nothing is open there) from the tree selection.
        """
        current = None
        editor = self._editors().get(self.right_panel.currentIndex())
        if editor is not None:
            current = self._leaf_for_image(editor.current_image_path)
        if current is None:
            current = self.file_list.currentItem()
        target = self.library_index.step(current, delta, visible_only=True)
        if target is None:
            return
        self.file_list.setCurrentItem(target)
        self.file_list.scrollToItem(target)
        self.on_image_selected(target)

    def on_tree_current_changed(self, current, _previous=None) -> None:
        """Arrow keys moved the library selection: load it once the keys come to rest."""
        if current is not None and current.data(0, USER_ROLE):
            self._tree_load_timer.start()

    def _load_current_tree_item(self) -> None:
        item = self.file_list.currentItem()
        name = item.data(0, USER_ROLE) if item is not None else None
        if name and name != self._shown_leaf_name:
            self.on_image_selected(item)

    def _install_image_paging_shortcuts(self) -> None:
        """PgDn / PgUp (registry: Global next/previous image) open the next / previous
        image from the library, the chrome and the display. The editor pages (Correct,
        Trace GT) bind the same keys themselves (they save first), so these are scoped
        to the widgets outside them instead of window-wide (no ambiguity)."""
        self._paging_shortcuts = []
        owners = (
            self.left_panel,
            self.ribbon,
            self.statusline,
            self.right_panel.widget(Panel.DISPLAY),
        )
        for owner in owners:
            installed = shortcuts.install_shortcuts(
                owner,
                shortcuts.GLOBAL,
                {
                    "next_image": lambda: self._page_image(1),
                    "previous_image": lambda: self._page_image(-1),
                },
            )
            self._paging_shortcuts += installed["next_image"] + installed["previous_image"]

    def _page_image(self, delta: int) -> None:
        editor = self._editors().get(self.right_panel.currentIndex())
        if editor is not None:
            # From the library while an editor is open: it saves before it moves.
            editor.navigate(delta)
        else:
            self.navigate_image(delta)

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

    # ==================== Panel Switching ====================

    def switch_right_panel(self, panel) -> bool:
        """Show ``panel`` (a :class:`Panel`, its index or its key); returns False
        (and stays put) if the editor being left refuses. An unknown key does nothing.

        Leaving the Correct or Trace GT editor first offers to save pending
        edits; the editor refuses when the user cancels, or when its save failed
        and the user kept editing.
        """
        try:
            target = Panel.coerce(panel)
        except ValueError:
            logger.warning("Unknown right panel %r", panel)
            return True
        current = self.right_panel.currentIndex()
        editor = self._editors().get(current)
        if editor is not None and target != current and not _released(editor):
            return False
        self.right_panel.setCurrentIndex(target)
        return True

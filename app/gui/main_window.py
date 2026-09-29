"""
Main window for the Root Viewer application.
Orchestrates the GUI components and handles core functionality.
"""

import logging
import os

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QIcon, QKeySequence, QShortcut
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

from app.gt import RegistryError, load_registry
from app.handlers.job_registry import JobRegistry
from app.naming import image_stem

from . import file_tree_manager, ui_panels, visualization_manager
from .display_controller import DisplayController
from .empty_state import ShortcutsDialog
from .gt_tracing_interface import GtTracingInterface
from .image_manager import ImageManager
from .library_index import count_label
from .mask_tracing_interface import MaskTracingInterface
from .panels import Panel
from .skeleton_correction_interface import SkeletonCorrectionInterface
from .task_progress import TaskProgressWidget

logger = logging.getLogger(__name__)

USER_ROLE = Qt.ItemDataRole.UserRole

#: Keyboard browsing in the library loads the image this long after the last move,
#: so holding an arrow key decodes only the image it stops on.
TREE_LOAD_DELAY_MS = 40


def _same_path(a: str | None, b: str | None) -> bool:
    """True when both are set and name the same file (case/separator-insensitive on Windows)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class _ProgressBarShim:
    """Compat facade exposing the legacy QProgressBar API on TaskProgressWidget.

    The status-bar QProgressBar was replaced by TaskProgressWidget. The mask and
    skeleton *generation* handlers still call the old bar API
    (setValue/show/hide/setFormat/setTextVisible); this shim maps those onto the
    new widget so they keep working unchanged.
    """

    def __init__(self, tp):
        self._tp = tp

    def setValue(self, v):
        self._tp.set_progress(v)

    def show(self):
        # Legacy callers call show() then setValue() repeatedly; start with a
        # generic op name so the labelled widget actually appears.
        if not self._tp.isVisible():
            self._tp.start("Working")

    def hide(self):
        self._tp.finish()

    def setFormat(self, *_a, **_k):
        pass  # text-on-bar disabled; the label carries the text now

    def setTextVisible(self, *_a, **_k):
        pass

    def setRange(self, *_a, **_k):
        pass

    def setMinimumWidth(self, *_a, **_k):
        pass


class MainWindow(QMainWindow):
    mask_saved = pyqtSignal(str)
    mask_cleared = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Root Viewer")
        self.setGeometry(100, 100, 1200, 700)

        # Set window icon with multiple sizes for better visibility
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "resources", "app_icon.ico"
        )
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            self.setWindowIcon(icon)

        # Initialize components.
        #
        # Handlers are imported lazily here rather than at module top to break a
        # circular import (F: mask_handler -> app.gui (package init) ->
        # main_window -> mask_handler). By the time __init__ runs, the app.gui
        # package is fully initialized, so these imports resolve cleanly and
        # `import app.handlers.mask_handler` succeeds standalone.
        from app.handlers.mask_generation_handler import MaskGenerationHandler
        from app.handlers.mask_handler import MaskHandler
        from app.handlers.skeleton_handler import SkeletonHandler

        # One background job at a time (generation / measurement); handlers register
        # their threads here, so it must exist before they are built.
        self.jobs = JobRegistry(self)
        self.image_manager = ImageManager(self)
        self.display_controller = DisplayController(self)
        self.skeleton_handler = SkeletonHandler(self)
        self.mask_handler = MaskHandler(self)
        self.mask_generation_handler = MaskGenerationHandler(self)
        self.mask_tracing_interface = MaskTracingInterface()
        # Multi-class ground-truth editor (Trace GT stage). Independent of the
        # ML pipeline: it reads/writes only gt/, never mask/ or skeletons/.
        self.gt_tracing_interface = GtTracingInterface()
        self.skeleton_correction_interface = SkeletonCorrectionInterface()
        self.root_length_viz = None
        self.root_area_viz = None

        # Tracks whether the mask-tracing interface signals are currently
        # connected, so repeated toggles do not stack duplicate connections
        # (F-015).
        self._mask_tracing_signals_connected = False

        # Connect the mask_saved and mask_cleared signals
        self.mask_saved.connect(self.highlight_saved_mask)
        self.mask_cleared.connect(self.unhighlight_cleared_mask)

        # Transient editor feedback (fill hints, clear/undo notes) -> toasts.
        self.mask_tracing_interface.notice.connect(self.notify)
        self.gt_tracing_interface.notice.connect(self.notify)
        self.gt_tracing_interface.navigate_requested.connect(self.navigate_image)
        self.gt_tracing_interface.gt_saved.connect(
            lambda p: self.notify(f"GT saved for {os.path.basename(p)}")
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

        # Keyboard-shortcut help dialog (F1 / ?).
        self._help_shortcut_f1 = QShortcut(QKeySequence("F1"), self)
        self._help_shortcut_f1.activated.connect(lambda: ShortcutsDialog(self).exec())
        self._help_shortcut_q = QShortcut(QKeySequence("?"), self)
        self._help_shortcut_q.activated.connect(lambda: ShortcutsDialog(self).exec())

    @property
    def loading_progress_bar(self):
        """Legacy QProgressBar facade backed by the TaskProgressWidget.

        Lets the mask/skeleton generation handlers keep calling the old bar API
        unchanged while the nicer status-bar widget is what actually renders.
        """
        return _ProgressBarShim(self.task_progress)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop Dash server threads before the window (and embedded viz widgets) are destroyed.

        Unsaved GT the user chose to keep (its save failed) cancels the close.
        """
        self.mask_tracing_interface.maybe_save_changes()
        if not self.gt_tracing_interface.maybe_save_changes():
            event.ignore()
            return
        try:
            if self.root_length_viz is not None:
                self.close_root_length_visualization()
            if self.root_area_viz is not None:
                self.close_root_area_visualization()
        except Exception:
            logger.exception("Error cleaning up visualizations on close")
        QApplication.processEvents()
        super().closeEvent(event)

    def _build_body(self) -> QWidget:
        """Build the left/right splitter body (presentation extract of init_ui).

        Returns a container widget holding the QSplitter. PRESERVES the
        ``right_panel.addWidget`` calls in their fixed index order
        (0=display, 1=mask_tracing, 2=visualization, 3=skeleton_correction,
        4=gt_tracing).
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

        # Right Panel (Stacked Widget for Display Area and Mask Tracing)
        self.right_panel = QStackedWidget()
        self.right_panel.addWidget(ui_panels.create_right_panel(self))
        self.right_panel.addWidget(self.mask_tracing_interface)
        self.visualization_widget = QWidget()  # Placeholder for visualization
        self.right_panel.addWidget(self.visualization_widget)
        self.right_panel.addWidget(self.skeleton_correction_interface)
        self.right_panel.addWidget(self.gt_tracing_interface)
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
        if callable(getattr(self, "_populate_action_bar", None)):
            self._populate_action_bar()
        if callable(getattr(self, "_activate_action_stage", None)):
            self._activate_action_stage("Library")
        return shell

    # Sentinel: presence signals the PR4 guided shell (app_stack) is wired, used
    # by the shell smoke test to gate the MainWindow contract assertions.
    _pr4_shell_ready = True

    def init_ui(self):
        # Guided app shell: a QStackedWidget that flips between the Welcome
        # screen (index 0) and the working shell/body (index 1). The window
        # starts on Welcome; load_images flips to the shell (T4.6).
        from app.gui.loading_overlay import LoadingOverlay
        from app.gui.welcome_screen import WelcomeWidget

        self.app_stack = QStackedWidget()
        self.welcome = WelcomeWidget(on_get_started=self.load_images, on_open_recent=self.open_path)
        self.app_stack.addWidget(self.welcome)  # index 0

        shell = self._build_shell()
        self.app_stack.addWidget(shell)  # index 1

        self.setCentralWidget(self.app_stack)
        self.app_stack.setCurrentIndex(0)

        # Full-window loading overlay (parented to the window), hidden until a
        # directory is chosen.
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()

        # Status Bar with first-class task-progress widget.
        # Replaces the old cramped status-bar QProgressBar (text-on-bar). The
        # legacy loading_progress_bar API is preserved via the _ProgressBarShim
        # exposed by the loading_progress_bar property, so generation handlers
        # are unchanged.
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.task_progress = TaskProgressWidget()
        self.status_bar.addPermanentWidget(self.task_progress)

        # Transient success/feedback toasts (bottom-right of the window). Hard
        # errors keep using QMessageBox.critical; this is additive.
        from app.gui.widgets import ToastManager

        self.toasts = ToastManager(self)

    def notify(self, message: str, kind: str = "success", timeout: int = 3200) -> None:
        """Show a transient toast. ``kind`` in success/info/warn/danger."""
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.show(message, kind=kind, timeout=timeout)

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
            ("mask tracing", getattr(self.mask_tracing_interface, "graphics_view", None)),
            ("GT tracing", getattr(self.gt_tracing_interface, "graphics_view", None)),
            (
                "skeleton correction",
                getattr(self.skeleton_correction_interface, "graphics_view", None),
            ),
        )
        for label, view in views:
            if view is None or not hasattr(view, "set_opengl_viewport_enabled"):
                continue
            try:
                view.set_opengl_viewport_enabled(enabled)
            except RuntimeError as e:
                logger.warning("Failed to toggle OpenGL on %s view: %s", label, e)

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
        """Open the directory dialog, then load the chosen folder."""
        dir_name = QFileDialog.getExistingDirectory(self, "Select Image Directory")
        if dir_name:
            self.open_path(dir_name)

    def open_path(self, dir_name):
        """Load a directory of images (no dialog).

        Shared by ``load_images`` (after the dialog) and the Welcome screen's
        recent-projects rows. A missing folder is reported and changes nothing.
        Otherwise flips to the working shell, shows the loading overlay, and
        kicks off ``image_manager.load_images``; the current library stays until
        the scan delivers images.
        """
        if not dir_name:
            return
        if not os.path.isdir(dir_name):
            self.notify(f"Folder not found: {dir_name}", "danger")
            return
        # Unsaved GT the user chose to keep (its save failed) cancels the open.
        gt = getattr(self, "gt_tracing_interface", None)
        if gt is not None and not gt.maybe_save_changes():
            return
        # Stash for on_loading_finished so it can persist a recent entry.
        self._last_loaded_dir = dir_name

        # Leave the Welcome screen for the working shell and show the
        # full-window loading overlay (PR4 T4.6).
        if getattr(self, "app_stack", None) is not None:
            self.app_stack.setCurrentIndex(1)
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.start("Loading images")

        # Show progress before loading.
        self.task_progress.start("Loading images")

        # Offer to save pending mask / GT edits, then drop the editors' history.
        if hasattr(self, "mask_tracing_interface"):
            self.mask_tracing_interface.reset()
        if hasattr(self, "gt_tracing_interface"):
            self.gt_tracing_interface.reset()

        # Load images through image manager
        self.image_manager.load_images(dir_name)

    def update_loading_progress(self, value):
        """Update the loading progress bar"""
        self.task_progress.set_progress(value)
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.set_progress(value)

    def on_loading_finished(self, images, fake_images, masks, has_fake_real_pairs):
        """Show a freshly scanned folder: build the library, then drop the overlay."""
        self._shown_leaf_name = None
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
        """Append the just-loaded dir to QSettings recent_projects (MRU, cap 8)."""
        dir_name = getattr(self, "_last_loaded_dir", None)
        if not dir_name:
            return
        try:
            import json
            from datetime import datetime

            from PyQt6.QtCore import QSettings

            settings = QSettings("LeakeyLab", "SPROUTS")
            raw = settings.value("recent_projects", "[]")
            try:
                entries = json.loads(raw) if isinstance(raw, str) else list(raw)
            except (ValueError, TypeError):
                entries = []
            if not isinstance(entries, list):
                entries = []

            entry = {
                "name": os.path.basename(os.path.normpath(dir_name)),
                "path": dir_name,
                "count": int(count),
                "ts": datetime.now().isoformat(),
            }
            # Dedup by path, most-recent-first, cap 8.
            entries = [e for e in entries if isinstance(e, dict) and e.get("path") != dir_name]
            entries.insert(0, entry)
            settings.setValue("recent_projects", json.dumps(entries[:8]))
        except Exception:
            logger.exception("Failed to record recent project")

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

    def _refresh_metrics_bar(self, stored_name=None):
        """Update the read-only metrics strip for the selected image.

        Pure presentation (Track M). Status is derived from existing on-disk /
        in-memory mask + skeleton state. Measured root length/area are not
        retained per-image at selection time (they live only in the batch CSV
        outputs), so they are passed as ``None`` rather than computed here.
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

        bar.set_metrics(length=None, area=None, status=status, name=stored_name)

    def generate_all(self):
        """Generate masks, then skeletons, as one user-visible operation.

        Mask generation runs on a QThread, so the skeleton pass is queued on its
        ``finished`` signal and starts only once masks exist on disk. The
        connection is one-shot — a later solo mask run must not re-trigger it.
        """
        self.mask_generation_handler.generate_masks()
        thread = getattr(self.mask_generation_handler, "generation_thread", None)
        if thread is None:
            return  # guard tripped (no directory / already running)

        def _then_skeleton(*_args):
            try:
                thread.finished.disconnect(_then_skeleton)
            except TypeError:
                pass
            self.skeleton_handler.generate_skeleton()

        thread.finished.connect(_then_skeleton)

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

        if self.right_panel.currentWidget() == self.mask_tracing_interface:
            self.mask_tracing_interface.load_image(image_path)
        elif self.right_panel.currentWidget() == self.gt_tracing_interface:
            gt = self.gt_tracing_interface
            gt.load_image(image_path)
            if gt.current_image_path and not _same_path(gt.current_image_path, image_path):
                # The editor kept its open image (its save failed and the user
                # kept editing): point the library back at that image.
                self._select_tree_image(gt.current_image_path)
                return
        elif self.right_panel.currentWidget() == self.skeleton_correction_interface:
            self.skeleton_correction_interface.load_image(
                image_path, images_base_folder=self.image_manager.original_folder
            )
        else:
            self.display_controller.display_selected_image_by_name(image_name)

        # Read-only: refresh the metrics strip for the now-selected image.
        self._refresh_metrics_bar(image_name)

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
        item = self.file_list.currentItem()
        name = item.data(0, USER_ROLE) if item is not None else None
        self._refresh_metrics_bar(name or getattr(controller, "current_name", None))

    # ==================== Mask Tracing ====================

    def toggle_mask_tracing(self):
        # Status badges read image_manager at paint time: no recolour pass here
        # (a saved mask keeps its badge in every stage, F-016).
        if self.right_panel.currentIndex() != Panel.MASK_TRACING:
            if not self.switch_right_panel("mask_tracing"):
                return  # the GT editor kept unsaved edits: stay there
            self.toggle_mask_tracing_button.setText("Return to Main View")

            # Connect signals for mask tracing interface.
            # Guarded so repeated enter->exit->enter cycles do not stack up
            # duplicate connections (F-015): each slot would otherwise fire
            # multiple times per signal because the exit branch removes only one
            # connection per disconnect call.
            if not self._mask_tracing_signals_connected:
                self.mask_tracing_interface.mask_saved.connect(self.on_mask_saved)
                self.mask_tracing_interface.mask_cleared.connect(self.on_mask_cleared)
                self.mask_tracing_interface.b_key_status_changed.connect(
                    self.on_b_key_status_changed
                )
                self._mask_tracing_signals_connected = True
        else:  # If in mask tracing view, switch back to main view
            self.switch_right_panel("display")
            self.toggle_mask_tracing_button.setText("✏️ Toggle Mask Tracing")

            # Disconnect signals (mirrors the guarded connect above, F-015).
            if self._mask_tracing_signals_connected:
                self.mask_tracing_interface.mask_saved.disconnect(self.on_mask_saved)
                self.mask_tracing_interface.mask_cleared.disconnect(self.on_mask_cleared)
                self.mask_tracing_interface.b_key_status_changed.disconnect(
                    self.on_b_key_status_changed
                )
                self._mask_tracing_signals_connected = False

        # Load the current image into the mask tracing interface
        current_item = self.file_list.currentItem()
        if current_item:
            self.on_image_selected(current_item)

    def on_b_key_status_changed(self, is_pressed):
        """Observer method for B key status changes in mask tracing interface."""
        if is_pressed:
            self.status_bar.showMessage("Scroll to resize the brush; release B when done", 3000)
        else:
            self.status_bar.clearMessage()

    def on_mask_saved(self, image_path):
        # mask_saved -> highlight_saved_mask records the mask in image_manager.
        self.mask_saved.emit(image_path)
        image_name = image_stem(image_path)
        if self.right_panel.currentWidget() == self.mask_tracing_interface:
            self.status_bar.showMessage(f"Mask saved for {image_name}", 3000)
            self.notify(f"Mask saved for {image_name}")
        self._refresh_metrics_if_current(image_name)

    def on_mask_cleared(self, image_path):
        # mask_cleared -> unhighlight_cleared_mask forgets the mask in image_manager.
        self.mask_cleared.emit(image_path)
        self._refresh_metrics_if_current(image_stem(image_path))

    def _refresh_metrics_if_current(self, image_name):
        item = self.file_list.currentItem()
        if item is not None and item.data(0, USER_ROLE) == image_name:
            self._refresh_metrics_bar(image_name)

    # ==================== Ground-truth Tracing (Trace GT) ====================

    def toggle_gt_tracing(self) -> None:
        """Enter / leave the multi-class GT editor (right panel index 4)."""
        if self.right_panel.currentIndex() != Panel.GT_TRACING:
            # Leaving mask tracing: go through its toggle so its signals are
            # disconnected.
            if self.right_panel.currentIndex() == Panel.MASK_TRACING:
                self.toggle_mask_tracing()
            self.switch_right_panel("gt_tracing")
        elif not self.switch_right_panel("display"):
            return  # unsaved GT the user kept: stay in the editor

        current_item = self.file_list.currentItem()
        if current_item:
            self.on_image_selected(current_item)

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

        In Trace GT, steps from the image open in the editor; otherwise (or when
        nothing is open there) from the tree selection.
        """
        current = None
        if self.right_panel.currentIndex() == Panel.GT_TRACING:
            current = self._leaf_for_image(self.gt_tracing_interface.current_image_path)
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
        """PgDn / PgUp open the next / previous image from the library and from the
        display, mask and skeleton pages (Trace GT binds its own, which save first)."""
        self._paging_shortcuts = []
        owners = (
            self.left_panel,
            self.right_panel.widget(Panel.DISPLAY),
            self.mask_tracing_interface,
            self.skeleton_correction_interface,
        )
        for owner in owners:
            for key, delta in ((Qt.Key.Key_PageDown, 1), (Qt.Key.Key_PageUp, -1)):
                shortcut = QShortcut(QKeySequence(key), owner)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(lambda d=delta: self._page_image(d))
                self._paging_shortcuts.append(shortcut)

    def _page_image(self, delta: int) -> None:
        if self.right_panel.currentIndex() == Panel.GT_TRACING:
            # From the library while tracing GT: the editor saves before it moves.
            self.gt_tracing_interface._navigate(delta)
        else:
            self.navigate_image(delta)

    def export_gt_dataset(self) -> None:
        """Open the dataset-export dialog for the loaded folder's GT tracings."""
        folder = getattr(self.image_manager, "original_folder", None)
        if not folder:
            self.notify("Load an image folder first", "warn")
            return
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

    # ==================== Skeleton Correction (isolated stream) ====================

    def toggle_skeleton_correction(self):
        """
        Toggle the Skeleton Correction editor view.

        Important: This editor is intentionally isolated from the app's existing
        skeleton generation + main display streams.
        """
        if self.right_panel.currentIndex() != Panel.SKELETON_CORRECTION:
            # If leaving mask tracing, ensure its signals are disconnected cleanly
            if self.right_panel.currentIndex() == Panel.MASK_TRACING:
                self.toggle_mask_tracing()

            if not self.switch_right_panel("skeleton_correction"):
                return  # the GT editor kept unsaved edits: stay there
            if hasattr(self, "toggle_skeleton_correction_button"):
                self.toggle_skeleton_correction_button.setText("Return to Main View")
        else:
            self.switch_right_panel("display")
            if hasattr(self, "toggle_skeleton_correction_button"):
                self.toggle_skeleton_correction_button.setText("✏️ Skeleton Correction")

        # Load the current image into the skeleton correction interface (if any)
        current_item = self.file_list.currentItem()
        if current_item:
            self.on_image_selected(current_item)

    # ==================== Panel Switching ====================

    def switch_right_panel(self, panel) -> bool:
        """Show ``panel`` (a :class:`Panel`, its index or its legacy key); returns False
        (and stays put) if the GT editor refuses. An unknown key does nothing.

        Leaving the mask / GT editor first offers to save pending edits. The
        GT editor refuses when its save failed and the user kept editing.
        """
        try:
            target = Panel.coerce(panel)
        except ValueError:
            logger.warning("Unknown right panel %r", panel)
            return True
        current = self.right_panel.currentIndex()
        if current == Panel.MASK_TRACING and target != Panel.MASK_TRACING:
            self.mask_tracing_interface.maybe_save_changes()
        if current == Panel.GT_TRACING and target != Panel.GT_TRACING:
            if not self.gt_tracing_interface.maybe_save_changes():
                return False
        self.right_panel.setCurrentIndex(target)
        return True

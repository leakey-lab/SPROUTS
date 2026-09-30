"""
Main window of SPROUTS.
Orchestrates the GUI components and handles core functionality.
"""

import logging
import os

from PyQt6.QtCore import QObject, QSettings, Qt, QThreadPool, QTimer, pyqtSignal
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

from app import APP_NAME
from app.config import ROOT_AREAS_CSV, ROOT_LENGTHS_CSV
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.gt import RegistryError, load_registry
from app.handlers.job_registry import JobRegistry
from app.naming import image_stem

from . import (
    file_tree_manager,
    shell_chrome,
    shortcuts,
    skeleton_graph_model,
    ui_panels,
    visualization_manager,
)
from .canvas_key_relay import CanvasKeyRelay
from .correct.editor import CorrectEditor
from .device_check import DeviceCheck
from .display_controller import DisplayController
from .empty_state import ShortcutsDialog
from .gt_tracing_interface import GtTracingInterface
from .image_manager import ImageManager, project_layout_of
from .library_index import count_label
from .panels import Panel
from .recent_projects import RecentProjectsStore
from .stage_status import StageStatus
from .task_progress import TaskProgressWidget
from .toast_clearance import FLOATING_GAP, ToastClearance
from .ui_feedback import QtUiFeedback
from .visualize_empty_state import VisualizeEmptyState
from .window_state import WindowStateStore

logger = logging.getLogger(__name__)

USER_ROLE = Qt.ItemDataRole.UserRole

#: Keyboard browsing in the library loads the image this long after the last move,
#: so holding an arrow key decodes only the image it stops on.
TREE_LOAD_DELAY_MS = 40

#: The first project of a session imports the dashboards' libraries (dash, plotly, pandas)
#: on a pool thread this long after it has loaded: about 1 s off the first dashboard
#: open, ~100 MiB (PKG-VIZ measurements), and out of the way of the library scan.
DASHBOARD_PREWARM_DELAY_MS = 2000

#: Buttons that start a batch job; disabled (with a tooltip saying why) while one runs.
JOB_BUTTONS = (
    "generate_all_button",
    "generate_button",
    "calculate_length_button",
    "calculate_area_button",
)

#: The LENGTH tooltip's line for an image with a skeleton correction (FR-03).
CORRECTION_NOT_MEASURED = (
    "A corrected skeleton exists (skeleton_corrections/); it is not measured: "
    "Measure reads the generated skeleton."
)
#: The STATUS tooltip of an image with a skeleton correction (W17, display only).
CORRECTED_STATUS_TIP = (
    "Overlay and Side by side draw the corrected skeleton (skeleton_corrections/); "
    "Measure reads the generated skeleton (skeletons/), so root length is the generated one's."
)

#: The one toast of a folder load (loaded / empty / failed): a newer load replaces it.
_LOAD_TOAST = "project-load"

#: The editors' floating overlays in the bottom row, under the toast column.
_EDITOR_CHROME = ("dock", "zoom_cluster")
#: The editors' side panels (Correct's Layers, Trace GT's Classes), right of the toast column.
_EDITOR_SIDE_CHROME = ("layer_panel", "class_panel")
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

    def __init__(self, *, settings: QSettings | None = None, detect_device: bool | None = None):
        """Build the window.

        ``settings`` (optional) is where window geometry, the last folder and the
        recent projects persist; by default the app's own QSettings. Headless
        (``offscreen``) runs without explicit settings neither restore nor save
        the window geometry / last folder, so test runs never touch the user's.

        ``detect_device``: resolve the inference device in the thread pool once the
        window first shows, for the status line's device chip (see
        :class:`~app.gui.device_check.DeviceCheck`). Default: on, except headless.
        """
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}[*]")  # "<project> — SPROUTS" once a folder loads

        self.window_state = WindowStateStore(settings)
        self.recent_projects = RecentProjectsStore(settings)
        self._persist_window_state = (
            settings is not None or QApplication.platformName() != "offscreen"
        )
        self._measurements: MeasurementIndex | None = None
        self._measurements_loading = False
        self._measurement_signals = _MeasurementSignals()
        self._measurement_signals.ready.connect(self._on_measurements_ready)
        # What Generate / Measure would work on (the status line's idle text, W12).
        self.stage_status = StageStatus(self)
        self.stage_status.changed.connect(self._refresh_idle_text)
        self._job_button_tooltips: dict[str, str] = {}
        if detect_device is None:
            detect_device = QApplication.platformName() != "offscreen"
        # Headless runs (tests) never import the dashboards' libraries behind a test's back.
        self._dashboards_prewarmed = QApplication.platformName() == "offscreen"

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

        # Transient editor feedback (fill hints, clear/undo notes, saves) -> toasts;
        # unsaved edits -> the title's modified marker; PgDn / PgUp after a save.
        for editor in self._editors().values():
            editor.notice.connect(self.notify)
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

        # scikit-image (the skeleton's thinning, ~0.2 s to import) loads in the thread pool
        # once the event loop runs, i.e. after main.py showed the window: neither the
        # start-up nor the first Correct open waits for it.
        QTimer.singleShot(0, self._prewarm_skeletonize)
        # The device chip: resolved in the pool once the window shows (torch loads there).
        self.device_check = (
            DeviceCheck(self, self.statusline, settings=settings) if detect_device else None
        )

    def _prewarm_skeletonize(self) -> None:
        """Start the scikit-image import in the thread pool (never on the GUI thread)."""
        QThreadPool.globalInstance().start(_prewarm_skeletonize_logged)

    def _wire_library_order(self) -> None:
        """The Library display and the Correct page read the next images ahead in library
        order, and the Correct page names the open image's place ("Image 12 / 40")."""
        index = self.library_index
        self.display_controller.set_neighbour_provider(index.neighbours)
        for setter, provider in (
            ("set_neighbour_provider", index.neighbours),
            ("set_position_provider", index.position_of),
        ):
            method = getattr(self.correct_editor, setter, None)
            if callable(method):  # a Correct page without read-ahead keeps working
                method(provider)

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

    def _editor_canvas(self):
        """``(canvas, shortcut scope)`` of the editor page on screen, else None."""
        editor = self._editors().get(self.right_panel.currentIndex())
        view = getattr(editor, "graphics_view", None)
        scope = getattr(editor, "SHORTCUT_SCOPE", None)
        return (view, scope) if view is not None and scope else None

    def _sync_window_modified(self, *_args) -> None:
        """Title marker ``*`` while any editor holds unsaved edits."""
        self.setWindowModified(any(e.has_unsaved_changes() for e in self._editors().values()))

    def _on_editor_dirty_changed(self, *_args) -> None:
        """An editor's unsaved state changed: the title marker follows, and a save in
        Correct may have written the open image's skeleton correction."""
        self._sync_window_modified()
        self._sync_open_correction()

    def _sync_open_correction(self) -> None:
        """Keep the Library's "corrected" state of the image open in Correct in step with
        the disk (W17, display only: Measure reads the generated skeleton, FR-03).

        Correct's ``skeleton_saved`` stays unconnected (FR-03), so this runs when the
        editor's unsaved state changes (a save) and whenever another image is selected:
        one ``os.path.isfile`` of that image's correction.
        """
        manager = self.image_manager
        path = getattr(self.correct_editor, "current_image_path", "")
        set_correction = getattr(manager, "set_correction", None)
        if not path or not callable(set_correction):
            return
        stem = image_stem(path)
        layout = project_layout_of(manager)
        if layout is None or manager.get_image_path(stem) is None:
            return
        present = os.path.isfile(layout.corrected_skeleton_path(stem))
        if present != manager.has_correction(stem):
            set_correction(stem, present)
            self.library_index.refresh_status(stem)

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
        # Panel.VISUALIZATION: shown while a dashboard opens, or when it has no CSV yet
        # (then it names the next step, with a button to that stage).
        self.visualization_widget = VisualizeEmptyState()
        self.visualization_widget.stage_requested.connect(self._go_to_stage)
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
        self.right_panel.currentChanged.connect(self._show_selection_on_display)
        return body

    def _show_selection_on_display(self, index: int) -> None:
        """The display page is back on screen (a stage left an editor or a dashboard): it
        shows the library's selection with its metrics. The editors move the selection on
        while it is hidden (PgDn, a clicked row), and may have saved a correction, so its
        last image could sit under another image's name and values."""
        if index != Panel.DISPLAY:
            return
        item = self.file_list.currentItem()
        name = item.data(0, USER_ROLE) if item is not None else None
        if not name or self.image_manager.get_image_path(name) is None:
            return  # nothing selected, or a row of the folder being replaced
        self._shown_leaf_name = name
        self._sync_open_correction()  # a correction saved in Correct shows in the overlay
        self.display_controller.display_selected_image_by_name(name)
        self._refresh_metrics_bar(name)

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

        # Loading card (parented to the window, below the top bar), hidden until a
        # directory is chosen.
        self.loading_overlay = LoadingOverlay(
            self, top_offset=lambda: self.ribbon.height() if self.ribbon.isVisible() else 0
        )
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
        # The stack keeps clear of the bottom chrome and moves when that chrome does (a
        # page switch shows the metrics bar, an editor lifts its zoom cluster).
        self.toast_clearance = ToastClearance(
            self, self.toasts, self._bottom_chrome, side=self._side_chrome
        )
        self.toast_clearance.watch(
            self.statusline,
            self.metrics_bar,
            *(
                getattr(e, name, None)
                for e in self._editors().values()
                for name in (*_EDITOR_CHROME, *_EDITOR_SIDE_CHROME)
            ),
        )

        shell_chrome.install_status_surface(self)
        # Handlers report progress / results / failures through this (AR-07).
        self.feedback = QtUiFeedback(
            self.task_progress,
            status_bar=self.status_bar,
            notifier=lambda message, kind: self.notify(message, kind),
            on_task_end=self.statusline.refresh_runtime,
        )

    def notify(
        self, message: str, kind: str = "success", timeout: int = 3200, *, key: str | None = None
    ) -> None:
        """Show a transient toast. ``kind`` in success/info/warn/danger; a ``key`` updates the
        live toast of that key instead of stacking another."""
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            toasts.show(message, kind=kind, timeout=timeout, key=key)

    def _toast_bottom_offset(self) -> int:
        """Pixels the toast stack keeps above the window's bottom edge (see
        :class:`~app.gui.toast_clearance.ToastClearance`)."""
        return self.toast_clearance.offset()

    def _bottom_chrome(self) -> list[tuple]:
        """``(widget, gap)`` of the chrome the toasts must not cover: the statusline, the
        Library's metrics bar, the current editor's dock and zoom cluster (hidden ones are
        skipped by the caller)."""
        chrome = [(getattr(self, "statusline", None), 0), (getattr(self, "metrics_bar", None), 0)]
        right_panel = getattr(self, "right_panel", None)
        editor = None
        if right_panel is not None:
            editor = self._editors().get(right_panel.currentIndex())
        for name in _EDITOR_CHROME:
            chrome.append((getattr(editor, name, None), FLOATING_GAP))
        return chrome

    def _side_chrome(self) -> list[tuple]:
        """``(widget, gap)`` of the current editor's side panel: the toasts keep their corner
        while they fit below it, else they move to its left."""
        right_panel = getattr(self, "right_panel", None)
        editor = None
        if right_panel is not None:
            editor = self._editors().get(right_panel.currentIndex())
        return [(getattr(editor, name, None), FLOATING_GAP) for name in _EDITOR_SIDE_CHROME]

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
        # WA_DeleteOnClose: exec() deletes the dialog before it returns, so it must not
        # be touched afterwards (a deleteLater() here raised on every close, RB-16).
        dialog.exec()

    def show_about(self) -> None:
        """About SPROUTS: version, calibration, runtime, models and how to cite."""
        from app.gui.about_dialog import AboutDialog

        AboutDialog(self).exec()  # WA_DeleteOnClose: exec() deletes it (RB-16)

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
        if not busy and self._current_stage() == "Measure":
            self._check_freshness()  # a Measure run rewrote a CSV
        page = getattr(self, "visualization_widget", None)
        if not busy and page is not None and page.is_blocked():
            # Visualize said why its dashboard could not open, and a run just ended (it may
            # have written the CSV, or it was the run it waited for): try again.
            if self.right_panel.currentIndex() == Panel.VISUALIZATION:
                self.show_dashboard(self.ribbon.visualize_kind())

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

    def show_dashboard(self, kind: str) -> None:
        """The Visualize stage: the ``kind`` dashboard ("length" / "area").

        Without its CSV (nothing measured yet) the stage page names the next step, with a
        button to it, instead of a message box (W5); otherwise the dashboard is built or
        brought forward (see :mod:`app.gui.visualization_manager`).
        """
        missing = self._missing_dashboard_data(kind)
        if missing is not None:
            title, body, detail, stage = missing
            if self.switch_right_panel(Panel.VISUALIZATION):
                self.visualization_widget.show_missing(
                    title, body, detail, stage=stage, action=f"Go to {stage}" if stage else ""
                )
            return
        self.visualization_widget.show_opening()
        getattr(self, f"show_root_{kind}_visualization")()

    def _missing_dashboard_data(self, kind: str) -> tuple[str, str, str, str | None] | None:
        """``(title, next step, expected CSV, stage)`` when the ``kind`` dashboard cannot open
        yet (no images, a run rewriting its data, or no CSV); None when it can, or is open
        already (the dashboard controller then decides)."""
        spec = getattr(visualization_manager, "SPECS", {}).get(kind)
        if spec is None or getattr(self, spec.attr, None) is not None:
            return None
        layout = project_layout_of(self.image_manager)
        if layout is None or not self.image_manager.images:
            return "No images loaded", "Open a folder of root images first (Ctrl+O).", "", None
        noun = "root length" if kind == "length" else "root area"
        is_busy = getattr(getattr(self, "skeleton_handler", None), "is_busy", None)
        if callable(is_busy) and is_busy(kind):  # a run rewrites what it would chart
            running = self.jobs.running_name() or "A run"
            return f"{running} is running", f"The {noun} dashboard opens when it ends.", "", None
        try:
            if spec.find_csv(layout).path:
                return None
        except Exception:  # noqa: BLE001 - the dashboard controller reports it then
            logger.exception("Could not look for the %s CSV", kind)
            return None
        manager = self.image_manager
        if kind == "length":
            inputs, made = manager.skeleton_stems, "skeletons"
            button, csv_path = "Root Length", os.path.join(layout.skeletons_dir, ROOT_LENGTHS_CSV)
        else:
            inputs, made = manager.masks, "masks"
            masks_dir = layout.resolve_masks_dir() or layout.mask_dir
            button, csv_path = "Root Area", os.path.join(masks_dir, ROOT_AREAS_CSV)
        if inputs:
            stage, step = "Measure", f"Run Measure → {button} to chart the {noun} of this trial."
        else:
            stage, step = "Generate", f"Generate {made} first, then run Measure → {button}."
        return f"No {noun} measurements yet", step, f"Looked for {csv_path}", stage

    def on_stage_changed(self, key: str) -> None:
        """The highlighted stage changed (any path): the status line says what Generate or
        Measure would work on (W12), and the dashboards are told, when they want to know
        (they are released a while after Visualize is left)."""
        if key == "Measure":
            self._check_freshness()
        self._refresh_idle_text()
        changed = getattr(visualization_manager, "stage_changed", None)
        if callable(changed):
            try:
                changed(self, key)
            except Exception:  # noqa: BLE001 - a stage switch must never fail on this
                logger.exception("The dashboards could not follow the stage %r", key)

    def stage_summary(self) -> str | None:
        """What a run of the stage on screen would work on: the status line's idle text on
        Generate and Measure (see :class:`~app.gui.stage_status.StageStatus`); None on the
        other stages (W12)."""
        return self.stage_status.text(self._current_stage(), self.image_manager)

    def _check_freshness(self) -> None:
        """Count Measure's inputs written after each CSV, in the thread pool (W12)."""
        self.stage_status.check(
            self._measurements,
            project_layout_of(self.image_manager),
            locate=not self._measurements_loading,  # never wait for a parse in the pool
        )

    def _refresh_idle_text(self) -> None:
        line = getattr(self, "statusline", None)
        if line is not None:
            line.refresh_idle()

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
            self.notify(f"Folder not found: {dir_name}", "danger", key=_LOAD_TOAST)
            self._refresh_recents()
            return
        # Pending edits: save or discard first; an editor the user keeps editing
        # (cancel, or a failed save) cancels the open. Asked before the overlay.
        for editor in self._editors().values():
            if not _released(editor):
                return
        # Stash for on_loading_finished so it can persist a recent entry.
        self._last_loaded_dir = dir_name

        # Leave the Welcome screen for the working shell and show the loading card
        # (PR4 T4.6), naming the folder: a large network folder can take a while.
        self.app_stack.setCurrentIndex(1)
        name = os.path.basename(os.path.normpath(dir_name)) or dir_name
        self.loading_overlay.set_filename(name, tooltip=dir_name)
        self.loading_overlay.start("Loading images")
        self.empty_state.set_loading(name)  # not "Load Images" under the loading card

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
        self.stage_status.reset()  # the previous project's counts
        if folder:  # the title bar and the taskbar name the open project
            project = os.path.basename(os.path.normpath(folder)) or folder
            self.setWindowTitle(f"{project} — {APP_NAME}[*]")
        self.populate_file_list()
        reset = getattr(self.display_controller, "reset", None)
        if callable(reset):
            reset()  # never keep showing the previous folder's image
        # The dashboards chart the previous project's CSVs: release them (servers,
        # pages, the OpenGL hold) and leave the Visualize stage if one was on screen (or
        # its page said what the previous project had to run first). After the new tree is
        # built: switching the canvases back to OpenGL processes events (queued tree slots).
        released = visualization_manager.release_all_visualizations(self)
        if released or self.right_panel.currentIndex() == Panel.VISUALIZATION:
            self._go_to_stage("Library")
        self._refresh_metrics_bar(None)
        if getattr(self, "loading_overlay", None) is not None:
            self.loading_overlay.hide()
        self.empty_state.set_loading(None)
        message = f"Loaded {count_label(len(images))}"
        self.task_progress.finish(message)
        self.statusline.refresh_idle()  # "Ready · N images" names this folder's count
        self.notify(message, key=_LOAD_TOAST)  # opening again updates it, never stacks

        # Swap the index-0 display page from the empty state to the real display
        # area now that images exist.
        if images and getattr(self, "display_page_stack", None) is not None:
            self.display_page_stack.setCurrentIndex(0)
        if images:
            # The library has the keyboard: the arrows browse at once (it went to the first
            # stage button when the Welcome screen hid, leaving a second highlighted stage).
            self.file_list.setFocus(Qt.FocusReason.OtherFocusReason)

        # Persist a recent-projects entry for the Welcome screen (FIX2).
        if images:
            self._record_recent_project(len(images))
        self._prewarm_measurements(locate=True)  # find and parse the CSVs before a selection
        self._prewarm_dashboards()

    def _prewarm_dashboards(self) -> None:
        """Import the dashboards' libraries once per session, shortly after a project loads."""
        if self._dashboards_prewarmed:
            return
        self._dashboards_prewarmed = True
        QTimer.singleShot(
            DASHBOARD_PREWARM_DELAY_MS,
            lambda: visualization_manager.prewarm_dashboards(web_engine=False),
        )

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
        self.empty_state.set_loading(None)
        if kind == "danger":
            self.task_progress.fail(message)
        else:
            self.task_progress.finish(message)
        self.notify(message, kind, key=_LOAD_TOAST)
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

    def _image_has_correction(self, stored_name) -> bool:
        """True when a skeleton correction of the image is known (display only, FR-03)."""
        has_correction = getattr(self.image_manager, "has_correction", None)
        return bool(stored_name and callable(has_correction) and has_correction(stored_name))

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
        corrected = self._image_has_correction(stored_name)
        if stored_name:
            if corrected:
                status = "Skeleton corrected"
            elif self._image_has_skeleton(stored_name):
                status = "Skeleton ready"
            elif self._image_has_mask(stored_name):
                status = "Mask ready"
            else:
                status = "No mask"

        measured = Measurement(None, None, None)
        outdated: dict[str, bool] = {}
        sources: dict[str, str | None] = {}
        index = self._measurements
        if stored_name and index is not None:
            try:
                measured = index.lookup(stored_name, load=False)
                # needs_load() waits for the CSV lock: never while a worker parses (its
                # ready signal refreshes the bar).
                if not self._measurements_loading and index.needs_load():
                    self._prewarm_measurements()
                # Each value against its own input: a saved mask outdates AREA, never LENGTH.
                outdated = index.outdated(stored_name)
                sources = index.sources()
            except Exception:
                logger.exception("Could not read the measured values of %s", stored_name)

        bar.set_metrics(
            length=measured.length_mm,
            area=measured.area_mm2,
            status=status,
            name=stored_name,
            error=measured.error,
            stale=outdated.get("length", False),
            source=sources.get("length"),
            area_stale=outdated.get("area", False),
            area_source=sources.get("area"),
            length_note=CORRECTION_NOT_MEASURED if corrected else None,
            status_tip=CORRECTED_STATUS_TIP if corrected else None,
        )

    def _prewarm_measurements(self, *, locate: bool = False) -> None:
        """Parse new / changed measurement CSVs in the thread pool (once at a time).

        ``locate``: run even when no CSV is known yet: the pool job also looks in the
        legacy folders, which the GUI thread never walks (once per opened project)."""
        index = self._measurements
        if index is None or self._measurements_loading:
            return
        if not locate and not index.needs_load():
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
            if self._current_stage() == "Measure":
                self._check_freshness()  # a CSV may have been found or re-read

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
        self._sync_open_correction()  # the image Correct had open may have been corrected

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
        self.statusline.refresh_idle()  # the Generate / Measure counts changed
        # The Correct page's read-ahead holds masks / skeletons the run may have rewritten.
        discard = getattr(self.correct_editor, "discard_read_ahead", None)
        if callable(discard):
            discard()

    def on_library_filtered(self) -> None:
        """The library search changed which images show: the Correct page's "Image i / n"
        counts the shown ones, so it asks again."""
        offer = getattr(self.correct_editor, "set_position_provider", None)
        if callable(offer):
            offer(self.library_index.position_of)

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
        if target == Panel.DISPLAY:
            return  # the display page shows the selection itself (_show_selection_on_display)
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
            browsing = self.file_list.hasFocus()  # the arrow keys keep stepping the tree
            self.on_image_selected(item)
            if browsing:
                self.file_list.setFocus()

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
                context=Qt.ShortcutContext.WidgetWithChildrenShortcut,  # one owner has them
            )
            self._paging_shortcuts += installed["next_image"] + installed["previous_image"]

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

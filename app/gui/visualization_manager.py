"""
Dashboard management for the main window: open, bring forward and close the
embedded root-length and root-area Dash dashboards.

The two dashboards differ only in data, described by a :class:`DashboardSpec`
(:data:`LENGTH`, :data:`AREA`). One :class:`DashboardController` per window
runs their shared lifecycle:

* ``show`` is idempotent: it builds a dashboard when none is alive, when its
  CSV changed on disk, or when it failed, and otherwise brings the live one
  forward. Showing one dashboard hides the other; it never closes it.
* ``close`` removes a dashboard exactly once, and ``toggle`` keeps the legacy
  open/close meaning for the old buttons. ``release_all`` closes both when
  another project is opened (they chart the previous project's CSVs).
* OpenGL canvas viewports stay off while any dashboard embeds a browser (Qt6
  cannot composite a QWebEngineView in a window holding a QOpenGLWidget) and
  come back when the last one closes, but only if they were on before.
* Dashboards outlive a visit to the Visualize stage, so a revisit is instant.
  ``stage_changed`` (called by MainWindow on every stage change) closes them
  :data:`IDLE_RELEASE_MS` after the stage was left: the canvases get OpenGL back
  and each dashboard's browser process (~150-250 MB) is freed.

The module functions keep their historical names. The dashboard classes are
imported on first use, so opening the app does not load the Dash stack.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import weakref
from collections.abc import Callable
from dataclasses import dataclass

# Qt6 only lets QtWebEngineWidgets load before the QApplication exists. The
# dashboard modules that use it are imported lazily, so import it here.
from PyQt6 import QtWebEngineWidgets  # noqa: F401
from PyQt6.QtCore import QEventLoop, Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.config import ROOT_AREAS_CSV, ROOT_LENGTHS_CSV
from app.gui.image_manager import project_layout_of
from app.project_layout import ProjectLayout, find_test_latest_dir

logger = logging.getLogger(__name__)

#: The shell stage that shows the dashboards (``shell_chrome`` stage key).
VISUALIZE_STAGE = "Visualize"
#: Dashboards are closed this long after the Visualize stage was left (5 min). A
#: revisit before brings the live page back at once (a frame, ~2 ms); after it a
#: dashboard opens again in about a second.
IDLE_RELEASE_MS = 5 * 60 * 1000
#: A release due while a mouse button is held (a brush stroke, a drag) or a popup
#: or dialog is open waits this long: the viewport swap must not cut an action.
IDLE_RELEASE_RETRY_MS = 5000

__all__ = [
    "AREA",
    "LENGTH",
    "SPECS",
    "CsvLookup",
    "DashboardController",
    "DashboardSpec",
    "ViewportGlSuspension",
    "close_root_area_visualization",
    "close_root_length_visualization",
    "close_visualization",
    "controller_for",
    "find_test_latest_dir",
    "prewarm_dashboards",
    "release_all_visualizations",
    "show_root_area_visualization",
    "show_root_length_visualization",
    "show_visualization",
    "stage_changed",
    "toggle_root_area_visualization",
    "toggle_root_length_visualization",
    "toggle_visualization",
    "visible_kind",
    "warm_dashboard_imports",
]

# Dashboard classes, imported on first use. Assigning the module attribute
# (tests, the UI harness) replaces the class the controller builds.
_DASHBOARD_MODULES = {
    "RootLengthVisualization": "app.visualization.root_length_visulization",
    "RootAreaVisualization": "app.visualization.root_area_visualization",
}


def __getattr__(name: str):
    module = _DASHBOARD_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


# --------------------------------------------------------------------------- #
#  What differs between the two dashboards
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CsvLookup:
    """A dashboard's CSV, or the title and text telling the user why there is none."""

    path: str | None
    title: str = ""
    message: str = ""


def _find_lengths_csv(layout: ProjectLayout) -> CsvLookup:
    path = layout.find_lengths_csv()
    if path:
        return CsvLookup(path)
    return CsvLookup(
        None,
        "No root length data",
        "No root length data was found.\n\n"
        "Run Measure → Root Length first (after generating skeletons).\n\n"
        "Expected file locations:\n"
        f"  • {os.path.join(layout.skeletons_dir, ROOT_LENGTHS_CSV)}\n"
        f"  • {os.path.join(layout.root, ROOT_LENGTHS_CSV)}",
    )


def _find_areas_csv(layout: ProjectLayout) -> CsvLookup:
    masks_dir = layout.resolve_masks_dir()
    if masks_dir is None:
        return CsvLookup(
            None,
            "No mask data",
            "No masks folder was found.\n\nGenerate masks first, then run Measure → Root Area.",
        )
    path = layout.find_areas_csv()
    if path:
        return CsvLookup(path)
    return CsvLookup(
        None,
        "No root area data",
        "No root area data was found.\n\n"
        "Run Measure → Root Area first (after generating masks).\n\n"
        f"Expected file:\n  • {os.path.join(masks_dir, ROOT_AREAS_CSV)}",
    )


@dataclass(frozen=True)
class DashboardSpec:
    """Everything that distinguishes one dashboard from the other."""

    kind: str  # "length" | "area"; also the SkeletonHandler.is_busy() key
    noun: str  # for messages
    attr: str  # MainWindow attribute holding the live dashboard (public contract)
    class_name: str  # dashboard class, resolved on this module
    needs_image_manager: bool  # constructor takes (csv_path, image_manager)
    closed_slot: str  # MainWindow slot connected to the dashboard's server_closed
    button_attr: str  # legacy button whose text follows open / closed
    open_text: str
    close_text: str
    calculator_attr: str  # SkeletonHandler thread attribute, for handlers without is_busy()
    find_csv: Callable[[ProjectLayout], CsvLookup]


LENGTH = DashboardSpec(
    kind="length",
    noun="root length",
    attr="root_length_viz",
    class_name="RootLengthVisualization",
    needs_image_manager=True,
    closed_slot="on_visualization_server_closed",
    button_attr="visualize_root_length_button",
    open_text="📊 Visualize Root Length",
    close_text="Close Visualization",
    calculator_attr="calculator_thread",
    find_csv=_find_lengths_csv,
)

AREA = DashboardSpec(
    kind="area",
    noun="root area",
    attr="root_area_viz",
    class_name="RootAreaVisualization",
    needs_image_manager=False,
    closed_slot="on_area_visualization_server_closed",
    button_attr="visualize_root_area_button",
    open_text="📈 Visualize Root Area",
    close_text="Close Area Visualization",
    calculator_attr="area_calculator_thread",
    find_csv=_find_areas_csv,
)

SPECS: dict[str, DashboardSpec] = {spec.kind: spec for spec in (LENGTH, AREA)}


def _spec(kind: str) -> DashboardSpec:
    try:
        return SPECS[kind]
    except KeyError:
        raise ValueError(f"unknown dashboard {kind!r}; expected one of {sorted(SPECS)}") from None


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _source_signature(path: str | None) -> tuple | None:
    """Identity of a CSV's content on disk (path, size, mtime), or None if unreadable."""
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns


def _opengl_viewports_in_use(window) -> bool:
    """True if any canvas of *window* currently renders through a QOpenGLWidget."""
    find_children = getattr(window, "findChildren", None)
    if find_children is None:
        return False
    try:
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget
    except ImportError:  # Qt built without OpenGL widgets: nothing to switch off
        return False
    return bool(find_children(QOpenGLWidget))


def _disconnect(signal, slot) -> None:
    if slot is None:
        return
    try:
        signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


class ViewportGlSuspension:
    """Keeps OpenGL canvas viewports off while any dashboard holds a browser.

    The first holder switches the canvases to raster viewports; the last one
    to leave restores OpenGL, and only if it was on when the first arrived, so
    ``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=0`` or a machine without OpenGL stays
    on raster viewports.
    """

    def __init__(self, set_enabled: Callable[[bool], None], is_enabled: Callable[[], bool]):
        self._set_enabled = set_enabled
        self._is_enabled = is_enabled
        self._holders: set[str] = set()
        self._restore = False

    def acquire(self, holder: str) -> None:
        if holder in self._holders:
            return
        if not self._holders:
            self._restore = self._is_enabled()
            if self._restore:
                self._set_enabled(False)
        self._holders.add(holder)

    def release(self, holder: str) -> None:
        if holder not in self._holders:
            return
        self._holders.discard(holder)
        if not self._holders and self._restore:
            self._restore = False
            self._set_enabled(True)


# --------------------------------------------------------------------------- #
#  Controller
# --------------------------------------------------------------------------- #
class DashboardController:
    """Opens, brings forward and closes the dashboards of one main window.

    Reads and writes only the window's public dashboard surface:
    ``right_panel``, ``file_list``, ``image_manager``, ``skeleton_handler``,
    ``switch_right_panel``, ``set_opengl_viewports_enabled``,
    ``on_image_selected``, ``notify``, the ``root_*_viz`` attributes, their
    ``on_*_server_closed`` slots and the legacy visualize buttons.
    """

    def __init__(self, main_window) -> None:
        self._window = weakref.ref(main_window)
        self._gl = ViewportGlSuspension(self._set_opengl, self._opengl_in_use)
        self._sources: dict[str, tuple | None] = {}  # kind -> CSV signature it was built from
        self._showing = False
        # Counts down from leaving the Visualize stage (see stage_changed). PyQt holds
        # the bound slot weakly, so the timer never keeps the controller alive.
        self._idle_timer = QTimer()
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle)

    @property
    def _mw(self):
        window = self._window()
        if window is None:
            raise RuntimeError("the main window of this dashboard controller is gone")
        return window

    # ------------------------------------------------------------------ queries
    def dashboard(self, spec: DashboardSpec):
        """The live dashboard of *spec*, or None."""
        return getattr(self._mw, spec.attr, None)

    def visible_kind(self) -> str | None:
        """Kind of the dashboard that is the right panel's current page, if any."""
        current = self._mw.right_panel.currentWidget()
        for spec in SPECS.values():
            dashboard = self.dashboard(spec)
            if dashboard is not None and dashboard is current:
                return spec.kind
        return None

    # ------------------------------------------------------------------ commands
    def show(self, spec: DashboardSpec) -> None:
        """Bring the dashboard forward, building (or rebuilding) it when needed."""
        if self._showing:
            return  # re-entered from processEvents while a dashboard is being built
        self._showing = True
        try:
            self._show(spec)
        finally:
            self._showing = False

    def close(self, spec: DashboardSpec) -> None:
        """Close and remove the dashboard (if open)."""
        dashboard = self.dashboard(spec)
        if dashboard is None:
            return
        mw = self._mw
        was_current = mw.right_panel.currentWidget() is dashboard
        self._set_button_text(spec, spec.open_text)
        if was_current:
            mw.switch_right_panel("display")
        self._discard(spec, dashboard)
        if was_current:
            item = mw.file_list.currentItem()
            if item is not None:
                mw.on_image_selected(item)

    def toggle(self, spec: DashboardSpec) -> None:
        """Legacy buttons: close the dashboard if it is open, else show it."""
        if self.dashboard(spec) is not None:
            self.close(spec)
        else:
            self.show(spec)

    def release_all(self) -> bool:
        """Close every dashboard: another project was opened, or Visualize was left long ago.

        After a project change they charted the previous project's CSVs; kept, they
        held a server, a browser page and the OpenGL suspension, and the one on
        screen stayed there under the new library. Unlike :meth:`close`, no image is
        re-shown: the caller shows the new project. Returns True when a dashboard
        was on screen (the display page is shown instead).
        """
        self._idle_timer.stop()
        mw = self._mw
        current = mw.right_panel.currentWidget()
        shown = False
        for spec in SPECS.values():
            dashboard = self.dashboard(spec)
            if dashboard is None:
                continue
            if dashboard is current:
                shown = True
                mw.switch_right_panel("display")
            self._set_button_text(spec, spec.open_text)
            self._discard(spec, dashboard)
        return shown

    # ------------------------------------------------------------------ idle release
    def stage_changed(self, key: str) -> None:
        """Count down to closing the dashboards while the Visualize stage is not shown.

        Leaving Visualize starts :data:`IDLE_RELEASE_MS`; moving between other
        stages keeps the countdown running (the time away is what counts), and
        coming back stops it, so the live pages are shown again at once.
        """
        if key == VISUALIZE_STAGE:
            self._idle_timer.stop()
        elif self._alive() and not self._idle_timer.isActive():
            self._idle_timer.start(IDLE_RELEASE_MS)

    def idle_release_pending(self) -> bool:
        """True while the dashboards are counting down to their release."""
        return self._idle_timer.isActive()

    def _alive(self) -> bool:
        return any(self.dashboard(spec) is not None for spec in SPECS.values())

    def _on_idle(self) -> None:
        """The countdown ended: close the dashboards, unless one is on screen again."""
        if self._window() is None or not self._alive():
            return
        if self._showing or self.visible_kind() is not None:
            return  # shown again by a path that did not report the stage
        if (
            QApplication.mouseButtons() != Qt.MouseButton.NoButton
            or QApplication.activePopupWidget() is not None
            or QApplication.activeModalWidget() is not None
        ):
            # Never swap the canvases' viewports under a stroke, a drag or a dialog.
            self._idle_timer.start(IDLE_RELEASE_RETRY_MS)
            return
        logger.info("Closing the dashboards: the Visualize stage was left a while ago")
        self.release_all()

    # ------------------------------------------------------------------ show
    def _show(self, spec: DashboardSpec) -> None:
        mw = self._mw
        current = self.dashboard(spec)
        if current is not None and self._is_up_to_date(spec, current):
            self._bring_forward(current)
            return
        if mw.file_list.topLevelItemCount() == 0:
            QMessageBox.warning(mw, "Warning", "No images loaded. Please load images first.")
            return
        if self._is_busy(spec):
            QMessageBox.information(
                mw,
                "Please wait",
                "A calculation is still running. Wait for it to finish before "
                f"opening the {spec.noun} visualization.",
            )
            return
        layout = self._project_layout()
        if layout is None:
            QMessageBox.warning(mw, "Warning", "The folder of the loaded images was not found.")
            return
        lookup = spec.find_csv(layout)
        if lookup.path is None:
            QMessageBox.information(mw, lookup.title, lookup.message)
            return
        # Leave the current page first so an open editor offers to save; the
        # GT editor may refuse (its save failed and the user kept editing).
        if mw.switch_right_panel("visualization") is False:
            return
        self._build(spec, lookup.path)
        if current is not None:  # a stale or failed one: replaced in place
            self._discard(spec, current, keep_opengl_off=True)

    def _is_up_to_date(self, spec: DashboardSpec, dashboard) -> bool:
        """False when *dashboard* failed or its CSV changed on disk."""
        if getattr(dashboard, "failed", False):
            return False
        if self._is_busy(spec):
            return True  # the CSV is being rewritten: keep showing what is there
        layout = self._project_layout()
        if layout is None:
            return False
        signature = _source_signature(spec.find_csv(layout).path)
        return signature == self._sources.get(spec.kind)

    def _bring_forward(self, dashboard) -> None:
        panel = self._mw.right_panel
        if panel.currentWidget() is dashboard:
            return
        if self._mw.switch_right_panel("visualization") is False:
            return  # an editor kept its unsaved edits
        panel.setCurrentWidget(dashboard)

    def _build(self, spec: DashboardSpec, csv_path: str) -> None:
        mw = self._mw
        self._gl.acquire(spec.kind)  # before the dashboard creates its browser widget
        try:
            cls = getattr(sys.modules[__name__], spec.class_name)
            if spec.needs_image_manager:
                dashboard = cls(csv_path, mw.image_manager)
            else:
                dashboard = cls(csv_path)
        except Exception:
            if self.dashboard(spec) is None:
                self._gl.release(spec.kind)
            raise
        slot = getattr(mw, spec.closed_slot, None)
        if slot is not None:
            dashboard.server_closed.connect(slot)
        notify = getattr(mw, "notify", None)
        if notify is not None and hasattr(dashboard, "export_saved"):
            dashboard.export_saved.connect(
                lambda path: notify(
                    f"Saved {os.path.basename(path)} in {os.path.dirname(path)}", "success"
                )
            )
        self._sources[spec.kind] = _source_signature(csv_path)
        # Set the attribute before the page switch: MainWindow collapses the
        # library when the current page is one of its dashboards.
        setattr(mw, spec.attr, dashboard)
        mw.right_panel.addWidget(dashboard)
        mw.right_panel.setCurrentWidget(dashboard)
        self._set_button_text(spec, spec.close_text)

    # ------------------------------------------------------------------ close
    def _discard(self, spec: DashboardSpec, dashboard, *, keep_opengl_off: bool = False) -> None:
        """Stop and remove *dashboard*, exactly once."""
        mw = self._mw
        if getattr(mw, spec.attr, None) is dashboard:
            # First, so the server_closed emitted below cannot re-enter close().
            setattr(mw, spec.attr, None)
            self._sources.pop(spec.kind, None)
        _disconnect(dashboard.server_closed, getattr(mw, spec.closed_slot, None))
        try:
            dashboard.cleanup_server()
        finally:
            mw.right_panel.removeWidget(dashboard)
            dashboard.deleteLater()
            if not keep_opengl_off:
                self._gl.release(spec.kind)

    # ------------------------------------------------------------------ plumbing
    def _project_layout(self) -> ProjectLayout | None:
        """Layout of the loaded project (``ImageManager.project_layout``)."""
        return project_layout_of(self._mw.image_manager)

    def _is_busy(self, spec: DashboardSpec) -> bool:
        """True while a run that writes (or invalidates) the dashboard's CSV is going."""
        handler = getattr(self._mw, "skeleton_handler", None)
        if handler is None:
            return False
        is_busy = getattr(handler, "is_busy", None)
        if callable(is_busy):
            return bool(is_busy(spec.kind))
        # Older SkeletonHandler without is_busy(): look at its threads.
        threads = (
            getattr(handler, spec.calculator_attr, None),
            getattr(getattr(handler, "skeleton_handler", None), "_thread", None),
        )
        return any(thread is not None and thread.isRunning() for thread in threads)

    def _set_button_text(self, spec: DashboardSpec, text: str) -> None:
        button = getattr(self._mw, spec.button_attr, None)
        if button is not None:
            button.setText(text)

    def _opengl_in_use(self) -> bool:
        return _opengl_viewports_in_use(self._mw)

    def _set_opengl(self, enabled: bool) -> None:
        setter = getattr(self._mw, "set_opengl_viewports_enabled", None)
        if setter is None:
            return
        setter(enabled)
        # Let the viewport swap settle before a browser widget is created (or
        # after the last one is gone). User input stays queued meanwhile.
        try:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        except RuntimeError as exc:  # no event loop mid-teardown; harmless
            logger.debug("processEvents failed: %s", exc)


_controllers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def controller_for(main_window) -> DashboardController:
    """The :class:`DashboardController` of *main_window*, created on first use."""
    controller = _controllers.get(main_window)
    if controller is None:
        controller = _controllers[main_window] = DashboardController(main_window)
    return controller


# --------------------------------------------------------------------------- #
#  Public API (MainWindow delegates here; names are a contract)
# --------------------------------------------------------------------------- #
def show_visualization(main_window, kind: str) -> None:
    """Show the ``kind`` dashboard ("length" or "area"): build it, or bring it forward."""
    spec = _spec(kind)
    try:
        controller_for(main_window).show(spec)
    except Exception as exc:  # noqa: BLE001 -- top-level UI guard
        logger.exception("Failed to show the %s visualization", spec.noun)
        QMessageBox.critical(main_window, "Error", f"Failed to create visualization: {exc}")


def close_visualization(main_window, kind: str) -> None:
    """Close and remove the ``kind`` dashboard, if it is open."""
    spec = _spec(kind)
    try:
        controller_for(main_window).close(spec)
    except Exception as exc:  # noqa: BLE001 -- top-level UI guard
        logger.exception("Error closing the %s visualization", spec.noun)
        QMessageBox.warning(main_window, "Warning", f"Error during cleanup: {exc}")


def toggle_visualization(main_window, kind: str) -> None:
    """Close the ``kind`` dashboard if it is open, else show it (legacy buttons)."""
    spec = _spec(kind)
    try:
        controller_for(main_window).toggle(spec)
    except Exception as exc:  # noqa: BLE001 -- top-level UI guard
        logger.exception("Error toggling the %s visualization", spec.noun)
        QMessageBox.critical(main_window, "Error", f"Error toggling visualization: {exc}")


def visible_kind(main_window) -> str | None:
    """ "length" or "area" when that dashboard is the right panel's page, else None."""
    return controller_for(main_window).visible_kind()


def warm_dashboard_imports() -> str:
    """Import the dashboard stack now (dash, plotly, pandas: ~1 s, ~100 MiB); blocking.

    Call it off the GUI thread (the main window's warm-up queue does, when the user
    heads for Visualize; see :mod:`app.gui.warmup`). The first dashboard's build worker
    imports the same modules anyway, so a failure costs nothing but the head start.
    Returns "" or why it failed (logged); never raises.
    """
    try:
        importlib.import_module("app.visualization.viz_window").prewarm_imports()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not prewarm the dashboard imports")
        return str(exc) or type(exc).__name__
    return ""


def _prewarm_imports_logged() -> None:
    """:func:`warm_dashboard_imports` as a thread-pool job (a failure is only logged)."""
    warm_dashboard_imports()


def prewarm_dashboards(*, web_engine: bool = True) -> None:
    """Start what the first dashboard open of a session waits for; call it at a quiet moment.

    dash, plotly and pandas are imported on a pool thread (0.7-1 s of the first
    open, ~100 MiB). With ``web_engine``, QtWebEngine is started on the calling
    (GUI) thread: ~0.4 s during which the GUI does not respond, ~95 MiB. Measured
    on a 46k-row trial, both together took the first open of a session from 3.16 s
    to 1.77 s to its first chart (median of 5) and its GUI freeze from 439 ms to 52 ms.
    The main window no longer calls this on a timer: its warm-up queue imports the
    stack when the user heads for Visualize (:func:`warm_dashboard_imports`).
    """
    QThreadPool.globalInstance().start(_prewarm_imports_logged)
    if web_engine:
        importlib.import_module("app.visualization.viz_window").prewarm_web_engine()


def release_all_visualizations(main_window) -> bool:
    """Close both dashboards because another project was opened.

    Returns True when one of them was on screen (the display page is shown
    instead; the caller moves the stage bar off Visualize).
    """
    controller = _controllers.get(main_window)
    if controller is None:
        return False  # no dashboard was ever opened in this window
    try:
        return controller.release_all()
    except Exception:  # noqa: BLE001 -- opening the project must not fail on this
        logger.exception("Error releasing the dashboards of the previous project")
        return False


def stage_changed(main_window, key: str) -> None:
    """The shell entered stage ``key`` ("Library", ..., "Visualize"); MainWindow calls this
    on every stage change.

    Leaving Visualize starts the countdown after which the dashboards are closed
    (:data:`IDLE_RELEASE_MS`); returning to it cancels the countdown.
    """
    controller = _controllers.get(main_window)
    if controller is None:
        return  # no dashboard was ever opened in this window
    try:
        controller.stage_changed(key)
    except Exception:  # noqa: BLE001 -- a stage change must never fail on this
        logger.exception("Error scheduling the release of the dashboards")


def toggle_root_length_visualization(main_window) -> None:
    """Close the root-length dashboard if it is open, else show it."""
    toggle_visualization(main_window, LENGTH.kind)


def show_root_length_visualization(main_window) -> None:
    """Show the root-length dashboard (idempotent: build it or bring it forward)."""
    show_visualization(main_window, LENGTH.kind)


def close_root_length_visualization(main_window) -> None:
    """Close and remove the root-length dashboard."""
    close_visualization(main_window, LENGTH.kind)


def toggle_root_area_visualization(main_window) -> None:
    """Close the root-area dashboard if it is open, else show it."""
    toggle_visualization(main_window, AREA.kind)


def show_root_area_visualization(main_window) -> None:
    """Show the root-area dashboard (idempotent: build it or bring it forward)."""
    show_visualization(main_window, AREA.kind)


def close_root_area_visualization(main_window) -> None:
    """Close and remove the root-area dashboard."""
    close_visualization(main_window, AREA.kind)

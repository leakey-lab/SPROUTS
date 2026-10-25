"""The window's widgets: the Welcome screen and the working shell (the top bar over the
library | pages body over the status line), the loading card, the status surface and
the toasts, which keep clear of the chrome at the bottom and at the side.

The right panel's pages are added in the fixed order of :class:`~app.gui.panels.Panel`
(display, Correct, the Visualize page, Trace GT; the dashboards are appended after them).
While a dashboard is on screen the library is collapsed, so the dashboard gets the width;
the keyboard focus the library held goes to the dashboard page meanwhile, and comes back
with the library (V2).

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.init_ui`` is :func:`init_ui`,
``MainWindow._build_body`` is :func:`build_body`): a slot stays a method of the window.
"""

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import canvas_gl, shell_chrome, ui_panels
from ..task_progress import TaskProgressWidget
from ..toast_clearance import FLOATING_GAP, ToastClearance
from ..ui_feedback import QtUiFeedback
from ..visualize_empty_state import VisualizeEmptyState

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

#: The editors' floating overlays in the bottom row, under the toast column.
_EDITOR_CHROME = ("dock", "zoom_cluster")
#: The editors' side panels (Correct's Layers, Trace GT's Classes), right of the toast column.
_EDITOR_SIDE_CHROME = ("layer_panel", "class_panel")


def init_ui(window: "MainWindow"):
    """Build the window's widgets: Welcome and the shell, the loading card, the status
    surface, the toasts and the feedback port the handlers report through."""
    # Guided app shell: a QStackedWidget that flips between the Welcome
    # screen (index 0) and the working shell/body (index 1). The window
    # starts on Welcome; load_images flips to the shell (T4.6).
    from app.gui.loading_overlay import LoadingOverlay
    from app.gui.welcome_screen import WelcomeWidget

    window.app_stack = QStackedWidget()
    window.welcome = WelcomeWidget(
        on_get_started=window.load_images,
        on_open_recent=window.open_path,
        recent_store=window.recent_projects,
        on_about=window.show_about,
    )
    window.app_stack.addWidget(window.welcome)  # index 0

    shell = window._build_shell()
    window.app_stack.addWidget(shell)  # index 1

    window.setCentralWidget(window.app_stack)
    window.app_stack.setCurrentIndex(0)

    # Loading card (parented to the window, below the top bar), hidden until a
    # directory is chosen.
    window.loading_overlay = LoadingOverlay(
        window, top_offset=lambda: window.ribbon.height() if window.ribbon.isVisible() else 0
    )
    window.loading_overlay.hide()

    # One status surface: the statusline shows the QStatusBar's messages and
    # hosts the task progress inline; the QStatusBar itself stays (API kept,
    # hidden) so every existing showMessage() caller keeps working.
    window.status_bar = QStatusBar()
    window.setStatusBar(window.status_bar)
    window.task_progress = TaskProgressWidget()
    window.status_bar.addPermanentWidget(window.task_progress)

    # Transient success/feedback toasts (bottom-right, above the statusline
    # and clear of an editor dock). Decisions keep using QMessageBox.
    from app.gui.widgets import ToastManager

    window.toasts = ToastManager(window)
    # The stack keeps clear of the bottom chrome and moves when that chrome does (a
    # page switch shows the metrics bar, an editor lifts its zoom cluster).
    window.toast_clearance = ToastClearance(
        window, window.toasts, window._bottom_chrome, side=window._side_chrome
    )
    if window.welcome.about_button is not None:  # counts only while Welcome shows
        window.toast_clearance.keep_clear_of(window.welcome.about_button)
    window.toast_clearance.watch(
        window.statusline,
        window.metrics_bar,
        *(
            getattr(e, name, None)
            for e in window._editors().values()
            for name in (*_EDITOR_CHROME, *_EDITOR_SIDE_CHROME)
        ),
    )

    shell_chrome.install_status_surface(window)
    # Handlers report progress / results / failures through this (AR-07).
    window.feedback = QtUiFeedback(
        window.task_progress,
        status_bar=window.status_bar,
        notifier=lambda message, kind: window.notify(message, kind),
        on_task_end=window.statusline.refresh_runtime,
    )
    # After the first paint: one warning when the canvases draw without OpenGL by accident.
    QTimer.singleShot(0, lambda: notify_canvas_path(window))


def notify_canvas_path(window: "MainWindow") -> bool:
    """Warn once (a toast) when the canvases draw slowly and the user did not choose it."""
    text = canvas_gl.slow_path_notice(QGuiApplication.platformName())
    if text is None:
        return False
    window.notify(text, "warn")
    return True


def build_shell(window: "MainWindow") -> QWidget:
    """Build the guided shell: one chrome row over the body (stretch) over
    the statusline. Returns the shell container.

    Presentation wrapper only — the body (and its four right_panel indices)
    is unchanged; the chrome row forwards to existing handlers.
    """

    shell = QWidget()
    col = QVBoxLayout(shell)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)

    col.addWidget(shell_chrome.build_topbar(window))
    col.addWidget(window._build_body(), 1)
    col.addWidget(shell_chrome.build_statusline(window))

    # Populate the stage-aware action bar now that the body (and the real
    # stage buttons created in create_left_panel) exists; then show the
    # default Library stage.
    window._populate_action_bar()
    window._activate_action_stage("Library")
    return shell


def build_body(window: "MainWindow") -> QWidget:
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
    left_panel = ui_panels.create_left_panel(window)
    left_panel.setMinimumWidth(170)  # names still legible at the floor
    window.left_panel = left_panel
    splitter.addWidget(left_panel)

    # Right Panel: the display, the editors and the dashboards (one page each).
    window.right_panel = QStackedWidget()
    window.right_panel.addWidget(ui_panels.create_right_panel(window))  # Panel.DISPLAY
    window.right_panel.addWidget(window.correct_editor)  # Panel.CORRECT
    # Panel.VISUALIZATION: shown while a dashboard opens, or when it has no CSV yet
    # (then it names the next step, with a button to that stage).
    window.visualization_widget = VisualizeEmptyState()
    window.visualization_widget.stage_requested.connect(window._go_to_stage)
    window.right_panel.addWidget(window.visualization_widget)
    window.right_panel.addWidget(window.gt_tracing_interface)  # Panel.GT_TRACING
    splitter.addWidget(window.right_panel)

    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([240, 960])
    window.body_splitter = splitter

    # When a visualization becomes the current right-panel widget, collapse
    # the library panel so the dashboard gets full width; restore it (and its
    # prior width) on the way out. Driven off currentChanged so every path
    # (ribbon switch, toggle, close) is covered.
    window._pre_viz_splitter_sizes = None
    window._library_focus = None  # the library widget that had the focus when it hid
    window.right_panel.currentChanged.connect(window._sync_library_for_panel)
    window.right_panel.currentChanged.connect(window._show_selection_on_display)
    return body


def current_widget_is_viz(window: "MainWindow") -> bool:
    """True while a dashboard is the right panel's page."""
    cur = window.right_panel.currentWidget()
    return cur is not None and cur in (
        getattr(window, "root_length_viz", None),
        getattr(window, "root_area_viz", None),
    )


def sync_library_for_panel(window: "MainWindow", *_args):
    """Collapse the library panel while a visualization is shown; restore it
    (and its previous width) otherwise. Idempotent and safe to call before
    any viz exists.

    The keyboard focus follows (V2). A panel that hides with the focus inside (the tree,
    after an image was picked) hands it to the dashboard page first: Qt passed it to the
    first stage button, which then looked highlighted beside Visualize and took Space and
    the arrow keys. When the panel returns, the widget that had the focus gets it back.
    """
    left = getattr(window, "left_panel", None)
    splitter = getattr(window, "body_splitter", None)
    if left is None or splitter is None:
        return
    if window._current_widget_is_viz():
        if left.isVisible():
            window._pre_viz_splitter_sizes = splitter.sizes()
            _hand_focus_to_page(window, left)
            left.setVisible(False)
    else:
        if not left.isVisible():
            left.setVisible(True)
            if window._pre_viz_splitter_sizes:
                splitter.setSizes(window._pre_viz_splitter_sizes)
                window._pre_viz_splitter_sizes = None
            _return_library_focus(window, left)
    toasts = getattr(window, "toasts", None)
    if toasts is not None:
        toasts.reposition()  # the editor dock under the toasts may have changed


def _hand_focus_to_page(window: "MainWindow", left: QWidget) -> None:
    """The library is about to hide: when it holds the window's focus, the page on screen
    takes it, and the library keeps the name of the widget that had it.

    The window's own focus widget, not the application's: while another window is active,
    the focus the window gets back on activation must not be inside a hidden panel either.
    """
    focused = window.focusWidget()
    if focused is None or not left.isAncestorOf(focused):
        return
    window._library_focus = focused
    window.right_panel.currentWidget().setFocus(Qt.FocusReason.OtherFocusReason)


def _return_library_focus(window: "MainWindow", left: QWidget) -> None:
    """The library is back: the widget that had the focus when it hid gets it again, as if
    the stage had been entered from the Library. An editor page keeps the focus it took
    itself when it showed (its canvas takes the keys), as it does from the Library."""
    widget = getattr(window, "_library_focus", None)
    window._library_focus = None
    if widget is None or not left.isAncestorOf(widget) or not widget.isVisible():
        return
    if window.right_panel.currentIndex() in window._editors():
        return
    widget.setFocus(Qt.FocusReason.OtherFocusReason)


def toast_bottom_offset(window: "MainWindow") -> int:
    """Pixels the toast stack keeps above the window's bottom edge (see
    :class:`~app.gui.toast_clearance.ToastClearance`)."""
    return window.toast_clearance.offset()


def bottom_chrome(window: "MainWindow") -> list[tuple]:
    """``(widget, gap)`` of the chrome the toasts must not cover: the statusline, the
    Library's metrics bar, the current editor's dock and zoom cluster (hidden ones are
    skipped by the caller)."""
    chrome = [(getattr(window, "statusline", None), 0), (getattr(window, "metrics_bar", None), 0)]
    right_panel = getattr(window, "right_panel", None)
    editor = None
    if right_panel is not None:
        editor = window._editors().get(right_panel.currentIndex())
    for name in _EDITOR_CHROME:
        chrome.append((getattr(editor, name, None), FLOATING_GAP))
    return chrome


def side_chrome(window: "MainWindow") -> list[tuple]:
    """``(widget, gap)`` of the current editor's side panel: the toasts keep their corner
    while they fit below it, else they move to its left."""
    right_panel = getattr(window, "right_panel", None)
    editor = None
    if right_panel is not None:
        editor = window._editors().get(right_panel.currentIndex())
    return [(getattr(editor, name, None), FLOATING_GAP) for name in _EDITOR_SIDE_CHROME]

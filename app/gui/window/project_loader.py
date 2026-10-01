"""The project loader: opening a folder of images, the scan's outcomes, recent projects.

``open_path`` leaves the Welcome screen for the working shell and shows the loading card;
``image_manager`` scans the folder off the GUI thread and reports back through
``update_loading_progress`` and ``on_loading_finished`` / ``on_loading_empty`` /
``on_loading_failed``. A missing folder, or an editor that keeps unsaved edits, changes
nothing; a folder without images keeps whatever was loaded.

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.open_path`` is :func:`open_path`), so code and
tests keep calling (and patching) the window. ``MainWindow.on_loading_finished`` runs
:func:`on_loading_finished`, then starts the warm-ups' settling time.
"""

import logging
import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog

from app import APP_NAME
from app.data_processing.measurement_index import MeasurementIndex

from .. import visualization_manager
from ..image_manager import project_layout_of
from ..library_index import count_label
from ..panels import Panel
from . import _common

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

#: The one toast of a folder load (loaded / empty / failed): a newer load replaces it.
_LOAD_TOAST = "project-load"


def load_images(window: "MainWindow"):
    """Open the directory dialog (at the last folder), then load the chosen folder."""
    start = window.window_state.last_folder() if window._persist_window_state else ""
    dir_name = QFileDialog.getExistingDirectory(window, "Select Image Directory", start)
    if dir_name:
        window.open_path(dir_name)


def open_path(window: "MainWindow", dir_name):
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
        window.notify(f"Folder not found: {dir_name}", "danger", key=_LOAD_TOAST)
        window._refresh_recents()
        return
    # Pending edits: save or discard first; an editor the user keeps editing
    # (cancel, or a failed save) cancels the open. Asked before the overlay.
    for editor in window._editors().values():
        if not _common.released(editor):
            return
    # Stash for on_loading_finished so it can persist a recent entry.
    window._last_loaded_dir = dir_name

    # Leave the Welcome screen for the working shell and show the loading card
    # (PR4 T4.6), naming the folder: a large network folder can take a while.
    window.app_stack.setCurrentIndex(1)
    name = os.path.basename(os.path.normpath(dir_name)) or dir_name
    window.loading_overlay.set_filename(name, tooltip=dir_name)
    window.loading_overlay.start("Loading images")
    window.empty_state.set_loading(name)  # not "Load Images" under the loading card

    # Show progress before loading.
    window.task_progress.start("Loading images")

    # The editors are clean now: drop their image and history (no prompt).
    for editor in window._editors().values():
        editor.reset()

    # Load images through image manager (warm-ups wait until it has settled).
    window._loading_project = True
    window.image_manager.load_images(dir_name)
    if window._persist_window_state:
        window.window_state.set_last_folder(dir_name)
    reset_viz = getattr(window.ribbon, "reset_visualize_selection", None)
    if callable(reset_viz):
        reset_viz()


def update_loading_progress(window: "MainWindow", value):
    """Update the loading progress bar"""
    window.task_progress.set_progress(value)
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.set_progress(value)


def on_loading_finished(window: "MainWindow", images, fake_images, masks, has_fake_real_pairs):
    """Show a freshly scanned folder: build the library, then drop the overlay."""
    window._loading_project = False
    window._shown_leaf_name = None
    layout = project_layout_of(window.image_manager)
    folder = layout.root if layout is not None else getattr(window, "_last_loaded_dir", None)
    window.metrics.index = MeasurementIndex(folder) if folder else None
    window.stage_status.reset()  # the previous project's counts
    if folder:  # the title bar and the taskbar name the open project
        project = os.path.basename(os.path.normpath(folder)) or folder
        window.setWindowTitle(f"{project} — {APP_NAME}[*]")
    window.populate_file_list()
    reset = getattr(window.display_controller, "reset", None)
    if callable(reset):
        reset()  # never keep showing the previous folder's image
    # The dashboards chart the previous project's CSVs: release them (servers,
    # pages, the OpenGL hold) and leave the Visualize stage if one was on screen (or
    # its page said what the previous project had to run first). After the new tree is
    # built: switching the canvases back to OpenGL processes events (queued tree slots).
    released = visualization_manager.release_all_visualizations(window)
    if released or window.right_panel.currentIndex() == Panel.VISUALIZATION:
        window._go_to_stage("Library")
    window.metrics.refresh(None)
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.hide()
    window.empty_state.set_loading(None)
    message = f"Loaded {count_label(len(images))}"
    window.task_progress.finish(message)
    window.statusline.refresh_idle()  # "Ready · N images" names this folder's count
    window.notify(message, key=_LOAD_TOAST)  # opening again updates it, never stacks

    # Swap the index-0 display page from the empty state to the real display
    # area now that images exist.
    if images and getattr(window, "display_page_stack", None) is not None:
        window.display_page_stack.setCurrentIndex(0)
    if images:
        # The library has the keyboard: the arrows browse at once (it went to the first
        # stage button when the Welcome screen hid, leaving a second highlighted stage).
        window.file_list.setFocus(Qt.FocusReason.OtherFocusReason)

    # Persist a recent-projects entry for the Welcome screen (FIX2).
    if images:
        window._record_recent_project(len(images))
    window.metrics.prewarm(locate=True)  # find and parse the CSVs before a selection


def on_loading_empty(window: "MainWindow", folder):
    """The scanned folder has no images: warn and keep whatever was loaded."""
    name = os.path.basename(os.path.normpath(folder or "")) or folder
    window._end_failed_load(f"No .jpg/.png images in {name} — nothing loaded", "warn")


def on_loading_failed(window: "MainWindow", folder, message):
    """The folder could not be scanned: report it and keep whatever was loaded."""
    window._end_failed_load(message, "danger")


def end_failed_load(window: "MainWindow", message, kind):
    """A load that delivered no images ends: the card goes, ``message`` is shown (a
    ``kind`` toast), and the Welcome screen returns when nothing was loaded before."""
    window._loading_project = False
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.hide()
    window.empty_state.set_loading(None)
    if kind == "danger":
        window.task_progress.fail(message)
    else:
        window.task_progress.finish(message)
    window.notify(message, kind, key=_LOAD_TOAST)
    if not window.image_manager.images and getattr(window, "app_stack", None) is not None:
        window.app_stack.setCurrentIndex(0)  # nothing to show: back to Welcome


def record_recent_project(window: "MainWindow", count):
    """Record the just-loaded dir in the recent projects (MRU, capped)."""
    dir_name = getattr(window, "_last_loaded_dir", None)
    if not dir_name:
        return
    try:
        window.recent_projects.add(dir_name, count)
    except Exception:
        logger.exception("Failed to record recent project")
    window._refresh_recents()


def refresh_recents(window: "MainWindow") -> None:
    """The Welcome screen lists the recent projects again (a failure is only logged)."""
    welcome = getattr(window, "welcome", None)
    if welcome is None:
        return
    try:
        welcome.refresh_recents()
    except Exception:
        logger.exception("Failed to refresh the recent projects")

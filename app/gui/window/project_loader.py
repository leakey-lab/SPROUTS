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
from app.calibration import PROJECT_FILENAME
from app.config import (
    GT_CLASSES_FILENAME,
    GT_DIRNAME,
    IMAGE_EXTENSIONS,
    MASK_DIRNAME,
    ROOT_AREAS_CSV,
    ROOT_LENGTHS_CSV,
    SKELETONS_DIRNAME,
)
from app.data_processing.measurement_index import MeasurementIndex
from app.project_layout import SKELETON_SUFFIX, ImageScan, project_layout_of
from app.provenance import RUN_MANIFEST_FILENAME

from .. import visualization_manager
from ..library_index import count_label
from ..panels import Panel
from . import _common

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

#: The one toast of a folder load (loaded / empty / failed): a newer load replaces it.
_LOAD_TOAST = "project-load"
#: The second toast of a load: the files the Library does not list (O6, O7).
_SKIPPED_TOAST = "project-load-skipped"
#: How many hidden file names the skipped toast spells out.
_HIDDEN_NAMED = 3

#: Opening a folder while a batch job runs is refused (R7, O15): the job's outputs
#: belong to the project it started in, and the progress row keeps its name and Cancel.
JOB_RUNNING_OPEN = "{name} is running. Cancel it or wait before opening another folder."


#: Opening a project's ``gt/`` folder is refused: as a project its ``mask/`` is the real
#: ``gt/mask``, and Generate's "Replace all predictions" would overwrite the human GT masks.
GT_FOLDER_OPEN = (
    "{name} is a project's GT folder, not a project. Open its parent folder, {parent}, instead."
)


def _has_prediction(folder: str) -> bool:
    """True when ``folder`` (a ``skeletons/``) holds a ``*_fake.png``; stops at the first."""
    try:
        with os.scandir(folder) as entries:
            return any(e.name.endswith(SKELETON_SUFFIX) for e in entries)
    except OSError:
        return False


def _is_project(path: str) -> bool:
    """True when the folder ``path`` shows signs of a project: its settings file, a Generate
    run record or Measure CSV, a ``*_fake.png`` prediction or a ``gt/`` of its own. Generate
    creates ``mask/`` and ``skeletons/`` in a project and never writes into a ``gt/``."""
    masks, skeletons = os.path.join(path, MASK_DIRNAME), os.path.join(path, SKELETONS_DIRNAME)
    marks = (
        os.path.join(path, PROJECT_FILENAME),
        os.path.join(masks, RUN_MANIFEST_FILENAME),
        os.path.join(skeletons, RUN_MANIFEST_FILENAME),
        os.path.join(masks, ROOT_AREAS_CSV),
        os.path.join(skeletons, ROOT_LENGTHS_CSV),
    )
    return (
        any(os.path.isfile(mark) for mark in marks)
        or os.path.isdir(os.path.join(path, GT_DIRNAME))
        or _has_prediction(skeletons)
    )


def gt_folder_parent(dir_name: str) -> str | None:
    """The folder that holds ``dir_name`` when ``dir_name`` is a project's GT folder, else
    ``None``. It is named ``gt`` (without case) and either holds ``classes.json``, or holds a
    ``mask/`` or ``skeletons/`` sub-folder and shows no sign of being a project itself
    (:func:`_is_project`: a project that is named ``gt`` gets those sub-folders from
    Generate). A project that merely has a ``gt/`` inside and a plain folder of photos named
    ``gt`` are not GT folders."""
    path = os.path.normpath(dir_name)
    if os.path.basename(path).casefold() != GT_DIRNAME:
        return None
    if os.path.isfile(os.path.join(path, GT_CLASSES_FILENAME)):
        return os.path.dirname(path)
    has_layers = any(
        os.path.isdir(os.path.join(path, sub)) for sub in (MASK_DIRNAME, SKELETONS_DIRNAME)
    )
    return os.path.dirname(path) if has_layers and not _is_project(path) else None


def _refuse_gt_folder(window: "MainWindow", dir_name: str) -> bool:
    """True (and a warning toast naming the folder to open) for a project's GT folder."""
    parent = gt_folder_parent(dir_name)
    if parent is None:
        return False
    name = os.path.basename(os.path.normpath(dir_name))
    window.notify(GT_FOLDER_OPEN.format(name=name, parent=parent), "warn")
    return True


def _refuse_while_job_runs(window: "MainWindow") -> bool:
    """True (and a warning toast) while a batch job runs: no folder opens then."""
    jobs = getattr(window, "jobs", None)  # a partial window (tests) runs no jobs
    if jobs is None or not jobs.is_busy():
        return False
    window.notify(JOB_RUNNING_OPEN.format(name=jobs.running_name() or "A task"), "warn")
    return True


def load_images(window: "MainWindow"):
    """Open the directory dialog (at the last folder), then load the chosen folder.

    Refused, without the dialog, while a batch job runs (R7)."""
    if _refuse_while_job_runs(window):
        return
    start = window.window_state.last_folder() if window._persist_window_state else ""
    dir_name = QFileDialog.getExistingDirectory(window, "Select Image Directory", start)
    if dir_name:
        window.open_path(dir_name)


def open_path(window: "MainWindow", dir_name):
    """Load a directory of images (no dialog).

    Shared by ``load_images`` (after the dialog) and the Welcome screen's
    recent-projects rows. A missing folder is reported and changes nothing,
    and so does an editor that keeps unsaved edits, and so does a running batch
    job (R7, O15: refused before the edits prompt and the loading card, so the
    progress row keeps the job's name and Cancel), and so does a project's own
    ``gt/`` folder (:func:`gt_folder_parent`: a warning names the folder to open).
    Otherwise drops what the
    status line and the toasts say about the project being left, flips to the
    working shell, shows the loading overlay, and kicks off
    ``image_manager.load_images``; the current library stays until the scan
    delivers images.
    """
    if not dir_name:
        return
    if _refuse_while_job_runs(window):
        return
    if not os.path.isdir(dir_name):
        window.notify(f"Folder not found: {dir_name}", "danger", key=_LOAD_TOAST)
        window._refresh_recents()
        return
    if _refuse_gt_folder(window, dir_name):
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
    # Then the previous project's messages go (V3): the status line shows the load's
    # progress at once, without recounting the project being left for its idle text.
    _clear_project_messages(window)

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


def _clear_project_messages(window: "MainWindow") -> None:
    """Drop the status line's message and the toasts: they are about the project being
    left (V3). A Measure run's "Saved <its CSV path>" stays on the status line for 15 s
    (``SAVED_PATH_STATUS_MS``) and its toast for a few seconds; kept, they named the
    previous project's file in the next one and hid its own summary of what a run would
    work on. Done when the open starts, not when the scan ends: the status line shows
    under the loading card."""
    window.status_bar.clearMessage()
    toasts = getattr(window, "toasts", None)
    for toast in toasts.toasts() if toasts is not None else ():
        toast.dismiss()


def skipped_text(scan: ImageScan) -> str:
    """What the Library does not list of ``scan``'s folder, as one sentence per kind
    (``""`` when it lists every image): files whose stem another file took (O7) and
    files of a format the app does not read (O6)."""
    parts = []
    hidden = scan.hidden
    if len(hidden) == 1:
        ((name, shown),) = hidden
        parts.append(f"1 image is not listed: {name} has the same name as {shown}")
    elif hidden:
        names = ", ".join(name for name, _ in hidden[:_HIDDEN_NAMED])
        more = ", …" if len(hidden) > _HIDDEN_NAMED else ""
        parts.append(
            f"{len(hidden)} images are not listed: each has the same name as another "
            f"image ({names}{more})"
        )
    unsupported = scan.unsupported
    if unsupported:
        suffixes = ", ".join(sorted({os.path.splitext(n)[1].lower() for n in unsupported}))
        if len(unsupported) == 1:
            parts.append(
                f"1 file ({suffixes}) is not supported and is not listed; convert it to PNG or JPEG"
            )
        else:
            parts.append(
                f"{len(unsupported)} files ({suffixes}) are not supported and are not "
                "listed; convert them to PNG or JPEG"
            )
    return ". ".join(parts)


def _skipped_of(window: "MainWindow") -> str:
    """:func:`skipped_text` of the scan just delivered (``""`` without one)."""
    scan = getattr(window.image_manager, "last_scan", None)
    return skipped_text(scan) if scan is not None else ""


def update_loading_progress(window: "MainWindow", value):
    """Update the loading progress bar"""
    window.task_progress.set_progress(value)
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.set_progress(value)


def on_loading_finished(window: "MainWindow", images, fake_images, masks, has_fake_real_pairs):
    """Show a freshly scanned folder: build the library, then drop the overlay.

    A failure up to the library tree propagates: the manager then loads nothing and the
    load ends as failed (see :func:`end_failed_load`). Once the tree shows the folder, it
    is open: a later failure is logged and the loading card still goes.
    """
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
    try:
        _after_the_library_is_shown(window, images)
    except Exception:
        logger.exception("Opening %s: a step after the library was shown failed", folder)
        _drop_loading_card(window, f"Loaded {count_label(len(images))}")


def _drop_loading_card(window: "MainWindow", message: str) -> None:
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.hide()
    window.empty_state.set_loading(None)
    window.task_progress.finish(message)


def _after_the_library_is_shown(window: "MainWindow", images) -> None:
    """The rest of :func:`on_loading_finished`, once the tree shows the new folder."""
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
    message = f"Loaded {count_label(len(images))}"
    _drop_loading_card(window, message)
    window.statusline.refresh_idle()  # "Ready · N images" names this folder's count
    window.notify(message, key=_LOAD_TOAST)  # opening again updates it, never stacks
    skipped = _skipped_of(window)
    if skipped:  # its own toast: the success toast stays "Loaded N images"
        window.notify(skipped, "warn", key=_SKIPPED_TOAST)

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
    """The scanned folder has no images: warn (naming the files of formats the app does
    not read, if any) and keep whatever was loaded."""
    name = os.path.basename(os.path.normpath(folder or "")) or folder
    formats = "/".join(IMAGE_EXTENSIONS)
    message = f"No {formats} images in {name} — nothing loaded"
    skipped = _skipped_of(window)
    window._end_failed_load(f"{message}. {skipped}" if skipped else message, "warn")


def on_loading_failed(window: "MainWindow", folder, message):
    """The folder could not be scanned: report it and keep whatever was loaded."""
    window._end_failed_load(message, "danger")


def end_failed_load(window: "MainWindow", message, kind):
    """A load that delivered no images ends: the card goes, ``message`` is shown (a
    ``kind`` toast), and the Welcome screen returns when nothing is loaded (before, or
    any more after a failed adoption: then the tree, the measured values and the title
    drop what a half-adopted folder left)."""
    window._loading_project = False
    if getattr(window, "loading_overlay", None) is not None:
        window.loading_overlay.hide()
    window.empty_state.set_loading(None)
    if kind == "danger":
        window.task_progress.fail(message)
    else:
        window.task_progress.finish(message)
    window.notify(message, kind, key=_LOAD_TOAST)
    if window.image_manager.images:
        return
    _forget_project(window)
    if getattr(window, "app_stack", None) is not None:
        window.app_stack.setCurrentIndex(0)  # nothing to show: back to Welcome


def _forget_project(window: "MainWindow") -> None:
    """Nothing is loaded: the tree, the measured values and the title say so too (a
    partial window, as in tests, has only some of them)."""
    metrics = getattr(window, "metrics", None)
    if metrics is not None:
        metrics.index = None
    populate = getattr(window, "populate_file_list", None)
    if callable(populate):
        try:
            populate()  # the manager's empty library: an empty tree
        except Exception:  # the failed load must still end (and the jobs start again)
            logger.exception("Could not clear the library tree")
    set_title = getattr(window, "setWindowTitle", None)
    if callable(set_title):
        set_title(f"{APP_NAME}[*]")


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

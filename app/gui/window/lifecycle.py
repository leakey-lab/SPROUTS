"""Closing the window: unsaved edits, a running job, then clean-up and the saved layout.

An editor that keeps unsaved edits (the user cancelled, or the save failed and they kept
editing) or a job the user keeps running cancels the close. Otherwise the pending
warm-ups and the device check are dropped, the dashboards are closed and the window's
layout is saved.

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow._stop_jobs_for_close`` is
:func:`stop_jobs_for_close`); ``MainWindow.closeEvent`` runs :func:`close_event`, then Qt's
own close.
"""

import logging
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QApplication, QMessageBox

from ._common import released

if TYPE_CHECKING:
    from PyQt6.QtGui import QCloseEvent

    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

# Closing with a running job: first wait (window visible, "Stopping…"), then a
# longer wait with the window hidden; a job still running after both is logged.
_CLOSE_WAIT_MS = 5000
_CLOSE_EXTRA_WAIT_MS = 120_000


def close_event(window: "MainWindow", event: "QCloseEvent") -> bool:
    """What ``MainWindow.closeEvent`` does before Qt's own close: False (and ``event``
    ignored) when an editor keeps unsaved edits or the user keeps a job running; True
    once the window is cleaned up and its layout saved (the close goes on)."""
    for editor in window._editors().values():
        if not released(editor):
            event.ignore()
            return False
    if not window._stop_jobs_for_close():
        event.ignore()
        return False
    # A warm-up that has not started is dropped (an import under way cannot be
    # stopped: the quit waits for it), and so is a device check still waiting.
    window.warmups.shutdown()
    if window.device_check is not None:
        window.device_check.cancel()
    try:
        if window.root_length_viz is not None:
            window.close_root_length_visualization()
        if window.root_area_viz is not None:
            window.close_root_area_visualization()
    except Exception:
        logger.exception("Error cleaning up visualizations on close")
    window._save_window_state()
    QApplication.processEvents()
    return True


def stop_jobs_for_close(window: "MainWindow") -> bool:
    """True when no job runs any more (asks before stopping one).

    The registry is shut down first, so a job chained to the stopped one
    (Generate: masks, then skeletons) can never start during or after the
    close. A job that outlives the first wait (a CPU batch in flight) gets
    the window hidden and a longer wait, instead of the process exiting
    under a running worker.
    """
    if not window.jobs.is_busy():
        window.jobs.shutdown()
        return True
    name = window.jobs.running_name() or "A task"
    answer = QMessageBox.question(window, "Quit", f"{name} is running. Stop it and quit?")
    if answer != QMessageBox.StandardButton.Yes:
        return False
    window.jobs.shutdown()
    window.feedback.status(f"Stopping: {name}…", 0)
    QApplication.processEvents()
    if window.jobs.stop_all(timeout_ms=_CLOSE_WAIT_MS):
        return True
    window.hide()
    if not window.jobs.stop_all(timeout_ms=_CLOSE_EXTRA_WAIT_MS):
        logger.warning("Job %r did not stop in time; quitting anyway", window.jobs.running_name())
    return True


def save_window_state(window: "MainWindow") -> None:
    """Save the window's geometry and the library's width (when this window persists
    them; a failure is only logged)."""
    if not window._persist_window_state:
        return
    try:
        if window._pre_viz_splitter_sizes:
            # The library is collapsed for a dashboard: store the user's layout.
            window.left_panel.setVisible(True)
            window.body_splitter.setSizes(window._pre_viz_splitter_sizes)
        window.window_state.save(window, window.body_splitter)
    except Exception:
        logger.exception("Failed to save the window state")

"""Plumbing shared by the mask and skeleton generation handlers.

``GenerationHandlerBase`` owns the handler's dependencies (``UiFeedback`` and the
``JobRegistry``, both injectable, defaulting to ``main_window.feedback`` /
``main_window.jobs`` with local stand-ins), the project folder lookup, starting a
worker as a named job (RB-07: refused politely while another job runs) and
wiring its Cancel button (PI-08). ``show_failure_report`` lists the inputs that
produced no output in ONE non-modal dialog, so a long unattended Generate
(masks, then skeletons) never stalls on a modal box (UXS-02).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.gui.feedback import UiFeedback
from app.handlers.job_registry import JobRegistry
from app.handlers.window_feedback import WindowFeedback
from app.handlers.worker_base import GuardedThread

logger = logging.getLogger(__name__)

_REPORT_PREVIEW = 20  # failed names listed in the dialog text (all go to the details)


class GenerationHandlerBase:
    """Dependencies + job lifecycle for one kind of generation run.

    Subclasses set ``job_label`` (the job's user-visible name) and keep the
    running worker in ``self._thread`` via :meth:`_start`.
    """

    job_label = "Generating"

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        self.main_window = main_window
        self._feedback = feedback
        self._jobs = jobs
        self._fallback_feedback: WindowFeedback | None = None
        self._fallback_jobs: JobRegistry | None = None
        # Strong reference: a running QThread must never be garbage-collected (F-025).
        self._thread: GuardedThread | None = None
        self._report_box: QMessageBox | None = None

    # ------------------------------------------------------------------ dependencies
    @property
    def feedback(self) -> UiFeedback:
        found = self._feedback or getattr(self.main_window, "feedback", None)
        if found is not None:
            return found
        if self._fallback_feedback is None:
            self._fallback_feedback = WindowFeedback(self.main_window)
        return self._fallback_feedback

    @property
    def jobs(self) -> JobRegistry:
        found = self._jobs or getattr(self.main_window, "jobs", None)
        if found is not None:
            return found
        if self._fallback_jobs is None:
            self._fallback_jobs = JobRegistry()
        return self._fallback_jobs

    def parent_widget(self) -> QWidget | None:
        return self.main_window if isinstance(self.main_window, QWidget) else None

    # ------------------------------------------------------------------ state
    def is_busy(self) -> bool:
        """True while this handler's worker is running."""
        return self._thread is not None and self._thread.isRunning()

    def project_folder(self) -> str | None:
        """The loaded image folder, or None when no images are loaded."""
        manager = getattr(self.main_window, "image_manager", None)
        images = getattr(manager, "images", None)
        if not images:
            return None
        folder = getattr(manager, "original_folder", None)
        if folder:
            return folder
        return os.path.dirname(next(iter(images.values())))

    def outputs_changed(self) -> None:
        """Tell the window that files on disk changed (mask status, metrics)."""
        callback = getattr(self.main_window, "on_outputs_changed", None)
        if callable(callback):
            callback()

    # ------------------------------------------------------------------ lifecycle
    def _start(self, thread: GuardedThread, on_success: Callable[[], None] | None = None) -> bool:
        """Start ``thread`` as the job ``job_label``; False (not started) if busy.

        ``on_success`` runs once the thread has completed (``thread.completed``)
        AND fully finished -- after the job registry released the job, so a
        chained job (Generate: masks, then skeletons) can start. It never runs
        after a cancel or a failure.
        """
        if self.is_busy():
            self.feedback.notify(f"{self.job_label} is already running", "warn")
            thread.deleteLater()
            return False
        if not self.jobs.try_start(self.job_label, thread):
            running = self.jobs.running_name() or "Another task"
            self.feedback.notify(f"{running} is running. Try again when it has finished.", "warn")
            thread.deleteLater()
            return False
        self._thread = thread
        if on_success is not None:
            self._chain_on_success(thread, on_success)
        return True

    @staticmethod
    def _chain_on_success(thread: GuardedThread, on_success: Callable[[], None]) -> None:
        # QThread's own finished (the subclasses shadow ``finished``) is emitted
        # after run() returned; the registry connected to it first, so it has
        # already cleared the job when this queued slot runs.
        thread_finished = QThread.finished.__get__(thread, QThread)

        def fire() -> None:
            if getattr(thread, "completed", False):
                QTimer.singleShot(0, on_success)

        thread_finished.connect(fire)

    def begin_task(self, label: str, thread: GuardedThread) -> None:
        """Show ``label`` as the running task, with Cancel wired to the worker."""
        self.feedback.task_started(label, cancelable=True, on_cancel=thread.cancel)


def show_failure_report(
    parent: QWidget | None, title: str, output_dir: str, failed: list[tuple[str, str]]
) -> QMessageBox:
    """One non-modal warning listing the inputs that got no output (and why)."""
    names = [name for name, _ in failed]
    preview = "\n".join(names[:_REPORT_PREVIEW])
    if len(names) > _REPORT_PREVIEW:
        preview += f"\n… and {len(names) - _REPORT_PREVIEW:,} more"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(f"{len(names):,} image(s) could not be processed and have no output.")
    box.setInformativeText(
        f"{preview}\n\nOther outputs were saved to:\n{output_dir}\n"
        "The run manifest (_sprouts_run.json) there lists every failure."
    )
    box.setDetailedText("\n".join(f"{name}: {reason}" for name, reason in failed))
    box.setModal(False)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.show()
    return box


__all__ = ["GenerationHandlerBase", "show_failure_report"]

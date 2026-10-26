"""Generate skeletons for the loaded image folder (thin Qt layer over skeleton_inference).

The worker loads torch and the cached skeleton model only when a run starts
(PG-04). Unreadable images are reported once, non-modally, and the run goes on
(PI-03 / RB-10); out-of-memory batches are split (PI-07); Cancel stops at the
next batch and keeps finished skeletons (PI-08); progress and results go
through ``UiFeedback`` (PI-12 / AR-07). On success the results are loaded with
``main_window.load_results(<skeletons folder>)``; after a Cancel or a failure the
window re-reads the outputs (``on_outputs_changed``), since the skeletons written
before it stay on disk (R7).
"""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.feedback import UiFeedback
from app.handlers.deps import SAVED_PATH_STATUS_MS, folder_label
from app.handlers.generation_base import (
    GenerationHandlerBase,
    every_input_failed,
    show_failure_report,
)
from app.handlers.job_registry import JobRegistry
from app.inference.batch_pipeline import progress_percent
from app.workers import GuardedThread, JobCancelled

logger = logging.getLogger(__name__)


class SkeletonGeneratorThread(GuardedThread):
    """Runs ``skeleton_inference.run_skeleton_generation`` off the GUI thread.

    Signals (``finished``, ``error`` and ``progress`` keep their legacy payloads):
        progress(int): percent done.
        progress_counts(int, int): (done, total) inputs.
        finished(str): the skeleton folder (when the run ended without Cancel or an
            error, also when every input failed; see ``completed``).
        error(str): user-facing message when the run failed (mirrors ``failed``).
    ``report`` holds the ``BatchReport`` once the run ended (completed or cancelled);
    ``completed`` turns True only when the run finished without cancel or error and
    did not fail on every input it attempted (R6: such a run is a failure).
    """

    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    progress_counts = pyqtSignal(int, int)

    def __init__(self, input_dir: str, output_dir: str) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.report = None
        self.completed = False
        self.failed.connect(self.error)

    def work(self) -> None:
        from app.inference import skeleton_inference  # torch loads here, off the GUI thread

        logger.info(
            "SkeletonGeneratorThread starting: input=%s output=%s", self.input_dir, self.output_dir
        )
        self.report = skeleton_inference.run_skeleton_generation(
            self.input_dir,
            self.output_dir,
            progress=self._report_progress,
            should_stop=self.is_cancelled,
        )
        # A Cancel during the last batch leaves report.cancelled False (every
        # image was done); it is still a cancel, so nothing chains (wf2 R1).
        if self.report.cancelled or self.is_cancelled():
            raise JobCancelled
        logger.info("SkeletonGeneratorThread complete: output=%s", self.output_dir)
        # When every input failed nothing was written: ``finished`` still reports
        # it, but the run is a failure (R6).
        self.completed = not every_input_failed(self.report)
        self.finished.emit(self.output_dir)

    def _report_progress(self, done: int, total: int) -> None:
        self.progress.emit(progress_percent(done, total))
        self.progress_counts.emit(done, total)


class GenerateSkeletonHandler(GenerationHandlerBase):
    """Starts skeleton generation for ``main_window``'s loaded folder.

    Args:
        main_window: provides ``image_manager``, ``load_results(folder)`` and,
            optionally, ``feedback`` (``UiFeedback``) and ``jobs`` (``JobRegistry``).
        feedback / jobs: explicit dependencies (tests, tools).
    """

    job_label = "Generating skeletons"

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        super().__init__(main_window, feedback=feedback, jobs=jobs)

    def generate_skeleton(self, input_dir: str | None = None) -> bool:
        """Start skeleton generation on ``input_dir``; True if a run started.

        ``input_dir`` None means the open project, else the folder dialog. Generate
        passes the mask pass's folder, so the chained pass stays with the project it
        started in even when another folder was opened meanwhile (R7).
        """
        if self.refuse_while_loading():  # before the dialog and the skeletons/ folder
            return False
        input_dir = input_dir or self.project_folder()
        if input_dir is None:
            input_dir = QFileDialog.getExistingDirectory(
                self.parent_widget(), "Select Input Folder"
            )
            if not input_dir:
                return False
        if self.refuse_if_busy():
            return False

        output_dir = os.path.normpath(os.path.join(input_dir, "skeletons"))
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            logger.exception("Could not create skeleton output directory %s", output_dir)
            QMessageBox.critical(
                self.parent_widget(),
                "Error",
                f"Could not create output directory:\n{output_dir}\n\n{exc}",
            )
            return False

        thread = SkeletonGeneratorThread(input_dir, output_dir)
        thread.progress_counts.connect(self.feedback.task_progress)
        thread.finished.connect(partial(self._on_finished, thread))
        thread.failed.connect(partial(self._on_failed, thread))
        thread.cancelled.connect(partial(self._on_cancelled, thread))
        if not self._start(thread):
            return False
        self.begin_task(self.job_label, thread)
        return True

    # ------------------------------------------------------------------ outcomes
    def _on_finished(self, thread: SkeletonGeneratorThread, results_dir: str) -> None:
        report = thread.report
        if every_input_failed(report):  # a failure, and no results to load (R6)
            failed = len(report.failed)
            message = f"Skeleton generation failed: none of {failed:,} images could be processed"
            logger.error(message)
            self.feedback.task_failed(message)
            self._report_box = show_failure_report(
                self.parent_widget(),
                "Skeleton generation",
                results_dir,
                report.failed,
                nothing_written=True,
            )
            return
        self.feedback.task_finished(f"Generated {len(report.written):,} skeletons")
        if report.failed:
            self.feedback.notify(f"{len(report.failed):,} images could not be processed", "warn")
            self._report_box = show_failure_report(
                self.parent_widget(), "Skeleton generation", results_dir, report.failed
            )
        else:
            # (W13) The folder's name in the toast, its full path in the status line.
            self.feedback.status(f"Skeletons saved to {results_dir}", SAVED_PATH_STATUS_MS)
            folder = folder_label(results_dir, self.project_folder())
            self.feedback.notify(f"Skeletons saved to the {folder} folder", "success")
        self.main_window.load_results(results_dir)

    def _on_failed(self, thread: SkeletonGeneratorThread, message: str) -> None:
        logger.error("Skeleton generation error: %s", message)
        self.feedback.task_failed("Skeleton generation failed")
        # Skeletons written before the error stay on disk (R7): show them.
        self.outputs_changed()
        QMessageBox.critical(self.parent_widget(), "Skeleton generation failed", message)

    def _on_cancelled(self, thread: SkeletonGeneratorThread) -> None:
        report = thread.report
        done = f" after {report.done:,} of {report.total:,} images" if report else ""
        self.feedback.task_finished(f"Skeleton generation cancelled{done}")
        self.feedback.notify(
            f"Skeleton generation cancelled{done}; finished skeletons were kept", "info"
        )
        self.outputs_changed()  # the kept skeletons: badges, display and metrics follow


__all__ = ["GenerateSkeletonHandler", "SkeletonGeneratorThread"]

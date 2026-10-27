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

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.handlers.deps import SAVED_PATH_STATUS_MS, folder_label
from app.handlers.generation_base import GenerationHandlerBase, GenerationThread, thread_slot

logger = logging.getLogger(__name__)


class SkeletonGeneratorThread(GenerationThread):
    """Runs ``skeleton_inference.run_skeleton_generation`` off the GUI thread.

    Besides :class:`GenerationThread`'s signals, ``finished(str)`` (legacy payload): the
    skeleton folder, when the run ended without Cancel or an error, also when every
    input failed (see ``completed``).
    """

    finished = pyqtSignal(str)

    def work(self) -> None:
        from app.inference import skeleton_inference  # torch loads here, off the GUI thread

        logger.info(
            "SkeletonGeneratorThread starting: input=%s output=%s", self.input_dir, self.output_dir
        )
        report = skeleton_inference.run_skeleton_generation(
            self.input_dir,
            self.output_dir,
            progress=self.progress_counts.emit,
            should_stop=self.is_cancelled,
        )
        self._conclude(report)  # raises JobCancelled after a Cancel
        logger.info("SkeletonGeneratorThread complete: output=%s", self.output_dir)
        self.finished.emit(self.output_dir)


class GenerateSkeletonHandler(GenerationHandlerBase):
    """Starts skeleton generation for ``main_window``'s loaded folder.

    Args:
        main_window: provides ``image_manager``, ``load_results(folder)`` and,
            optionally, ``feedback`` (``UiFeedback``) and ``jobs`` (``JobRegistry``).
        feedback / jobs: explicit dependencies (tests, tools).
    """

    job_label = "Generating skeletons"
    title = "Skeleton generation"
    noun = "skeletons"

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
        thread.finished.connect(thread_slot(self._on_finished, thread))
        thread.failed.connect(thread_slot(self._on_failed, thread))
        thread.cancelled.connect(thread_slot(self._on_cancelled, thread))
        if not self._start(thread):
            return False
        self.begin_task(self.job_label, thread)
        return True

    # ------------------------------------------------------------------ outcomes
    def _on_finished(self, thread: SkeletonGeneratorThread, results_dir: str) -> None:
        report = thread.report
        if self._ended_with_no_output(report, results_dir):  # and no results to load (R6)
            return
        self.feedback.task_finished(f"Generated {len(report.written):,} skeletons")
        if report.failed:
            self._report_failures(results_dir, report.failed)
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


__all__ = ["GenerateSkeletonHandler", "SkeletonGeneratorThread"]

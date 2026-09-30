"""Generate masks for the loaded image folder (thin Qt layer over mask_inference).

* RB-01 / PI-04 / FR-04 / UXS-01: before starting, ``mask/`` is scanned once and
  existing masks are classified (hand-corrected = RGBA PNG, model = greyscale).
  If any exist the user chooses "Keep existing masks (generate missing only)"
  (default), "Overwrite all (N hand-corrected)" or Cancel. Cancel starts no
  thread, so ``main_window.generate_all`` chains no skeleton pass.
* PG-04 / PI-11: importing or constructing the handler loads neither torch nor
  the model; the worker loads the cached model on first use ("Loading mask model").
* PI-12 / UXS-02 / AR-07: progress, completion and failures go through
  ``UiFeedback`` (named operations, done/total counts, Cancel); a successful run
  ends with a toast, not a modal box, so an unattended Generate never stalls.
* PI-03 / PI-07 / PI-08 / PI-09 / PI-13: see ``app.inference.mask_inference``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import partial
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.gui.feedback import UiFeedback
from app.handlers.generation_base import (
    GenerationHandlerBase,
    every_input_failed,
    show_failure_report,
)
from app.handlers.job_registry import JobRegistry
from app.handlers.worker_base import GuardedThread, JobCancelled
from app.inference import runtime
from app.inference.batch_pipeline import progress_percent
from app.inference.mask_origin import ExistingMasks, OverwritePolicy, scan_existing_masks
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

ConfirmOverwrite = Callable[[ExistingMasks, int], "OverwritePolicy | None"]


class MaskGenerationThread(GuardedThread):
    """Runs ``mask_inference.generate_masks`` off the GUI thread.

    Signals (``progress``, ``finished`` and ``error`` keep their legacy payloads):
        progress(int): percent done.
        progress_counts(int, int): (done, total) inputs.
        stage(str): "Loading mask model" / "Generating masks".
        finished(str, list): output folder and the names of failed inputs
            (emitted when the run ended without Cancel or an error, also when
            every input failed; see ``completed``).
        error(str): user-facing message when the run failed (mirrors ``failed``).
    ``report`` holds the ``BatchReport`` once the run ended (completed or cancelled);
    ``completed`` turns True only when the run finished without cancel or error and
    did not fail on every input it attempted (R6: such a run is a failure, so
    nothing chains on it).
    """

    progress = pyqtSignal(int)
    progress_counts = pyqtSignal(int, int)
    stage = pyqtSignal(str)
    finished = pyqtSignal(str, list)
    error = pyqtSignal(str)

    def __init__(
        self,
        input_dir: str,
        output_dir: str,
        model: Any = None,
        *,
        skip_stems: frozenset[str] = frozenset(),
        policy: str | None = None,
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.model = model
        self.skip_stems = frozenset(skip_stems)
        self.policy = policy
        self.report = None
        self.completed = False
        self.failed.connect(self.error)

    def work(self) -> None:
        from app.inference import mask_inference  # torch/torchvision load here, off the GUI

        self.report = mask_inference.generate_masks(
            self.input_dir,
            self.output_dir,
            skip_stems=self.skip_stems,
            model=self.model,
            policy=self.policy,
            progress=self._report_progress,
            should_stop=self.is_cancelled,
            on_stage=self.stage.emit,
        )
        # A Cancel during the last batch leaves report.cancelled False (every
        # image was done); it is still a cancel, so nothing chains (wf2 R1).
        if self.report.cancelled or self.is_cancelled():
            raise JobCancelled
        # When every input failed (e.g. out of memory even on single images) nothing
        # was written: ``finished`` still reports it, but nothing chains (R6).
        self.completed = not every_input_failed(self.report)
        self.finished.emit(self.output_dir, self.report.failed_names)

    def _report_progress(self, done: int, total: int) -> None:
        self.progress.emit(progress_percent(done, total))
        self.progress_counts.emit(done, total)


def ask_overwrite_policy(
    parent: QWidget | None, existing: ExistingMasks, total: int
) -> OverwritePolicy | None:
    """Ask what to do with existing masks; None means Cancel (the default is Keep)."""
    manual = len(existing.manual)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Masks already exist")
    box.setText(f"{existing.count:,} of {total:,} images already have a mask.")
    detail = (
        f"{manual:,} of them were corrected by hand. "
        if manual
        else "None of them look hand-corrected. "
    )
    box.setInformativeText(
        detail + "Keep them and generate only the missing masks, or replace every mask "
        "with a new model prediction. Replaced masks cannot be restored."
    )
    keep = box.addButton(
        "Keep existing masks (generate missing only)", QMessageBox.ButtonRole.AcceptRole
    )
    box.addButton(
        f"Overwrite all ({manual:,} hand-corrected)", QMessageBox.ButtonRole.DestructiveRole
    )
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(keep)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is keep:
        return OverwritePolicy.KEEP_EXISTING
    if clicked is None or clicked is cancel:
        return None
    return OverwritePolicy.OVERWRITE_ALL


class MaskGenerationHandler(GenerationHandlerBase):
    """Starts mask generation for ``main_window``'s loaded folder.

    Args:
        main_window: provides ``image_manager`` and, optionally, ``feedback``
            (``UiFeedback``), ``jobs`` (``JobRegistry``) and ``on_outputs_changed()``.
        feedback / jobs: explicit dependencies (tests, tools).
        confirm_overwrite: ``(existing, total) -> OverwritePolicy | None``; default
            is the :func:`ask_overwrite_policy` dialog.
    """

    job_label = "Generating masks"

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
        confirm_overwrite: ConfirmOverwrite | None = None,
    ) -> None:
        super().__init__(main_window, feedback=feedback, jobs=jobs)
        self._confirm = confirm_overwrite or partial(ask_overwrite_policy, self.parent_widget())
        self._started: MaskGenerationThread | None = None

    @property
    def generation_thread(self) -> MaskGenerationThread | None:
        """The worker started by the latest ``generate_masks()`` call, else None.

        ``main_window.generate_all`` chains the skeleton pass through
        ``generate_masks(on_success=...)``, which runs only when this worker
        ``completed`` (not on its ``finished``, also emitted when every input failed).
        """
        return self._started

    @property
    def model_available(self) -> bool:
        """True when the mask checkpoint exists (the model itself loads on first use)."""
        return os.path.isfile(runtime.mask_weights_path())

    def generate_masks(self, on_success: Callable[[], None] | None = None) -> bool:
        """Ask about existing masks, then start generation. True if a run started.

        ``on_success`` (e.g. the skeleton pass of Generate) runs after a completed
        run once the job registry is free; never after Cancel or a failure.
        """
        self._started = None
        parent = self.parent_widget()
        if not self.model_available:
            QMessageBox.warning(
                parent,
                "Mask model missing",
                "Mask generation is unavailable: the model weights were not found at\n"
                f"{runtime.mask_weights_path()}",
            )
            return False
        input_dir = self.project_folder()
        if input_dir is None:
            QMessageBox.warning(parent, "No images", "Load an image folder first.")
            return False
        if self.is_busy():
            self.feedback.notify(f"{self.job_label} is already running", "warn")
            return False

        layout = ProjectLayout(input_dir)
        try:
            stems = set(layout.scan_images())
            existing = scan_existing_masks(layout, stems)
        except OSError as exc:
            logger.exception("Could not scan %s", input_dir)
            QMessageBox.critical(parent, "Mask generation", f"Could not read the folder:\n{exc}")
            return False
        policy = OverwritePolicy.KEEP_EXISTING
        if existing:
            chosen = self._confirm(existing, len(stems))
            if chosen is None:
                return False
            policy = chosen

        thread = MaskGenerationThread(
            input_dir,
            layout.mask_dir,
            skip_stems=policy.skip_stems(existing),
            policy=policy.value,
        )
        thread.stage.connect(partial(self._on_stage, thread))
        thread.progress_counts.connect(self.feedback.task_progress)
        thread.finished.connect(partial(self._on_finished, thread))
        thread.failed.connect(partial(self._on_failed, thread))
        thread.cancelled.connect(partial(self._on_cancelled, thread))
        self._started = thread
        if not self._start(thread, on_success):
            self._started = None
            return False
        return True

    # ------------------------------------------------------------------ outcomes
    def _on_stage(self, thread: MaskGenerationThread, label: str) -> None:
        self.begin_task(label, thread)

    def _on_finished(self, thread: MaskGenerationThread, output_dir: str, _failed: list) -> None:
        report = thread.report
        if every_input_failed(report):  # a failure, not "Generated 0 masks" (R6)
            failed = len(report.failed)
            message = f"Mask generation failed: none of {failed:,} images could be processed"
            logger.error(message)
            self.feedback.task_failed(message)
            self._report_box = show_failure_report(
                self.parent_widget(),
                "Mask generation",
                output_dir,
                report.failed,
                nothing_written=True,
            )
            self.outputs_changed()
            return
        written, kept = len(report.written), len(report.skipped)
        message = f"Generated {written:,} masks"
        if kept:
            message += f", kept {kept:,} existing"
        self.feedback.task_finished(message)
        if report.failed:
            self.feedback.notify(f"{len(report.failed):,} images could not be processed", "warn")
            self._report_box = show_failure_report(
                self.parent_widget(), "Mask generation", output_dir, report.failed
            )
        else:
            self.feedback.notify(f"{message}. Saved to {output_dir}", "success")
        self.outputs_changed()

    def _on_failed(self, thread: MaskGenerationThread, message: str) -> None:
        logger.error("Mask generation failed: %s", message)
        self.feedback.task_failed("Mask generation failed")
        QMessageBox.critical(self.parent_widget(), "Mask generation failed", message)
        self.outputs_changed()

    def _on_cancelled(self, thread: MaskGenerationThread) -> None:
        report = thread.report
        done = f" after {report.done:,} of {report.total:,} images" if report else ""
        self.feedback.task_finished(f"Mask generation cancelled{done}")
        self.feedback.notify(f"Mask generation cancelled{done}; finished masks were kept", "info")
        self.outputs_changed()


__all__ = ["MaskGenerationHandler", "MaskGenerationThread", "ask_overwrite_policy"]

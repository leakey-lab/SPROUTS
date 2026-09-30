"""Measure stage (root length / root area) and the skeleton-generation entry point.

Each measurement runs as one named batch job (``main_window.jobs``) on a
:class:`~app.inference.metric_thread.MetricCalculatorThread`, reports through a
:class:`~app.gui.feedback.UiFeedback`, can be cancelled, and never crashes the
app: a CSV held open by Excel ends in a Retry prompt that re-saves the rows
already measured.
"""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.gui.feedback import UiFeedback
from app.gui.image_manager import project_layout_of
from app.handlers.deps import SAVED_PATH_STATUS_MS, HandlerDependencies, folder_label
from app.handlers.job_registry import JobRegistry
from app.inference.measure_run import AREA, LENGTH, SPECS, MeasureRun, MetricSpec
from app.inference.metric_thread import MetricCalculatorThread
from app.project_layout import ProjectLayout

from .generate_skeleton_handler import GenerateSkeletonHandler

logger = logging.getLogger(__name__)

# Per metric kind: the attribute holding its running thread (read by other modules).
_THREAD_ATTRS = {LENGTH.kind: "calculator_thread", AREA.kind: "area_calculator_thread"}


def job_name(spec: MetricSpec) -> str:
    """Batch-job / progress name of a measurement, e.g. "Measuring root length"."""
    return f"Measuring {spec.label}"


class SkeletonHandler(HandlerDependencies):
    """Starts skeleton generation and the two Measure jobs for ``main_window``.

    Args:
        main_window: provides ``image_manager`` (the project folder) and,
            optionally, ``feedback`` (a ``UiFeedback``) and ``jobs`` (a
            ``JobRegistry``); both fall back to local stand-ins.
        feedback / jobs / generation: explicit dependencies (tests, tools).
    """

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
        generation: GenerateSkeletonHandler | None = None,
    ) -> None:
        super().__init__(main_window, feedback=feedback, jobs=jobs)
        self.skeleton_handler = generation or GenerateSkeletonHandler(main_window)
        # Strong references to the running threads (F-024); cleared only after
        # the thread has fully finished (RB-08).
        self.calculator_thread: MetricCalculatorThread | None = None
        self.area_calculator_thread: MetricCalculatorThread | None = None
        self._retry_jobs: dict[str, MeasureRun] = {}  # measured rows whose CSV save failed
        self._retry_box: QMessageBox | None = None

    # ------------------------------------------------------------------ public API
    def generate_skeleton(self):
        self.skeleton_handler.generate_skeleton()

    def is_busy(self, kind: str | None = None) -> bool:
        """True while a run that writes (or invalidates) ``kind``'s CSV is going.

        ``kind`` is "length" or "area"; ``None`` means any Measure job. Skeleton
        generation counts as busy for "length" (it rewrites the skeletons).
        """
        kinds = (kind,) if kind else tuple(_THREAD_ATTRS)
        if any(self._running(getattr(self, _THREAD_ATTRS[k], None)) for k in kinds):
            return True
        return kind == LENGTH.kind and self._generation_busy()

    def calculate_root_length(self):
        """Measure root length of every ``*_fake.png`` in the project's skeleton folder."""
        layout = self._layout()
        if layout is None:
            return
        folder = layout.resolve_skeletons_dir()
        if folder is None:
            self._warn("No skeleton images found. Generate skeletons first.")
            return
        self._measure(LENGTH, layout, folder, layout.scan_skeletons)

    def calculate_root_area(self):
        """Measure root area of every ``*.png`` mask in the project's mask folder."""
        layout = self._layout()
        if layout is None:
            return
        folder = layout.resolve_masks_dir()
        if folder is None:
            self._warn("No mask images found. Generate masks first.")
            return
        existing = [d for d in layout.mask_dir_candidates if os.path.isdir(d)]
        if len(existing) > 1:
            logger.warning("Both %s exist; measuring %s", " and ".join(existing), folder)
            # Generate and Correct save to mask/: say that it is left out (R7).
            others = ", ".join(os.path.relpath(d, layout.root) for d in existing if d != folder)
            self._warn(
                f"Measuring the older {os.path.relpath(folder, layout.root)} folder: "
                f"masks in {others} are not measured."
            )
        self._measure(AREA, layout, folder, partial(layout.scan_masks, extensions=(".png",)))

    def retry_measure(self, kind: str) -> bool:
        """Save the rows of ``kind``'s failed run again (no re-measuring); False if none."""
        job = self._retry_jobs.get(kind)
        if job is None:
            return False
        return self._start(job)

    # ------------------------------------------------------------------ starting
    def _layout(self) -> ProjectLayout | None:
        layout = project_layout_of(self.main_window.image_manager)
        if layout is None:
            self._warn("No images loaded. Load images first.")
        return layout

    def _measure(self, spec: MetricSpec, layout: ProjectLayout, folder: str, scan) -> None:
        if self.is_busy(spec.kind):
            self._warn(f"{job_name(spec)} is already running.")
            return
        try:
            images = scan(folder)
        except OSError as exc:
            logger.exception("Cannot list %s", folder)
            QMessageBox.critical(
                self._parent(), "Measure", f"Cannot read the folder:\n{folder}\n\n{exc}"
            )
            return
        if not images:
            self._warn(f"No images to measure in {folder}.")
            return
        self._start(MeasureRun(spec, images, folder, project_root=layout.root))

    def _start(self, job: MeasureRun) -> bool:
        spec = job.spec
        thread = MetricCalculatorThread(job)
        thread.finished.connect(partial(self._on_finished, thread))
        thread.failed.connect(partial(self._on_failed, thread))
        thread.cancelled.connect(partial(self._on_cancelled, thread))
        thread.progress.connect(self.feedback.task_progress)
        name = job_name(spec)
        if not self.jobs.try_start(name, thread):
            running = self.jobs.running_name() or "Another task"
            self._warn(f"{running} is running. Measure when it has finished.")
            thread.deleteLater()
            return False
        self._retry_jobs.pop(spec.kind, None)
        setattr(self, _THREAD_ATTRS[spec.kind], thread)
        what = "saving" if job.rows is not None else f"{len(job.images):,} images"
        self.feedback.task_started(f"{name} ({what})", cancelable=True, on_cancel=thread.cancel)
        return True

    # ------------------------------------------------------------------ outcomes
    def _on_finished(self, thread: MetricCalculatorThread, csv_path: str) -> None:
        self._release(thread)
        rows = thread.job.rows or []
        errors = sum(1 for row in rows if row.get("Error"))
        label = thread.spec.label.capitalize()
        name = os.path.basename(csv_path)
        # (W13) The toast names the file and its folder (a full path is one word it can
        # only elide mid-word); the full path goes to the status line and its tooltip.
        self.feedback.status(f"Saved {csv_path}", SAVED_PATH_STATUS_MS)
        self.feedback.task_finished(f"{label} measured for {len(rows):,} images")
        if errors:
            self.feedback.notify(
                f"{errors:,} images could not be measured: see the Error column of {name}",
                "warn",
            )
        else:
            folder = folder_label(os.path.dirname(csv_path), thread.job.project_root)
            self.feedback.notify(f"Saved {name} in the {folder} folder", "success")

    def _on_failed(self, thread: MetricCalculatorThread, message: str) -> None:
        self._release(thread)
        self.feedback.task_failed(f"{job_name(thread.spec)} failed")
        if thread.job.rows is not None:  # measured, but the CSV could not be saved
            self._retry_jobs[thread.spec.kind] = thread.job
            self._offer_retry(thread.spec.kind, message)
        else:
            QMessageBox.warning(self._parent(), "Measure failed", message)

    def _on_cancelled(self, thread: MetricCalculatorThread) -> None:
        self._release(thread)
        self.feedback.task_finished(f"{job_name(thread.spec)} cancelled; no CSV written")

    def _offer_retry(self, kind: str, message: str) -> None:
        """Non-modal Retry/Cancel prompt; Retry re-saves the kept rows.

        The box deletes itself once answered (RB-16); ``_retry_box`` is the newest
        open prompt, cleared only by that prompt's own answer.
        """
        box = QMessageBox(self._parent())
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Could not save the results")
        box.setText(message)
        box.setInformativeText("The measurements are kept. Press Retry to save them again.")
        box.setStandardButtons(QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Retry)
        box.finished.connect(partial(self._on_retry_answer, kind, box))
        self._retry_box = box
        box.open()

    def _on_retry_answer(self, kind: str, box: QMessageBox, result: int) -> None:
        if self._retry_box is box:  # an older prompt's answer keeps the newer box (RB-16)
            self._retry_box = None
        if result == QMessageBox.StandardButton.Retry.value:
            self.retry_measure(kind)
        else:
            self._retry_jobs.pop(kind, None)

    # ------------------------------------------------------------------ helpers
    def _release(self, thread: MetricCalculatorThread) -> None:
        """Drop our reference only once the thread has fully finished (RB-08).

        The result signals are emitted inside ``run()``, so the thread is still
        running when their slots start; ``wait()`` returns as soon as ``run()``
        has returned.
        """
        thread.wait()
        attr = _THREAD_ATTRS[thread.spec.kind]
        if getattr(self, attr, None) is thread:
            setattr(self, attr, None)
        thread.deleteLater()

    def _generation_busy(self) -> bool:
        gen = self.skeleton_handler
        is_busy = getattr(gen, "is_busy", None)
        if callable(is_busy):
            return bool(is_busy())
        return self._running(getattr(gen, "_thread", None))  # older generation handler

    @staticmethod
    def _running(thread: Any) -> bool:
        return thread is not None and thread.isRunning()

    def _parent(self) -> QWidget | None:
        return self.main_window if isinstance(self.main_window, QWidget) else None

    def _warn(self, message: str) -> None:
        self.feedback.notify(message, "warn")


__all__ = ["SPECS", "SkeletonHandler", "job_name"]

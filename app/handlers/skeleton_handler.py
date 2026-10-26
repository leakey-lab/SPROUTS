"""Measure stage (root length / root area) and the skeleton-generation entry point.

Each measurement runs as one named batch job (``main_window.jobs``) on a
:class:`~app.inference.metric_thread.MetricCalculatorThread`, reports through a
:class:`~app.feedback.UiFeedback`, can be cancelled, and never crashes the
app: a CSV held open by Excel ends in a Retry prompt that re-saves the rows
already measured.

GT first (O1): the predictions (``skeletons/`` / ``mask/``) and the GT files
(``gt/skeletons/`` / ``gt/mask/``) are listed here, split by image (O37), and handed to the
job; which file each image is measured from is decided on the job's worker, never here.
"""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.calibration import (
    PROJECT_FILENAME,
    aspect_mismatches,
    default_calibration,
    fov_label,
    load_project_calibration,
)
from app.feedback import UiFeedback
from app.gui.image_manager import project_layout_of
from app.handlers.deps import SAVED_PATH_STATUS_MS, HandlerDependencies, folder_label
from app.handlers.job_registry import JobRegistry
from app.inference.measure_run import (
    AREA,
    LENGTH,
    SPECS,
    MeasureRun,
    MetricSpec,
    mixed_sizes,
    scan_png_files,
    sizes_px,
    split_inputs,
)
from app.inference.measure_sources import SIZE, UNEDITED, UNREADABLE
from app.inference.metric_thread import MetricCalculatorThread
from app.masks.store import MASK_THRESHOLD
from app.project_layout import ProjectLayout

from .generate_skeleton_handler import GenerateSkeletonHandler

logger = logging.getLogger(__name__)

# Per metric kind: the attribute holding its running thread (read by other modules).
_THREAD_ATTRS = {LENGTH.kind: "calculator_thread", AREA.kind: "area_calculator_thread"}


def _noun(spec: MetricSpec, n: int) -> str:
    """ "mask(s)" for the area metric, "skeleton(s)" for the length metric."""
    noun = "mask" if spec.kind == AREA.kind else "skeleton"
    return noun if n == 1 else noun + "s"


#: How the end-of-run notice counts each reason a GT file was not used.
_SKIP_WORDS = (
    (SIZE, "not the photo's size"),
    (UNEDITED, "equal to the prediction"),
    (UNREADABLE, "unreadable"),
)


#: Sizes named in the mixed-size warning; all of them are in the provenance (``inputs.sizes_px``).
_SIZES_LISTED = 3


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
        self._again_box: QMessageBox | None = None  # the "Measure again?" prompt

    # ------------------------------------------------------------------ public API
    def generate_skeleton(self, input_dir: str | None = None) -> bool:
        """Start skeleton generation on ``input_dir`` (None: the open project, else the
        folder dialog); True if a run started."""
        return self.skeleton_handler.generate_skeleton(input_dir=input_dir)

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
        """Measure the root length of every image with a skeleton: its GT skeleton
        (``gt/skeletons/<stem>.png``) where valid, else its ``*_fake.png`` in the project's
        skeleton folder (O1)."""
        layout = self._layout()
        if layout is None:
            return
        folder = layout.resolve_skeletons_dir()
        if folder is None:
            if not self._has_gt(layout.gt_skeletons_dir):
                self._warn("No skeleton images found. Generate skeletons first.")
                return
            folder = layout.skeletons_dir  # GT only: the CSV goes where Measure writes it
        self._measure(
            LENGTH,
            layout,
            folder,
            lambda d: (layout.scan_skeletons(d), []),
            layout.gt_skeletons_dir,
        )

    def calculate_root_area(self):
        """Measure the root area of every image with a mask: its GT mask
        (``gt/mask/<stem>.png``) where readable and the photo's size, else its ``*.png`` in the project's mask
        folder (O1)."""
        layout = self._layout()
        if layout is None:
            return
        folder = layout.resolve_masks_dir()
        if folder is None:
            if not self._has_gt(layout.gt_mask_dir):
                self._warn("No mask images found. Generate masks first.")
                return
            folder = layout.mask_dir  # GT only: the CSV goes where Measure writes it
        existing = [d for d in layout.mask_dir_candidates if os.path.isdir(d)]
        if len(existing) > 1:
            logger.warning("Both %s exist; measuring %s", " and ".join(existing), folder)
            # Generate and Correct save to mask/: say that it is left out (R7).
            others = ", ".join(os.path.relpath(d, layout.root) for d in existing if d != folder)
            self._warn(
                f"Measuring the older {os.path.relpath(folder, layout.root)} folder: "
                f"masks in {others} are not measured."
            )
        self._measure(AREA, layout, folder, scan_png_files, layout.gt_mask_dir)

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

    @staticmethod
    def _has_gt(gt_folder: str) -> bool:
        """True when ``gt_folder`` holds a ``*.png`` (one scandir)."""
        return bool(scan_png_files(gt_folder)[0])

    def _measure(
        self, spec: MetricSpec, layout: ProjectLayout, folder: str, scan, gt_folder: str
    ) -> None:
        """``scan(folder)`` -> ``(name -> path, shadowed)`` of the predictions; the GT files
        are the ``*.png`` of ``gt_folder``. Both are split by image (:func:`split_inputs`);
        the job decides, per image, which one it measures."""
        if self.is_busy(spec.kind):
            self._warn(f"{job_name(spec)} is already running.")
            return
        try:
            images, shadowed = scan(folder)
        except OSError as exc:
            self._cannot_read(folder, exc)
            return
        try:
            gt_files, gt_shadowed = scan_png_files(gt_folder)
        except OSError as exc:
            self._cannot_read(gt_folder, exc)
            return
        if not images and not gt_files:
            self._warn(f"No images to measure in {folder}.")
            return
        try:
            image_stems = layout.scan_images()
        except OSError as exc:
            self._cannot_read(layout.root, exc)
            return
        # O37: a mask or skeleton whose image is gone gets no row; the files stay on disk.
        # GT files follow the same rules; they are named with their folder (gt/...).
        split = split_inputs(images, image_stems, shadowed)
        gt_split = split_inputs(gt_files, image_stems, gt_shadowed)
        gt_prefix = os.path.relpath(gt_folder, layout.root).replace(os.sep, "/") + "/"
        without_image = [*split.without_image, *(gt_prefix + n for n in gt_split.without_image)]
        duplicates = [*split.duplicates, *(gt_prefix + n for n in gt_split.duplicates)]
        if not split.kept and not gt_split.kept:
            n = len(without_image)
            self._warn(
                f"No images to measure in {folder}: its {n:,} {_noun(spec, n)} "
                f"{'has' if n == 1 else 'have'} no image in the project."
            )
            return
        # O3: the project's field of view (sprouts_project.json), else the default.
        calibration = load_project_calibration(layout.root)
        started = self._start(
            MeasureRun(
                spec,
                split.kept,
                folder,
                project_root=layout.root,
                without_image=without_image,
                duplicates=duplicates,
                gt_files=gt_split.kept,
                photos=image_stems,
                calibration=calibration,
                ask_if_unchanged=True,
            )
        )
        if started and calibration.problem:  # said only for a run that really started
            self._warn(
                f"{PROJECT_FILENAME} was not used ({calibration.problem}): measured with the "
                f"default field of view {fov_label(default_calibration().calibration)}."
            )

    def _cannot_read(self, folder: str, exc: OSError) -> None:
        logger.exception("Cannot list %s", folder)
        QMessageBox.critical(
            self._parent(), "Measure", f"Cannot read the folder:\n{folder}\n\n{exc}"
        )

    def _start(self, job: MeasureRun) -> bool:
        if self.refuse_while_loading():  # R7: never while a folder loads
            return False
        spec = job.spec
        thread = MetricCalculatorThread(job)
        thread.finished.connect(partial(self._on_finished, thread))
        thread.failed.connect(partial(self._on_failed, thread))
        thread.cancelled.connect(partial(self._on_cancelled, thread))
        thread.unchanged.connect(partial(self._on_unchanged, thread))
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
        self._notify_skipped(thread.job, name)
        self._notify_gt_not_used(thread.job, name)
        self._notify_sizes(thread.job, rows)
        self._notify_non_binary(thread.spec, rows)

    def _notify_sizes(self, job: MeasureRun, rows: list[dict]) -> None:
        """Warn, once, about the sizes of the measured files: one warning for one cause.

        * The files of one source group have more than one size (M2): the photos differ,
          and one field of view was applied to all of them (sizes in the provenance,
          ``inputs.sizes_px``).
        * Files have another width / height ratio than the reference size the project file
          states (O3): the field of view spans each whole image, so it fits only files of the
          reference's shape.

        When both apply (an odd-shaped file among others), one warning says both."""
        fov = fov_label(job.calibration.calibration)
        sizes = mixed_sizes(rows)
        reference = job.calibration.reference_size_px
        n = aspect_mismatches(sizes_px(rows), reference)
        shape = None
        if n and reference is not None:
            shape = (
                f"another shape than the {reference[0]}×{reference[1]} reference in "
                f"{PROJECT_FILENAME}"
            )
        if sizes is None and shape is None:
            return
        if sizes is None:
            self._warn(
                f"{n:,} measured {'file' if n == 1 else 'files'} {'has' if n == 1 else 'have'} "
                f"{shape}: the field of view was applied to each whole image."
            )
            return
        listed = [f"{size} ×{count:,}" for size, count in list(sizes.items())[:_SIZES_LISTED]]
        if len(sizes) > _SIZES_LISTED:
            listed.append("…")
        head = f"The measured files have {len(sizes)} sizes ({', '.join(listed)})"
        if shape is None:
            self._warn(f"{head}: one {fov} field of view was applied to each.")
        else:
            self._warn(
                f"{head}, and {n:,} {'has' if n == 1 else 'have'} {shape}: "
                f"one {fov} field of view was applied to each whole image."
            )

    def _notify_non_binary(self, spec: MetricSpec, rows: list[dict]) -> None:
        """Warn, once, when measured masks have grey levels (M5, O10): the pixels above
        ``MASK_THRESHOLD`` were counted as root. The count is in the provenance
        (``outputs.n_non_binary``)."""
        if spec.kind != AREA.kind:
            return
        n = sum(1 for row in rows if row.get("_non_binary"))
        if n:
            self._warn(
                f"{n:,} {'mask' if n == 1 else 'masks'} {'has' if n == 1 else 'have'} grey "
                f"levels: pixels above {MASK_THRESHOLD} were counted as root."
            )

    def _notify_gt_not_used(self, job: MeasureRun, csv_name: str) -> None:
        """Say how many GT files were not measured, and why (named in the provenance)."""
        report = job.gt_report
        if report is None or not report.skipped:
            return
        reasons = [why for _stem, why in report.skipped]
        counts = [f"{reasons.count(key):,} {words}" for key, words in _SKIP_WORDS if key in reasons]
        n = len(reasons)
        self._warn(
            f"{n:,} GT {_noun(job.spec, n)} not used: {', '.join(counts)} "
            f"(named in {csv_name}.provenance.json)"
        )

    def _notify_skipped(self, job: MeasureRun, csv_name: str) -> None:
        """Say how many inputs were left out (O37) and where they are named."""
        spec = job.spec
        if job.without_image:
            n = len(job.without_image)
            self._warn(
                f"{n:,} {_noun(spec, n)} skipped: {'its' if n == 1 else 'their'} image is no "
                f"longer in the project (named in {csv_name}.provenance.json)"
            )
        if job.duplicates:
            n = len(job.duplicates)
            self._warn(
                f"{n:,} {_noun(spec, n)} skipped: "
                f"{'a file' if n == 1 else 'files'} with the same name apart from letter case "
                f"{'is' if n == 1 else 'are'} measured instead "
                f"(named in {csv_name}.provenance.json)"
            )

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

    def _on_unchanged(self, thread: MetricCalculatorThread) -> None:
        """Nothing changed since the CSV was written: end the run's task, then ask."""
        self._release(thread)
        job = thread.job
        name = os.path.basename(job.csv_path)
        self.feedback.task_finished(f"Nothing changed since the last measurement: {name} kept")
        self._ask_measure_again(job)

    def _ask_measure_again(self, job: MeasureRun) -> None:
        """Non-modal "Measure again?" prompt (default Cancel); Yes measures with no more
        questions. Deletes itself once answered; ``_again_box`` is the newest open prompt."""
        n = len(job.images)
        name = os.path.basename(job.csv_path)
        box = QMessageBox(self._parent())
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Measure")
        box.setText("Nothing changed since the last measurement. Measure again?")
        box.setInformativeText(
            f"{name} already holds the values of these {n:,} {_noun(job.spec, n)}."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Measure again")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.finished.connect(partial(self._on_again_answer, job, box))
        self._again_box = box
        box.open()

    def _on_again_answer(self, job: MeasureRun, box: QMessageBox, result: int) -> None:
        if self._again_box is box:  # an older prompt's answer keeps the newer box
            self._again_box = None
        if result != QMessageBox.StandardButton.Yes.value:
            return
        self._start(
            MeasureRun(
                job.spec,
                job.predictions,
                job.output_dir,
                project_root=job.project_root,
                without_image=job.without_image,
                duplicates=job.duplicates,
                gt_files=job.gt_files,
                photos=job.photos or None,
                calibration=job.calibration,
            )
        )

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

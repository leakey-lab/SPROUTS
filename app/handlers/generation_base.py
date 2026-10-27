"""Plumbing shared by the mask and skeleton generation handlers.

``GenerationHandlerBase`` gets its dependencies (``UiFeedback`` and the
``JobRegistry``) from :class:`~app.handlers.deps.HandlerDependencies`, and owns
the project folder lookup (``ImageManager.project_layout``), starting a
worker as a named job (RB-07: refused politely while another job runs) and
wiring its Cancel button (PI-08), and the outcomes both kinds share (every input
failed, a partial failure, Cancel). ``show_failure_report`` lists the inputs that
produced no output in ONE non-modal dialog, so a long unattended Generate
(masks, then skeletons) never stalls on a modal box (UXS-02). A run in which
every input failed (:func:`every_input_failed`) ends as a failure, so nothing
chains on it (R6). Both workers build on :class:`GenerationThread`; the slots
connected to a worker's signals hold it weakly (:func:`thread_slot`), so a finished
worker and its report are freed as soon as the handler lets it go.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.feedback import UiFeedback
from app.handlers.deps import HandlerDependencies
from app.handlers.job_registry import JobRegistry
from app.project_layout import project_layout_of
from app.workers import GuardedThread, JobCancelled

if TYPE_CHECKING:
    from app.inference.batch_engine import BatchReport

logger = logging.getLogger(__name__)

_REPORT_PREVIEW = 20  # failed names listed in the dialog text (all go to the details)


class GenerationThread(GuardedThread):
    """A generation run off the GUI thread; ``work()`` hands its report to :meth:`_conclude`.

    Signals (each subclass declares its own ``finished``, with its legacy payload):
        progress_counts(int, int): (done, total) inputs.
    ``report`` holds the ``BatchReport`` once the run ended (completed or cancelled);
    ``completed`` turns True only when the run finished without cancel or error and
    did not fail on every input it attempted (R6: such a run is a failure, so
    nothing chains on it).
    """

    progress_counts = pyqtSignal(int, int)

    def __init__(self, input_dir: str, output_dir: str) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.report: BatchReport | None = None
        self.completed = False

    def _conclude(self, report: BatchReport) -> None:
        """Keep ``report``; raise ``JobCancelled`` after a Cancel, else set ``completed``."""
        self.report = report
        # A Cancel during the last batch leaves report.cancelled False (every
        # image was done); it is still a cancel, so nothing chains (wf2 R1).
        if report.cancelled or self.is_cancelled():
            raise JobCancelled
        # When every input failed (e.g. out of memory even on single images) nothing
        # was written: ``finished`` still reports it, but nothing chains (R6).
        self.completed = not every_input_failed(report)


def thread_slot(method: Callable[..., Any], thread: QThread) -> Callable[..., None]:
    """A slot for ``thread``'s signals: ``method(thread, *args)`` while the thread lives.

    ``partial(method, thread)`` would let the thread's own slot proxies hold it, a cycle
    that only the cyclic GC breaks, and never once ``gc.freeze()`` has frozen it (C3-05,
    as the loader thread of ``ImageManager``). With a weak reference, reference counting
    frees a finished thread as soon as its handler lets it go.
    """
    ref = weakref.ref(thread)

    def slot(*args: Any) -> None:
        alive = ref()
        if alive is not None:
            method(alive, *args)

    return slot


class GenerationHandlerBase(HandlerDependencies):
    """Dependencies + job lifecycle for one kind of generation run.

    Subclasses set ``job_label`` (the job's user-visible name), ``title`` and ``noun``
    (the run and what it writes, in its messages) and keep the running worker in
    ``self._thread`` via :meth:`_start`.
    """

    job_label = "Generating"
    title = "Generation"
    noun = "outputs"

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        super().__init__(main_window, feedback=feedback, jobs=jobs)
        # Strong reference: a running QThread must never be garbage-collected (F-025).
        self._thread: GuardedThread | None = None
        self._report_box: QMessageBox | None = None

    def parent_widget(self) -> QWidget | None:
        return self.main_window if isinstance(self.main_window, QWidget) else None

    # ------------------------------------------------------------------ state
    def is_busy(self) -> bool:
        """True while this handler's worker is running."""
        return self._thread is not None and self._thread.isRunning()

    def project_folder(self) -> str | None:
        """The loaded project's folder (``ImageManager.project_layout``), or None."""
        layout = project_layout_of(getattr(self.main_window, "image_manager", None))
        return layout.root if layout is not None else None

    def refuse_if_busy(self) -> bool:
        """True (and a warning toast) while this handler's worker is running."""
        if self.is_busy():
            self.feedback.notify(f"{self.job_label} is already running", "warn")
            return True
        return False

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
        after a cancel (even one that landed during the last batch) or a failure,
        including a run in which every input failed (R6). A value that is not
        callable chains nothing: a button's ``clicked`` connected straight to a
        handler passes its ``checked`` flag (False) as ``on_success``. Refused (False)
        while a folder loads (R7).
        """
        if self.refuse_while_loading() or self.refuse_if_busy():
            thread.deleteLater()
            return False
        if not self.jobs.try_start(self.job_label, thread):
            running = self.jobs.running_name() or "Another task"
            self.feedback.notify(f"{running} is running. Try again when it has finished.", "warn")
            thread.deleteLater()
            return False
        self._thread = thread
        if callable(on_success):
            self._chain_on_success(thread, on_success)
        return True

    @staticmethod
    def _chain_on_success(thread: GuardedThread, on_success: Callable[[], None]) -> None:
        # QThread's own finished (the subclasses shadow ``finished``) is emitted
        # after run() returned; the registry connected to it first, so it has
        # already cleared the job when this queued slot runs.
        thread_finished = QThread.finished.__get__(thread, QThread)

        def fire(ended: GuardedThread) -> None:
            # ``completed`` stays False after any Cancel, including one that landed
            # during the last batch (the worker checks is_cancelled() itself; Qt
            # clears the interruption flag once the thread has finished), and after
            # a run in which every input failed (R6: every_input_failed).
            if getattr(ended, "completed", False):
                QTimer.singleShot(0, on_success)

        thread_finished.connect(thread_slot(fire, thread))

    def begin_task(self, label: str, thread: GuardedThread) -> None:
        """Show ``label`` as the running task, with Cancel wired to the worker."""
        self.feedback.task_started(label, cancelable=True, on_cancel=thread.cancel)

    # ------------------------------------------------------------------ outcomes
    def _ended_with_no_output(self, report: BatchReport, output_dir: str) -> bool:
        """True when every input failed (R6): the task has failed and the one report is shown."""
        if not every_input_failed(report):
            return False
        message = f"{self.title} failed: none of {len(report.failed):,} images could be processed"
        logging.getLogger(type(self).__module__).error(message)  # under the handler's own name
        self.feedback.task_failed(message)
        self._report_box = show_failure_report(
            self.parent_widget(), self.title, output_dir, report.failed, nothing_written=True
        )
        return True

    def _report_failures(self, output_dir: str, failed: list[tuple[str, str]]) -> None:
        """A warning toast and the one non-modal report of the inputs that got no output."""
        self.feedback.notify(f"{len(failed):,} images could not be processed", "warn")
        self._report_box = show_failure_report(self.parent_widget(), self.title, output_dir, failed)

    def _on_cancelled(self, thread: GenerationThread) -> None:
        report = thread.report
        done = f" after {report.done:,} of {report.total:,} images" if report else ""
        self.feedback.task_finished(f"{self.title} cancelled{done}")
        self.feedback.notify(
            f"{self.title} cancelled{done}; finished {self.noun} were kept", "info"
        )
        self.outputs_changed()  # the kept outputs: badges, display and metrics follow


def every_input_failed(report: BatchReport) -> bool:
    """True when the run attempted inputs and every input failed: none has an output (R6).

    Such a run is a failure, not a success with 0 outputs: its worker leaves
    ``completed`` False (nothing chains) and its handler ends the task with
    ``task_failed``. A run with nothing to do (no inputs, or every input kept by
    the overwrite policy) is not one, nor is a run that kept some existing outputs:
    those inputs did not fail, and Generate still chains the skeleton pass, which
    reads the images, not the masks.
    """
    return bool(report.failed) and not report.written and not report.skipped


def show_failure_report(
    parent: QWidget | None,
    title: str,
    output_dir: str,
    failed: list[tuple[str, str]],
    *,
    nothing_written: bool = False,
) -> QMessageBox:
    """One non-modal warning listing the inputs that got no output (and why).

    ``nothing_written``: no input of the run got an output (R6), so the text
    mentions no other outputs.
    """
    names = [name for name, _ in failed]
    preview = "\n".join(names[:_REPORT_PREVIEW])
    if len(names) > _REPORT_PREVIEW:
        preview += f"\n… and {len(names) - _REPORT_PREVIEW:,} more"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(f"{len(names):,} image(s) could not be processed and have no output.")
    if nothing_written:
        box.setInformativeText(
            f"{preview}\n\nNo output was written. The run manifest (_sprouts_run.json) in\n"
            f"{output_dir}\nlists every failure."
        )
    else:
        box.setInformativeText(
            f"{preview}\n\nOther outputs were saved to:\n{output_dir}\n"
            "The run manifest (_sprouts_run.json) there lists every failure."
        )
    box.setDetailedText("\n".join(f"{name}: {reason}" for name, reason in failed))
    box.setModal(False)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.show()
    return box


__all__ = [
    "GenerationHandlerBase",
    "GenerationThread",
    "every_input_failed",
    "show_failure_report",
    "thread_slot",
]

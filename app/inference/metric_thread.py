"""Qt thread that runs one :class:`~app.inference.measure_run.MeasureRun` off the GUI thread."""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import pyqtSignal

from app.handlers.worker_base import GuardedThread
from app.inference.measure_run import MeasureRun, MetricSpec

logger = logging.getLogger(__name__)


class MetricCalculatorThread(GuardedThread):
    """Measure every image, then write the CSV; never lets an exception escape.

    Signals:
        finished(str): the CSV path ("" when there was nothing to measure).
            Shadows ``QThread.finished`` (the app-wide contract for workers).
        progress(int): percent of images measured (0..100).
        failed(str): user-facing error (e.g. the CSV is open in Excel); the
            measured rows stay on :attr:`job`, so ``MetricCalculatorThread(job)``
            retries only the write.
        cancelled(): stopped after ``cancel()``; no CSV was written.
    """

    finished = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, job: MeasureRun) -> None:
        super().__init__()
        self.job = job

    @property
    def spec(self) -> MetricSpec:
        return self.job.spec

    @property
    def output_dir(self) -> str:
        return self.job.output_dir

    def work(self) -> None:
        name = type(self).__name__
        if not self.job.images:
            logger.debug("%s: no images, skipping", name)
            self.finished.emit("")
            return
        logger.info("%s: processing %d images → %s", name, len(self.job.images), self.output_dir)
        t0 = time.monotonic()
        self.job.compute(self.progress.emit, should_stop=self.is_cancelled)
        self.check_cancelled()
        csv_path = self.job.write()
        logger.info("%s: done in %.1fs, wrote %s", name, time.monotonic() - t0, csv_path)
        self.finished.emit(csv_path)

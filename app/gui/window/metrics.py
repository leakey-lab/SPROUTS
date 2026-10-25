"""The metrics controller: the Library's read-only metrics bar and the Measure CSVs behind it.

Status comes from the mask / skeleton state in ``image_manager``. Root length / area are
the values the Measure run wrote to its CSVs, read through :class:`MeasurementIndex` (never
computed here, never parsed on the GUI thread: an unparsed CSV is parsed in the thread
pool, then the bar is refreshed again).

:class:`MetricsController` owns that state; :class:`~app.gui.main_window.MainWindow` holds
it as ``metrics`` and keeps its old names (``_refresh_metrics_bar``, ``_measurements``, …)
as thin delegators for the code and tests that use them. The window's own code calls the
controller directly, so the browse path costs no extra call.
"""

import logging
from typing import TYPE_CHECKING, Any, Protocol

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from app.data_processing.measurement_index import Measurement, MeasurementIndex

from ..image_manager import project_layout_of
from ._common import USER_ROLE

logger = logging.getLogger(__name__)

#: The LENGTH tooltip's line for an image with a skeleton correction (FR-03).
CORRECTION_NOT_MEASURED = (
    "A corrected skeleton exists (skeleton_corrections/); it is not measured: "
    "Measure reads the generated skeleton."
)
#: The STATUS tooltip of an image with a skeleton correction (W17, display only).
CORRECTED_STATUS_TIP = (
    "Overlay and Side by side draw the corrected skeleton (skeleton_corrections/); "
    "Measure reads the generated skeleton (skeletons/), so root length is the generated one's."
)

if TYPE_CHECKING:

    class MetricsHost(Protocol):
        """What the controller reads from the window, when a call runs: ``file_list`` and
        ``metrics_bar`` exist only once ``init_ui`` has run (``metrics_bar`` may be absent)."""

        image_manager: Any
        display_controller: Any
        stage_status: Any
        file_list: Any

        def _current_stage(self) -> str | None: ...


class _MeasurementSignals(QObject):
    """Carries 'CSV parsed' from a pool thread back to the GUI thread.

    Unparented and kept alive by the worker's closure, so a worker that ends
    after the window was destroyed emits into nothing instead of a dead object.
    """

    ready = pyqtSignal(object)


class MetricsController:
    """The metrics bar of the selected image, and the measurement CSVs it reads.

    ``index`` is the project's :class:`MeasurementIndex` (None before a folder loads),
    ``loading`` is True while a pool job parses its CSVs, and ``signals`` carries that
    job's result back. The window connects ``signals.ready`` to its own slot
    (``MainWindow._on_measurements_ready``), so the slot runs on the GUI thread and a job
    that ends after the window was destroyed reaches nothing.
    """

    def __init__(self, window: "MetricsHost") -> None:
        self._window = window
        self.index: MeasurementIndex | None = None
        self.loading = False
        self.signals = _MeasurementSignals()

    def image_has_mask(self, stored_name):
        """Return True if the image has a saved mask.

        ``image_manager`` is the single source of truth: filled by the folder scan,
        updated on ``mask_saved`` / ``mask_cleared`` and after generation runs.
        """
        return self._window.image_manager.has_mask(stored_name)

    def image_has_skeleton(self, stored_name):
        """Return True if a processed ``<name>_fake.png`` skeleton is known for the image."""
        return self._window.image_manager.has_skeleton(stored_name)

    def image_has_correction(self, stored_name) -> bool:
        """True when a skeleton correction of the image is known (display only, FR-03)."""
        has_correction = getattr(self._window.image_manager, "has_correction", None)
        return bool(stored_name and callable(has_correction) and has_correction(stored_name))

    def current_image_name(self) -> str | None:
        """Stem of the image selected in the library (else the one on the display)."""
        file_list = getattr(self._window, "file_list", None)
        item = file_list.currentItem() if file_list is not None else None
        name = item.data(0, USER_ROLE) if item is not None else None
        return name or getattr(self._window.display_controller, "current_name", None)

    def refresh(self, stored_name=None):
        """Update the read-only metrics strip for the selected image.

        Status comes from the mask / skeleton state in ``image_manager``. Root
        length / area are the values the Measure run wrote to its CSVs, read
        through :class:`MeasurementIndex` (never computed here, never parsed on
        the GUI thread: an unparsed CSV is parsed in the pool, then this runs again).
        """
        bar = getattr(self._window, "metrics_bar", None)
        if bar is None:
            return

        status = None
        corrected = self.image_has_correction(stored_name)
        if stored_name:
            if corrected:
                status = "Skeleton corrected"
            elif self.image_has_skeleton(stored_name):
                status = "Skeleton ready"
            elif self.image_has_mask(stored_name):
                status = "Mask ready"
            else:
                status = "No mask"

        measured = Measurement(None, None, None)
        outdated: dict[str, bool] = {}
        sources: dict[str, str | None] = {}
        by_content: dict[str, bool] = {}
        index = self.index
        if stored_name and index is not None:
            try:
                measured = index.lookup(stored_name, load=False)
                # needs_load() waits for the CSV lock: never while a worker parses (its
                # ready signal refreshes the bar).
                if not self.loading and index.needs_load():
                    self.prewarm()
                # Each value against its own input: a saved mask outdates AREA, never LENGTH.
                outdated = index.outdated(stored_name)
                sources = index.sources()
                # Without a record of its inputs, a CSV's staleness is by file time (R5).
                by_content = index.by_content()
            except Exception:
                logger.exception("Could not read the measured values of %s", stored_name)

        bar.set_metrics(
            length=measured.length_mm,
            area=measured.area_mm2,
            status=status,
            name=stored_name,
            error=measured.error,
            stale=outdated.get("length", False),
            source=sources.get("length"),
            area_stale=outdated.get("area", False),
            area_source=sources.get("area"),
            length_note=CORRECTION_NOT_MEASURED if corrected else None,
            status_tip=CORRECTED_STATUS_TIP if corrected else None,
            by_time=not by_content.get("length", True),
            area_by_time=not by_content.get("area", True),
        )

    def refresh_if_current(self, image_name):
        """Refresh the bar when ``image_name`` is the library's selected image."""
        item = self._window.file_list.currentItem()
        if item is not None and item.data(0, USER_ROLE) == image_name:
            self.refresh(image_name)

    def prewarm(self, *, locate: bool = False) -> None:
        """Parse new / changed measurement CSVs in the thread pool (once at a time).

        ``locate``: run even when no CSV is known yet: the pool job also looks in the
        legacy folders, which the GUI thread never walks (once per opened project)."""
        index = self.index
        if index is None or self.loading:
            return
        if not locate and not index.needs_load():
            return
        self.loading = True
        signals = self.signals

        def work(index=index, signals=signals):
            try:
                index.prewarm()
            except BaseException:  # noqa: BLE001 - SystemExit too: it must report back
                # Unreported, the metrics would stay loading for good: no parse after it,
                # the warm-ups blocked and the bar never refreshed.
                logger.exception("Could not read the measurement CSVs")
            try:
                signals.ready.emit(index)
            except RuntimeError:
                pass  # the window is gone

        QThreadPool.globalInstance().start(work)

    def on_ready(self, index) -> None:
        """A pool job has parsed the CSVs of ``index``: the bar shows them, and the Measure
        stage's counts follow (a CSV may have been found or re-read)."""
        self.loading = False
        if index is self.index:
            self.refresh(self.current_image_name())
            if self._window._current_stage() == "Measure":
                self.check_freshness()  # a CSV may have been found or re-read

    def check_freshness(self) -> None:
        """Count Measure's inputs written after each CSV, in the thread pool (W12)."""
        self._window.stage_status.check(
            self.index,
            project_layout_of(self._window.image_manager),
            locate=not self.loading,  # never wait for a parse in the pool
        )

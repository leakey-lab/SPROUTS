"""The metrics controller: the Library's read-only metrics bar and the Measure CSVs behind it.

Status comes from the mask / skeleton state in ``image_manager``, in the Library badge's
words (:func:`app.gui.library_index.status_text`). Root length / area are the values the
Measure run wrote to its CSVs, read through :class:`MeasurementIndex` (never computed here,
never parsed on the GUI thread: an unparsed CSV is parsed in the thread pool, then the bar
is refreshed again). Each value's tooltip names the file it was measured from (the GT file
or the prediction, O1) and, when the image's GT file was not used, why.

:class:`MetricsController` owns that state; :class:`~app.gui.main_window.MainWindow` holds
it as ``metrics`` and keeps its old names (``_refresh_metrics_bar``, ``_measurements``, …)
as thin delegators for the code and tests that use them. The window's own code calls the
controller directly, so the browse path costs no extra call.
"""

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from app.calibration import (
    ProjectCalibration,
    calibration_record,
    calibration_words,
    load_project_calibration,
)
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.gt.files import SOURCE_GT, SOURCE_NONE, SOURCE_PREDICTION
from app.inference.measure_sources import SIZE, UNEDITED, UNREADABLE

from ..image_manager import project_layout_of
from ..library_index import status_text
from ..metrics_bar import differs_from_current, fov_owner_tip, fov_text
from ._common import USER_ROLE

logger = logging.getLogger(__name__)

#: The LENGTH / AREA tooltip line for an image whose GT file the last Measure run did not
#: use (O1): ``noun`` "skeleton" / "mask", ``folder`` "skeletons" / "mask", ``why`` the reason.
GT_NOT_USED = (
    "A GT {noun} exists (gt/{folder}/) but was not used: {why}. Measure read the prediction."
)
#: The STATUS tooltip of an image with a GT skeleton (W17).
GT_STATUS_TIP = (
    "Overlay and Side by side draw the GT skeleton (gt/skeletons/) when one exists, "
    "else the prediction (skeletons/)."
)
#: Why a GT file was not used, in :data:`GT_NOT_USED`'s words. A GT skeleton and a GT mask
#: are both checked against their photo, so both notes name the photo.
_WHY = {
    "skeleton": {
        SIZE: "it is not the photo's size",
        UNEDITED: "it equals the prediction",
        UNREADABLE: "it or its photo could not be read",
    },
    "mask": {
        SIZE: "it is not the photo's size",
        UNREADABLE: "it or its photo could not be read",
    },
}
_FOLDER = {"skeleton": "skeletons", "mask": "mask"}

if TYPE_CHECKING:

    class MetricsHost(Protocol):
        """What the controller reads from the window, when a call runs: ``file_list`` and
        ``metrics_bar`` exist only once ``init_ui`` has run (``metrics_bar`` may be absent)."""

        image_manager: Any
        display_controller: Any
        stage_status: Any
        file_list: Any

        def _current_stage(self) -> str | None: ...


def _source(measured_from: tuple[str, str] | None, csv_path: str | None) -> str | None:
    """What a value was read from, for its tooltip ("Measured value from …"): the file it
    was measured from (``gt/skeletons/g.png (GT)``, ``skeletons/p_fake.png (prediction)``)
    and the CSV that holds it."""
    if measured_from is None:
        return csv_path
    path, source = measured_from
    label = f"{path} ({'GT' if source == SOURCE_GT else 'prediction'})"
    return f"{label}, recorded in {csv_path}" if csv_path else label


def _gt_note(
    noun: str, state: str, reason: str | None, outdated: bool | None, source: str | None
) -> str | None:
    """:data:`GT_NOT_USED` when the image has a GT ``noun`` that its value's Measure run did
    not use (``reason``) and the row says so (``source`` is ``prediction``); ``None``
    otherwise, and for an outdated value (its tip says to measure again: a GT file saved
    after the CSV is new, not skipped)."""
    why = _WHY[noun].get(reason or "")
    if state != SOURCE_GT or source != SOURCE_PREDICTION or why is None or outdated:
        return None
    return GT_NOT_USED.format(noun=noun, folder=_FOLDER[noun], why=why)


def _shown_fov(
    measured: Measurement,
    calibrations: Mapping[str, Mapping[str, float] | None],
    csvs: Mapping[str, str | None],
    current: ProjectCalibration | None = None,
) -> tuple[str, str]:
    """``(text, tooltip)`` of the FOV cell: the field of view recorded with the CSV of the
    shown value (length's, else area's), and when that differs from ``current`` (the open
    project's, O3; ``None``: the running default) the line saying so; without such a
    record, ``current``'s (M2). The texts say "project" for a project file's calibration,
    else "app" (:func:`app.calibration.calibration_words`)."""
    ours = None if current is None else calibration_record(current)
    words = calibration_words(current)
    shown = (("length", measured.length_mm), ("area", measured.area_mm2))
    for metric, value in shown:
        recorded = calibrations.get(metric)
        if value is None or recorded is None:
            continue
        text = fov_text(recorded)
        name = os.path.basename(csvs.get(metric) or "") or "its CSV"
        tip = f"Field of view recorded with {name}"
        if differs_from_current(recorded, ours):
            tip += f"\n{words.uses.capitalize()} {fov_text(ours)}"
        return text, tip
    return fov_text(ours), fov_owner_tip(current)


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
        # A located prewarm asked for while a parse ran: it starts when that parse reports.
        self._locate_pending = False

    def image_has_mask(self, stored_name):
        """Return True if the image has a saved mask.

        ``image_manager`` is the single source of truth: filled by the folder scan,
        updated on ``mask_saved`` / ``mask_cleared`` and after generation runs.
        """
        return self._window.image_manager.has_mask(stored_name)

    def image_has_skeleton(self, stored_name):
        """Return True if a processed ``<name>_fake.png`` skeleton is known for the image."""
        return self._window.image_manager.has_skeleton(stored_name)

    def image_has_gt_skeleton(self, stored_name) -> bool:
        """True when a GT skeleton of the image is known (Measure reads it first, O1)."""
        has_gt = getattr(self._window.image_manager, "has_gt_skeleton", None)
        return bool(stored_name and callable(has_gt) and has_gt(stored_name))

    def current_calibration(self) -> ProjectCalibration:
        """The open project's field of view (its ``sprouts_project.json``, O3), else the
        app's default (also without a project). Read when the file changed (one stat)."""
        index = self.index
        return load_project_calibration(index.root if index is not None else None)

    def current_image_name(self) -> str | None:
        """Stem of the image selected in the library (else the one on the display)."""
        file_list = getattr(self._window, "file_list", None)
        item = file_list.currentItem() if file_list is not None else None
        name = item.data(0, USER_ROLE) if item is not None else None
        return name or getattr(self._window.display_controller, "current_name", None)

    def refresh(self, stored_name=None):
        """Update the read-only metrics strip for the selected image.

        Status comes from the mask / skeleton state in ``image_manager``, in the Library
        badge's words. Root length / area are the values the Measure run wrote to its CSVs,
        read through :class:`MeasurementIndex` (never computed here, never parsed on the GUI
        thread: an unparsed CSV is parsed in the pool, then this runs again). Each value's
        tooltip names the file it was measured from (GT or prediction) and its CSV, and
        says when the image's GT file was not used and why (a GT file saved after the CSV
        shows the outdated tip instead).
        """
        bar = getattr(self._window, "metrics_bar", None)
        if bar is None:
            return

        status = None
        mask_state = skeleton_state = SOURCE_NONE
        if stored_name:
            manager = self._window.image_manager
            mask_state = manager.mask_state(stored_name)
            skeleton_state = manager.skeleton_state(stored_name)
            status = status_text(mask_state, skeleton_state, manager.has_gt_labels(stored_name))

        measured = Measurement(None, None, None)
        outdated: dict[str, bool] = {}
        csvs: dict[str, str | None] = {}
        by_content: dict[str, bool] = {}
        skipped: dict[str, str] = {}
        measured_from: dict = {}
        calibrations: dict = {}
        method_notes: dict[str, str] = {}
        current = self.current_calibration()
        index = self.index
        if stored_name and index is not None:
            try:
                measured = index.lookup(stored_name, load=False)
                # needs_load() waits for the CSV lock: never while a worker parses (its
                # ready signal refreshes the bar).
                if not self.loading and index.needs_load():
                    self.prewarm()
                # Each value against its own files: a saved mask outdates AREA, never LENGTH.
                outdated = index.outdated(stored_name)
                csvs = index.sources()
                # Without a record of its inputs, a CSV's staleness is by file time (R5).
                by_content = index.by_content()
                skipped = index.gt_skip_reason(stored_name)
                measured_from = index.measured_from(stored_name)
                calibrations = index.calibrations()
                # Measured with another field of view or length method: outdated (O3).
                method_notes = index.method_notes(current)
            except Exception:
                logger.exception("Could not read the measured values of %s", stored_name)

        bar.set_fov(*_shown_fov(measured, calibrations, csvs, current))
        bar.set_metrics(
            length=measured.length_mm,
            area=measured.area_mm2,
            status=status,
            name=stored_name,
            error=measured.error,
            stale=outdated.get("length", False),
            source=_source(measured_from.get("length"), csvs.get("length")),
            area_stale=outdated.get("area", False),
            area_source=_source(measured_from.get("area"), csvs.get("area")),
            length_note=_gt_note(
                "skeleton",
                skeleton_state,
                skipped.get("length"),
                outdated.get("length"),
                measured.length_source,
            ),
            status_tip=GT_STATUS_TIP if skeleton_state == SOURCE_GT else None,
            by_time=not by_content.get("length", True),
            area_by_time=not by_content.get("area", True),
            area_note=_gt_note(
                "mask", mask_state, skipped.get("area"), outdated.get("area"), measured.area_source
            ),
            method_notes=method_notes,
        )

    def refresh_if_current(self, image_name):
        """Refresh the bar when ``image_name`` is the library's selected image."""
        item = self._window.file_list.currentItem()
        if item is not None and item.data(0, USER_ROLE) == image_name:
            self.refresh(image_name)

    def prewarm(self, *, locate: bool = False) -> None:
        """Parse new / changed measurement CSVs in the thread pool (once at a time).

        ``locate``: run even when no CSV is known yet: the pool job also looks in the
        legacy folders, which the GUI thread never walks (when a project opens, and after a
        Measure run). Asked for while a parse runs, it starts when that parse reports: the
        parse may be the previous project's, or may have searched before a run's CSV was
        written."""
        index = self.index
        if index is None:
            return
        if self.loading:
            self._locate_pending = self._locate_pending or locate
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
        stage's counts follow (a CSV may have been found or re-read). A located prewarm
        refused meanwhile starts first; its own report refreshes the bar."""
        self.loading = False
        if self._locate_pending:
            self._locate_pending = False
            self.prewarm(locate=True)
            if self.loading:
                return
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

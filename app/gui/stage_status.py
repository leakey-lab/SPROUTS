"""What the Generate and Measure stages would work on, for the status line (W12).

The Qt side of :mod:`app.gui.stage_summary`: :meth:`StageStatus.text` phrases the library
state for the stage on screen, and :meth:`StageStatus.check` counts, in the thread pool,
each metric's inputs and those changed since its CSV (one ``os.scandir`` per folder
and one of the images,
~60 ms at 46k files, never on the GUI thread; against the CSV's record of its inputs when
there is one, R5); ``changed`` is emitted once that count is in. A newer check, or
:meth:`StageStatus.reset` (another project), makes an older count void.

Generate's phrase walks every image name twice (~5 ms on the GUI thread at 46k images), and
the status line asks for it on every idle refresh while Generate is on screen (V8). It is
counted again only when the library's outputs change: see :meth:`StageStatus.text`.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from app.inference.measure_inputs import load_inputs_record
from app.project_layout import SKELETON_SUFFIX

from .stage_summary import Freshness, generate_text, input_freshness, measure_text

logger = logging.getLogger(__name__)

__all__ = ["StageStatus"]


class _Signals(QObject):
    """Carries a pool count back to the GUI thread (unparented and kept alive by the job's
    closure: a job that ends after its StageStatus is gone emits into nothing)."""

    ready = pyqtSignal(object, object)  # (token, {"length": Freshness, "area": Freshness})


class StageStatus(QObject):
    """The Generate / Measure idle text of one window.

    Signals:
        changed(): Measure's counts came in (read :meth:`text` again).
    """

    changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._freshness: dict[str, Freshness] | None = None
        self._token: object | None = None
        # Generate's phrase and what it was counted from: (images, masks, skeletons,
        # their sizes, text). The collections themselves are held, so none of them can be
        # freed and a new one take its id while the phrase is kept.
        self._generate: tuple | None = None
        self._signals = _Signals()
        self._signals.ready.connect(self._on_ready)

    def text(self, stage: str | None, manager) -> str | None:
        """What a run of ``stage`` would work on, from ``manager`` (the window's
        ImageManager: ``images``, ``masks``, ``skeleton_stems``); None on other stages or
        without images."""
        images = getattr(manager, "images", None)
        if not images:
            return None
        if stage == "Generate":
            return self._generate_text(images, manager.masks, manager.skeleton_stems)
        if stage == "Measure":
            fresh = self._freshness or {}
            return measure_text(
                len(manager.skeleton_stems),
                len(manager.masks),
                fresh.get("length"),
                fresh.get("area"),
            )
        return None

    def _generate_text(self, images, masks, skeletons) -> str:
        """:func:`generate_text`, counted again only when the outputs changed (V8).

        The library replaces its collections when a folder loads and when a run's outputs
        are re-read; a mask saved or cleared in an editor adds or removes the image's key
        in place, which changes the size of ``masks``. Only library images get a mask that
        way, so its count of images without a mask follows the size of ``masks`` alone.
        """
        sizes = (len(images), len(masks), len(skeletons))
        kept = self._generate
        if (
            kept is not None
            and kept[0] is images
            and kept[1] is masks
            and kept[2] is skeletons
            and kept[3] == sizes
        ):
            return kept[4]
        text = generate_text(images, masks, skeletons)
        self._generate = (images, masks, skeletons, sizes, text)
        return text

    def reset(self) -> None:
        """Forget the counts (another project): an older count still on its way is void."""
        self._freshness = self._token = self._generate = None

    def check(self, index, layout, *, locate: bool = True) -> None:
        """Count each metric's inputs, and those changed since its CSV, in the thread pool.

        ``index`` is the window's MeasurementIndex (where the CSVs are); ``locate`` lets it
        look for them first (a few stats; pass False while a pool job parses them, since
        that call would wait for it). The inputs are the files next to each CSV, or the
        folders Measure would read when there is no CSV yet. Each CSV's record of its inputs
        (``<csv>.inputs.json``) is read in the pool too; with one, content is compared.
        """
        if index is None or layout is None:
            return
        if locate:
            index.needs_load()
        sources = index.sources()
        length_csv, area_csv = sources.get("length"), sources.get("area")
        length_dir = os.path.dirname(length_csv) if length_csv else layout.skeletons_dir
        if area_csv:
            area_dir = os.path.dirname(area_csv)
        else:
            area_dir = layout.resolve_masks_dir() or layout.mask_dir
        token = self._token = object()
        signals = self._signals
        root = layout.root

        def freshness(folder, suffix, csv_path, images) -> Freshness:
            record = load_inputs_record(csv_path) if csv_path else None
            return input_freshness(
                folder, suffix, csv_path, record=record, project_root=root, images=images
            )

        def work() -> None:
            try:
                images = layout.scan_images()  # inputs whose image is gone are skipped (O37)
                result = {
                    "length": freshness(length_dir, SKELETON_SUFFIX, length_csv, images),
                    "area": freshness(area_dir, ".png", area_csv, images),
                }
            except Exception:  # noqa: BLE001 - the idle text then keeps the plain counts
                logger.exception("Could not count the Measure inputs")
                return
            try:
                signals.ready.emit(token, result)
            except RuntimeError:
                pass  # the window is gone

        QThreadPool.globalInstance().start(work)

    def _on_ready(self, token, result) -> None:
        if token is not self._token:
            return  # an older count (another project, or a newer count is on its way)
        self._freshness = result
        self.changed.emit()

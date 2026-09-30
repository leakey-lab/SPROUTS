"""Library state: which images a project folder has, and which have masks / skeletons.

:class:`ImageManager` is the single source of truth for per-image output status
(``has_mask`` / ``has_skeleton``): it is filled by one folder scan and kept current by
the editors' save/clear signals and by :meth:`ImageManager.refresh_outputs` after a
generation run, so nothing on the GUI thread stats files per image.

:class:`ImageLoaderThread` scans a folder off the GUI thread and also builds the library
hierarchy there. Opening another folder never kills a scan: the old one is asked to stop
and its late result is dropped by a generation token.

Once a folder has loaded, its output folders are swept for the temp files of killed
writes (:func:`sweep_project_temp_files`), on a daemon thread (R8).
"""

import logging
import os
import threading
from functools import partial

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFileDialog

from app.gui.library_index import index_names
from app.io_utils import sweep_stale_temp_files
from app.naming import image_stem, parse_image_name
from app.project_layout import ProjectLayout

logger = logging.getLogger(__name__)

# The sweep starts this long after a folder has loaded: it lists the same 46k-file folders
# as the scan (+15 % scan time when both ran at once) and should not compete with the
# first image either (R8).
TEMP_SWEEP_DELAY_MS = 2000


def sweep_project_temp_files(layout: ProjectLayout) -> list[str]:
    """Delete the stale temp files of killed writes in ``layout``'s output folders (R8).

    Every folder of :attr:`ProjectLayout.output_dirs`; ``gt/`` also loses the
    ``<name>.tmp`` the GT writers used before R8. Returns the removed paths; never
    raises for I/O (see :func:`app.io_utils.sweep_stale_temp_files`).
    """
    removed: list[str] = []
    for folder in layout.output_dirs:
        legacy = layout.is_legacy_gt_temp if folder == layout.gt_dir else None
        removed += sweep_stale_temp_files(folder, also=legacy)
    return removed


class ImageLoaderThread(QThread):
    """Scans ``folder_path`` for images, masks and skeletons, and builds the hierarchy.

    Signals:
        progress(int): percent, per scan phase.
        finished(images, fake_images, masks, has_fake_real_pairs): the scan result
            (``fake_images`` is always empty; skeleton paths are resolved lazily).
        failed(str): the folder is missing or unreadable (``finished`` is not emitted).

    An interrupted scan (``requestInterruption()``) emits nothing. After ``finished``,
    ``hierarchy``, ``labels`` (library row texts), ``skeleton_stems`` and
    ``corrected_stems`` (images with a skeleton correction) hold the extra results.
    """

    progress = pyqtSignal(int)
    finished = pyqtSignal(dict, dict, dict, bool)
    failed = pyqtSignal(str)

    def __init__(self, folder_path):
        super().__init__()
        self.folder_path = folder_path
        self.hierarchy = None
        self.labels: dict[str, str] = {}
        self.skeleton_stems: set[str] = set()
        self.corrected_stems: set[str] = set()

    def run(self):
        if not os.path.isdir(self.folder_path):
            self.failed.emit(f"Folder not found: {self.folder_path}")
            return
        layout = ProjectLayout(self.folder_path)
        try:
            images = layout.scan_images()
            if self._stop(50):
                return
            masks = layout.scan_masks()
            if self._stop(70):
                return
            self.skeleton_stems = layout.scan_skeleton_stems()
            self.corrected_stems = layout.scan_corrected_stems()
            if self._stop(85):
                return
            self.hierarchy, self.labels = index_names(images)
        except OSError as exc:
            logger.exception("Failed to scan image folder %s", self.folder_path)
            self.failed.emit(f"Cannot read {self.folder_path}: {exc.strerror or exc}")
            return
        if self._stop(100):
            return
        self.finished.emit(images, {}, masks, False)

    def _stop(self, percent: int) -> bool:
        """Report ``percent``; True when the scan was superseded and must stop."""
        if self.isInterruptionRequested():
            return True
        self.progress.emit(percent)
        return False


def project_layout_of(manager: object) -> ProjectLayout | None:
    """Layout of ``manager``'s project: ``original_folder``, else the first image's folder.

    ``manager`` is the window's :class:`ImageManager` or any stand-in with
    ``original_folder`` / ``images``; None when nothing is loaded. Generate,
    Measure, Visualize and the window all resolve the project through here, so
    they can never disagree about it.
    """
    folder = getattr(manager, "original_folder", None)
    if folder:
        return ProjectLayout(folder)
    images = getattr(manager, "images", None)
    if images:
        return ProjectLayout.for_image(next(iter(images.values())))
    return None


class ImageManager:
    def __init__(self, main_window=None):
        self.images = {}
        self.fake_images = {}
        self.masks = {}
        self.skeleton_stems: set[str] = set()
        # Images with a skeleton correction (display only: Measure reads the generated
        # skeleton, FR-03).
        self.corrected_stems: set[str] = set()
        self.has_fake_real_pairs = False
        self.original_folder = None
        self.loader_thread = None
        self.main_window = main_window
        self.current_view_mode = None
        self.processed_base_path = None
        # Cache for hierarchical structure
        self._hierarchy_cache = None
        self._hierarchy_cache_valid = False
        self._labels_cache = None
        # Scan bookkeeping: only the newest scan may deliver; superseded scans are kept
        # referenced until their thread really ends (never terminate()).
        self._generation = 0
        self._pending_folder = None
        self._retired_threads: set[QThread] = set()

    # ------------------------------------------------------------------ loading
    def load_images(self, folder_path=None):
        """Scan ``folder_path`` (asks with a dialog when None) in the background.

        The current library stays until the scan delivers images; a failed or
        image-less scan is reported to the main window and changes nothing.
        """
        if folder_path is None:
            folder_path = QFileDialog.getExistingDirectory(None, "Select Image Directory")
        if not folder_path:
            return

        self._retire_loader()
        self._generation += 1
        self._pending_folder = folder_path
        thread = ImageLoaderThread(folder_path)
        self.loader_thread = thread
        if self.main_window:
            thread.progress.connect(self.main_window.update_loading_progress)
        thread.finished.connect(partial(self._on_scan_finished, self._generation, thread))
        thread.failed.connect(partial(self._on_scan_failed, self._generation))
        thread.start()

    @staticmethod
    def _start_temp_sweep(folder_path) -> None:
        """Sweep the project's output folders for stale temp files on a daemon thread (R8).

        One scandir per folder (~70 ms for a 46k-file ``mask/``), which the GUI thread
        must not pay; a failure is logged and never reaches the library.
        """

        def run() -> None:
            try:
                removed = sweep_project_temp_files(ProjectLayout(folder_path))
            except Exception:  # housekeeping: never let it disturb opening the project
                logger.exception("Stale temp-file sweep failed in %s", folder_path)
                return
            if removed:
                logger.info("Removed %d stale temp file(s) in %s", len(removed), folder_path)

        threading.Thread(target=run, name="stale-temp-sweep", daemon=True).start()

    def _retire_loader(self):
        """Ask a running scan to stop; keep it alive until its thread finishes."""
        old = self.loader_thread
        self.loader_thread = None
        if old is None or not old.isRunning():
            return
        old.requestInterruption()
        self._retired_threads.add(old)
        # QThread's own finished (the class shadows it with the result signal).
        QThread.finished.__get__(old, QThread).connect(
            lambda t=old: self._retired_threads.discard(t)
        )

    def _on_scan_finished(self, generation, thread, images, fake_images, masks, pairs):
        if generation != self._generation:
            return  # a newer folder was opened meanwhile
        folder, self._pending_folder = self._pending_folder, None
        if not images:
            if self.main_window:
                self.main_window.on_loading_empty(folder)
            return
        self.original_folder = folder
        self.on_loading_finished(
            images,
            fake_images,
            masks,
            pairs,
            hierarchy=thread.hierarchy,
            labels=thread.labels,
            skeleton_stems=thread.skeleton_stems,
            corrected_stems=thread.corrected_stems,
        )
        QTimer.singleShot(TEMP_SWEEP_DELAY_MS, partial(self._start_temp_sweep, folder))

    def _on_scan_failed(self, generation, message):
        if generation != self._generation:
            return
        folder, self._pending_folder = self._pending_folder, None
        if self.main_window:
            self.main_window.on_loading_failed(folder, message)

    def on_loading_finished(
        self,
        images,
        fake_images,
        masks,
        has_fake_real_pairs,
        *,
        hierarchy=None,
        labels=None,
        skeleton_stems=None,
        corrected_stems=None,
    ):
        """Adopt a scan result as the library (``original_folder`` is already set).

        ``hierarchy`` / ``labels`` / ``skeleton_stems`` / ``corrected_stems`` come
        precomputed from the loader thread; without them they are computed here (the
        first two lazily).
        """
        self._clear_images()
        self.images = images
        self.masks = masks
        self._find_processed_base_path()
        self._hierarchy_cache = hierarchy
        self._hierarchy_cache_valid = hierarchy is not None
        self._labels_cache = labels if hierarchy is not None else None
        if skeleton_stems is not None:
            self.skeleton_stems = set(skeleton_stems)
        elif self.original_folder:
            self.skeleton_stems = self._layout().scan_skeleton_stems()
        if corrected_stems is not None:
            self.corrected_stems = set(corrected_stems)
        elif self.original_folder:
            self.corrected_stems = self._layout().scan_corrected_stems()
        if self.main_window:
            self.main_window.on_loading_finished(
                images, self.fake_images, masks, self.has_fake_real_pairs
            )

    def _clear_images(self):
        self.images = {}
        self.fake_images.clear()
        self.has_fake_real_pairs = False
        self.masks = {}
        self.skeleton_stems = set()
        self.corrected_stems = set()
        self.processed_base_path = None
        # Invalidate cache
        self._hierarchy_cache = None
        self._hierarchy_cache_valid = False
        self._labels_cache = None

    # ------------------------------------------------------------ project folder
    def project_layout(self) -> ProjectLayout | None:
        """Layout of the loaded project: ``original_folder``, else the first image's folder.

        The one project-folder lookup shared by Generate, Measure, Visualize and
        the window (see :func:`project_layout_of`); None when nothing is loaded.
        """
        return project_layout_of(self)

    # ------------------------------------------------------------ output status
    def _layout(self) -> ProjectLayout:
        return ProjectLayout(self.original_folder)

    def _find_processed_base_path(self):
        if not self.original_folder:
            return
        skeletons = self._layout().skeletons_dir
        self.processed_base_path = skeletons if os.path.isdir(skeletons) else None

    def refresh_outputs(self):
        """Re-read mask/ and skeletons/ after a generation run (two scandirs)."""
        if not self.original_folder:
            return
        layout = self._layout()
        self.masks = layout.scan_masks()
        self.skeleton_stems = layout.scan_skeleton_stems()
        self.fake_images.clear()
        self._find_processed_base_path()

    def mask_path_for(self, name) -> str | None:
        """Where the mask editor saves the mask of ``name`` (stem or image path).

        ``<image folder>/mask/<stem>.png`` (the editor's rule); None for an unknown stem.
        """
        image_path = self.get_image_path(image_stem(name))
        if image_path is None and os.path.isabs(os.fspath(name)):
            image_path = os.fspath(name)
        return ProjectLayout.for_image(image_path).mask_path(image_path) if image_path else None

    def set_mask(self, name, path=None):
        """Record that ``name`` (stem or image path) has a mask at ``path`` (None: none)."""
        stem = image_stem(name)
        if path:
            self.masks[stem] = path
        else:
            self.masks.pop(stem, None)

    def has_mask(self, name):
        return name in self.masks

    def has_skeleton(self, name) -> bool:
        return name in self.skeleton_stems

    def has_correction(self, name) -> bool:
        """True when a skeleton correction of ``name`` was found (display only, FR-03)."""
        return name in self.corrected_stems

    def set_correction(self, name, present: bool) -> None:
        """Record whether ``name`` (stem or image path) has a skeleton correction."""
        stem = image_stem(name)
        if present:
            self.corrected_stems.add(stem)
        else:
            self.corrected_stems.discard(stem)

    def corrected_skeleton_path(self, name) -> str | None:
        """``skeleton_corrections/<stem>_skeleton.png`` of ``name`` when one was found."""
        stem = image_stem(name)
        if stem not in self.corrected_stems or not self.original_folder:
            return None
        return self._layout().corrected_skeleton_path(stem)

    # ------------------------------------------------------------ view / lookup
    def get_fake_image_path(self, name):
        if name in self.fake_images:
            return self.fake_images[name]

        if self.current_view_mode in ["Overlay", "Side by Side"] and self.processed_base_path:
            fake_path = os.path.join(self.processed_base_path, f"{name}_fake.png")
            if os.path.exists(fake_path):
                self.fake_images[name] = fake_path
                self.has_fake_real_pairs = True
                return fake_path

        return None

    def reload_for_view_mode(self, view_mode):
        """Remember the display mode (state only: the library tree does not depend on it)."""
        self.current_view_mode = view_mode
        if view_mode == "Single Image":
            self.fake_images.clear()
            self.has_fake_real_pairs = False

    def get_image_names(self):
        return list(self.images.keys())

    def get_image_path(self, name):
        return self.images.get(name)

    def parse_image_name(self, name):
        """Field, tube, depth, date and time of ``name`` (see :mod:`app.naming`)."""
        parsed = parse_image_name(name)
        return {
            "original_name": name,
            "field": parsed.field,
            "tube_number": parsed.tube,
            "length_position": parsed.position,
            "date": parsed.date,
            "time": parsed.time,
        }

    def get_hierarchical_structure(self):
        """Field -> Tube -> Date -> [(depth, name)], cached until the images change."""
        if not (self._hierarchy_cache_valid and self._hierarchy_cache is not None):
            self._hierarchy_cache, self._labels_cache = index_names(self.images)
            self._hierarchy_cache_valid = True
        return self._hierarchy_cache

    def get_leaf_labels(self) -> dict[str, str]:
        """Library row text per image (computed with the hierarchy)."""
        self.get_hierarchical_structure()
        return self._labels_cache or {}

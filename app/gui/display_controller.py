"""Library display: the selected image as Single, Overlay or Side by side.

``MagnifyingGraphicsView`` is the canvas. It shares the zoom / fit / pan model of
every canvas (:class:`CanvasViewMixin`) and, having no tools, pans on a left
drag. ``DisplayController`` decides what the canvas shows for the selection:

* Single: the image.
* Overlay: the image with its skeleton on top, in the SPROUTS skeleton colour at 80 %.
* Side by side: the image and its skeleton, the skeleton scaled to the image's
  height.

The skeleton drawn is the image's GT skeleton (``gt/skeletons/<stem>.png``) when one exists,
else its ML skeleton (``skeletons/<stem>_fake.png``). Measure reads the GT skeleton first
too, where it is valid (O1), and the metrics bar says "Skeleton: GT".

The photo (and the side-by-side skeleton) is a
:class:`~app.gui.canvas_layers.photo.PhotoLayerItem`, decoded like every canvas
(:data:`app.enhancement.DECODE_FLAGS`: EXIF orientation ignored): smooth when
magnified, alias-free when zoomed out. The overlay is a
:class:`~app.gui.canvas_layers.skeleton.SkeletonLayerItem` without vectors or halo,
drawn from the generated file itself (its lines are about 2 px wide): max-pooled
when zoomed out, bilinear otherwise, so it keeps its soft look without pixel
blocks. The items choose their own filtering and cache nothing per zoom.

Every new image is fitted to the canvas (and stays fitted on resize until the
user zooms or pans). A message replaces the canvas when nothing is selected or
the image cannot be decoded, so the canvas is never blank or stale.

Once an image is shown, the images PgDn / PgUp / the arrow keys open next (the
neighbour provider MainWindow sets from the library order) are decoded on a worker
thread, and the photo shown is kept (:class:`~app.gui.display_read_ahead.DisplayReadAhead`):
stepping on or back, or switching the view mode, shows a photo without decoding it on
the GUI thread, and never a stale one (a read is used only while its file is unchanged).

One decode per photo across the Library and the Correct page (MainWindow wires them):
the Correct page takes the photos it opens and reads ahead from this cache
(:meth:`DisplayController.take_photo`, which forgets them), and back in the Library a photo
the Correct page holds (``set_photo_source``) is shown and kept instead of decoded again.
"""

import html
import logging
import os
from collections.abc import Callable, Sequence

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QVBoxLayout

from app.enhancement import DECODE_FLAGS
from app.gui.key_text import platform_keys
from app.gui.widgets import tokens
from app.io_utils import file_stamp, imread_unicode

from .canvas_background import configure_canvas_background
from .canvas_layers.photo import PhotoLayerItem
from .canvas_layers.skeleton import SkeletonLayerItem
from .canvas_view import CanvasViewMixin
from .display_read_ahead import DisplayReadAhead

logger = logging.getLogger(__name__)

#: ``provider(path)``: the images the user would open next from ``path``, nearest first.
NeighbourProvider = Callable[[str], Sequence[str]]
#: ``source(path)``: another page's decoded photo of ``path`` with its stamp, or None.
PhotoSource = Callable[[str], "tuple[tuple, np.ndarray] | None"]

#: Neighbours decoded ahead: the next image and the previous one.
READ_AHEAD_COUNT = 2


def _decode_photo(path: str):
    """The photo as the display decodes it (the read-ahead worker's job)."""
    return imread_unicode(path, DECODE_FLAGS)


class MagnifyingGraphicsView(CanvasViewMixin, QGraphicsView):
    """Library display canvas. It has no tools, so a left drag pans."""

    FIT_MARGIN = 2  # nothing floats over this canvas; keep the image edge to edge

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Image display")
        self._init_canvas(pan_mode=True)
        configure_canvas_background(self)
        self.setup_high_quality_rendering()

    def setup_high_quality_rendering(self):
        """Configure high-quality rendering settings to reduce noise during zoom"""
        # Enable all quality render hints
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.LosslessImageRendering
            | QPainter.RenderHint.TextAntialiasing
        )

        # No background cache: the background is one solid colour, and a cache is redrawn
        # (on GL uploaded again) at every zoom step, 1.2 ms of each GL zoom frame.

        # Optimize for interactive use. Not DontAdjustForAntialiasing: with the
        # Antialiasing hint, items paint up to a pixel past their bounds, and
        # partial viewport updates would leave those edges behind.
        self.setOptimizationFlags(QGraphicsView.OptimizationFlag.DontSavePainterState)

    def show_message(self, text: str, hint: str = "") -> None:
        """Replace the scene with a centred message, at 100 % zoom."""
        scene = QGraphicsScene()
        item = scene.addText("")
        body = f'<div style="color:{tokens.TEXT_MUTED}; font-size:14px;">{html.escape(text)}</div>'
        if hint:
            body += (
                f'<div style="color:{tokens.TEXT_FAINT}; font-size:12px;">{html.escape(hint)}</div>'
            )
        item.setHtml(body)
        document = item.document()
        option = document.defaultTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        document.setDefaultTextOption(option)
        item.setTextWidth(document.idealWidth())  # centring needs a text width
        self.setScene(scene)
        self.clear_content_rect()  # Ctrl+0 and resizes leave the message alone
        self.setSceneRect(item.sceneBoundingRect())  # smaller than the view: centred
        self.resetTransform()


class DisplayController:
    """What the Library canvas shows for the selected image (see the module docstring)."""

    SINGLE, OVERLAY, SIDE_BY_SIDE = "Single Image", "Overlay", "Side by Side"
    OVERLAY_OPACITY = 0.8  # the skeleton reads clearly, the root under it stays visible
    SIDE_BY_SIDE_GAP = 12  # image pixels between the image and its skeleton
    EMPTY_MESSAGE = "Select an image in the library"
    EMPTY_HINT = platform_keys("Ctrl+wheel to zoom  ·  Ctrl+0 to fit  ·  drag to pan")

    def __init__(self, main_window):
        self.main_window = main_window
        self.magnifying_view: MagnifyingGraphicsView | None = None
        self.current_name: str | None = None
        self.current_image: str | None = None
        self.current_fake_image: str | None = None
        self.read_ahead = DisplayReadAhead(_decode_photo)
        self._neighbours: NeighbourProvider | None = None
        self._photo_source: PhotoSource | None = None

    def set_neighbour_provider(self, provider: NeighbourProvider | None) -> None:
        """``provider(path)``: the images the user would open next, nearest first; the
        first :data:`READ_AHEAD_COUNT` are decoded ahead (None: nothing is read ahead)."""
        self._neighbours = provider
        if provider is None:
            self.read_ahead.clear()

    def set_photo_source(self, source: PhotoSource | None) -> None:
        """``source(path)``: the Correct page's decoded photo of ``path`` with its stamp
        (shared, read-only), shown and kept instead of decoding it again (None: decode)."""
        self._photo_source = source

    def take_photo(self, path: str) -> tuple[tuple, np.ndarray] | None:
        """The decoded photo of ``path`` with its stamp, handed over to the Correct page and
        forgotten here (it holds the photo now); None when there is none. Never waits."""
        return self.read_ahead.take(path)

    def setup_display_area(self, parent) -> MagnifyingGraphicsView:
        """Put the canvas into ``parent``, showing the placeholder, and return it."""
        layout = QVBoxLayout(parent)
        self.magnifying_view = MagnifyingGraphicsView()
        layout.addWidget(self.magnifying_view)
        self.clear_magnifying_view()
        return self.magnifying_view

    def reset(self) -> None:
        """Forget the selection (e.g. another folder was opened) and show the placeholder."""
        self.current_name = self.current_image = self.current_fake_image = None
        self.read_ahead.clear()
        self.clear_magnifying_view()

    # ------------------------------------------------------------- selection
    def display_selected_image_by_name(self, name):
        """Show the library image ``name`` in the current view mode, then read its
        neighbours ahead."""
        self.current_name = name
        self.current_image = self.main_window.image_manager.get_image_path(name)
        # The skeleton belongs to this image: look it up again, never reuse the
        # previous image's (PG-02).
        self.current_fake_image = None
        self.update_display()
        self._read_neighbours_ahead()

    def _read_neighbours_ahead(self) -> None:
        """Decode the next and previous images on the worker (the photo shown stays kept)."""
        provider, current = self._neighbours, self.current_image
        if provider is None or not current:
            return
        try:
            paths = [str(p) for p in provider(current) or () if p][:READ_AHEAD_COUNT]
        except Exception:  # noqa: BLE001 - stepping still works, it just decodes itself
            logger.exception("The neighbours of %s are unknown", current)
            return
        self.read_ahead.retain([current, *paths])
        for path in paths:
            if not self.read_ahead.holds(path):
                self._shared_photo(path)  # the Correct page read it: kept, not decoded
        self.read_ahead.request(paths)

    def update_display_mode(self):
        """Show the current image in the newly chosen mode (the library tree is untouched)."""
        self.main_window.image_manager.reload_for_view_mode(self._view_mode())
        self.update_display()

    def update_display(self):
        """Show the current image in the current view mode (the placeholder without one)."""
        if not self.current_image:
            self.clear_magnifying_view()
            return
        mode = self._view_mode()
        if mode == self.OVERLAY:
            self.display_overlay_image()
        elif mode == self.SIDE_BY_SIDE:
            self.display_side_by_side_images()
        else:
            self.display_single_image()

    def _view_mode(self) -> str:
        return self.main_window.view_mode_combo.currentText()

    # ----------------------------------------------------------------- modes
    def display_single_image(self):
        """The image alone."""
        photo = self._photo_item()
        if photo is not None:
            self.update_scene_items([photo])

    def display_overlay_image(self):
        """The image with its skeleton drawn on top in the skeleton colour."""
        photo = self._photo_item()
        if photo is None:
            return
        items = [photo]
        overlay = self._skeleton_overlay(*photo.image_size())
        if overlay is not None:
            items.append(overlay)
        self.update_scene_items(items)

    def display_side_by_side_images(self):
        """The image, and beside it the skeleton scaled to the same height."""
        photo = self._photo_item()
        if photo is None:
            return
        items = [photo]
        width, height = photo.image_size()
        path = self._current_skeleton_path()
        skeleton = imread_unicode(path, DECODE_FLAGS) if path else None
        if skeleton is not None:
            skeleton_item = PhotoLayerItem()
            skeleton_item.set_image(skeleton)
            skeleton_item.setScale(height / skeleton.shape[0])
            skeleton_item.setPos(width + self.SIDE_BY_SIDE_GAP, 0)
            items.append(skeleton_item)
        elif path:
            logger.warning("Failed to load skeleton %s", path)
        self.update_scene_items(items)

    # --------------------------------------------------------------- helpers
    def _photo_item(self) -> PhotoLayerItem | None:
        """The selected image as a photo item (Unicode-safe decode, RB-06); if it cannot be
        decoded, say so on the canvas and in a toast and return None.

        A photo decoded ahead (or kept since it was shown), or one the Correct page holds,
        is used while its file is unchanged; otherwise it is decoded here and kept.
        """
        path = self.current_image
        image = self.read_ahead.get(path)
        if image is None:
            image = self._shared_photo(path)
        if image is None:
            stamp = file_stamp(path)
            image = imread_unicode(path, DECODE_FLAGS)
            self.read_ahead.keep(path, stamp, image)
        if image is not None:
            item = PhotoLayerItem()
            item.set_image(image)
            return item
        message = f"Could not open {os.path.basename(self.current_image)}"
        logger.warning("%s (%s)", message, self.current_image)
        self.clear_magnifying_view(message)
        self._notify(message, "danger")
        return None

    def _shared_photo(self, path: str):
        """The photo of ``path`` the Correct page holds (then kept here too), while its file
        is the one decoded; None otherwise."""
        source = self._photo_source
        if source is None:
            return None
        try:
            found = source(path)
        except Exception:  # noqa: BLE001 - the display decodes the photo itself
            logger.exception("The Correct page's photo of %s is unavailable", path)
            return None
        if found is None:
            return None  # the usual case: no stat for it (three per Library click)
        stamp = file_stamp(path)
        if stamp[1:] == (None,) or tuple(found[0][1:]) != stamp[1:]:
            return None
        self.read_ahead.keep(path, stamp, found[1])
        return found[1]

    def _current_skeleton_path(self) -> str | None:
        """Path of the skeleton drawn for the current image, looked up for this image only:
        its GT skeleton (``gt/skeletons/``) when one exists, else its ML skeleton.

        Measure reads the GT skeleton first too, where it is valid (O1); the metrics bar
        says which one an image has.
        """
        if self.current_fake_image is None:
            name = self.current_name or os.path.splitext(os.path.basename(self.current_image))[0]
            manager = self.main_window.image_manager
            gt_skeleton = getattr(manager, "gt_skeleton_path_for", None)
            path = gt_skeleton(name) if callable(gt_skeleton) else None
            if path and not os.path.isfile(path):
                path = None  # removed since the scan: the ML skeleton, as before
            self.current_fake_image = path or manager.get_fake_image_path(name)
            if self.current_fake_image is None:
                self._status(f"No skeleton found for {name}")
        return self.current_fake_image

    def _skeleton_overlay(self, width: int, height: int) -> SkeletonLayerItem | None:
        """The skeleton file over a ``width`` x ``height`` image, at 80 %, or None.

        Otsu-binarized (F-017: only 0 and 255 are drawn), kept at the file's own size and
        scaled onto the image by the item: no nearest blow-up of the file.
        """
        path = self._current_skeleton_path()
        if not path:
            return None
        gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)  # Unicode-safe (RB-06)
        if gray is None:
            logger.warning("Failed to load skeleton %s", path)
            return None
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        file_height, file_width = binary.shape
        item = SkeletonLayerItem(halo=False, vectors=False)
        item.set_mask(binary, scale=(width / file_width, height / file_height))
        item.setOpacity(self.OVERLAY_OPACITY)
        item.setZValue(1)
        return item

    def update_scene_items(self, items: list[QGraphicsItem]) -> None:
        """Show ``items`` in a fresh scene, fitted to the canvas."""
        scene = QGraphicsScene()
        bounds = QRectF()
        for item in items:
            scene.addItem(item)
            bounds = bounds.united(item.sceneBoundingRect())
        view = self.magnifying_view
        view.setScene(scene)
        view.set_content_rect(bounds)
        view.fit_to_view()

    def clear_magnifying_view(self, message: str | None = None):
        """Show ``message`` instead of an image (default: ask for a selection)."""
        if message is None:
            self.magnifying_view.show_message(self.EMPTY_MESSAGE, self.EMPTY_HINT)
        else:
            self.magnifying_view.show_message(message)

    def _status(self, message: str, timeout_ms: int = 3000) -> None:
        bar = getattr(self.main_window, "status_bar", None)
        if bar is not None:
            bar.showMessage(message, timeout_ms)

    def _notify(self, message: str, kind: str) -> None:
        notify = getattr(self.main_window, "notify", None)
        if notify is not None:
            notify(message, kind)

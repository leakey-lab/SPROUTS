"""Library display: the selected image as Single, Overlay or Side by side.

``MagnifyingGraphicsView`` is the canvas. It shares the zoom / fit / pan model of
every canvas (:class:`CanvasViewMixin`) and, having no tools, pans on a left
drag. ``DisplayController`` decides what the canvas shows for the selection:

* Single: the image.
* Overlay: the image with its ML skeleton (``skeletons/<stem>_fake.png``) on top,
  in the SPROUTS skeleton colour.
* Side by side: the image and its skeleton, the skeleton scaled to the image's
  height.

Every new image is fitted to the canvas (and stays fitted on resize until the
user zooms or pans). A message replaces the canvas when nothing is selected or
the image cannot be decoded, so the canvas is never blank or stale.
"""

import html
import logging
import os

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
)

from app.gui.widgets import tokens
from app.io_utils import imread_unicode

from .canvas_background import configure_canvas_background
from .canvas_view import CanvasViewMixin

logger = logging.getLogger(__name__)


class MagnifyingGraphicsView(CanvasViewMixin, QGraphicsView):
    """Library display canvas. It has no tools, so a left drag pans."""

    FIT_MARGIN = 2  # nothing floats over this canvas; keep the image edge to edge

    def __init__(self, parent=None):
        super().__init__(parent)
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

        # Enable caching for better performance
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)

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


def _overlay_color_table(alpha: int) -> list[int]:
    """Indexed8 colour table: 0 transparent, every foreground value in the skeleton colour.

    F-017: the overlay is Otsu-binarized, so it holds only 0 and 255; colouring
    1..255 keeps it correct should a soft mask ever produce values in between.
    """
    color = QColor(tokens.SKEL)
    color.setAlpha(alpha)
    return [0] + [color.rgba()] * 255


class DisplayController:
    """What the Library canvas shows for the selected image (see the module docstring)."""

    SINGLE, OVERLAY, SIDE_BY_SIDE = "Single Image", "Overlay", "Side by Side"
    OVERLAY_ALPHA = 204  # 80 %: the skeleton reads clearly, the root under it stays visible
    SIDE_BY_SIDE_GAP = 12  # image pixels between the image and its skeleton
    EMPTY_MESSAGE = "Select an image in the library"
    EMPTY_HINT = "Ctrl+wheel to zoom  ·  Ctrl+0 to fit  ·  drag to pan"

    def __init__(self, main_window):
        self.main_window = main_window
        self.magnifying_view: MagnifyingGraphicsView | None = None
        self.current_name: str | None = None
        self.current_image: str | None = None
        self.current_fake_image: str | None = None
        self._overlay_colors = _overlay_color_table(self.OVERLAY_ALPHA)

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
        self.clear_magnifying_view()

    # ------------------------------------------------------------- selection
    def display_selected_image_by_name(self, name):
        """Show the library image ``name`` in the current view mode."""
        self.current_name = name
        self.current_image = self.main_window.image_manager.get_image_path(name)
        # The skeleton belongs to this image: look it up again, never reuse the
        # previous image's (PG-02).
        self.current_fake_image = None
        self.update_display()

    def update_display_mode(self):
        """Show the current image in the newly chosen mode (the library tree is untouched)."""
        self.main_window.image_manager.reload_for_view_mode(self._view_mode())
        self.update_display()

    def update_display(self):
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
        image = self._load_current_image()
        if image is not None:
            self.update_scene_items([QGraphicsPixmapItem(image)])

    def display_overlay_image(self):
        """The image with its skeleton drawn on top in the skeleton colour."""
        image = self._load_current_image()
        if image is None:
            return
        items = [QGraphicsPixmapItem(image)]
        overlay = self._skeleton_overlay(image.width(), image.height())
        if overlay is not None:
            overlay_item = QGraphicsPixmapItem(overlay)
            overlay_item.setZValue(1)
            items.append(overlay_item)
        self.update_scene_items(items)

    def display_side_by_side_images(self):
        """The image, and beside it the skeleton scaled to the same height."""
        image = self._load_current_image()
        if image is None:
            return
        items = [QGraphicsPixmapItem(image)]
        path = self._current_skeleton_path()
        skeleton = QPixmap(path) if path else QPixmap()
        if not skeleton.isNull():
            skeleton_item = QGraphicsPixmapItem(skeleton)
            skeleton_item.setScale(image.height() / skeleton.height())
            skeleton_item.setPos(image.width() + self.SIDE_BY_SIDE_GAP, 0)
            items.append(skeleton_item)
        elif path:
            logger.warning("Failed to load skeleton %s", path)
        self.update_scene_items(items)

    # --------------------------------------------------------------- helpers
    def _load_current_image(self) -> QPixmap | None:
        """Decode the selected image; if that fails, say so on the canvas and in a toast."""
        pixmap = QPixmap(self.current_image)
        if not pixmap.isNull():
            return pixmap
        message = f"Could not open {os.path.basename(self.current_image)}"
        logger.warning("%s (%s)", message, self.current_image)
        self.clear_magnifying_view(message)
        self._notify(message, "danger")
        return None

    def _current_skeleton_path(self) -> str | None:
        """Path of the current image's ML skeleton, looked up for this image only."""
        if self.current_fake_image is None:
            name = self.current_name or os.path.splitext(os.path.basename(self.current_image))[0]
            self.current_fake_image = self.main_window.image_manager.get_fake_image_path(name)
            if self.current_fake_image is None:
                self._status(f"No skeleton found for {name}")
        return self.current_fake_image

    def _skeleton_overlay(self, width: int, height: int) -> QPixmap | None:
        """The skeleton as a translucent ``width`` x ``height`` overlay, or None."""
        path = self._current_skeleton_path()
        if not path:
            return None
        gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)  # Unicode-safe (RB-06)
        if gray is None:
            logger.warning("Failed to load skeleton %s", path)
            return None
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if binary.shape != (height, width):
            binary = cv2.resize(binary, (width, height), interpolation=cv2.INTER_NEAREST)
        binary = np.ascontiguousarray(binary)
        # F-018: the QImage only wraps ``binary``'s buffer; fromImage() copies the
        # pixels while ``binary`` is still alive.
        image = QImage(binary.data, width, height, width, QImage.Format.Format_Indexed8)
        image.setColorTable(self._overlay_colors)
        return QPixmap.fromImage(image)

    def update_scene_items(self, items):
        """Show ``items`` in a fresh scene, fitted to the canvas."""
        scene = QGraphicsScene()
        bounds = QRectF()
        for item in items:
            if isinstance(item, QGraphicsPixmapItem):
                item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                item.setCacheMode(QGraphicsPixmapItem.CacheMode.DeviceCoordinateCache)
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

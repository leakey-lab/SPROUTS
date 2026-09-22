import logging
import os

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

from .canvas_background import configure_canvas_background

logger = logging.getLogger(__name__)


def _env_bool(name: str, *, default: bool) -> bool:
    v = os.environ.get(name, None)
    if v is None:
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "on")


# OpenGL viewports are enabled by default for smooth/fast QGraphicsView rendering.
# NOTE (Qt6): QtWebEngine (QWebEngineView) uses a QQuickWidget internally and cannot
# share a top-level window with any QOpenGLWidget. We temporarily disable these
# viewports when opening the embedded visualizations.
DEFAULT_OPENGL_VIEWPORT_ENABLED = _env_bool("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", default=True)

try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget

    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False


class MagnifyingGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        # Set up high-quality rendering
        self.setup_high_quality_rendering()

        self._opengl_viewport_enabled = False
        self.set_opengl_viewport_enabled(DEFAULT_OPENGL_VIEWPORT_ENABLED)
        configure_canvas_background(self)

        # Zoom control
        self.zoom = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0

        # Set transformation anchor for better zoom quality
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_opengl_viewport_enabled(self, enabled: bool) -> None:
        """Enable/disable QOpenGLWidget viewport at runtime.

        This is used to temporarily disable OpenGL while QtWebEngine visualizations
        are shown (Qt6 limitation: QQuickWidget + QOpenGLWidget in same window).
        """
        enabled = bool(enabled)

        if enabled and self._opengl_viewport_enabled:
            return
        if (not enabled) and (not self._opengl_viewport_enabled):
            return

        if enabled and HAS_OPENGL:
            try:
                self.setViewport(QOpenGLWidget())
                self._opengl_viewport_enabled = True
                return
            except (RuntimeError, OSError) as e:
                # OpenGL context creation can fail on headless / driverless
                # systems; fall through to a standard software viewport.
                logger.warning("Failed to set OpenGL viewport: %s", e)

        # Replace with a standard QWidget viewport (removes any existing QOpenGLWidget).
        self.setViewport(QWidget())
        self._opengl_viewport_enabled = False

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

        # Optimize for interactive use
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontSavePainterState
            | QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
        )

    def wheelEvent(self, event: QWheelEvent):
        """Enhanced wheel event with high-quality zooming"""
        # Check for Ctrl modifier for zoom
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            zoom_in = event.angleDelta().y() > 0

            if zoom_in and self.zoom < self.max_zoom:
                factor = 1.25
                self.zoom *= factor
            elif not zoom_in and self.zoom > self.min_zoom:
                factor = 0.8
                self.zoom *= factor
            else:
                event.ignore()
                return

            # Apply high-quality scaling
            self.scale_with_quality(factor)
            event.accept()
        else:
            # Allow normal scrolling when Ctrl is not pressed
            super().wheelEvent(event)

    def scale_with_quality(self, factor):
        """Apply scaling with high quality settings to reduce noise"""
        # Ensure smooth transformation for all items before scaling
        if self.scene():
            for item in self.scene().items():
                if isinstance(item, QGraphicsPixmapItem):
                    item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                    item.setCacheMode(QGraphicsPixmapItem.CacheMode.DeviceCoordinateCache)

        # Apply the scaling
        self.scale(factor, factor)

        # Force a high-quality update
        self.viewport().update()

    def reset_zoom(self):
        """Reset zoom to fit the scene"""
        self.resetTransform()
        self.zoom = 1.0
        if self.scene():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for zoom control"""
        if event.key() == Qt.Key.Key_0 and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+0 to reset zoom
            self.reset_zoom()
            event.accept()
        elif (
            event.key() == Qt.Key.Key_Plus
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            # Ctrl++ to zoom in
            if self.zoom < self.max_zoom:
                factor = 1.25
                self.zoom *= factor
                self.scale_with_quality(factor)
            event.accept()
        elif (
            event.key() == Qt.Key.Key_Minus
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            # Ctrl+- to zoom out
            if self.zoom > self.min_zoom:
                factor = 0.8
                self.zoom *= factor
                self.scale_with_quality(factor)
            event.accept()
        else:
            super().keyPressEvent(event)


class DisplayController:
    def __init__(self, main_window):
        self.main_window = main_window
        self.magnifying_view = None
        self.current_image = None
        self.current_fake_image = None

    def setup_display_area(self, parent):
        layout = QVBoxLayout(parent)

        self.magnifying_view = MagnifyingGraphicsView()
        layout.addWidget(self.magnifying_view)

        # Show helpful message about zoom controls
        if self.main_window and hasattr(self.main_window, "status_bar"):
            self.main_window.status_bar.showMessage(
                "Tip: Use Ctrl+Mouse Wheel to zoom, Ctrl+0 to reset, Ctrl+/- for keyboard zoom",
                10000,
            )

    def display_selected_image(self, item):
        """Handle image selection with lazy loading (for backward compatibility)"""
        name = item.text()
        self.display_selected_image_by_name(name)

    def display_selected_image_by_name(self, name):
        """Handle image selection by name with lazy loading"""
        self.current_image = self.main_window.image_manager.get_image_path(name)

        view_mode = self.main_window.view_mode_combo.currentText()
        if view_mode in ["Overlay", "Side by Side"]:
            # Lazy load the processed image when needed
            self.current_fake_image = self.main_window.image_manager.get_fake_image_path(name)

            if not self.current_fake_image:
                self.main_window.status_bar.showMessage(
                    f"No processed image found for {name}", 3000
                )

        self.update_display()

    def update_display_mode(self):
        """Handle view mode changes"""
        view_mode = self.main_window.view_mode_combo.currentText()

        # Reload images based on the new view mode
        self.main_window.image_manager.reload_for_view_mode(view_mode)

        # Refresh the file list in the main window
        self.main_window.populate_file_list()

        # Update the display
        self.update_display()

    def update_display(self):
        view_mode = self.main_window.view_mode_combo.currentText()

        if self.current_image:
            self.magnifying_view.show()
            if view_mode == "Single Image":
                self.display_single_image()
            elif view_mode == "Overlay":
                self.display_overlay_image()
            elif view_mode == "Side by Side":
                self.display_side_by_side_images()
        else:
            self.clear_magnifying_view()

    def display_single_image(self):
        if self.current_image:
            pixmap = QPixmap(self.current_image)
            self.set_magnifying_view_image(pixmap)
        else:
            self.clear_magnifying_view()

    def display_overlay_image(self):
        """Display overlay with lazy-loaded processed image using layered items"""
        if not self.current_image:
            return

        # Load processed image if needed
        if not self.current_fake_image:
            base_name = os.path.splitext(os.path.basename(self.current_image))[0]
            self.current_fake_image = self.main_window.image_manager.get_fake_image_path(base_name)

        # 1. Base Image
        real_pixmap = QPixmap(self.current_image)
        if real_pixmap.isNull():
            return

        real_item = QGraphicsPixmapItem(real_pixmap)
        real_item.setZValue(0)

        items = [real_item]

        # 2. Overlay Image (if available)
        if self.current_fake_image:
            fake_image_gray = cv2.imread(self.current_fake_image, cv2.IMREAD_GRAYSCALE)
            if fake_image_gray is not None:
                # Binarize
                _, binary_mask = cv2.threshold(
                    fake_image_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )

                # Resize if needed
                if (real_pixmap.width(), real_pixmap.height()) != (
                    binary_mask.shape[1],
                    binary_mask.shape[0],
                ):
                    binary_mask = cv2.resize(
                        binary_mask,
                        (real_pixmap.width(), real_pixmap.height()),
                        interpolation=cv2.INTER_NEAREST,
                    )

                # Ensure mask is contiguous for QImage
                if not binary_mask.flags["C_CONTIGUOUS"]:
                    binary_mask = np.ascontiguousarray(binary_mask)

                height, width = binary_mask.shape

                # F-018: QImage wraps the numpy buffer without copying it, so
                # the backing array MUST stay alive for as long as the QImage
                # references it. Hold a reference on the controller; QPixmap
                # .fromImage() below deep-copies the pixels, after which the
                # array is free to be replaced.
                self._overlay_backing_array = binary_mask

                # Create QImage pointing directly to the numpy buffer
                # Format_Indexed8: 1 byte per pixel
                overlay_image = QImage(
                    binary_mask.data,
                    width,
                    height,
                    width,
                    QImage.Format.Format_Indexed8,
                )

                # Define color table.
                # F-017: the mask is Otsu-binarized above, so it contains ONLY
                # 0 (background) and 255 (foreground) — there is no value in the
                # 1..254 range. The table therefore makes index 0 fully
                # transparent and colors everything from the first foreground
                # value (1) upward, so it stays correct even if a future soft
                # mask produces intermediate values rather than silently
                # dropping foreground pixels below an arbitrary cutoff.
                color_table = [0] * 256

                # Fill the foreground range with the color.
                neon_green = QColor(20, 255, 57, 128).rgba()
                for i in range(1, 256):
                    color_table[i] = neon_green

                overlay_image.setColorTable(color_table)

                overlay_pixmap = QPixmap.fromImage(overlay_image)
                overlay_item = QGraphicsPixmapItem(overlay_pixmap)
                overlay_item.setZValue(1)
                items.append(overlay_item)
            else:
                logger.warning(
                    "Failed to load fake image data for %s",
                    os.path.basename(self.current_image),
                )
        else:
            logger.info(
                "No fake image found for %s - showing single image view",
                os.path.basename(self.current_image),
            )

        self.update_scene_items(items)

    def display_side_by_side_images(self):
        """Display original and processed images side by side using positioned items."""
        if not self.current_image:
            return

        # Load the original image
        real_pixmap = QPixmap(self.current_image)
        if real_pixmap.isNull():
            return

        real_item = QGraphicsPixmapItem(real_pixmap)
        real_item.setZValue(0)

        items = [real_item]

        # Try to lazy load the processed image if needed
        if not self.current_fake_image:
            base_name = os.path.splitext(os.path.basename(self.current_image))[0]
            self.current_fake_image = self.main_window.image_manager.get_fake_image_path(base_name)

        # Processed Image
        if self.current_fake_image:
            # Convert _fake.png to _real.png for the processed image
            real_processed_path = self.current_fake_image.replace("_fake.png", "_real.png")

            # Note: The original code loaded 'real_processed_path' as 'real_pixmap' for the right side?
            # Re-reading original logic: "painter.drawPixmap(0, 0, real_pixmap); painter.drawPixmap(width, 0, fake_pixmap)"
            # Wait, the original code loaded `real_pixmap = QPixmap(real_processed_path)` which overrode the variable.
            # So left image = processed_real, right image = processed_fake.

            # Let's verify exactly what "Side by Side" usually means in this context.
            # Usually it's Real vs Fake.
            # The previous code did:
            # real_pixmap = QPixmap(real_processed_path)  <-- This is the "real" input to the model
            # fake_pixmap = QPixmap(self.current_fake_image) <-- This is the output

            right_pixmap = QPixmap(self.current_fake_image)
            left_pixmap = QPixmap(real_processed_path)

            if not left_pixmap.isNull():
                left_item = QGraphicsPixmapItem(left_pixmap)
                left_item.setPos(0, 0)
                items = [
                    left_item
                ]  # Replace the original real_item since we are showing the specific pair

            if not right_pixmap.isNull():
                right_item = QGraphicsPixmapItem(right_pixmap)
                # Position it to the right of the first image
                width = left_pixmap.width() if not left_pixmap.isNull() else real_pixmap.width()
                right_item.setPos(width, 0)
                items.append(right_item)

        else:
            self.main_window.status_bar.showMessage("Processed image not available", 3000)

        self.update_scene_items(items)

    def set_magnifying_view_image(self, pixmap):
        """Legacy wrapper for single image display"""
        item = QGraphicsPixmapItem(pixmap)
        self.update_scene_items([item])

    def update_scene_items(self, items):
        """Update the scene with a list of graphics items"""
        scene = QGraphicsScene()

        # Calculate bounding rect for all items to fit view
        total_rect = None

        for item in items:
            # Set high-quality transformation mode
            if isinstance(item, QGraphicsPixmapItem):
                item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                item.setCacheMode(QGraphicsPixmapItem.CacheMode.DeviceCoordinateCache)

            scene.addItem(item)

            # Expand bounding rect
            item_rect = item.sceneBoundingRect()
            if total_rect is None:
                total_rect = item_rect
            else:
                total_rect = total_rect.united(item_rect)

        self.magnifying_view.setScene(scene)

        # Fit view while maintaining aspect ratio
        if total_rect:
            self.magnifying_view.fitInView(total_rect, Qt.AspectRatioMode.KeepAspectRatio)

        # Reset zoom level when setting new image
        self.magnifying_view.zoom = 1.0

    def clear_magnifying_view(self):
        scene = QGraphicsScene()
        scene.addText("Select an image to display")
        self.magnifying_view.setScene(scene)
        self.magnifying_view.show()

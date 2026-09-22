"""
Drawing tools for the mask tracing interface.
Provides mixin class with drawing, flood fill, and undo/redo functionality.

The working mask is an ARGB pixmap: opaque ``brush_color`` where the mask is
set, fully transparent elsewhere. Strokes are painted without antialiasing so
the canvas shows exactly the binary mask that gets saved.
"""

import cv2
import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QImage, QPainter, QPen, QPixmap


def pixmap_to_argb_array(pixmap: QPixmap) -> np.ndarray:
    """Copy a pixmap into an ``(h, w, 4)`` uint8 array in QImage ARGB32 byte order (B, G, R, A)."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, : width * 4].reshape(height, width, 4).copy()


def argb_array_to_pixmap(arr: np.ndarray) -> QPixmap:
    """Inverse of :func:`pixmap_to_argb_array`."""
    arr = np.ascontiguousarray(arr)
    height, width = arr.shape[:2]
    image = QImage(arr.data, width, height, width * 4, QImage.Format.Format_ARGB32)
    # QImage wraps arr without copying; .copy() detaches before arr goes away.
    return QPixmap.fromImage(image.copy())


class MaskDrawingMixin:
    """
    Mixin class providing drawing functionality for mask tracing.
    Should be used with a class that has:
    - self.mask_pixmap: QPixmap for the mask
    - self.brush_color: QColor for the brush (the mask display colour)
    - self.brush_size: int for brush diameter in image pixels
    - self.eraser_button: checkable button for eraser mode
    - self.last_point: QPoint or None for last drawn point
    - self.undo_stack / self.redo_stack: bounded deques of QPixmap snapshots
    - self.refresh_mask_display(): push mask_pixmap to the scene
    - self.mark_dirty(): flag unsaved changes
    """

    def draw_point(self, pos):
        """
        Paint a dot (first point of a stroke) or a segment from the last point.

        Args:
            pos: QPoint image pixel to draw at
        """
        if not self.mask_pixmap:
            return

        painter = QPainter(self.mask_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self.eraser_button.isChecked():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)

        # Pixel centres, so the stroke is centred on the pixel under the cursor
        # (and on the brush preview ring).
        centre = QPointF(pos.x() + 0.5, pos.y() + 0.5)
        if self.last_point is not None:
            pen = QPen(
                self.brush_color,
                self.brush_size,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            start = QPointF(self.last_point.x() + 0.5, self.last_point.y() + 0.5)
            painter.drawLine(start, centre)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self.brush_color))
            r = self.brush_size / 2
            painter.drawEllipse(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r))

        painter.end()

        self.last_point = pos
        self.refresh_mask_display()

    def flood_fill(self, pos) -> bool:
        """
        Fill the closed outline that contains ``pos`` with the brush colour.

        Filling uses the outer contour of the traced mask, so any holes inside
        that outline are filled too. Clicking outside every closed outline does
        nothing (filling the open background would cover the whole image).

        Args:
            pos: QPoint image pixel to start the fill

        Returns:
            True if a region was filled.
        """
        if not self.mask_pixmap:
            return False

        arr = pixmap_to_argb_array(self.mask_pixmap)
        binary = (arr[:, :, 3] > 0).astype(np.uint8) * 255
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        seed = (float(pos.x()), float(pos.y()))
        target = next((c for c in contours if cv2.pointPolygonTest(c, seed, False) >= 0), None)
        if target is None:
            return False

        region = np.zeros(binary.shape, np.uint8)
        cv2.drawContours(region, [target], -1, 255, -1)
        inside = region == 255

        c = self.brush_color
        arr[inside] = (c.blue(), c.green(), c.red(), 255)

        # The undo snapshot is taken by begin_stroke before dispatching here,
        # so we must NOT save again or a single fill would cost two undos.
        self.mask_pixmap = argb_array_to_pixmap(arr)
        self.refresh_mask_display()
        return True

    def save_for_undo(self):
        """Save current state for undo with stack size limit."""
        if self.mask_pixmap:
            # undo_stack is a bounded deque, so appending past max_stack_size
            # auto-evicts the oldest entry.
            self.undo_stack.append(self.mask_pixmap.copy())

            # Clear redo stack as new action invalidates redo history
            self.redo_stack.clear()

    def undo(self):
        """Undo the last action."""
        if self.undo_stack:
            self.redo_stack.append(self.mask_pixmap.copy())
            self.mask_pixmap = self.undo_stack.pop()
            self.mark_dirty()
            self.refresh_mask_display()

    def redo(self):
        """Redo the last undone action."""
        if self.redo_stack:
            self.undo_stack.append(self.mask_pixmap.copy())
            self.mask_pixmap = self.redo_stack.pop()
            self.mark_dirty()
            self.refresh_mask_display()

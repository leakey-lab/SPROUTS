"""
Drawing tools of the raster editors (Correct Mask and Trace GT).

The editor's document is a ``(h, w)`` uint8 *plane* (mask: 0/1, GT: class
ids), committed in :class:`app.gui.patch_history.PatchHistory`. It is shown as
an ARGB canvas pixmap (``mask_pixmap``): each plane value is drawn in the
colour of the editor's lookup table (0 = transparent). Strokes are painted
straight onto the canvas without antialiasing, so the canvas always shows
exactly the plane that gets saved; at the end of a stroke the painted box is
read back into the plane and recorded as one undoable patch.

Every change repaints only its box: a stroke segment calls
``refresh_mask_region(rect)``, never a whole-canvas update or copy.
"""

from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QImage, QPainter, QPen, QPixmap

from .patch_history import Patch, Rect

# Extra pixels around a segment's pen box (round caps, rasterization slack).
_DIRTY_PAD = 2


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


def _qrect(rect: Rect) -> QRect:
    return QRect(*rect)


def _rect(qrect: QRect) -> Rect:
    return qrect.x(), qrect.y(), qrect.width(), qrect.height()


class MaskDrawingMixin:
    """
    Brush, eraser, fill, clear and patch undo/redo on a plane-backed canvas.

    The host class provides:

    - ``mask_pixmap``: the ARGB canvas (``None`` while nothing is open);
    - ``edits``: a :class:`PatchHistory` holding the committed plane;
    - ``brush_color`` / ``brush_size`` / ``eraser_button`` / ``last_point``;
    - ``_plane_lut()``: ``(256, 4)`` uint8 BGRA colour of each plane value;
    - ``_argb_to_plane(argb, rect)``: canvas pixels of ``rect`` -> plane values;
    - ``_fill_source(plane)``: ``(uint8 0/255 outline image, value to fill with)``;
    - ``refresh_mask_region(QRectF)`` / ``refresh_mask_display()``: repaint;
    - ``mark_dirty()`` and the hooks ``_on_patch(patch, forward)`` / ``_after_stroke()``.
    """

    _stroke_rect: QRect | None = None

    # ------------------------------------------------------------ history
    @property
    def undo_stack(self):
        """Undoable actions, oldest first (one entry per stroke, fill or clear)."""
        return self.edits.undo_stack

    @property
    def redo_stack(self):
        return self.edits.redo_stack

    # ------------------------------------------------------------ strokes
    def draw_point(self, pos):
        """Paint a dot (first point of a stroke) or a segment from the last point.

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
        r = self.brush_size / 2
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
            box = QRectF(start, centre).normalized()
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self.brush_color))
            painter.drawEllipse(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r))
            box = QRectF(centre, centre)
        painter.end()

        pad = r + _DIRTY_PAD
        dirty = box.adjusted(-pad, -pad, pad, pad).toAlignedRect()
        dirty = dirty.intersected(self.mask_pixmap.rect())
        self._stroke_rect = dirty if self._stroke_rect is None else self._stroke_rect.united(dirty)
        self.last_point = pos
        self.refresh_mask_region(QRectF(dirty))

    def commit_stroke(self) -> Patch | None:
        """Record the strokes painted since the last commit as one undoable action."""
        rect, self._stroke_rect = self._stroke_rect, None
        if rect is None or rect.isEmpty() or self.edits.plane is None:
            return None
        after = self._argb_to_plane(pixmap_to_argb_array(self.mask_pixmap.copy(rect)), _rect(rect))
        return self._commit(_rect(rect), after)

    def _commit(self, rect: Rect, after: np.ndarray) -> Patch:
        patch = self.edits.commit(rect, after)
        self._on_patch(patch, True)
        return patch

    # ------------------------------------------------------------ plane -> canvas
    def _render_plane_rect(self, rect: Rect) -> None:
        """Repaint ``rect`` of the canvas from the committed plane."""
        x, y, w, h = self.edits.clip(rect)
        if not (w and h and self.mask_pixmap):
            return
        argb = np.ascontiguousarray(self._plane_lut()[self.edits.plane[y : y + h, x : x + w]])
        image = QImage(argb.data, w, h, w * 4, QImage.Format.Format_ARGB32)
        painter = QPainter(self.mask_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(QPoint(x, y), image)
        painter.end()
        self.refresh_mask_region(QRectF(x, y, w, h))

    def _plane_to_pixmap(self) -> QPixmap:
        """The whole canvas rendered from the committed plane."""
        return argb_array_to_pixmap(self._plane_lut()[self.edits.plane])

    # ------------------------------------------------------------ tools
    def flood_fill(self, pos) -> bool:
        """
        Fill the closed outline that contains ``pos`` (one undoable action).

        Filling uses the outer contour of the traced outline (see
        ``_fill_source``), so any holes inside that outline are filled too.
        Clicking outside every closed outline does nothing (filling the open
        background would cover the whole image).

        Args:
            pos: QPoint image pixel to start the fill

        Returns:
            True if a region was filled.
        """
        plane = self.edits.plane
        if plane is None or not self.mask_pixmap:
            return False
        binary, value = self._fill_source(plane)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        seed = (float(pos.x()), float(pos.y()))
        target = next((c for c in contours if cv2.pointPolygonTest(c, seed, False) >= 0), None)
        if target is None:
            return False

        x, y, w, h = cv2.boundingRect(target)
        region = np.zeros((h, w), np.uint8)
        cv2.drawContours(region, [target], -1, 255, -1, offset=(-x, -y))
        after = plane[y : y + h, x : x + w].copy()
        after[region == 255] = value
        self._commit((x, y, w, h), after)
        self._render_plane_rect((x, y, w, h))
        return True

    def _clear_canvas(self) -> None:
        """Set the whole plane to 0 (transparent) as one undoable action."""
        plane = self.edits.plane
        height, width = plane.shape
        self._commit((0, 0, width, height), np.zeros_like(plane))
        self.mask_pixmap.fill(Qt.GlobalColor.transparent)
        self.refresh_mask_display()

    def undo(self):
        """Undo the last action."""
        self._step_history(self.edits.undo, False)

    def redo(self):
        """Redo the last undone action."""
        self._step_history(self.edits.redo, True)

    def _step_history(self, step, forward: bool) -> None:
        if self.drawing:
            return  # a stroke in progress is not in the history yet
        patch = step()
        if patch is None:
            return
        self._render_plane_rect(patch.rect)
        self._on_patch(patch, forward)
        self.mark_dirty()
        self._after_stroke()

    def _on_patch(self, patch: Patch, forward: bool) -> None:
        """Hook: ``patch`` was applied (``forward``) or reverted. No-op here."""

    def _after_stroke(self) -> None:
        """Hook: an action changed the canvas (stroke, fill, clear, undo, redo). No-op here."""

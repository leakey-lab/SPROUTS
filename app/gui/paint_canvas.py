"""The editable raster canvas of the Correct mask layer and of Trace GT (plan contract C9).

A raster editor's document is a ``(h, w)`` uint8 *plane* (mask: 0/1, GT: class ids)
committed in a :class:`~app.gui.patch_history.PatchHistory`. :class:`PaintCanvas` shows it
as an ARGB canvas, each plane value in its lookup-table colour (0 = transparent), and
paints on it. The painting is today's ``MaskDrawingMixin`` moved verbatim: pixel centres,
round caps, no antialiasing, ``CompositionMode_Source`` (``DestinationOut`` to erase), so
the canvas always shows exactly the plane that gets saved; :meth:`PaintCanvas.commit`
reads the painted box back into the plane as one undoable patch.

The canvas is a ``QImage`` in ``Format_ARGB32_Premultiplied``, the raster engine's native
format: a stroke segment paints in place (never copying the canvas) and reports only its
box, the segment's bounds padded by the brush radius + 2 pixels. :meth:`PaintCanvas.rebuild`
writes the plane through a premultiplied colour table straight into the canvas pixels,
more than ten times faster than today's ``QPixmap`` of ``lut[plane]`` and pixel-identical.
There is no abort: a stroke in progress is always committed before anything else changes
the plane (C2).

:func:`pixmap_to_argb_array` and :func:`argb_array_to_image` convert a canvas to and from
an ``(h, w, 4)`` uint8 array of ``Format_ARGB32`` bytes (B, G, R, A, not premultiplied).
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap

from .patch_history import Patch, PatchHistory, Rect

__all__ = ["PaintCanvas", "Rect", "argb_array_to_image", "pixmap_to_argb_array"]

# Extra pixels around a segment's pen box (round caps, rasterization slack).
_DIRTY_PAD = 2


def pixmap_to_argb_array(canvas: QImage | QPixmap) -> np.ndarray:
    """A copy of ``canvas`` as an ``(h, w, 4)`` uint8 array: B, G, R, A of ``Format_ARGB32``."""
    image = canvas.toImage() if isinstance(canvas, QPixmap) else canvas
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    if image.isNull():
        return np.zeros((height, width, 4), np.uint8)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, : width * 4].reshape(height, width, 4).copy()


def argb_array_to_image(arr: np.ndarray) -> QImage:
    """Inverse of :func:`pixmap_to_argb_array`: an owning ``Format_ARGB32`` image of ``arr``."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    height, width = arr.shape[:2]
    image = QImage(arr.data, width, height, width * 4, QImage.Format.Format_ARGB32)
    return image.copy()  # the QImage wraps arr without copying; detach before arr goes away


def _premultiplied_words(lut: np.ndarray) -> np.ndarray:
    """The 256 ``lut`` colours as premultiplied ARGB32 pixel words, premultiplied by Qt."""
    row = np.ascontiguousarray(lut, dtype=np.uint8)
    if row.shape != (256, 4):
        raise ValueError(f"a plane lookup table is (256, 4) B, G, R, A rows, not {row.shape}")
    image = QImage(row.data, 256, 1, 256 * 4, QImage.Format.Format_ARGB32)
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return np.frombuffer(ptr, np.int32)[:256].copy()


def _pixel_words(image: QImage) -> np.ndarray:
    """The pixels of a 32-bit ``image`` as a writable ``(h, w)`` int32 array (no copy).

    ``bits()`` copies the image only when it is shared; a canvas never is, so writes land in
    the canvas itself. 32-bit rows carry no padding.
    """
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    return np.frombuffer(ptr, np.int32).reshape(image.height(), image.width())


def _plane_image(plane: np.ndarray, lut: np.ndarray) -> QImage:
    """The whole plane drawn in ``lut``'s colours, as a premultiplied ARGB image.

    A colour-table lookup written straight into the new image's pixels, with the table
    premultiplied by Qt: the pixels of ``QPixmap.fromImage`` of ``lut[plane]`` (today's
    canvas), with no intermediate ARGB array and no second pass.
    """
    plane = np.ascontiguousarray(plane, dtype=np.uint8)
    height, width = plane.shape
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        return image
    cv2.LUT(plane, _premultiplied_words(lut), dst=_pixel_words(image))
    return image


def _rect(qrect: QRect) -> Rect:
    return qrect.x(), qrect.y(), qrect.width(), qrect.height()


class PaintCanvas:
    """The editable ARGB canvas over a PatchHistory plane (Correct mask layer, Trace GT).

    Painting is today's MaskDrawingMixin, moved verbatim (mask_drawing_tools.py:85-223):
    pixel centres, round caps, no antialiasing, CompositionMode_Source / DestinationOut,
    dirty rect = segment box padded by r + 2. The canvas is a QImage in
    Format_ARGB32_Premultiplied, so painting a segment never copies it.

    The owner supplies the plane's meaning: ``lut()`` is the ``(256, 4)`` B, G, R, A colour
    of each plane value, ``to_plane(argb, rect)`` turns a painted box back into plane
    values and ``fill_source(plane)`` gives the ``(0/255 outline image, value)`` a fill
    uses. ``on_region(rect)`` / ``on_replaced()`` report repaints; ``on_patch(patch,
    forward)`` follows every commit, fill, clear (forward), redo (forward) and undo.
    """

    def __init__(
        self,
        history: PatchHistory,
        *,
        lut: Callable[[], np.ndarray],
        to_plane: Callable[[np.ndarray, Rect], np.ndarray],
        fill_source: Callable[[np.ndarray], tuple[np.ndarray, int]],
        on_region: Callable[[QRect], None],
        on_replaced: Callable[[], None],
        on_patch: Callable[[Patch, bool], None] | None = None,
    ) -> None:
        self.history = history
        self.image: QImage | None = None  # None while history.plane is None
        self._lut = lut
        self._to_plane = to_plane
        self._fill_source = fill_source
        self._on_region = on_region
        self._on_replaced = on_replaced
        self._on_patch = on_patch
        # The stroke in progress: its last point (None between strokes), pen and painted box.
        self._last: QPoint | None = None
        self._stroke_rect: QRect | None = None
        self._colour = QColor()
        self._size = 1
        self._erase = False

    # ------------------------------------------------------------------ canvas
    def rebuild(self) -> None:
        """Draw the whole canvas from ``history.plane`` (None when there is no plane).

        A stroke in progress ends with the canvas it was painted on: its pixels were never
        in the plane, so nothing is committed (call :meth:`commit` first to keep them). A
        canvas of the plane's size is drawn over in place (the next image of a folder):
        every pixel is written, and no new full-size image is made (1.4 ms -> 0.2 ms at
        2048 x 1536).
        """
        self._last = None
        self._stroke_rect = None
        plane = self.history.plane
        image = self.image
        if plane is None:
            self.image = None
        elif image is not None and (image.height(), image.width()) == plane.shape:
            words = _premultiplied_words(self._lut())
            cv2.LUT(np.ascontiguousarray(plane, dtype=np.uint8), words, dst=_pixel_words(image))
        else:
            self.image = _plane_image(plane, self._lut())
        self._on_replaced()

    def render_rect(self, rect: Rect) -> None:
        """Repaint ``rect`` of the canvas from the committed plane (clipped to it).

        The box goes through the premultiplied colour table straight into the canvas
        pixels, as :meth:`rebuild` does: the pixels Qt's own premultiply of ``lut[plane]``
        gives (a whole 2048 x 1536 box in about 0.2 ms; drawing a ``lut[plane]`` array with
        a painter took about 26 ms).
        """
        x, y, w, h = self.history.clip(rect)
        if not (w and h and self.image is not None):
            return
        box = _pixel_words(self.image)[y : y + h, x : x + w]
        cv2.LUT(
            self.history.plane[y : y + h, x : x + w], _premultiplied_words(self._lut()), dst=box
        )
        self._on_region(QRect(x, y, w, h))

    # ------------------------------------------------------------------ strokes
    def begin(self, pos: QPoint, color: QColor, size: int, *, erase: bool) -> None:
        """Start a stroke at image pixel ``pos``: its first dab (a disc of ``size`` px).

        A stroke still in progress is committed first (there is no abort). Nothing happens
        while there is no canvas.
        """
        if self.in_stroke():
            self.commit()
        if self.image is None:
            return
        self._colour = QColor(color)
        self._size = size
        self._erase = bool(erase)
        self._stroke_rect = None
        self._draw(pos)

    def extend(self, pos: QPoint) -> None:
        """Paint the segment from the stroke's last point to ``pos`` (no-op between strokes)."""
        if self._last is not None and self.image is not None:
            self._draw(pos)

    def in_stroke(self) -> bool:
        """True between :meth:`begin` and the :meth:`commit` that ends the stroke."""
        return self._last is not None

    def commit(self) -> Patch | None:
        """End the stroke: read its box back into the plane as one undoable action.

        None when nothing was painted since the last commit (no history entry).
        """
        rect, self._stroke_rect = self._stroke_rect, None
        self._last = None
        if rect is None or rect.isEmpty() or self.history.plane is None or self.image is None:
            return None
        after = self._to_plane(pixmap_to_argb_array(self.image.copy(rect)), _rect(rect))
        return self._commit(_rect(rect), after)

    def _draw(self, pos: QPoint) -> None:
        """Today's ``draw_point``: a dot (first point of a stroke) or a segment."""
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._erase:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)

        # Pixel centres, so the stroke is centred on the pixel under the cursor
        # (and on the brush preview ring).
        centre = QPointF(pos.x() + 0.5, pos.y() + 0.5)
        r = self._size / 2
        if self._last is not None:
            pen = QPen(
                self._colour,
                self._size,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            start = QPointF(self._last.x() + 0.5, self._last.y() + 0.5)
            painter.drawLine(start, centre)
            box = QRectF(start, centre).normalized()
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._colour))
            painter.drawEllipse(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r))
            box = QRectF(centre, centre)
        painter.end()

        pad = r + _DIRTY_PAD
        dirty = box.adjusted(-pad, -pad, pad, pad).toAlignedRect()
        dirty = dirty.intersected(self.image.rect())
        self._stroke_rect = dirty if self._stroke_rect is None else self._stroke_rect.united(dirty)
        self._last = QPoint(pos)
        self._on_region(dirty)

    # ------------------------------------------------------------------ actions
    def fill(self, pos: QPoint) -> Patch | None:
        """Fill the closed outline that contains ``pos`` (one undoable action).

        Filling uses the outer contour of the outline (see ``fill_source``), so holes inside
        it are filled too. Outside every closed outline nothing happens and None is returned
        (filling the open background would cover the whole image; no history entry).
        """
        if self.in_stroke():
            self.commit()
        plane = self.history.plane
        if plane is None or self.image is None:
            return None
        binary, value = self._fill_source(plane)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        seed = (float(pos.x()), float(pos.y()))
        target = next((c for c in contours if cv2.pointPolygonTest(c, seed, False) >= 0), None)
        if target is None:
            return None

        x, y, w, h = cv2.boundingRect(target)
        region = np.zeros((h, w), np.uint8)
        cv2.drawContours(region, [target], -1, 255, -1, offset=(-x, -y))
        after = plane[y : y + h, x : x + w].copy()
        after[region == 255] = value
        patch = self._commit((x, y, w, h), after)
        self.render_rect((x, y, w, h))
        return patch

    def clear(self) -> Patch | None:
        """Set the whole plane to 0 (transparent) as one undoable action; None without a plane.

        A stroke in progress is committed first, so Clear is undone back to it.
        """
        if self.in_stroke():
            self.commit()
        plane = self.history.plane
        if plane is None or self.image is None:
            return None
        height, width = plane.shape
        patch = self._commit((0, 0, width, height), np.zeros_like(plane))
        self.image.fill(Qt.GlobalColor.transparent)
        self._on_replaced()
        return patch

    def undo(self) -> Patch | None:
        """Undo the last action and repaint its box; None when there is nothing to undo.

        Also None while a stroke is in progress: it is not in the history yet.
        """
        return self._step(self.history.undo, forward=False)

    def redo(self) -> Patch | None:
        """Redo the last undone action and repaint its box; None when there is nothing to redo."""
        return self._step(self.history.redo, forward=True)

    def _step(self, step: Callable[[], Patch | None], *, forward: bool) -> Patch | None:
        if self.in_stroke():
            return None  # a stroke in progress is not in the history yet
        patch = step()
        if patch is None:
            return None
        self.render_rect(patch.rect)
        if self._on_patch is not None:
            self._on_patch(patch, forward)
        return patch

    def _commit(self, rect: Rect, after: np.ndarray) -> Patch:
        patch = self.history.commit(rect, after)
        if self._on_patch is not None:
            self._on_patch(patch, True)
        return patch

"""A graphics item that draws an editable canvas pixmap without owning it.

``QGraphicsPixmapItem.setPixmap(pm)`` keeps its own implicitly shared copy of
``pm``. The next ``QPainter(pm)`` then has to detach, which deep-copies the
whole canvas on every stroke segment, and ``setPixmap`` also invalidates the
whole item. :class:`CanvasPixmapItem` instead reads the pixmap through a source
callable at paint time, so no second reference exists, and it repaints only
the exposed part:

* after painting a region of the pixmap, call ``update(rect)`` (the standard
  ``QGraphicsItem.update``) with that region, e.g. a stroke segment's bounds;
* after the pixmap was replaced, resized or changed wholesale (load, undo,
  fill, clear), call :meth:`sync`.

The pixmap is drawn without smoothing: canvas pixels stay crisp when zoomed.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPointF, QRectF, QSizeF
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

PixmapSource = Callable[[], "QPixmap | None"]


class CanvasPixmapItem(QGraphicsItem):
    """Draws ``source()`` (a QPixmap or None) at the item origin, exposed rect only."""

    def __init__(self, source: PixmapSource, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._size = self._source_size()
        # Makes option.exposedRect the dirty part rather than the whole item.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def _source_size(self) -> QSizeF:
        pixmap = self._source()
        if pixmap is None or pixmap.isNull():
            return QSizeF()
        return QSizeF(pixmap.size())

    def sync(self) -> None:
        """Pick up a replaced or wholesale-changed source pixmap and repaint the item."""
        size = self._source_size()
        if size != self._size:
            self.prepareGeometryChange()
            self._size = size
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(QPointF(0, 0), self._size)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        pixmap = self._source()
        if pixmap is None or pixmap.isNull():
            return
        exposed = option.exposedRect.toAlignedRect().intersected(pixmap.rect())
        if exposed.isEmpty():
            return
        hint = QPainter.RenderHint.SmoothPixmapTransform
        smooth = painter.testRenderHint(hint)
        painter.setRenderHint(hint, False)
        painter.drawPixmap(exposed, pixmap, exposed)
        painter.setRenderHint(hint, smooth)

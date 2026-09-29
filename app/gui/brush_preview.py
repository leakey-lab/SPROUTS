"""Scene-space brush / eraser ring, exact at any zoom.

The ring is two ellipse items in the canvas scene: a dark cosmetic halo (for
contrast on bright pixels) under a thin ring in the tool colour. The ellipse
rect is in scene units (image pixels), so the ring always covers exactly the
footprint of the stroke or eraser on screen, while the cosmetic pens keep a
constant screen width. Nothing is rebuilt when the zoom changes, unlike an OS
cursor bitmap sized in screen pixels.

Used by the mask / GT editor (brush and eraser) and the skeleton eraser.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsScene

from app.gui.widgets import tokens

HALO_ALPHA = 170


class BrushPreview:
    """A footprint ring that follows the pointer in a ``QGraphicsScene``."""

    HALO_WIDTH = 3
    RING_WIDTH = 1
    Z_VALUE = 10.0  # above the image (0) and the mask / skeleton overlay (1)

    def __init__(
        self,
        scene: QGraphicsScene,
        *,
        color: QColor | str = tokens.TEXT,
        z_value: float = Z_VALUE,
    ) -> None:
        self.outer = QGraphicsEllipseItem()
        self.inner = QGraphicsEllipseItem()
        halo = QColor(tokens.BG_0)
        halo.setAlpha(HALO_ALPHA)
        halo_pen = QPen(halo, self.HALO_WIDTH)
        halo_pen.setCosmetic(True)
        self.outer.setPen(halo_pen)
        for item in (self.outer, self.inner):
            item.setZValue(z_value)
            item.setVisible(False)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            scene.addItem(item)
        self._center = QPointF()
        self._diameter = 1.0
        self.set_style(color)

    def set_style(self, color: QColor | str, *, dashed: bool = False) -> None:
        """Colour of the thin ring; ``dashed`` marks an eraser."""
        pen = QPen(QColor(color), self.RING_WIDTH)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        self.inner.setPen(pen)

    def set_diameter(self, diameter: float) -> None:
        """Footprint diameter in image pixels."""
        self._diameter = max(0.0, float(diameter))
        self._place()

    def move_to(self, scene_pos: QPointF) -> None:
        """Centre the ring exactly on ``scene_pos``."""
        self._center = QPointF(scene_pos)
        self._place()

    def move_to_pixel(self, pixel: QPoint) -> None:
        """Centre the ring on the centre of image ``pixel``, where strokes are centred."""
        self.move_to(QPointF(pixel.x() + 0.5, pixel.y() + 0.5))

    def set_visible(self, visible: bool) -> None:
        for item in (self.outer, self.inner):
            item.setVisible(bool(visible))

    def is_visible(self) -> bool:
        return self.inner.isVisible()

    def rect(self) -> QRectF:
        """The footprint in scene coordinates."""
        return self.inner.rect()

    def _place(self) -> None:
        r = self._diameter / 2
        rect = QRectF(self._center.x() - r, self._center.y() - r, 2 * r, 2 * r)
        # setRect() returns early for an unchanged rect, so hovering inside one
        # pixel costs no repaint.
        self.outer.setRect(rect)
        self.inner.setRect(rect)

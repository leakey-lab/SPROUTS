"""Scene-space brush / eraser ring (app/gui/brush_preview.py; UXE-10, PE-15).

The ring must show the real footprint at any zoom: its rect is in image pixels,
centred on the pixel under the cursor like the stroke and the cv2 eraser, and
drawn with cosmetic pens so no cursor bitmap is rebuilt per zoom step.
"""

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QTransform
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

from app.gui.brush_preview import BrushPreview
from app.gui.widgets import tokens


@pytest.fixture
def scene(app):
    return QGraphicsScene()


def test_ring_is_the_footprint_centred_on_the_pixel_centre(scene):
    ring = BrushPreview(scene)
    ring.set_diameter(20)
    ring.move_to_pixel(QPoint(100, 100))
    assert ring.rect() == QRectF(90.5, 90.5, 20, 20)
    assert ring.outer.rect() == ring.inner.rect() == ring.rect()


def test_move_to_uses_the_exact_scene_position(scene):
    ring = BrushPreview(scene)
    ring.set_diameter(7)
    ring.move_to(QPointF(10.0, 4.0))
    assert ring.rect() == QRectF(6.5, 0.5, 7, 7)


def test_resizing_keeps_the_centre(scene):
    ring = BrushPreview(scene)
    ring.move_to_pixel(QPoint(40, 30))
    ring.set_diameter(8)
    ring.set_diameter(30)
    assert ring.rect().center() == QPointF(40.5, 30.5)
    assert ring.rect().width() == 30


@pytest.mark.parametrize("zoom", [1.0, 4.0])
def test_ring_on_screen_scales_with_zoom_while_its_pens_do_not(app, scene, zoom):
    ring = BrushPreview(scene)
    ring.set_diameter(20)
    ring.move_to_pixel(QPoint(50, 50))
    view = QGraphicsView(scene)
    view.setTransform(QTransform.fromScale(zoom, zoom))
    on_screen = view.viewportTransform().mapRect(ring.rect())
    assert on_screen.width() == pytest.approx(20 * zoom, abs=1)
    for item in (ring.outer, ring.inner):
        assert item.pen().isCosmetic()  # constant screen width at any zoom
    assert ring.outer.pen().widthF() > ring.inner.pen().widthF()  # halo under the ring


def test_visibility_covers_both_rings_and_starts_hidden(scene):
    ring = BrushPreview(scene)
    assert not ring.is_visible()
    ring.set_visible(True)
    assert ring.outer.isVisible() and ring.inner.isVisible() and ring.is_visible()
    ring.set_visible(False)
    assert not ring.outer.isVisible() and not ring.inner.isVisible()


def test_rings_ignore_the_mouse_and_sit_above_the_image(scene):
    ring = BrushPreview(scene)
    for item in (ring.outer, ring.inner):
        assert item.scene() is scene
        assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
        assert item.zValue() > 1  # above image (0) and mask/skeleton overlay (1)


def test_style_sets_the_ring_colour_and_dash(scene):
    ring = BrushPreview(scene)
    assert ring.inner.pen().color() == QColor(tokens.TEXT)  # default colour
    ring.set_style(tokens.DANGER, dashed=True)
    assert ring.inner.pen().color() == QColor(tokens.DANGER)
    assert ring.inner.pen().style() == Qt.PenStyle.DashLine
    assert ring.inner.pen().isCosmetic()
    ring.set_style(QColor(tokens.MASK))
    assert ring.inner.pen().style() == Qt.PenStyle.SolidLine
    assert ring.inner.pen().color() == QColor(tokens.MASK)

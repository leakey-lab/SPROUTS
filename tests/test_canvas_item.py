"""CanvasPixmapItem (app/gui/canvas_item.py, PE-04).

The item paints a pixmap it does not own, read through a source callable, so a
QPainter on the source never deep-copies it (implicit-sharing detach), and it
repaints only the exposed rect without smoothing.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
)

from app.gui.canvas_item import CanvasPixmapItem  # noqa: E402

RED = QColor(255, 0, 0)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Holder:
    def __init__(self, pixmap):
        self.pixmap = pixmap


def _filled(w, h, color=RED) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(color)
    return pm


def _render(scene, rect: QRectF) -> QImage:
    img = QImage(int(rect.width()), int(rect.height()), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    scene.render(p, QRectF(img.rect()), rect)
    p.end()
    return img


def test_painting_the_source_after_display_does_not_deep_copy_it(app):
    holder = _Holder(_filled(64, 48))
    scene = QGraphicsScene()
    scene.addItem(CanvasPixmapItem(lambda: holder.pixmap))
    _render(scene, QRectF(0, 0, 64, 48))  # the item has been painted
    serial = holder.pixmap.cacheKey() >> 32

    for i in range(5):
        p = QPainter(holder.pixmap)
        p.fillRect(i, i, 2, 2, QColor(0, 255, 0))
        p.end()

    assert holder.pixmap.cacheKey() >> 32 == serial  # same data, no detach copy
    assert _render(scene, QRectF(0, 0, 64, 48)).pixelColor(4, 4) == QColor(0, 255, 0)


def test_bounding_rect_follows_the_source_after_sync(app):
    holder = _Holder(None)
    item = CanvasPixmapItem(lambda: holder.pixmap)
    assert item.boundingRect().isEmpty()

    holder.pixmap = _filled(30, 20)
    item.sync()
    assert item.boundingRect() == QRectF(0, 0, 30, 20)

    holder.pixmap = _filled(8, 6)
    item.sync()
    assert item.boundingRect() == QRectF(0, 0, 8, 6)

    holder.pixmap = None
    item.sync()
    assert item.boundingRect().isEmpty()


def test_paint_draws_only_the_exposed_rect(app):
    holder = _Holder(_filled(40, 40))
    item = CanvasPixmapItem(lambda: holder.pixmap)
    item.sync()
    target = QImage(40, 40, QImage.Format.Format_ARGB32)
    target.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = QRectF(0, 0, 10, 10)

    p = QPainter(target)
    item.paint(p, option, None)
    p.end()

    assert target.pixelColor(5, 5) == RED
    assert target.pixelColor(20, 20).alpha() == 0


def test_paint_without_a_source_pixmap_draws_nothing(app):
    item = CanvasPixmapItem(lambda: None)
    target = QImage(10, 10, QImage.Format.Format_ARGB32)
    target.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = QRectF(0, 0, 10, 10)
    p = QPainter(target)
    item.paint(p, option, None)
    p.end()
    assert target.pixelColor(5, 5).alpha() == 0


def test_zoomed_canvas_pixels_stay_crisp(app):
    """Nearest-neighbour even when the painter asks for smooth pixmap scaling."""
    src = QImage(2, 1, QImage.Format.Format_ARGB32)
    src.setPixelColor(QPoint(0, 0), QColor(0, 0, 0))
    src.setPixelColor(QPoint(1, 0), QColor(255, 255, 255))
    holder = _Holder(QPixmap.fromImage(src))
    scene = QGraphicsScene()
    scene.addItem(CanvasPixmapItem(lambda: holder.pixmap))

    img = QImage(16, 8, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    scene.render(p, QRectF(0, 0, 16, 8), QRectF(0, 0, 2, 1))
    after = p.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.end()

    assert img.pixelColor(7, 4) == QColor(0, 0, 0)  # last column of the black pixel
    assert img.pixelColor(8, 4) == QColor(255, 255, 255)
    assert after  # the painter's own hint is restored for later items


def test_opacity_and_visibility_use_the_standard_item_api(app):
    holder = _Holder(_filled(4, 4))
    scene = QGraphicsScene()
    item = CanvasPixmapItem(lambda: holder.pixmap)
    item.sync()
    scene.addItem(item)
    item.setOpacity(0.5)
    half = _render(scene, QRectF(0, 0, 4, 4)).pixelColor(1, 1)
    assert 100 <= half.alpha() <= 155
    item.setVisible(False)
    assert _render(scene, QRectF(0, 0, 4, 4)).pixelColor(1, 1).alpha() == 0

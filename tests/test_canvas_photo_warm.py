"""The photo warms the pyramid levels one zoom step away when the view is idle
(app/gui/canvas_layers/photo.py).

After a paint, what a zoom step (``WARM_STEP``, the ladder's largest ratio, in or out) would
draw is warmed once the view has been still for ``WARM_DELAY_MS``: the tiles of the levels
drawn at that zoom, over the area such a step can show (inside the view for a step in; the
view grown by half its size on every side for a step out, whatever the anchor), converted
in idle slices of ``WARM_BUDGET_MS``. On an OpenGL paint engine the next frames draw those tiles
once, invisibly, so their textures are uploaded before the first frame that shows them.
A new image drops the warm-up of the old one.
"""

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
)

from app.gui.canvas_layers.photo import PhotoLayerItem, TiledLevels  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def photo(w: int = 1400, h: int = 1000, tile: int = 64) -> PhotoLayerItem:
    """An item whose levels are tiled at ``tile`` px (level 0 to 2 here)."""
    rng = np.random.default_rng(1)
    item = PhotoLayerItem()
    item.TILE = tile
    item.set_image(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))
    return item


def paint(item: PhotoLayerItem, zoom: float, exposed: QRectF) -> None:
    """Paint ``exposed`` (item px) at ``zoom`` into a raster image, as a view would."""
    size = (int(exposed.width() * zoom) + 2, int(exposed.height() * zoom) + 2)
    image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.scale(zoom, zoom)
    painter.translate(-exposed.left(), -exposed.top())
    option = QStyleOptionGraphicsItem()
    option.exposedRect = exposed
    item.paint(painter, option, None)
    painter.end()


def levels_built(item: PhotoLayerItem) -> set[int]:
    return {key[0] for key in item.built_tiles()}


def warm_all(item: PhotoLayerItem) -> None:
    for _ in range(1000):
        if not item.warm_levels(budget_ms=1000.0):
            return
    raise AssertionError("the warm-up never ended")


def test_a_step_into_a_coarser_level_is_warmed_ahead():
    item = photo()
    area = QRectF(300, 200, 400, 300)
    paint(item, 0.9, area)  # level 0 (the blend starts at 0.76)
    assert levels_built(item) == {0}
    warm_all(item)
    assert levels_built(item) == {0, 1}  # 0.9 / 1.5 = 0.6: level 0 blended with level 1
    before = item.built_tiles()
    paint(item, 0.6, QRectF(250, 160, 500, 380))  # zoomed out about the view: inside the grown area
    assert item.built_tiles() == before  # nothing converted in the frame


def test_a_step_into_a_finer_level_is_warmed_ahead():
    item = photo()
    area = QRectF(300, 200, 600, 400)
    paint(item, 0.4, area)  # level 1
    assert levels_built(item) == {1}
    warm_all(item)
    assert levels_built(item) == {0, 1}  # 0.4 * 1.5 = 0.6: level 0 blended with level 1
    before = item.built_tiles()
    paint(item, 0.6, QRectF(400, 250, 400, 300))  # zoomed in: inside the view
    paint(item, 0.4 / 1.5, QRectF(0, 0, 900, 600))  # zoomed out 1.5x about the view's corner
    assert item.built_tiles() == before


def test_nothing_is_warmed_when_a_step_stays_on_the_same_levels():
    item = photo(640, 480)
    paint(item, 2.0, QRectF(0, 0, 640, 480))  # 1.33 and 3.0: level 0 only
    assert item.warm_levels(budget_ms=1000.0) is False
    assert levels_built(item) == {0}


def test_the_warm_up_runs_by_itself_once_the_view_is_idle():
    item = photo()
    scene = QGraphicsScene()
    scene.addItem(item)
    paint(item, 0.9, QRectF(300, 200, 400, 300))
    assert levels_built(item) == {0}
    deadline = time.perf_counter() + 3.0
    while levels_built(item) != {0, 1} and time.perf_counter() < deadline:
        QTest.qWait(20)
    assert levels_built(item) == {0, 1}
    scene.removeItem(item)


def test_each_idle_slice_keeps_to_its_budget():
    item = photo(4000, 3000, tile=64)  # many small tiles to warm
    paint(item, 0.9, QRectF(0, 0, 4000, 3000))
    t0 = time.perf_counter()
    assert item.warm_levels(budget_ms=2.0) is True  # more remains
    assert (time.perf_counter() - t0) * 1000 < 50  # one slice, not the whole warm-up


def test_a_new_image_drops_the_warm_up_of_the_old_one():
    item = photo()
    paint(item, 0.9, QRectF(300, 200, 400, 300))
    item.set_image(np.zeros((480, 640, 3), np.uint8))
    assert item.warm_levels(budget_ms=1000.0) is False
    assert item.built_tiles() == {}


def test_a_deleted_item_ignores_its_pending_warm_up():
    scene = QGraphicsScene()
    item = photo()
    scene.addItem(item)
    paint(item, 0.9, QRectF(300, 200, 400, 300))
    scene.clear()  # deletes the C++ item while its warm-up timer is pending
    QTest.qWait(PhotoLayerItem.WARM_DELAY_MS + 100)  # the timer fires: no error


def test_tiles_made_ahead_are_uploaded_once_on_a_gl_engine_only():
    levels = TiledLevels(_tile_maker(), tile=64, margin=2, single_pixels=64 * 64)
    size, image = (300, 200), (300, 200)
    assert levels.warm(0, size, image, QRectF(0, 0, 300, 200), time.perf_counter() + 10) is False
    made = levels.built()
    assert len(made) == 5 * 4 and levels.pending_uploads() == len(made)

    image_ = QImage(10, 10, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image_)
    assert levels.upload(painter, 6) is False  # a raster engine uploads nothing
    assert levels.pending_uploads() == len(made)
    painter.end()

    painter = QPainter(image_)
    levels.draw(painter, 0, size, image, QRectF(0, 0, 60, 60))  # tiles drawn for real
    painter.end()
    assert levels.pending_uploads() < len(made)


def _tile_maker():
    from PyQt6.QtGui import QPixmap

    def make(level, x0, y0, x1, y1):
        pixmap = QPixmap(x1 - x0, y1 - y0)
        pixmap.fill()
        return pixmap

    return make

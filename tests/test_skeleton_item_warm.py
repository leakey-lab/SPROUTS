"""The skeleton layer item warms its vector tiles when idle (KNOWN_ISSUES #6, S3 / Q5).

The first frame of a zoom to 200 % and up used to draw pixel tiles and extract vectors under
its 4 ms budget. Once the changes settle, an item shown in a view extracts every tile in
short idle slices, so that frame finds them ready. Warming changes no pixel on screen, never
runs for an item no view shows (a render alone) or for the Library's items, and a change
drops only the tiles it touches.
"""

from __future__ import annotations

import os
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView  # noqa: E402

from app.gui.canvas_layers.skeleton import SkeletonLayerItem  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

W, H = 1100, 700  # 5 x 3 tiles of 256 px


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def views():
    shown: list[QGraphicsView] = []
    yield shown
    delete_widgets(shown)


def _busy(seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mask = np.zeros((H, W), np.uint8)
    for _ in range(120):
        pts = rng.integers(0, (W, H), size=(5, 2)).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(mask, [pts], False, 255, 1)
    return mask


def _shown(views: list, item: SkeletonLayerItem) -> QGraphicsView:
    scene = QGraphicsScene()
    scene.addItem(item)
    view = QGraphicsView(scene)
    views.append(view)
    view.resize(500, 350)
    view.scale(0.4, 0.4)
    view.show()
    return view


def _all_tiles() -> set[tuple[int, int]]:
    return {(tx, ty) for tx in range(5) for ty in range(3)}


def _wait(app, done, seconds: float = 5.0) -> bool:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        if done():
            return True
        time.sleep(0.002)
    return done()


def _render(item: SkeletonLayerItem, zoom: float = 2.0) -> np.ndarray:
    image = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    source = QRectF(300, 200, 800 / zoom, 600 / zoom)
    item.scene().render(painter, QRectF(image.rect()), source)
    painter.end()
    return np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8).copy()


def test_a_shown_item_warms_every_tile_once_the_changes_settle(app, views):
    item = SkeletonLayerItem()
    item.WARM_DELAY_MS = 20
    _shown(views, item)
    item.set_mask(_busy())

    assert item.vector_tiles() == {}  # set_mask itself extracts nothing
    assert _wait(app, lambda: set(item.vector_tiles()) == _all_tiles())


def test_warmed_tiles_draw_exactly_what_lazy_extraction_draws(app, views):
    mask = _busy()
    warmed = SkeletonLayerItem()
    warmed.WARM_DELAY_MS = 0
    _shown(views, warmed)
    warmed.set_mask(mask)
    assert _wait(app, lambda: set(warmed.vector_tiles()) == _all_tiles())
    lazy = SkeletonLayerItem()
    lazy.EXTRACT_BUDGET_MS = float("inf")
    lazy_scene = QGraphicsScene()  # no view: it extracts in its paint only
    lazy_scene.addItem(lazy)
    lazy.set_mask(mask)

    assert np.array_equal(_render(warmed), _render(lazy))


def test_a_change_waits_and_warms_again_only_the_tiles_it_dropped(app, views):
    mask = _busy()
    item = SkeletonLayerItem()
    item.WARM_DELAY_MS = 20
    _shown(views, item)
    item.set_mask(mask)
    assert _wait(app, lambda: set(item.vector_tiles()) == _all_tiles())
    kept = item.vector_tiles()

    mask[300:310, 600:640] = 255
    item.WARM_DELAY_MS = 400
    item.set_mask(mask, changed=(600, 300, 40, 10))
    app.processEvents()
    dropped = _all_tiles() - set(item.vector_tiles())

    assert dropped == {(2, 1)}  # x 600..640, y 300..310: tile (2, 1) only
    assert _wait(app, lambda: set(item.vector_tiles()) == _all_tiles())
    for key, revision in item.vector_tiles().items():
        if key not in dropped:
            assert revision == kept[key]  # untouched tiles were not extracted again


def test_items_no_view_shows_and_library_items_never_warm(app, views):
    alone = SkeletonLayerItem()
    alone.WARM_DELAY_MS = 0
    scene = QGraphicsScene()
    scene.addItem(alone)
    alone.set_mask(_busy())
    library = SkeletonLayerItem(halo=False, vectors=False)
    library.WARM_DELAY_MS = 0
    _shown(views, library)
    library.set_mask(_busy())

    _wait(app, lambda: False, seconds=0.15)

    assert alone.vector_tiles() == {} and library.vector_tiles() == {}

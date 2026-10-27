"""A partial repaint of the vector skeleton draws what the full frame drew (X2-06).

From 200 % the skeleton is a 2 px line stroked with ``drawLines`` at the item's opacity
(0.85 on the skeleton layer, 0.425 as a reference layer). Qt strokes ``drawLines`` 16 lines
at a time, and the round caps that meet at a joint blend once inside a batch and twice across
a batch boundary, so the lines that share a batch decide how bright a joint is. A partial
repaint of a raster viewport (the brush ring's hover, a stroke, a marker update) stroked only
the lines near its rect: other batches, so its joints came out brighter or darker than the
full frame's and the ring left a trail. The 2 px tier now strokes the full frame's line list
on every repaint, so a part repainted is the full frame's pixels. Display only.
"""

from __future__ import annotations

import os

os.environ["ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"] = "0"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView  # noqa: E402

from app.gui.canvas_layers.skeleton import SkeletonLayerItem  # noqa: E402
from app.gui.skeleton_graph_model import _skeletonize_uint8  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

W, H = 200, 150
SIDE = 24  # device px of each region repainted: about a brush ring's hover update


@pytest.fixture
def shown_views():
    """Views a test shows, closed and deleted afterwards even when the test fails."""
    views: list[QGraphicsView] = []
    yield views
    delete_widgets(views)


def _bent() -> np.ndarray:
    """Joints at every step: a sharp V, an arc and a few crossing strokes."""
    mask = np.zeros((H, W), np.uint8)
    cv2.line(mask, (5, 5), (25, 60), 255, 1)
    cv2.line(mask, (25, 60), (30, 5), 255, 1)
    cv2.ellipse(mask, (90, 60), (30, 40), 0, 0, 270, 255, 1)
    cv2.line(mask, (130, 10), (190, 140), 255, 1)
    cv2.line(mask, (190, 20), (120, 130), 255, 1)
    cv2.line(mask, (10, 140), (110, 110), 255, 1)
    return _skeletonize_uint8(mask)


def _view(views: list, item: SkeletonLayerItem, zoom: float) -> QGraphicsView:
    """A shown raster-viewport view (partial updates) of the whole skeleton at ``zoom``."""
    view = QGraphicsView()
    views.append(view)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setFrameShape(QGraphicsView.Shape.NoFrame)
    view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    view.setBackgroundBrush(QColor(255, 255, 255))
    scene = QGraphicsScene(view)
    view.setScene(scene)
    scene.addItem(item)
    view.resize(int(W * zoom), int(H * zoom))
    view.scale(zoom, zoom)
    view.centerOn(W / 2, H / 2)
    view.show()
    for _ in range(5):
        QApplication.processEvents()
    return view


def _pixels(image: QImage) -> np.ndarray:
    data = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    return data.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4].copy()


def _regions(view: QGraphicsView, mask: np.ndarray, count: int) -> list[QRect]:
    """``count`` regions of ``SIDE`` device px centred on skeleton pixels, spread along it."""
    ys, xs = np.nonzero(mask)
    step = max(1, len(xs) // count)
    bounds = view.viewport().rect()
    out = []
    for x, y in zip(xs[::step][:count], ys[::step][:count], strict=False):
        centre = view.mapFromScene(float(x) + 0.5, float(y) + 0.5)
        rect = QRect(centre.x() - SIDE // 2, centre.y() - SIDE // 2, SIDE, SIDE)
        out.append(rect.intersected(bounds))
    return out


@pytest.mark.parametrize("opacity", [0.85, 0.425])  # the skeleton layer, as a reference
@pytest.mark.parametrize("zoom", [2.0, 3.0])
def test_a_partial_repaint_draws_the_full_frames_joints(app, shown_views, opacity, zoom):
    mask = _bent()
    item = SkeletonLayerItem()
    item.EXTRACT_BUDGET_MS = float("inf")  # every tile in the first paint
    item.setOpacity(opacity)
    item.set_mask(mask)
    view = _view(shown_views, item, zoom)
    viewport = view.viewport()
    full = QImage(viewport.size(), QImage.Format.Format_ARGB32_Premultiplied)
    full.fill(0)
    viewport.render(full)
    assert item.mode_painted() == "vector"  # the 2 px line over its halo
    expected = _pixels(full)

    differing = []
    for rect in _regions(view, mask, 24):
        part = full.copy()
        viewport.render(part, rect.topLeft(), QRegion(rect))  # repaints only ``rect``
        assert item.mode_painted() == "vector"
        if not np.array_equal(_pixels(part), expected):
            differing.append((rect.x(), rect.y()))

    assert differing == []


def test_the_whole_viewport_rendered_again_is_unchanged(app, shown_views):
    """A full repaint is deterministic (the reference the regions are compared with)."""
    item = SkeletonLayerItem()
    item.EXTRACT_BUDGET_MS = float("inf")
    item.setOpacity(0.85)
    item.set_mask(_bent())
    view = _view(shown_views, item, 2.0)
    viewport = view.viewport()
    first = QImage(viewport.size(), QImage.Format.Format_ARGB32_Premultiplied)
    first.fill(0)
    viewport.render(first)
    again = first.copy()
    viewport.render(again, QPoint(0, 0), QRegion(viewport.rect()))
    assert np.array_equal(_pixels(again), _pixels(first))

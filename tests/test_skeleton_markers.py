"""Windowed endpoints and the dirty-rect endpoint markers (KNOWN_ISSUES #6, S2).

- ``SkeletonCorrectionModel.endpoints()`` recomputes only around the pixels that changed:
  after every step of seeded random edit sequences (the tools' calls, undo / redo, writes
  in place, a replaced mask) it must equal a whole-frame recomputation, set and order.
- The markers item (``DotsItem``) repaints only the markers that appeared or went: after
  such a partial repaint of its device cache the screen must equal a fresh item's.
- Markers, handles and previews sit on pixel centres, where the line is drawn.
- QPixmapCache must hold the markers' device cache on a large viewport (its 10 MiB default
  is smaller than one 2600 x 1500 pixmap, and a pixmap that does not fit is never kept).
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmapCache, QTransform  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
)

from app.gui.skeleton_graph_model import (  # noqa: E402
    SkeletonCorrectionModel,
    _compute_degree_map,
    _ensure_uint8_binary,
)
from app.gui.skeleton_overlay import DotsItem, SkeletonDecorations  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- windowed endpoints
def _full_endpoints(mask: np.ndarray) -> list[tuple[int, int]]:
    """The endpoints as computed before S2: the whole frame, row-major."""
    skel = _ensure_uint8_binary(mask) > 0
    ys, xs = np.nonzero(skel & (_compute_degree_map(skel) == 1))
    return list(zip(xs.tolist(), ys.tolist(), strict=True))


def _walk(rng, w, h, n, step):
    x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
    points = [(x, y)]
    for _ in range(n - 1):
        x = int(np.clip(x + rng.integers(-step, step + 1), 0, w - 1))
        y = int(np.clip(y + rng.integers(-step, step + 1), 0, h - 1))
        points.append((x, y))
    return points


def _random_step(model: SkeletonCorrectionModel, rng) -> None:
    h, w = model.mask.shape
    roll = rng.random()
    if roll < 0.45:  # a tool action
        model.push_undo()
        if rng.random() < 0.15:  # the legacy protocol: a write in place right after
            x, y = int(rng.integers(0, w - 6)), int(rng.integers(0, h - 6))
            model.mask[y : y + 3, x : x + 6] = 255
        kind = int(rng.integers(0, 4))
        if kind == 0:
            model.draw_polyline(_walk(rng, w, h, int(rng.integers(2, 7)), 40), thickness=3)
        elif kind == 1:
            pts = _walk(rng, w, h, 4, 25)
            for p0, p1 in zip(pts, pts[1:], strict=False):
                model.erase_segment(p0, p1, int(rng.integers(1, 9)))
        elif kind == 2:
            model.erase_polyline(_walk(rng, w, h, 3, 30), 9)
        else:
            model.draw_polyline(_walk(rng, w, h, 2, 60), thickness=1)
        if rng.random() < 0.8:
            model.skeletonize()
    elif roll < 0.65:
        model.undo()
    elif roll < 0.80:
        model.redo()
    elif roll < 0.86:
        model.erase_circle(_walk(rng, w, h, 1, 0)[0], int(rng.integers(1, 6)))  # no push
    elif roll < 0.90:
        model.push_undo()
        model.clear()
    elif roll < 0.93:
        model.mask = np.ascontiguousarray(model.mask.copy())  # the array replaced
    else:
        model.skeletonize()


@pytest.mark.parametrize(("seed", "size"), [(1, (160, 120)), (2, (320, 240)), (3, (641, 479))])
def test_windowed_endpoints_equal_a_whole_frame_recompute(seed, size):
    rng = np.random.default_rng(seed)
    model = SkeletonCorrectionModel()
    w, h = size
    start = np.zeros((h, w), np.uint8)
    for _ in range(30):
        pts = np.array(_walk(rng, w, h, 6, 30), np.int32).reshape(-1, 1, 2)
        cv2.polylines(start, [pts], False, 255, 3)
    model.load_from_raster(start, target_size=size)
    for step in range(250):
        _random_step(model, rng)
        assert model.endpoints() == _full_endpoints(model.mask), (seed, step)
        xy = model.endpoint_array()
        assert xy.shape == (len(model.endpoints()), 2)
        assert [tuple(p) for p in xy.tolist()] == model.endpoints()


def test_endpoints_of_a_one_pixel_and_an_empty_image():
    model = SkeletonCorrectionModel()
    model.set_empty((1, 1))
    assert model.endpoints() == []
    model.push_undo()
    model.mask[0, 0] = 255
    assert model.endpoints() == []  # an isolated pixel has no neighbour: not a tip
    model.set_empty((2, 1))
    model.push_undo()
    model.draw_polyline([(0, 0), (1, 0)], thickness=1)
    assert model.endpoints() == [(0, 0), (1, 0)]
    assert model.undo() and model.endpoints() == []


def test_a_small_edit_recomputes_only_its_window(monkeypatch):
    import app.gui.skeleton_graph_model as sgm

    model = SkeletonCorrectionModel()
    model.set_empty((2048, 1536))
    model.push_undo()
    for y in range(20, 1500, 40):
        model.draw_polyline([(10, y), (2000, y + 10)], thickness=1)
    model.skeletonize()  # a known fixed point: the next action thins a window
    model.endpoints()
    boxes = []
    real = sgm._tip_indices

    def spy(mask, box=None):
        boxes.append(box)
        return real(mask, box)

    monkeypatch.setattr(sgm, "_tip_indices", spy)
    model.push_undo()
    model.draw_polyline([(1000, 700), (1040, 720)], thickness=3)
    model.skeletonize()
    assert model.endpoints() == _full_endpoints(model.mask)
    assert len(boxes) == 1 and boxes[0] is not None
    x0, y0, x1, y1 = boxes[0]
    assert (x1 - x0) * (y1 - y0) < 100 * 100  # the edit's neighbourhood, not the frame


# ------------------------------------------------------------------- markers item
def _view(scene: QGraphicsScene, zoom: float, size=(400, 300)) -> QGraphicsView:
    view = QGraphicsView(scene)
    view.setFrameShape(QFrame.Shape.NoFrame)  # the scene fits exactly: nothing scrolls
    view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setRenderHints(QPainter.RenderHint.Antialiasing)
    view.setSceneRect(QRectF(0, 0, size[0] / zoom, size[1] / zoom))
    view.setTransform(QTransform.fromScale(zoom, zoom))
    view.resize(*size)
    view.show()
    return view


def _screen(view: QGraphicsView) -> QImage:
    view.viewport().repaint()
    return view.viewport().grab().toImage()


def _markers(scene: QGraphicsScene) -> DotsItem:
    deco = SkeletonDecorations(scene)
    return deco, deco.endpoints_item


def _close(*views: QGraphicsView) -> None:
    delete_widgets(list(views))


@pytest.mark.parametrize("zoom", [0.5, 1.0, 3.0])
def test_a_partial_marker_repaint_equals_a_fresh_render(app, zoom):
    rng = np.random.default_rng(7)
    extent = (400 / zoom, 300 / zoom)
    first = np.column_stack(
        (rng.integers(0, int(extent[0]), 300), rng.integers(0, int(extent[1]), 300))
    )
    first = np.unique(first, axis=0)
    second = np.concatenate((first[3:], [[int(extent[0] // 2), int(extent[1] // 3)]]))

    scene = QGraphicsScene()
    deco, markers = _markers(scene)
    assert markers.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    deco.set_endpoints(first)
    view = _view(scene, zoom)
    _screen(view)  # the device cache holds every marker now
    updates = []
    real_update = markers.update
    markers.update = lambda *rect: (updates.append(rect), real_update(*rect))[1]

    deco.set_endpoints(second)

    assert updates and all(rect for rect in updates)  # only rects, never the whole item
    edited = _screen(view)
    fresh_scene = QGraphicsScene()
    fresh_deco, _ = _markers(fresh_scene)
    fresh_deco.set_endpoints(second)
    fresh = _view(fresh_scene, zoom)
    assert edited == _screen(fresh)
    _close(view, fresh)


def test_unchanged_markers_repaint_nothing(app):
    scene = QGraphicsScene()
    deco, markers = _markers(scene)
    deco.set_endpoints([(10, 10), (40, 40)])
    updates = []
    markers.update = lambda *rect: updates.append(rect)
    deco.set_endpoints(np.array([[10, 10], [40, 40]]))
    assert updates == []


def test_markers_outside_the_bounds_grow_them(app):
    scene = QGraphicsScene()
    deco, markers = _markers(scene)
    deco.set_endpoints([(10, 10)])
    deco.set_endpoints([(10, 10), (300, 200)])
    assert markers.boundingRect().contains(markers.path().boundingRect())
    deco.set_endpoints([])
    assert markers.path().isEmpty() and markers.boundingRect().isEmpty()


def test_markers_handles_and_previews_sit_on_pixel_centres(app):
    scene = QGraphicsScene()
    deco = SkeletonDecorations(scene)
    deco.set_endpoints([(10, 20)])
    deco.highlight_endpoint(QPoint(10, 20))
    deco.show_polyline([QPoint(10, 20), QPoint(30, 20)], QPoint(30, 40), smooth=False)
    deco.show_connect(QPoint(10, 20), QPoint(50, 60))

    for item in (deco.endpoints_item, deco.highlight_item):
        assert item.path().boundingRect().center() == QPointF(10.5, 20.5)
    assert deco.handles_item.path().boundingRect().center() == QPointF(20.5, 20.5)
    path = deco.polyline_item.path()
    assert [path.elementAt(i) for i in range(path.elementCount())][0].x == 10.5
    assert path.currentPosition() == QPointF(30.5, 40.5)  # the rubber band ends on the cursor
    line = deco.connect_item.line()
    assert (line.x1(), line.y1(), line.x2(), line.y2()) == (10.5, 20.5, 50.5, 60.5)


def test_a_big_viewport_keeps_the_marker_cache(app):
    scene = QGraphicsScene()
    deco, _ = _markers(scene)
    deco.set_endpoints([(x, y) for x in range(5, 2600, 97) for y in range(5, 1500, 89)])
    before = QPixmapCache.cacheLimit()
    view = _view(scene, 1.0, size=(2600, 1500))
    _screen(view)
    try:
        assert QPixmapCache.cacheLimit() * 1024 >= 2600 * 1500 * 4
    finally:
        QPixmapCache.setCacheLimit(before)
        _close(view)


def _covered(item: DotsItem, scale: float, exposed: QRectF | None = None) -> int:
    image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    option = QStyleOptionGraphicsItem()
    if exposed is not None:
        option.exposedRect = exposed
    item.paint(painter, option, None)
    painter.end()
    alpha = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    return int((alpha.reshape(200, 200, 4)[:, :, 3] > 0).sum())


def test_a_paint_draws_only_the_dots_it_exposes(app):
    item = DotsItem(4)
    item.setPen(QPen(QColor(tokens.BG_0), 1))
    item.set_points([(20.5, 20.5), (150.5, 150.5)])
    both = _covered(item, 1.0)
    one = _covered(item, 1.0, QRectF(0, 0, 60, 60))
    assert 0 < one < both and abs(both - 2 * one) <= 4


class _CountingPainter(QPainter):
    """Counts the dot sprites a paint blits."""

    def __init__(self, device):
        super().__init__(device)
        self.blits = 0

    def drawPixmap(self, *args):  # noqa: N802 - Qt name
        self.blits += 1
        super().drawPixmap(*args)


def test_a_paint_blits_only_the_dots_on_the_device(app):
    item = DotsItem(4)
    item.set_points([(20.5, 20.5), (150.5, 150.5), (900.5, 40.5), (40.5, -300.5)])
    image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = _CountingPainter(image)
    item.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    assert painter.blits == 2  # the two dots off the image are skipped

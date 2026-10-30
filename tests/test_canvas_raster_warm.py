"""The editable raster layer warms what the next zoom step draws when the view is idle
(app/gui/canvas_layers/raster.py).

After a paint, ``WARM_DELAY_MS`` of stillness later, the mask / GT canvas makes ahead, in
idle slices of ``WARM_BUDGET_MS``, the tiles a zoom step would draw (``WARM_STEP`` in over
the view, out over the view grown by half its size on every side): its exact-pixel tiles
from 100 % up, its coverage tiles of the level below. From a minified zoom it also makes the
exact tiles that actual pixels about the view's centre shows (Ctrl+1, a click on the zoom
readout, the zoom presets). Only while a view shows the item; an edit drops the tiles it
touches at every level, a new canvas drops the warm-up; coverage tiles are kept for the
level drawn and the levels planned only (bounded memory). On an OpenGL engine the next
frames draw the tiles made ahead once, invisibly (their texture upload); on the raster
engine nothing is drawn and nothing runs once the warm-up is done.
"""

import time

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
)

from app.gui import canvas_gl
from app.gui.canvas_layers.raster import RasterLayerItem
from app.gui.canvas_view import CanvasViewMixin
from tests.qt_teardown import flush_deleted
from tests.render_metrics import qimage_to_rgb

W, H = 1400, 1000
VIEW = (400, 300)
CENTRE = (W / 2, H / 2)


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self):
        super().__init__(None)
        self._init_canvas()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(Qt.GlobalColor.black)


class _Canvas:
    """Holds the canvas QImage the way PaintCanvas does (the item reads it at paint time)."""

    def __init__(self, image: QImage | None):
        self.image = image


def _mask_image(w: int = W, h: int = H, seed: int = 7) -> QImage:
    plane = np.zeros((h, w), np.uint8)
    rng = np.random.default_rng(seed)
    for _ in range(40):
        pts = rng.integers(0, (w, h), size=(5, 2)).astype(np.int32)
        cv2.polylines(plane, [pts], False, 1, int(rng.integers(1, 12)))
    bgra = np.zeros((h, w, 4), np.uint8)
    bgra[plane > 0] = (160, 214, 95, 255)
    straight = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    return straight.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _build(app, monkeypatch, image: QImage, *, tile: int = 64, cover_tile: int = 128, skip=None):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    canvas = _Canvas(image)
    item = RasterLayerItem(lambda: canvas.image, skip=skip)
    item.TILE = tile
    item.COVER_TILE = cover_tile
    view = _View()
    scene = QGraphicsScene(view)
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, image.width(), image.height()))
    view.resize(*VIEW)
    return view, item, canvas


@pytest.fixture
def page(app, monkeypatch):
    """A 400 x 300 raster view over a 1400 x 1000 mask canvas (64 px exact tiles, 128 px
    coverage tiles), not painted yet."""
    view, item, canvas = _build(app, monkeypatch, _mask_image())
    yield view, item, canvas
    view.scene().removeItem(item)
    view.close()
    view.deleteLater()
    del view
    flush_deleted()


def show(view, zoom: float, centre=CENTRE) -> None:
    """``zoom`` about ``centre``, then one synchronous frame (the first paint sees it)."""
    view.set_zoom(zoom)
    view.centerOn(QPointF(*centre))
    if not view.isVisible():
        view.show()
        QApplication.processEvents()
        view.centerOn(QPointF(*centre))
    view.viewport().repaint()


def warm_all(item: RasterLayerItem) -> None:
    for _ in range(10000):
        if not item.warm_tiles(budget_ms=1000.0):
            return
    raise AssertionError("the warm-up never ended")


def levels(tiles: dict) -> set[int]:
    return {key[0] for key in tiles}


def test_a_step_in_to_exact_pixels_is_warmed_ahead(page):
    view, item, _canvas = page
    show(view, 0.8)  # coverage level 0; a step in (x 1.5) shows exact pixels
    assert item.built_tiles() == {}
    warm_all(item)
    before = item.built_tiles()
    assert before
    show(view, 1.2)  # the step in about the centre
    assert item.built_tiles() == before  # nothing made in the step's frame


def test_a_step_out_to_the_coverage_is_warmed_ahead(page):
    view, item, _canvas = page
    show(view, 1.0)  # exact pixels; a step out (/ 1.5) shows coverage level 0
    assert item.coverage_tiles() == {}
    warm_all(item)
    before = item.coverage_tiles()
    assert levels(before) == {0}
    show(view, 0.7)  # inside the view grown by half its size
    assert item.coverage_tiles() == before


def test_a_step_into_a_finer_coverage_level_is_warmed_ahead(page):
    view, item, _canvas = page
    show(view, 0.4)  # coverage level 1; a step in (0.6) draws level 0
    assert levels(item.coverage_tiles()) == {1}
    warm_all(item)
    warmed = {k: v for k, v in item.coverage_tiles().items() if k[0] == 0}
    assert warmed
    show(view, 0.6)
    assert {k: v for k, v in item.coverage_tiles().items() if k[0] == 0} == warmed


def test_actual_pixels_about_the_centre_is_warmed_ahead(page):
    view, item, _canvas = page
    show(view, 0.3)  # a fit-like zoom: no step reaches 100 %
    warm_all(item)
    before = item.built_tiles()
    assert before
    view.set_zoom(1.0)  # Ctrl+1 / the zoom readout: 100 % about the view's centre
    view.viewport().repaint()
    assert item.built_tiles() == before
    view.set_zoom(2.0)  # the 200 % preset shows part of it
    view.viewport().repaint()
    assert item.built_tiles() == before


def test_the_warm_up_runs_by_itself_once_the_view_is_idle(page):
    view, item, _canvas = page
    show(view, 0.8)
    deadline = time.perf_counter() + 3.0
    while not item.built_tiles() and time.perf_counter() < deadline:
        QTest.qWait(20)
    assert item.built_tiles()


def test_nothing_is_warmed_while_the_view_is_hidden(page):
    view, item, _canvas = page
    show(view, 0.8)
    view.hide()
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 250)
    assert item.built_tiles() == {}
    assert item.warm_tiles(budget_ms=1000.0) is False  # the plan was dropped


def test_nothing_is_warmed_while_the_layer_is_hidden(page):
    view, item, _canvas = page
    show(view, 0.8)
    item.setVisible(False)
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 250)
    assert item.built_tiles() == {}


class _PaintCounter(QObject):
    def __init__(self):
        super().__init__()
        self.paints = 0

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint:
            self.paints += 1
        return False


def test_the_warm_up_ends_and_asks_for_no_frame_on_a_raster_viewport(page):
    view, item, _canvas = page
    show(view, 0.8)
    deadline = time.perf_counter() + 3.0
    while not item.built_tiles() and time.perf_counter() < deadline:
        QTest.qWait(20)
    QTest.qWait(100)  # the last slices
    counter = _PaintCounter()
    view.viewport().installEventFilter(counter)
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 300)
    view.viewport().removeEventFilter(counter)
    assert counter.paints == 0  # no upload frames without OpenGL, no loop
    assert item.pending_uploads() > 0  # made ahead, left for a GL engine to upload


def test_each_idle_slice_keeps_to_its_budget(app, monkeypatch):
    view, item, _canvas = _build(app, monkeypatch, _mask_image(3000, 2400), tile=16)
    try:
        show(view, 0.3)  # actual pixels about the centre: ~520 tiles of 16 px
        t0 = time.perf_counter()
        assert item.warm_tiles(budget_ms=0.5) is True  # more remains
        assert (time.perf_counter() - t0) * 1000 < 50  # one slice, not the whole warm-up
        made = len(item.built_tiles()) + len(item.coverage_tiles())
        assert item.warm_tiles(budget_ms=0.5) is True
        assert len(item.built_tiles()) + len(item.coverage_tiles()) > made  # it goes on
    finally:
        view.scene().removeItem(item)
        view.close()
        view.deleteLater()
        flush_deleted()


def _grab(view) -> np.ndarray:
    view.viewport().repaint()
    return qimage_to_rgb(view.viewport().grab().toImage()).astype(int)


def _fresh(app, monkeypatch, canvas: _Canvas, zoom: float) -> np.ndarray:
    view, item, _ = _build(app, monkeypatch, canvas.image)
    try:
        show(view, zoom)
        return _grab(view)
    finally:
        view.scene().removeItem(item)
        view.close()
        view.deleteLater()


def _paint_canvas(canvas: _Canvas, rect: QRect) -> None:
    painter = QPainter(canvas.image)
    painter.fillRect(rect, QColor(240, 168, 104))
    painter.end()


def test_an_edit_drops_the_warmed_coverage_tiles_it_touches(app, monkeypatch, page):
    view, item, canvas = page
    show(view, 1.0)
    warm_all(item)  # coverage level 0 made ahead
    rect = QRect(560, 400, 90, 70)  # in view, inside the warmed area
    _paint_canvas(canvas, rect)
    item.canvas_changed(rect)
    show(view, 0.7)
    assert np.abs(_grab(view) - _fresh(app, monkeypatch, canvas, 0.7)).max() <= 1


def test_an_edit_drops_the_warmed_exact_tiles_it_touches(app, monkeypatch, page):
    view, item, canvas = page
    show(view, 0.8)
    warm_all(item)  # exact tiles made ahead
    rect = QRect(650, 470, 40, 30)
    _paint_canvas(canvas, rect)
    item.canvas_changed(rect)
    show(view, 1.2)
    assert np.abs(_grab(view) - _fresh(app, monkeypatch, canvas, 1.2)).max() == 0


def test_the_coverage_keeps_only_the_levels_drawn_and_planned(page):
    view, item, _canvas = page
    for zoom in (0.9, 0.6, 0.4, 0.25, 0.15, 0.1):
        show(view, zoom)
        warm_all(item)
    show(view, 0.9)  # level 0 drawn; a step out (0.6) plans level 0 too
    warm_all(item)  # the other levels are let go when idle (not in the zoom's frame)
    assert levels(item.coverage_tiles()) == {0}


def test_a_new_canvas_drops_the_warm_up(page):
    view, item, canvas = page
    show(view, 0.8)
    canvas.image = _mask_image(seed=8)
    item.canvas_replaced()
    assert item.warm_tiles(budget_ms=1000.0) is False
    assert item.built_tiles() == {} and item.coverage_tiles() == {}


def test_a_drawn_tile_is_no_longer_waiting_for_its_upload(page):
    view, item, _canvas = page
    show(view, 0.8)
    warm_all(item)  # exact tiles for the step in, coverage tiles for the step out
    waiting = item.pending_uploads()
    assert waiting >= len(item.built_tiles()) > 0  # every tile made ahead waits
    show(view, 1.2)  # draws some of them for real
    assert 0 < item.pending_uploads() < waiting


@pytest.mark.parametrize("size", [(1, 1), (33, 7), (640, 480), (1001, 777), (2048, 1536)])
def test_coverage_levels_are_sized_and_counted_without_building_them(size):
    """The warm-up plans coverage levels by arithmetic: the pyramid it would build agrees."""
    from app.gui.canvas_layers.raster import _Coverage, _coverage_count
    from app.imaging.pyramid import ImagePyramid, area_half

    w, h = size
    item = RasterLayerItem(lambda: QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied))
    half = area_half(np.zeros((h, w, 4), np.uint8))
    pyramid = ImagePyramid(half, min_side=_Coverage.MIN_SIDE)
    assert _coverage_count(w, h, _Coverage.MIN_SIDE) == 1 + pyramid.count
    assert item._level_size(0) == (w, h)
    for level in range(1, 1 + pyramid.count):
        assert item._level_size(level) == pyramid.level_size(level - 1)


def _as_gl(monkeypatch) -> None:
    """Paint engines count as OpenGL (the offscreen raster engine stands in for one)."""
    from app.gui.canvas_layers import photo, raster

    monkeypatch.setattr(raster, "_is_gl", lambda painter: True)
    monkeypatch.setattr(photo, "_is_gl", lambda painter: True)


def test_a_gl_frame_uploads_a_few_tiles_of_one_kind(page, monkeypatch):
    view, item, _canvas = page
    show(view, 0.8)  # a step in shows exact tiles: they go first
    warm_all(item)
    view.viewport().repaint()  # tiles in view were drawn for real: not waiting any more
    _as_gl(monkeypatch)
    seen = []
    for _ in range(200):
        exact, cover = len(item._undrawn), item._cover.pending_uploads()
        if not exact + cover:
            break
        view.viewport().repaint()
        seen.append((exact - len(item._undrawn), cover - item._cover.pending_uploads()))
    assert item.pending_uploads() == 0
    assert all((e == 0) != (c == 0) for e, c in seen)  # one kind per frame
    assert max(e for e, _ in seen) <= RasterLayerItem.UPLOADS_PER_FRAME
    assert max(c for _, c in seen) <= RasterLayerItem.COVER_UPLOADS_PER_FRAME
    kinds = ["exact" if e else "cover" for e, _ in seen]
    assert kinds == sorted(kinds, key=lambda k: k != "exact")  # exact tiles first


def test_on_gl_idle_frames_upload_what_was_made_ahead_then_stop(page, monkeypatch):
    view, item, _canvas = page
    _as_gl(monkeypatch)
    show(view, 0.4)  # coverage: the step in's coverage level goes first
    deadline = time.perf_counter() + 5.0
    while (not item.built_tiles() or item.pending_uploads()) and time.perf_counter() < deadline:
        QTest.qWait(20)
    assert item.built_tiles() and item.pending_uploads() == 0  # all uploaded by idle frames
    counter = _PaintCounter()
    view.viewport().installEventFilter(counter)
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 300)
    view.viewport().removeEventFilter(counter)
    assert counter.paints <= 1  # then nothing but the last frame's own re-check


def test_a_deleted_item_ignores_its_pending_warm_up(app):
    image = _mask_image(300, 200)
    scene = QGraphicsScene()
    item = RasterLayerItem(lambda: image)
    scene.addItem(item)
    target = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(target)
    painter.scale(0.8, 0.8)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = item.boundingRect()
    item.paint(painter, option, None)  # plans a warm-up
    painter.end()
    scene.clear()  # deletes the C++ item while its warm-up timer is pending
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 100)  # the timer fires: no error


@pytest.fixture
def skipped_page(app, monkeypatch):
    """``page`` with an owner's ``skip`` (``state["skip"]``, True at first)."""
    state = {"skip": True}
    view, item, canvas = _build(app, monkeypatch, _mask_image(), skip=lambda: state["skip"])
    yield view, item, state
    view.scene().removeItem(item)
    view.close()
    view.deleteLater()
    del view
    flush_deleted()


def test_a_skipped_canvas_draws_plans_and_makes_nothing(skipped_page):
    """While ``skip()`` is True (a fully transparent reference layer) the item keeps its size
    but makes no tile, plans no warm-up and draws nothing."""
    view, item, _state = skipped_page
    show(view, 1.0)
    assert not _grab(view).any()  # the black background only (the mask is not drawn)
    show(view, 0.4)
    QTest.qWait(RasterLayerItem.WARM_DELAY_MS + 200)  # an idle warm-up would run now

    assert item.boundingRect() == QRectF(0, 0, W, H)
    assert item.built_tiles() == {} and item.coverage_tiles() == {}
    assert item.warm_tiles(budget_ms=1000.0) is False and item.built_tiles() == {}
    assert item.pending_uploads() == 0


def test_a_canvas_no_longer_skipped_is_drawn_and_warmed_again(skipped_page):
    view, item, state = skipped_page
    show(view, 1.0)
    assert item.built_tiles() == {}

    state["skip"] = False
    item.update()
    show(view, 1.0)

    assert item.built_tiles() and _grab(view).any()  # the tiles in view, drawn
    warm_all(item)
    assert item.coverage_tiles()  # the step out's level, made ahead

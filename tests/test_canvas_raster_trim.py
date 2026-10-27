"""RasterLayerItem draws only the tiles that can change a pixel (app/gui/canvas_layers/raster.py).

At device scale >= 1 a fully transparent exact tile is not drawn, and where the raster engine
scales the tiles a tile is cut after its content (drawing transparent pixels over a frame
changes nothing). Each tile's content box is read from the canvas once and then grown by every
reported edit. The frames must be byte-identical to drawing every tile whole, full and partial,
at every device scale.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"] = "0"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QPointF, QRect, QRectF  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QStyleOptionGraphicsItem  # noqa: E402

from app.gui.canvas_layers import raster  # noqa: E402
from app.gui.canvas_layers.raster import RasterLayerItem  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402

GREY = QColor(96, 96, 96)
W, H = 700, 600  # 3 x 3 tiles of 256 px, the last row and column partial
# (zoom, devicePixelRatio): device scales 1 to 8, fractional ratios and .5 ties included
SCALES = [(1.0, 1.0), (1.37, 1.0), (2.0, 1.0), (8.0, 1.0), (1.0, 1.25), (0.8, 1.5), (1.0, 1.5)]
SCALES += [(1.25, 1.5), (1.0, 1.75), (0.5, 2.0), (1.0, 2.0), (3.7, 2.0)]


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


class _WholeTiles(RasterLayerItem):
    """Every exact tile drawn whole, as before the trim."""

    def _paint_tiles(self, painter: QPainter, image: QImage, exposed: QRectF) -> None:
        t = self.TILE
        area = exposed.toAlignedRect()
        dev = painter.deviceTransform()
        for ty in range(area.top() // t, area.bottom() // t + 1):
            for tx in range(area.left() // t, area.right() // t + 1):
                pixmap = self._tile(image, tx, ty)
                w, h = pixmap.width(), pixmap.height()
                target = raster.snapped_rect(tx * t, ty * t, w, h, dev)
                painter.drawPixmap(target, pixmap, QRectF(0, 0, w, h))


class _Canvas:
    """Holds the canvas the way PaintCanvas does (the items read it at paint time)."""

    def __init__(self, image: QImage) -> None:
        self.image = image


def _image(bgra: np.ndarray) -> QImage:
    """A premultiplied canvas (the PaintCanvas format) of straight B, G, R, A bytes."""
    bgra = np.ascontiguousarray(bgra)
    h, w = bgra.shape[:2]
    straight = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    return straight.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _canvas(kind: str) -> QImage:
    colour = QColor(tokens.MASK)
    paint = (colour.blue(), colour.green(), colour.red(), 255)
    plane = np.zeros((H, W), np.uint8)
    if kind == "sparse":
        cv2.line(plane, (300, 40), (380, 90), 1, 3)  # inside tile (1, 0)
        plane[0, 0] = plane[250, 300] = 1  # tile (0, 0) corner; near tile (1, 0)'s bottom
        plane[511, 255] = 1  # the last column and row of tile (0, 1)
        plane[H - 1, W - 1] = 1  # the canvas corner (tile (2, 2), partial)
    elif kind == "dense":
        rng = np.random.default_rng(20261027)
        for _ in range(16):
            pts = rng.integers(0, (W, H), size=(4, 2)).astype(np.int32)
            cv2.polylines(plane, [pts], False, 1, int(rng.integers(1, 9)))
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[plane > 0] = paint
    if kind == "translucent":  # alpha only, in one corner of tile (2, 1)
        bgra[300:310, 520:530, 3] = 90
    return _image(bgra)


def _scene(item: RasterLayerItem) -> RasterLayerItem:
    scene = QGraphicsScene()
    scene.addItem(item)
    _SCENES.append(scene)
    return item


_SCENES: list[QGraphicsScene] = []


@pytest.fixture(scope="module", autouse=True)
def _drop_scenes():
    yield
    _SCENES.clear()


def _bytes(image: QImage) -> bytes:
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return bytes(ptr)


def _frame(item, zoom, dpr, origin, region: QRegion | None = None, base: QImage | None = None):
    """The item's scene drawn into 360 x 300 device px at ``zoom`` from scene point ``origin``;
    with ``region`` (logical px) only that part is cleared and drawn again over ``base``."""
    w, h = 360, 300
    if base is None:
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(GREY)
    else:
        image = base.copy()
    painter = QPainter(image)
    lw, lh = w / dpr, h / dpr
    if region is not None:
        painter.setClipRegion(region)
        painter.fillRect(QRectF(0, 0, lw, lh), GREY)
    source = QRectF(origin[0], origin[1], lw / zoom, lh / zoom)
    item.scene().render(painter, QRectF(0, 0, lw, lh), source)
    painter.end()
    return image


REGIONS = [
    QRegion(QRect(40, 30, 37, 29)),
    QRegion(QRect(7, 9, 120, 33)).united(QRegion(QRect(150, 120, 33, 41))),
    QRegion(QRect(100, 60, 150, 100)),
]


@pytest.mark.parametrize("kind", ["sparse", "blank", "dense", "translucent"])
@pytest.mark.parametrize(("zoom", "dpr"), SCALES)
def test_trimmed_tiles_render_like_whole_tiles(kind, zoom, dpr):
    image = _canvas(kind)
    trimmed = _scene(RasterLayerItem(lambda: image))
    whole = _scene(_WholeTiles(lambda: image))
    for origin in ((0.0, 0.0), (241.3, 237.6), (380.0, 290.5)):
        full = _frame(trimmed, zoom, dpr, origin)
        assert _bytes(full) == _bytes(_frame(whole, zoom, dpr, origin)), origin
        for region in REGIONS:  # what a hover or a stroke repaints
            part = _frame(trimmed, zoom, dpr, origin, region, full)
            assert _bytes(part) == _bytes(_frame(whole, zoom, dpr, origin, region, full))


@pytest.mark.parametrize("size", [(1, 1), (1, 300), (300, 1), (257, 3), (3, 257), (513, 260)])
def test_tiny_and_thin_canvases_render_like_whole_tiles(size):
    w, h = size
    bgra = np.zeros((h, w, 4), np.uint8)
    bgra[-1, -1] = bgra[0, 0] = (200, 100, 50, 255)  # first and last pixel
    bgra[h // 2, : max(1, w // 3)] = (10, 20, 30, 120)
    image = _image(bgra)
    trimmed = _scene(RasterLayerItem(lambda: image))
    whole = _scene(_WholeTiles(lambda: image))
    for zoom, dpr in ((1.0, 1.0), (1.5, 1.0), (3.0, 2.0), (8.0, 1.25)):
        for origin in ((0.0, 0.0), (max(0.0, w - 20.5), max(0.0, h - 15.5))):
            got = _frame(trimmed, zoom, dpr, origin)
            assert _bytes(got) == _bytes(_frame(whole, zoom, dpr, origin)), (zoom, dpr, origin)


class _CountingPainter(QPainter):
    """Counts the tile pixmaps drawn and keeps the clip rects set while drawing."""

    def __init__(self, device) -> None:
        super().__init__(device)
        self.drawn: list[QRectF] = []
        self.clips: list[QRectF] = []

    def drawPixmap(self, *args) -> None:  # noqa: N802 - Qt name
        self.drawn.append(QRectF(args[0]))
        super().drawPixmap(*args)

    def setClipRect(self, *args) -> None:  # noqa: N802 - Qt name
        self.clips.append(QRectF(args[0]))
        super().setClipRect(*args)


def _paint_counted(item: RasterLayerItem, scale: float) -> _CountingPainter:
    target = QImage(int(W * scale) + 2, int(H * scale) + 2, QImage.Format.Format_ARGB32)
    painter = _CountingPainter(target)
    painter.scale(scale, scale)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = item.boundingRect()
    item.paint(painter, option, None)
    painter.end()
    return painter


def test_a_fully_transparent_tile_is_made_but_not_drawn():
    image = _canvas("blank")
    item = RasterLayerItem(lambda: image)
    painter = _paint_counted(item, 1.0)
    assert painter.drawn == []
    assert len(item.built_tiles()) == 9  # still made: warm-up and uploads stay as they were
    image = _canvas("sparse")
    painter = _paint_counted(RasterLayerItem(lambda: image), 1.0)
    assert len(painter.drawn) == 4  # tiles (0, 0), (1, 0), (0, 1) and (2, 2) hold pixels


def test_a_tile_is_cut_after_its_content_on_the_raster_engine():
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[10:20, 30:40] = 255  # tile (0, 0): content up to x 40, y 20
    image = _image(bgra)
    painter = _paint_counted(RasterLayerItem(lambda: image), 2.0)
    assert len(painter.drawn) == 1
    cut = painter.clips[-1]  # the item's exposed clip comes first
    assert cut.right() == pytest.approx(42.0) and cut.bottom() == pytest.approx(22.0)
    assert cut.left() < 0 and cut.top() < 0  # the first column and row drawn never move


@pytest.mark.parametrize(("scale", "cuts"), [(1.5, 0), (2.0, 0), (3.0, 1)])
def test_a_cut_is_made_only_where_it_leaves_out_enough(scale, cuts):
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[:256, :250] = 255  # tile (0, 0) only, 6 px short: a cut leaves out 4 px x 256 rows
    image = _image(bgra)
    painter = _paint_counted(RasterLayerItem(lambda: image), scale)
    # 1024 item px are 1024 * scale ** 2 device px: a cut pays from raster._CUT_MIN_PX on
    assert len(painter.clips) - 1 == cuts


def test_an_unscaled_paint_reads_no_extent_and_never_cuts():
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[10:20, 30:40] = 255  # tile (0, 0) only
    image = _image(bgra)
    item = RasterLayerItem(lambda: image)
    painter = _paint_counted(item, 1.0)  # 100 %: a plain blit, cheaper than a clip
    assert len(painter.drawn) == 1 and len(painter.clips) == 1  # the exposed clip only
    assert item._boxes[(0, 0)] is raster._FILLED and item._boxes[(1, 0)] is None
    painter = _paint_counted(item, 2.0)  # scaled: the extent is read now, then cut after it
    assert item._boxes[(0, 0)] == (30, 10, 40, 20) and len(painter.clips) == 2


def test_no_cut_on_an_opengl_engine(monkeypatch):
    monkeypatch.setattr(raster, "_is_gl", lambda painter: True)
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[10:20, 30:40] = 255
    image = _image(bgra)
    item = RasterLayerItem(lambda: image)
    item._upload = lambda painter: False  # no GL texture uploads on a raster target
    painter = _paint_counted(item, 2.0)
    assert len(painter.drawn) == 1 and len(painter.clips) == 1  # the exposed clip only
    assert item._boxes[(0, 0)] is raster._FILLED  # only "blank or not" was read


def _stroke(canvas: _Canvas, item, a, b, width, *, erase=False) -> None:
    """Paint (or erase) a line the way PaintCanvas does and report its box."""
    painter = QPainter(canvas.image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    mode = QPainter.CompositionMode
    painter.setCompositionMode(
        mode.CompositionMode_DestinationOut if erase else mode.CompositionMode_Source
    )
    painter.setPen(QPen(QColor(tokens.MASK), width))
    painter.drawLine(QPointF(*a), QPointF(*b))
    painter.end()
    pad = width / 2 + 2
    box = QRectF(QPointF(*a), QPointF(*b)).normalized().adjusted(-pad, -pad, pad, pad)
    item.canvas_changed(box.toAlignedRect().intersected(canvas.image.rect()))


@pytest.mark.parametrize("kind", ["blank", "sparse"])
def test_edits_grow_the_boxes_and_frames_stay_identical(kind, monkeypatch):
    canvas = _Canvas(_canvas(kind))
    item = _scene(RasterLayerItem(lambda: canvas.image))
    whole = _scene(_WholeTiles(lambda: canvas.image))
    _paint_counted(item, 1.5)  # every tile shown, scaled: every box read once, with extent
    worked = []
    real = raster._content_box
    monkeypatch.setattr(raster, "_content_box", lambda *a: worked.append(a[1:3]) or real(*a))
    edits = [((100, 100), (420, 140), 9, False), ((600, 500), (690, 590), 3, False)]
    edits += [((90, 95), (430, 145), 15, True), ((250, 250), (262, 262), 1, False)]
    for a, b, width, erase in edits:
        _stroke(canvas, item, a, b, width, erase=erase)
        whole.canvas_changed(QRect(0, 0, W, H))
        for zoom, dpr in ((1.5, 1.0), (1.0, 2.0), (4.0, 1.0)):
            for origin in ((0.0, 0.0), (200.0, 180.0)):
                got = _frame(item, zoom, dpr, origin)
                assert _bytes(got) == _bytes(_frame(whole, zoom, dpr, origin)), (a, zoom)
    assert worked == []  # strokes grew the boxes on record: no canvas scan per stroke frame


def test_a_replaced_canvas_works_its_boxes_out_again():
    canvas = _Canvas(_canvas("sparse"))
    item = _scene(RasterLayerItem(lambda: canvas.image))
    _frame(item, 1.0, 1.0, (0.0, 0.0))
    assert item._boxes[(1, 1)] is None and item._boxes[(1, 0)] is not None
    canvas.image = _canvas("blank")
    item.canvas_replaced()
    assert item._boxes == {}
    _frame(item, 1.0, 1.0, (0.0, 0.0))
    assert set(item._boxes.values()) == {None}


def test_a_straight_alpha_canvas_is_drawn_whole():
    straight = _canvas("sparse").convertToFormat(QImage.Format.Format_ARGB32)
    item = RasterLayerItem(lambda: straight)
    assert len(_paint_counted(item, 1.0).drawn) == 9  # not premultiplied: no box is read

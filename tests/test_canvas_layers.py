"""Canvas render items (app/gui/canvas_layers, contract C6, spec section 10).

* ``PhotoLayerItem`` - the photo, smooth at every zoom: an area pyramid level chosen from
  the device scale (zoom x devicePixelRatio), drawn bilinear, exposed rect only.
* ``RasterLayerItem`` - an editable ARGB canvas: exact pixels at device scale >= 1 from
  256 px tiles (a stroke re-keys only the tiles it touched), area coverage below 1.

Offscreen raster viewport; thresholds from the zoom-render and contracts scouts.
"""

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import (
    QGraphicsScene,
    QStyleOptionGraphicsItem,
)

from app.gui.canvas_layers import device_scale
from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.canvas_layers.raster import RasterLayerItem
from app.gui.widgets import tokens
from tests.render_metrics import (
    area_reference,
    chirp_alias_std,
    chirp_image,
    equal_neighbour_fraction,
    mean_run,
    psnr,
    qimage_to_rgb,
    remap_reference,
    render_view,
    ring_std,
    sine_image,
    sine_step_ratio,
    stripes_image,
    zone_plate,
)

SEED = 20260929

# Render tests measure the raster engine (offscreen has no GL anyway).
pytestmark = pytest.mark.usefixtures("app", "no_prompts", "gl_off")


def _photo_bgr(w=640, h=480, seed=SEED) -> np.ndarray:
    """A photo-like BGR image: seeded noise blurred to realistic local contrast."""
    noise = np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 1.2)


def _inner(render, w, h, margin=3):
    """Device rect (x0, y0, x1, y1) inside both the viewport and the image, shrunk by margin."""
    height, width = render.rgb.shape[:2]
    x0 = max(0, int(np.ceil(render.dx)) + margin)
    y0 = max(0, int(np.ceil(render.dy)) + margin)
    x1 = min(width, int(np.floor(render.dx + w * render.zoom)) - margin)
    y1 = min(height, int(np.floor(render.dy + h * render.zoom)) - margin)
    return x0, y0, x1, y1


def _paint_item(item, image: QImage, scale: float, exposed: QRectF | None = None) -> None:
    """Paint ``item`` into ``image`` at ``scale``; with ``exposed``, repaint only that part
    the way a view does: clip to it and clear it before the item paints again."""
    painter = QPainter(image)
    painter.scale(scale, scale)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = item.boundingRect() if exposed is None else exposed
    if exposed is not None:
        painter.setClipRect(exposed)  # what the view's paint-event region does
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(exposed, Qt.GlobalColor.transparent)  # the background redraw
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    item.paint(painter, option, None)
    painter.end()


def _blank(w, h, dpr=1.0) -> QImage:
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(0)
    return image


def _premultiplied(image: QImage) -> np.ndarray:
    """``(h, w, 4)`` int premultiplied B, G, R, A: what a translucent layer composites
    with (un-premultiplied RGB would blow a +-1 alpha step up into a large colour step)."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4).astype(int)


# ------------------------------------------------------------------- render metrics
def test_render_metrics_helpers_measure_known_patterns():
    sine = sine_image(64, 8)
    stripes = stripes_image(16, 4)
    assert psnr(sine, sine) == float("inf")
    assert psnr(np.zeros((4, 4)), np.full((4, 4), 255)) == pytest.approx(0.0)
    assert equal_neighbour_fraction(np.zeros((3, 5), np.uint8)) == 1.0
    assert equal_neighbour_fraction(stripes) == 0.0
    assert mean_run(np.repeat(stripes, 4, axis=1)) == pytest.approx(4.0)
    assert sine_step_ratio(sine, sine) == pytest.approx(1.0)
    assert sine_step_ratio(np.repeat(sine, 3, axis=1), sine) == pytest.approx(1.0)  # blocks
    assert stripes.mean() == pytest.approx(127.5)
    assert chirp_alias_std(np.full((8, 200), 127, np.uint8), 0.3) == 0.0
    assert chirp_image().shape == (64, 640)
    assert ring_std(np.full((300, 300), 90, np.uint8), 0.25) == 0.0
    plate = zone_plate(64)
    assert plate.shape == (64, 64) and plate.max() >= 250 and plate.min() <= 5
    src = np.arange(12, dtype=np.uint8).reshape(3, 4)
    assert np.array_equal(remap_reference(src, 1.0, 0, 0, 4, 3, cv2.INTER_NEAREST), src)
    assert remap_reference(src, 2.0, 0, 0, 8, 6, cv2.INTER_NEAREST)[5, 7] == src[2, 3]
    assert area_reference(np.full((8, 8), 200, np.uint8), 0.5, 0, 0, 4, 4)[1, 1] == 200


def test_render_view_grabs_items_at_a_known_mapping():
    image = np.zeros((40, 60, 3), np.uint8)
    image[:, :, 2] = 255  # BGR red
    item = PhotoLayerItem()
    item.set_image(image)
    render = render_view([item], 2.0, (200, 160))
    assert render.rgb.shape == (160, 200, 3)
    assert render.zoom == pytest.approx(2.0)
    cx, cy = int(render.dx + 60), int(render.dy + 40)  # image centre on the device
    assert tuple(render.rgb[cy, cx]) == (255, 0, 0)
    assert tuple(render.rgb[0, 0]) == (0, 0, 0)  # background outside the 120x80 image
    assert item.scene() is None  # handed back, reusable


def test_device_scale_counts_device_pixels_per_item_pixel():
    for dpr, scale, expected in ((1.0, 0.25, 0.25), (2.0, 0.25, 0.5), (2.0, 3.0, 6.0)):
        image = _blank(10, 10, dpr)
        painter = QPainter(image)
        painter.scale(scale, scale)
        assert device_scale(painter) == pytest.approx(expected)
        painter.rotate(30)
        assert device_scale(painter) == pytest.approx(expected)
        painter.end()


# ---------------------------------------------------------------------------- photo
def test_photo_at_800_percent_is_bilinear_not_blocks():
    bgr = _photo_bgr()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    item = PhotoLayerItem()
    item.set_image(bgr)
    render = render_view([item], 8.0, (640, 480))
    x0, y0, x1, y1 = _inner(render, 640, 480)
    shown = render.rgb[y0:y1, x0:x1]
    args = (render.zoom, render.dx, render.dy, 640, 480)
    bilinear = remap_reference(rgb, *args, cv2.INTER_LINEAR)[y0:y1, x0:x1]
    nearest = remap_reference(rgb, *args, cv2.INTER_NEAREST)[y0:y1, x0:x1]
    assert psnr(shown, bilinear) >= 40.0
    assert psnr(shown, bilinear) >= psnr(shown, nearest) + 5.0
    assert equal_neighbour_fraction(shown) < 0.4
    assert mean_run(shown) < 3.0
    assert item.level_painted() == 0


def test_photo_at_100_percent_equals_the_source():
    bgr = _photo_bgr()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    item = PhotoLayerItem()
    item.set_image(bgr)
    render = render_view([item], 1.0, (500, 400), centre=(300.0, 250.0))
    assert render.dx == int(render.dx) and render.dy == int(render.dy)  # integer scroll
    x0, y0, x1, y1 = _inner(render, 640, 480, margin=0)
    expected = remap_reference(rgb, 1.0, render.dx, render.dy, 500, 400, cv2.INTER_NEAREST)
    diff = np.abs(render.rgb[y0:y1, x0:x1].astype(int) - expected[y0:y1, x0:x1].astype(int))
    assert diff.max() <= 1


def test_photo_at_25_percent_is_as_alias_free_as_the_area_ideal():
    plate = zone_plate(1200)
    item = PhotoLayerItem()
    item.set_image(plate)
    render = render_view([item], 0.25, (400, 400))
    origin = (render.dx, render.dy)
    ideal = area_reference(plate, 0.25, render.dx, render.dy, 400, 400)
    assert ring_std(render.rgb, 0.25, origin) <= 1.2 * ring_std(ideal, 0.25, origin)
    assert item.level_painted() == 2


@pytest.mark.parametrize("zoom", [0.25, 0.33, 0.5])
def test_photo_minified_stripes_average_to_mid_grey(zoom):
    item = PhotoLayerItem()
    item.set_image(stripes_image(640, 480))
    render = render_view([item], zoom, (400, 300))
    x0, y0, x1, y1 = _inner(render, 640, 480)
    shown = render.rgb[y0:y1, x0:x1].astype(float)
    assert shown.mean() == pytest.approx(127.5, abs=12)
    assert shown.std() < 25  # no full-contrast moire bands


@pytest.mark.parametrize(("dpr", "level"), [(1.0, 2), (2.0, 1)])
def test_photo_level_follows_the_device_scale_including_hidpi(dpr, level):
    item = PhotoLayerItem()
    item.set_image(_photo_bgr())
    _paint_item(item, _blank(400, 300, dpr), 0.25)
    assert item.level_painted() == level


def test_photo_level_painted_is_minus_one_before_any_paint():
    item = PhotoLayerItem()
    item.set_image(_photo_bgr(64, 48))
    assert item.level_painted() == -1


def test_photo_pixmap_is_level_zero_with_the_source_pixels():
    bgr = _photo_bgr(97, 61)
    item = PhotoLayerItem()
    item.set_image(bgr)
    pixmap = item.pixmap()
    assert (pixmap.width(), pixmap.height()) == (97, 61)
    assert np.array_equal(qimage_to_rgb(pixmap.toImage()), cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    gray = bgr[:, :, 1].copy()
    item.set_image(gray)
    assert np.array_equal(qimage_to_rgb(item.pixmap().toImage())[:, :, 0], gray)
    assert item.image_size() == (97, 61)


def test_photo_keeps_the_array_by_reference_and_never_writes_it():
    bgr = _photo_bgr(64, 48)
    before = bgr.copy()
    bgr.setflags(write=False)
    item = PhotoLayerItem()
    item.set_image(bgr)
    for zoom in (0.2, 0.5, 1.0, 3.0):
        render_view([item], zoom, (120, 100))
    assert np.array_equal(bgr, before)


def test_photo_set_image_none_empties_the_item():
    item = PhotoLayerItem()
    item.set_image(_photo_bgr(64, 48))
    item.set_image(None)
    assert item.image_size() == (0, 0)
    assert item.boundingRect().isEmpty()
    assert item.pixmap().isNull()
    _paint_item(item, _blank(50, 50), 1.0)  # paints nothing, does not fail


def test_photo_new_image_leaves_no_stale_level_at_25_percent():
    item = PhotoLayerItem()
    item.set_image(np.full((480, 640, 3), 255, np.uint8))
    first = render_view([item], 0.25, (300, 240))
    assert first.rgb.max() == 255 and item.level_painted() == 2
    item.set_image(np.zeros((480, 640, 3), np.uint8))
    second = render_view([item], 0.25, (300, 240))
    assert second.rgb.max() <= 1


@pytest.mark.parametrize("zoom", [0.37, 0.6, 3.3])
def test_photo_partial_repaint_matches_a_full_repaint(zoom):
    item = PhotoLayerItem()
    item.set_image(_photo_bgr(320, 240))
    size = (int(320 * zoom) + 2, int(240 * zoom) + 2)
    full = _blank(*size)
    _paint_item(item, full, zoom)
    part = _blank(*size)
    _paint_item(item, part, zoom)
    exposed = QRectF(101.3, 67.7, 41.0, 23.0)  # item px, like a moved brush ring
    _paint_item(item, part, zoom, exposed)
    a = qimage_to_rgb(full).astype(int)
    b = qimage_to_rgb(part).astype(int)
    assert np.abs(a - b).max() <= 1  # no seam at the exposed rect's border


def test_photo_restores_the_painter_hints():
    item = PhotoLayerItem()
    item.set_image(_photo_bgr(64, 48))
    image = _blank(80, 60)
    painter = QPainter(image)
    hints = QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
    painter.setRenderHints(hints)
    painter.scale(0.7, 0.7)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = item.boundingRect()
    item.paint(painter, option, None)
    assert painter.renderHints() == hints
    painter.end()


def _photo(image: np.ndarray, tile: int) -> PhotoLayerItem:
    item = PhotoLayerItem()
    item.TILE = tile  # 64: levels over 256 x 256 px are drawn in 64 px tiles
    item.set_image(image)
    return item


@pytest.mark.parametrize("zoom", [0.23, 0.37, 0.6, 1.0, 2.35, 5.0])
def test_photo_tiles_render_like_one_image(zoom):
    image = _photo_bgr(660, 500)  # levels 0 and 1 tiled, level 2 one pixmap
    size = (int(660 * zoom) + 2, int(500 * zoom) + 2)
    tiled, whole = _blank(*size), _blank(*size)
    _paint_item(_photo(image, 64), tiled, zoom)
    _paint_item(_photo(image, 4096), whole, zoom)
    diff = np.abs(qimage_to_rgb(tiled).astype(int) - qimage_to_rgb(whole).astype(int))
    assert diff.max() <= 1  # no seam, gap or overlap at tile borders


@pytest.mark.parametrize("zoom", [0.37, 3.3])
def test_photo_tiled_partial_repaint_matches_a_full_repaint(zoom):
    item = _photo(_photo_bgr(660, 500), 64)
    size = (int(660 * zoom) + 2, int(500 * zoom) + 2)
    full, part = _blank(*size), _blank(*size)
    _paint_item(item, full, zoom)
    _paint_item(item, part, zoom)
    _paint_item(item, part, zoom, QRectF(50.3, 40.7, 41.0, 49.0))  # across tile borders
    assert np.abs(qimage_to_rgb(full).astype(int) - qimage_to_rgb(part).astype(int)).max() <= 1


def test_photo_converts_only_the_tiles_it_shows():
    item = _photo(_photo_bgr(660, 500), 64)
    assert item.built_tiles() == {}  # set_image converts nothing
    render_view([item], 2.0, (100, 80), centre=(32.0, 32.0))
    assert set(item.built_tiles()) == {(0, 0, 0)}  # (level, tx, ty)
    render_view([item], 0.5, (100, 80), centre=(32.0, 32.0))
    level_one = {key for key in item.built_tiles() if key[0] == 1}
    assert (1, 0, 0) in level_one and len(level_one) < 6 * 4  # of the 6 x 4 level-1 tiles


# --------------------------------------------------------------------------- raster
class _Canvas:
    """Holds the canvas QImage the way PaintCanvas does (the item reads it at paint time)."""

    def __init__(self, image: QImage | None):
        self.image = image


def _bgra_to_image(bgra: np.ndarray) -> QImage:
    """An owning ARGB32_Premultiplied QImage (the PaintCanvas format) of straight BGRA bytes."""
    bgra = np.ascontiguousarray(bgra)
    h, w = bgra.shape[:2]
    straight = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    return straight.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _encoded_canvas(w=700, h=600) -> tuple[np.ndarray, QImage]:
    """Opaque canvas whose colour encodes its own pixel position (R, B: x, y mod 256; G: the
    256 px tile of x and y), so a grab can be decoded back to image coordinates."""
    xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    bgra = np.zeros((h, w, 4), np.uint8)
    bgra[..., 0] = ys % 256
    bgra[..., 1] = (xs // 256) * 64 + (ys // 256) * 16
    bgra[..., 2] = xs % 256
    bgra[..., 3] = 255
    return bgra, _bgra_to_image(bgra)


def _decode(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    x = (g // 64) * 256 + r
    y = ((g % 64) // 16) * 256 + b
    return x, y, g % 16 == 0


def _mask_canvas(w=640, h=480) -> np.ndarray:
    """Straight BGRA mask canvas: root-like strokes in the mask colour, transparent elsewhere."""
    plane = np.zeros((h, w), np.uint8)
    rng = np.random.default_rng(SEED)
    for _ in range(14):
        pts = rng.integers(0, (w, h), size=(4, 2)).astype(np.int32)
        cv2.polylines(plane, [pts], False, 1, int(rng.integers(1, 9)))
    cv2.circle(plane, (w // 3, h // 2), 40, 1, -1)
    colour = QColor(tokens.MASK)
    bgra = np.zeros((h, w, 4), np.uint8)
    bgra[plane > 0] = (colour.blue(), colour.green(), colour.red(), 255)
    return bgra


def _runs(seq: np.ndarray) -> np.ndarray:
    change = np.flatnonzero(np.diff(seq)) + 1
    bounds = np.concatenate([[0], change, [len(seq)]])
    return np.diff(bounds)[1:-1]  # drop the partial first / last runs


def test_raster_pixels_are_exact_at_integer_zooms():
    bgra, image = _encoded_canvas()
    item = RasterLayerItem(lambda: image)
    rgb = np.ascontiguousarray(bgra[..., 2::-1])
    for zoom, centre in ((1.0, (350.0, 300.0)), (4.0, (256.0, 256.0)), (20.0, (511.7, 255.2))):
        render = render_view([item], zoom, (640, 480), centre=centre)
        x0, y0, x1, y1 = _inner(render, 700, 600, margin=0)
        expected = remap_reference(rgb, zoom, render.dx, render.dy, 640, 480, cv2.INTER_NEAREST)
        assert np.array_equal(render.rgb[y0:y1, x0:x1], expected[y0:y1, x0:x1]), zoom


def test_raster_zoomed_pixels_stay_crisp_under_a_smooth_painter():
    """Nearest-neighbour even when the painter asks for smooth pixmap scaling."""
    src = QImage(2, 1, QImage.Format.Format_ARGB32_Premultiplied)
    src.setPixelColor(QPoint(0, 0), QColor(0, 0, 0))
    src.setPixelColor(QPoint(1, 0), QColor(255, 255, 255))
    scene = QGraphicsScene()
    scene.addItem(RasterLayerItem(lambda: src))
    img = QImage(16, 8, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    scene.render(painter, QRectF(0, 0, 16, 8), QRectF(0, 0, 2, 1))
    after = painter.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.end()
    assert img.pixelColor(7, 4) == QColor(0, 0, 0)  # last column of the black pixel
    assert img.pixelColor(8, 4) == QColor(255, 255, 255)
    assert after  # the painter's own hint is restored for later items


@pytest.mark.parametrize("zoom", [1.288, 2.35, 3.7])
@pytest.mark.parametrize("centre", [(256.0, 256.0), (512.3, 300.7)])
def test_raster_fractional_zooms_show_every_pixel_in_order_without_blending(zoom, centre):
    bgra, image = _encoded_canvas()
    item = RasterLayerItem(lambda: image)
    render = render_view([item], zoom, (900, 700), centre=centre)  # Antialiasing view
    x0, y0, x1, y1 = _inner(render, 700, 600, margin=1)
    xs, ys, clean = _decode(render.rgb[y0:y1, x0:x1])
    assert clean.all()  # no blended colour anywhere, tile borders included
    shown = bgra[ys, xs][..., 2::-1]
    assert np.array_equal(shown, render.rgb[y0:y1, x0:x1])  # each pixel is a canvas pixel
    lo, hi = int(np.floor(zoom)), int(np.ceil(zoom))
    for seq in (xs[len(xs) // 2], ys[:, ys.shape[1] // 2], xs[3], ys[:, -4]):
        steps = np.diff(seq)
        assert set(np.unique(steps).tolist()) <= {0, 1}  # monotonic, no image pixel skipped
        runs = _runs(seq)
        assert runs.min() >= lo and runs.max() <= hi
    # the columns / rows either side of every tile border are shown
    for border in (255, 256, 511, 512):
        if xs.min() < border < xs.max():
            assert border in xs[len(xs) // 2]
        if ys.min() < border < ys.max():
            assert border in ys[:, ys.shape[1] // 2]


def test_raster_minified_coverage_matches_the_area_reference():
    bgra = _mask_canvas()
    image = _bgra_to_image(bgra)
    item = RasterLayerItem(lambda: image)
    render = render_view([item], 0.25, (300, 240))
    over_black = np.ascontiguousarray(cv2.cvtColor(bgra[..., :3], cv2.COLOR_BGR2RGB))
    reference = area_reference(over_black, 0.25, render.dx, render.dy, 300, 240)
    x0, y0, x1, y1 = _inner(render, 640, 480, margin=2)
    assert psnr(render.rgb[y0:y1, x0:x1], reference[y0:y1, x0:x1]) >= 60.0


@pytest.mark.parametrize("row", [240, 241, 242, 243])
@pytest.mark.parametrize("zoom", [0.25, 0.33, 0.5, 0.75])
def test_raster_thin_line_stays_visible_when_minified(row, zoom):
    bgra = np.zeros((480, 640, 4), np.uint8)
    colour = QColor(tokens.MASK)
    bgra[row, 20:620] = (colour.blue(), colour.green(), colour.red(), 255)
    image = _bgra_to_image(bgra)
    item = RasterLayerItem(lambda: image)
    shown = render_view([item], zoom, (500, 400), centre=(320.0, 240.0))
    item.setVisible(False)
    hidden = render_view([item], zoom, (500, 400), centre=(320.0, 240.0))
    diff = np.abs(shown.rgb.astype(int) - hidden.rgb.astype(int)).max(axis=2)
    y_dev = int(row * zoom + shown.dy)
    band = diff[max(0, y_dev - 2) : y_dev + 3]
    cols = range(int(np.ceil(40 * zoom + shown.dx)), int(np.floor(600 * zoom + shown.dx)))
    visible = [band[:, c].max() >= 1 for c in cols]
    assert np.mean(visible) >= 0.95


def test_raster_canvas_changed_rekeys_only_the_touched_tiles():
    _bgra, image = _encoded_canvas()
    canvas = _Canvas(image)
    item = RasterLayerItem(lambda: canvas.image)
    render_view([item], 1.0, (900, 700), centre=(350.0, 300.0))  # every tile shown
    before = item.built_tiles()
    assert set(before) == {(tx, ty) for tx in range(3) for ty in range(3)}
    painter = QPainter(canvas.image)
    painter.fillRect(QRect(250, 10, 10, 5), QColor(255, 0, 255))
    painter.end()
    item.canvas_changed(QRect(250, 10, 10, 5))  # crosses the border of tiles (0, 0) / (1, 0)
    after_render = render_view([item], 1.0, (900, 700), centre=(350.0, 300.0))
    after = item.built_tiles()
    changed = {key for key in before if after.get(key) != before[key]}
    assert changed == {(0, 0), (1, 0)}
    x, y = int(252 + after_render.dx), int(12 + after_render.dy)
    assert tuple(after_render.rgb[y, x]) == (255, 0, 255)  # the new pixels are shown


def test_raster_canvas_replaced_rebuilds_every_tile_and_follows_the_size():
    _bgra, image = _encoded_canvas()
    canvas = _Canvas(image)
    item = RasterLayerItem(lambda: canvas.image)
    render_view([item], 1.0, (900, 700), centre=(350.0, 300.0))
    before = item.built_tiles()
    canvas.image = _bgra_to_image(np.full((600, 700, 4), 255, np.uint8))
    item.canvas_replaced()
    render = render_view([item], 1.0, (900, 700), centre=(350.0, 300.0))
    after = item.built_tiles()
    assert set(after) == set(before) and all(after[k] != before[k] for k in before)
    assert render.rgb[int(render.dy + 300), int(render.dx + 350)].tolist() == [255, 255, 255]
    canvas.image = _bgra_to_image(np.zeros((20, 30, 4), np.uint8))
    item.canvas_replaced()
    assert item.boundingRect() == QRectF(0, 0, 30, 20)
    canvas.image = None
    item.canvas_replaced()
    assert item.boundingRect().isEmpty()
    assert item.built_tiles() == {}


def test_raster_tiles_are_only_built_where_shown():
    _bgra, image = _encoded_canvas()
    item = RasterLayerItem(lambda: image)
    render_view([item], 4.0, (400, 300), centre=(100.0, 100.0))  # inside tile (0, 0)
    assert set(item.built_tiles()) == {(0, 0)}


def test_raster_painting_the_source_after_display_does_not_copy_it():
    _bgra, image = _encoded_canvas(64, 48)
    canvas = _Canvas(image)
    item = RasterLayerItem(lambda: canvas.image)
    for zoom in (1.0, 0.4):  # nearest tiles and minified coverage
        render_view([item], zoom, (100, 80))
    serial = canvas.image.cacheKey() >> 32
    for i in range(5):
        painter = QPainter(canvas.image)
        painter.fillRect(i, i, 2, 2, QColor(0, 255, 0))
        painter.end()
        item.canvas_changed(QRect(i, i, 2, 2))
    assert canvas.image.cacheKey() >> 32 == serial  # same data, no detach copy
    render = render_view([item], 1.0, (100, 80), centre=(32.0, 24.0))
    assert tuple(render.rgb[int(render.dy + 4), int(render.dx + 4)]) == (0, 255, 0)


def test_raster_minified_render_follows_canvas_changes():
    bgra = _mask_canvas()
    canvas = _Canvas(_bgra_to_image(bgra))
    item = RasterLayerItem(lambda: canvas.image)
    render_view([item], 0.3, (300, 240))  # builds the coverage
    painter = QPainter(canvas.image)
    painter.fillRect(QRect(401, 97, 57, 33), QColor(tokens.SKEL))
    painter.end()
    item.canvas_changed(QRect(401, 97, 57, 33))
    updated = render_view([item], 0.3, (300, 240))
    fresh = render_view([RasterLayerItem(lambda: canvas.image)], 0.3, (300, 240))
    assert np.array_equal(updated.rgb, fresh.rgb)


def _raster(image: QImage, cover_tile: int) -> RasterLayerItem:
    item = RasterLayerItem(lambda: image)
    item.COVER_TILE = cover_tile  # 64: coverage levels over 128 x 128 px are tiled
    return item


@pytest.mark.parametrize("zoom", [0.3, 0.45, 0.6, 0.9])
def test_raster_tiled_coverage_renders_like_one_image(zoom):
    image = _bgra_to_image(_mask_canvas(330, 250))
    size = (int(330 * zoom) + 2, int(250 * zoom) + 2)
    tiled, whole = _blank(*size), _blank(*size)
    _paint_item(_raster(image, 64), tiled, zoom)
    _paint_item(_raster(image, 4096), whole, zoom)
    assert np.abs(_premultiplied(tiled) - _premultiplied(whole)).max() <= 1


@pytest.mark.parametrize("zoom", [0.3, 0.7, 2.0])
def test_raster_straight_alpha_canvas_renders_like_premultiplied(zoom):
    bgra = _mask_canvas(330, 250)
    bgra[::7, :, 3] = 90  # some translucent rows, so the two formats really differ
    premultiplied = _bgra_to_image(bgra)
    straight = premultiplied.convertToFormat(QImage.Format.Format_ARGB32)
    a = render_view([RasterLayerItem(lambda: premultiplied)], zoom, (300, 240))
    b = render_view([RasterLayerItem(lambda: straight)], zoom, (300, 240))
    assert np.abs(a.rgb.astype(int) - b.rgb.astype(int)).max() <= 1


def test_raster_stroke_while_minified_remakes_only_the_touched_cover_tiles():
    canvas = _Canvas(_bgra_to_image(_mask_canvas(330, 250)))
    item = RasterLayerItem(lambda: canvas.image)
    item.COVER_TILE = 64
    render_view([item], 0.7, (300, 240))  # level 0 coverage, 6 x 4 tiles of 64 px
    before = item.coverage_tiles()
    assert {key[0] for key in before} == {0} and len(before) == 24
    painter = QPainter(canvas.image)
    painter.fillRect(QRect(100, 100, 10, 10), QColor(tokens.SKEL))  # inside tile (1, 1)
    painter.end()
    item.canvas_changed(QRect(100, 100, 10, 10))
    after_render = render_view([item], 0.7, (300, 240))
    after = item.coverage_tiles()
    assert {key for key in before if after.get(key) != before[key]} == {(0, 1, 1)}
    fresh = render_view([RasterLayerItem(lambda: canvas.image)], 0.7, (300, 240))
    assert np.abs(after_render.rgb.astype(int) - fresh.rgb.astype(int)).max() <= 1


@pytest.mark.parametrize("zoom", [0.3, 0.6, 2.35])
def test_raster_partial_repaint_matches_a_full_repaint(zoom):
    image = _bgra_to_image(_mask_canvas(320, 240))
    item = RasterLayerItem(lambda: image)
    size = (int(320 * zoom) + 2, int(240 * zoom) + 2)
    full = _blank(*size)
    _paint_item(item, full, zoom)
    part = _blank(*size)
    _paint_item(item, part, zoom)
    _paint_item(item, part, zoom, QRectF(101.3, 67.7, 41.0, 23.0))
    assert np.abs(_premultiplied(full) - _premultiplied(part)).max() <= 1


def test_raster_paint_draws_only_the_exposed_rect():
    image = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 0))
    item = RasterLayerItem(lambda: image)
    target = QImage(40, 40, QImage.Format.Format_ARGB32)
    target.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = QRectF(0, 0, 10, 10)
    painter = QPainter(target)
    item.paint(painter, option, None)
    painter.end()
    assert target.pixelColor(5, 5) == QColor(255, 0, 0)
    assert target.pixelColor(20, 20).alpha() == 0


def test_raster_paint_without_a_source_draws_nothing():
    item = RasterLayerItem(lambda: None)
    assert item.boundingRect().isEmpty()
    target = _blank(10, 10)
    _paint_item(item, target, 1.0, QRectF(0, 0, 10, 10))
    assert target.pixelColor(5, 5).alpha() == 0


@pytest.mark.parametrize(("dpr", "tiles"), [(1.0, False), (2.0, True)])
def test_raster_exact_or_coverage_follows_the_device_scale_including_hidpi(dpr, tiles):
    _bgra, image = _encoded_canvas(300, 200)
    item = RasterLayerItem(lambda: image)
    _paint_item(item, _blank(300, 200, dpr), 0.75)  # device scale 0.75 or 1.5
    assert bool(item.built_tiles()) is tiles


def test_raster_restores_the_painter_hints():
    _bgra, image = _encoded_canvas(64, 48)
    item = RasterLayerItem(lambda: image)
    for scale in (3.0, 0.4):
        target = _blank(200, 150)
        painter = QPainter(target)
        hints = (
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.setRenderHints(hints)
        painter.scale(scale, scale)
        option = QStyleOptionGraphicsItem()
        option.exposedRect = item.boundingRect()
        item.paint(painter, option, None)
        assert painter.renderHints() == hints
        painter.end()


def test_smooth_photo_and_crisp_layer_share_one_scene():
    photo = PhotoLayerItem()
    photo.set_image(_photo_bgr(200, 150))
    photo.setZValue(0)
    _bgra, image = _encoded_canvas(100, 150)
    layer = RasterLayerItem(lambda: image)
    layer.setZValue(1)  # covers the left half of the photo
    top = PhotoLayerItem()
    top.set_image(_photo_bgr(50, 40, seed=SEED + 1))
    top.setPos(140, 100)
    top.setZValue(2)  # painted after the layer: must still be smooth
    render = render_view([photo, layer, top], 4.0, (800, 600), centre=(100.0, 75.0))
    ox, oy = render.dx, render.dy
    left = render.rgb[int(oy + 10 * 4) : int(oy + 140 * 4), int(ox + 10 * 4) : int(ox + 90 * 4)]
    _xs, _ys, clean = _decode(left)
    assert clean.all() and mean_run(left) >= 3.5  # the crisp layer: 4 px blocks
    right = render.rgb[int(oy + 10 * 4) : int(oy + 90 * 4), int(ox + 110 * 4) : int(ox + 135 * 4)]
    assert equal_neighbour_fraction(right) < 0.4  # the photo, right of the layer
    corner = render.rgb[
        int(oy + 105 * 4) : int(oy + 135 * 4), int(ox + 145 * 4) : int(ox + 185 * 4)
    ]
    assert equal_neighbour_fraction(corner) < 0.4  # the top photo, painted after the layer


def test_raster_opacity_and_visibility_use_the_standard_item_api():
    image = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 0))
    scene = QGraphicsScene()
    item = RasterLayerItem(lambda: image)
    scene.addItem(item)

    def render() -> QImage:
        out = QImage(4, 4, QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        scene.render(painter, QRectF(0, 0, 4, 4), QRectF(0, 0, 4, 4))
        painter.end()
        return out

    item.setOpacity(0.5)
    assert 100 <= render().pixelColor(1, 1).alpha() <= 155
    item.setVisible(False)
    assert render().pixelColor(1, 1).alpha() == 0

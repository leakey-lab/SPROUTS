"""The photo between 100 % and 50 % (app/gui/canvas_layers/photo.py, display only).

Below 100 % the full-size level is drawn minified, by up to 2x at 50 % where the pyramid's
half-size level takes over, so fine texture shimmers there. From ``BLEND_FROM`` of that
octave down (device scale ``2 ** -BLEND_FROM``, about 76 %) the half-size level is blended
over it, its weight rising linearly in log scale to 1 at 50 %: the draw is continuous where
the level changes, and zooms from 76 % up stay as sharp as before. On real 640 x 480 images
this is at least as close to the ideal area resample as the plain draw at every zoom (and
closer from 55 % to 70 %); blending lower octaves made them softer, so only this one blends.
"""

import cv2
import numpy as np
import pytest

from app.gui.canvas_layers.photo import PhotoLayerItem
from app.imaging.pyramid import area_half
from tests.render_metrics import (
    area_reference,
    chirp_alias_std,
    chirp_image,
    chirp_pan_delta,
    psnr,
    render_view,
)

pytestmark = pytest.mark.usefixtures("app")


def jpeg_texture(w: int = 640, h: int = 480, seed: int = 3) -> np.ndarray:
    """A soil-like BGR texture as the data comes: blobs plus grain, JPEG quality 40."""
    rng = np.random.default_rng(seed)
    base = cv2.resize(
        rng.integers(0, 256, (h // 4, w // 4, 3), dtype=np.uint8),
        (w, h),
        interpolation=cv2.INTER_CUBIC,
    )
    grain = rng.normal(0, 18, (h, w, 3))
    photo = np.clip(base.astype(float) + grain, 0, 255).astype(np.uint8)
    ok, data = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 40])
    assert ok
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def rendered(item: PhotoLayerItem, zoom: float, size) -> tuple[np.ndarray, float, float]:
    render = render_view([item], zoom, size)
    return render.rgb, render.dx, render.dy


def photo(image: np.ndarray) -> PhotoLayerItem:
    item = PhotoLayerItem()
    item.set_image(image)
    return item


@pytest.mark.parametrize(
    ("zoom", "level", "blended"),
    [(1.0, 0, False), (0.9, 0, False), (0.8, 0, False), (0.7, 0, True), (0.55, 0, True),
     (0.5, 1, False), (0.3, 1, False), (0.25, 2, False)],
)  # fmt: skip
def test_only_zooms_between_76_and_50_percent_blend_in_the_half_size_level(zoom, level, blended):
    item = photo(jpeg_texture(160, 120))
    render_view([item], zoom, (120, 90))
    assert item.level_painted() == level
    assert (item.blend_painted() > 0) == blended


def test_the_blend_weight_rises_to_one_at_50_percent():
    item = photo(jpeg_texture(160, 120))
    weights = []
    for zoom in (0.75, 0.7, 0.65, 0.6, 0.55, 0.51):
        render_view([item], zoom, (120, 90))
        weights.append(item.blend_painted())
    assert weights == sorted(weights) and weights[-1] > 0.95


def test_the_draw_is_continuous_where_the_level_changes(monkeypatch):
    """Just above 50 % the photo looks like the half-size level alone (what 50 % shows);
    the plain draw of the full-size level there differed visibly (the pop at the switch)."""
    image = jpeg_texture()
    half = PhotoLayerItem()
    half.set_image(area_half(image))
    half.setScale(2.0)  # the half-size level where the full image is: level 1 at the zoom
    zoom, size = 0.502, (321, 241)
    level_one, dx1, dy1 = rendered(half, zoom, size)
    blended, dx, dy = rendered(photo(image), zoom, size)
    monkeypatch.setattr(PhotoLayerItem, "BLEND_FROM", 1.0)  # the draw before the blend
    plain, _, _ = rendered(photo(image), zoom, size)
    assert (dx, dy) == (dx1, dy1)
    inner = (slice(3, -3), slice(3, -3))

    def mean_diff(a: np.ndarray) -> float:
        return float(np.abs(a[inner].astype(int) - level_one[inner].astype(int)).mean())

    assert mean_diff(blended) < 1.0
    assert mean_diff(plain) > 5.0


def _chirp_metrics(zoom: float) -> tuple[float, float]:
    out = []
    for shift in (0, 1):  # the second is shifted by one source pixel (a pan)
        item = photo(chirp_image(640, 64, shift))
        size = (int(640 * zoom) + 40, 100)
        render = render_view([item], zoom, size, centre=(320.0, 32.0))
        x0, y0 = int(round(render.dx)), int(round(render.dy))
        rows = slice(max(0, y0 + 4), y0 + int(64 * zoom) - 4)
        out.append(render.rgb[rows, max(0, x0) : x0 + int(640 * zoom)])
    return chirp_alias_std(out[0], zoom), chirp_pan_delta(out[0], out[1], zoom)


@pytest.mark.parametrize("zoom", [0.55, 0.6])
def test_fine_texture_shimmers_far_less_just_above_50_percent(zoom):
    """The plain draw measured 55.0 / 87.4 (0.55) and 61.1 / 95.2 (0.6) here."""
    alias, pan = _chirp_metrics(zoom)
    assert alias <= 35 and pan <= 55


@pytest.mark.parametrize("zoom", [0.6, 0.65, 0.7, 0.8, 0.9])
def test_a_photo_stays_at_least_as_close_to_the_ideal_as_the_plain_draw(monkeypatch, zoom):
    image = jpeg_texture()
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    w, h = int(640 * zoom), int(480 * zoom)
    blended, dx, dy = rendered(photo(image), zoom, (w, h))
    ideal = area_reference(rgb, zoom, dx, dy, w, h)
    monkeypatch.setattr(PhotoLayerItem, "BLEND_FROM", 1.0)  # the draw before the blend
    plain, pdx, pdy = rendered(photo(image), zoom, (w, h))
    assert (pdx, pdy) == (dx, dy)
    inner = (slice(3, -3), slice(3, -3))
    assert psnr(blended[inner], ideal[inner]) >= psnr(plain[inner], ideal[inner]) - 0.1

"""SkeletonLayerItem (app/gui/canvas_layers/skeleton.py, contract C6, spec section 10.3).

The 1 px skeleton raster is drawn as a line at every zoom: below device scale 1 the line +
halo overlay through a max-pool pyramid, from 1 to 2 the overlay at level 0 (nearest), and
from 2 device px per skeleton px m-adjacency vector links between pixel centres, extracted
lazily per 256 px tile at paint time. ``vectors=False`` (the Library's 2 px generated file)
never draws vectors or nearest pixels: bilinear from scale 1.
"""

from __future__ import annotations

import os

os.environ["ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"] = "0"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QTransform  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
)

from app.gui.canvas_layers.skeleton import SkeletonLayerItem  # noqa: E402
from app.gui.skeleton_overlay import overlay_indices  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from app.imaging.skeleton_vectors import skeleton_links, tile_segments  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

GREY = QColor(96, 96, 96)
HALO_ALPHA = 170


pytestmark = pytest.mark.usefixtures("no_prompts")


# ------------------------------------------------------------------------------ helpers
_SCENES: list[QGraphicsScene] = []  # keep every scene referenced for the module


@pytest.fixture(scope="module", autouse=True)
def _drop_scenes():
    """The scenes go with the module (with their items' warm-up timers, which would fire in
    a later test file)."""
    yield
    _SCENES.clear()


def _in_scene(item: SkeletonLayerItem) -> SkeletonLayerItem:
    scene = QGraphicsScene()
    scene.addItem(item)
    _SCENES.append(scene)
    return item


def _unbudgeted(item: SkeletonLayerItem) -> SkeletonLayerItem:
    """Every exposed tile is extracted in the first paint (renders become deterministic)."""
    item.EXTRACT_BUDGET_MS = float("inf")
    return item


def _render(
    item: SkeletonLayerItem,
    zoom: float,
    size: tuple[int, int],
    *,
    origin: tuple[float, float] = (0.0, 0.0),
    dpr: float = 1.0,
    hints: QPainter.RenderHint | None = None,
    background: QColor = GREY,
) -> np.ndarray:
    """RGB (h, w, 3) of the item's scene rendered at ``zoom`` into ``size`` device px.

    ``origin`` is the scene point at the image's top-left; the device scale the item sees
    is ``zoom * dpr`` (times its own ``scale`` transform).
    """
    w, h = size
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(background)
    painter = QPainter(img)
    if hints is not None:
        painter.setRenderHints(hints)
    lw, lh = w / dpr, h / dpr
    item.scene().render(
        painter, QRectF(0, 0, lw, lh), QRectF(origin[0], origin[1], lw / zoom, lh / zoom)
    )
    painter.end()
    return _rgb(img)


def _rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())
    return rows[:, : img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def _rgb_of(color: str | QColor) -> tuple[int, int, int]:
    c = QColor(color)
    return (c.red(), c.green(), c.blue())


def _over(color: str, alpha: int, background: QColor = GREY) -> np.ndarray:
    """``color`` at ``alpha`` composited over an opaque ``background`` (hand formula)."""
    a = alpha / 255.0
    fg = np.array(_rgb_of(color), float)
    bg = np.array(_rgb_of(background), float)
    return fg * a + bg * (1.0 - a)


def _mask(w: int, h: int, pixels) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    for x, y in pixels:
        m[y, x] = 255
    return m


def _line_mask() -> np.ndarray:
    """40x30: a horizontal line on row 10 (x 5..30) and a vertical one on column 20."""
    m = np.zeros((30, 40), np.uint8)
    m[10, 5:31] = 255
    m[11:25, 20] = 255
    return m


# ------------------------------------------------------------------------ step 1: API
def test_zoom_one_draws_the_line_in_the_skeleton_colour_over_a_dark_halo(app):
    mask = _line_mask()
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)

    rgb = _render(item, 1.0, (40, 30))

    assert item.mode_painted() == "raster"
    line = mask > 0
    assert (rgb[line] == _rgb_of(tokens.SKEL)).all()
    halo_px = rgb[9, 12]  # directly above the horizontal line
    assert np.abs(halo_px - _over(tokens.BG_0, HALO_ALPHA)).max() <= 2
    assert tuple(rgb[2, 2]) == _rgb_of(GREY)  # far from the line: untouched


def test_halo_false_draws_the_line_only(app):
    mask = _line_mask()
    item = _in_scene(SkeletonLayerItem(halo=False))
    item.set_mask(mask)

    rgb = _render(item, 1.0, (40, 30))

    assert (rgb[mask > 0] == _rgb_of(tokens.SKEL)).all()
    assert (rgb[mask == 0] == _rgb_of(GREY)).all()


def test_the_line_colour_follows_the_color_argument(app):
    mask = _line_mask()
    item = _in_scene(SkeletonLayerItem(color=tokens.MASK, halo=False))
    item.set_mask(mask)

    rgb = _render(item, 1.0, (40, 30))

    assert (rgb[mask > 0] == _rgb_of(tokens.MASK)).all()


def test_scale_becomes_the_item_transform_and_bounds_stay_in_skeleton_pixels(app):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(np.zeros((256, 341), np.uint8), scale=(640 / 341, 480 / 256))

    assert item.boundingRect() == QRectF(0, 0, 341, 256)
    assert item.transform() == QTransform.fromScale(640 / 341, 480 / 256)
    scene_rect = item.sceneBoundingRect()
    assert scene_rect.width() == pytest.approx(640) and scene_rect.height() == pytest.approx(480)

    item.set_mask(np.zeros((256, 341), np.uint8))  # default scale: identity again
    assert item.transform() == QTransform()


def test_revision_counts_every_set_mask_call(app):
    item = SkeletonLayerItem()
    assert item.revision() == 0
    item.set_mask(_line_mask())
    item.set_mask(_line_mask(), changed=(1, 1, 2, 2))
    item.set_mask(None)
    assert item.revision() == 3


def test_no_mask_means_an_empty_item_that_paints_nothing(app):
    item = _in_scene(SkeletonLayerItem())
    assert item.mode_painted() == ""
    item.set_mask(_line_mask())
    item.set_mask(None)

    assert item.boundingRect().isEmpty()
    rgb = _render(item, 1.0, (40, 30))
    assert (rgb == _rgb_of(GREY)).all()
    assert item.mode_painted() == ""


def test_paint_draws_only_the_exposed_rect(app):
    mask = _line_mask()
    item = SkeletonLayerItem(halo=False)
    item.set_mask(mask)
    target = QImage(40, 30, QImage.Format.Format_ARGB32_Premultiplied)
    target.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = QRectF(0, 0, 12, 30)  # the left part of the horizontal line only

    painter = QPainter(target)
    item.paint(painter, option, None)
    painter.end()

    assert target.pixelColor(8, 10) == QColor(tokens.SKEL)
    assert target.pixelColor(25, 10).alpha() == 0  # line pixel outside the exposed rect


def test_painting_restores_the_painters_render_hints(app):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(_line_mask())
    wanted = QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
    img = QImage(40, 30, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(GREY)
    painter = QPainter(img)
    painter.setRenderHints(wanted)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = QRectF(0, 0, 40, 30)

    item.paint(painter, option, None)
    after = painter.renderHints()
    painter.end()

    assert after & wanted == wanted


def test_no_vector_tiles_are_extracted_below_the_vector_scale(app):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(_line_mask())
    _render(item, 1.0, (40, 30))
    assert item.vector_tiles() == {}


# ------------------------------------------------------- step 2: Qt-free vector extraction
TILE = 256


def _links(mask: np.ndarray, box=None) -> set[tuple[int, int, int, int]]:
    return {tuple(int(v) for v in row) for row in skeleton_links(mask, box)}


def _segments(mask: np.ndarray, box) -> set[tuple[int, int, int, int]]:
    return {tuple(int(v) for v in row) for row in tile_segments(mask, box)}


def _unit_steps(segment) -> list[tuple[int, int, int, int]]:
    """Expand a merged run (x0, y0, x1, y1) back into its unit links (a dot stays a dot)."""
    x0, y0, x1, y1 = (int(v) for v in segment)
    n = max(abs(x1 - x0), abs(y1 - y0))
    if n == 0:
        return [(x0, y0, x0, y0)]
    dx, dy = (x1 - x0) // n, (y1 - y0) // n
    return [(x0 + i * dx, y0 + i * dy, x0 + (i + 1) * dx, y0 + (i + 1) * dy) for i in range(n)]


def _tiles(mask: np.ndarray):
    h, w = mask.shape
    for ty in range(0, h, TILE):
        for tx in range(0, w, TILE):
            yield (tx, ty, TILE, TILE)


def _random_skeleton(seed: int, w: int = 600, h: int = 540, walks: int = 12) -> np.ndarray:
    """8-connected random walks (with crossings, spurs and blobs where walks meet)."""
    rng = np.random.default_rng(seed)
    mask = np.zeros((h, w), np.uint8)
    steps = np.array([(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)])
    for _ in range(walks):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        for _ in range(int(rng.integers(50, 900))):
            mask[y, x] = 255
            dx, dy = steps[rng.integers(0, 8)]
            x, y = int(np.clip(x + dx, 0, w - 1)), int(np.clip(y + dy, 0, h - 1))
    for _ in range(40):  # scattered isolated pixels
        mask[int(rng.integers(0, h)), int(rng.integers(0, w))] = 255
    return mask


def test_l_corner_links_skip_the_corner_diagonal():
    mask = _mask(6, 6, [(1, 1), (1, 2), (1, 3), (2, 3), (3, 3)])
    assert _links(mask) == {(1, 1, 1, 2), (1, 2, 1, 3), (1, 3, 2, 3), (2, 3, 3, 3)}
    assert _segments(mask, (0, 0, 6, 6)) == {(1, 1, 1, 3), (1, 3, 3, 3)}


def test_t_junction_links_join_the_stem_to_the_bar_only():
    mask = _mask(6, 6, [(1, 1), (2, 1), (3, 1), (2, 2), (2, 3)])
    assert _links(mask) == {(1, 1, 2, 1), (2, 1, 3, 1), (2, 1, 2, 2), (2, 2, 2, 3)}
    assert _segments(mask, (0, 0, 6, 6)) == {(1, 1, 3, 1), (2, 1, 2, 3)}


def test_an_isolated_pixel_is_a_dot():
    mask = _mask(6, 6, [(4, 2)])
    assert _links(mask) == {(4, 2, 4, 2)}
    assert _segments(mask, (0, 0, 6, 6)) == {(4, 2, 4, 2)}


def test_diagonal_lines_link_pixel_centres_and_merge_into_one_run():
    diagonal = _mask(6, 6, [(0, 0), (1, 1), (2, 2), (3, 3)])
    assert _links(diagonal) == {(0, 0, 1, 1), (1, 1, 2, 2), (2, 2, 3, 3)}
    assert _segments(diagonal, (0, 0, 6, 6)) == {(0, 0, 3, 3)}

    anti = _mask(6, 6, [(3, 0), (2, 1), (1, 2), (0, 3)])
    assert _links(anti) == {(3, 0, 2, 1), (2, 1, 1, 2), (1, 2, 0, 3)}
    assert _segments(anti, (0, 0, 6, 6)) == {(3, 0, 0, 3)}


def test_a_four_connected_staircase_has_no_corner_triangles():
    mask = _mask(6, 6, [(0, 0), (1, 0), (1, 1), (2, 1), (2, 2), (3, 2)])
    expected = {(0, 0, 1, 0), (1, 0, 1, 1), (1, 1, 2, 1), (2, 1, 2, 2), (2, 2, 3, 2)}
    assert _links(mask) == expected
    assert _segments(mask, (0, 0, 6, 6)) == expected  # no collinear chain to merge


def test_a_two_by_two_blob_is_a_square_without_diagonals():
    mask = _mask(4, 4, [(1, 1), (2, 1), (1, 2), (2, 2)])
    assert _links(mask) == {(1, 1, 2, 1), (1, 2, 2, 2), (1, 1, 1, 2), (2, 1, 2, 2)}


def test_pixels_on_the_image_border_link_without_wrapping():
    mask = _mask(5, 4, [(4, 0), (4, 1), (0, 3), (1, 3), (0, 0)])
    assert _links(mask) == {(4, 0, 4, 1), (0, 3, 1, 3), (0, 0, 0, 0)}


def test_links_belong_to_the_tile_of_their_first_pixel():
    mask = np.zeros((300, 300), np.uint8)
    mask[5, 250:261] = 255  # a horizontal line across the x = 256 tile border

    assert _segments(mask, (0, 0, TILE, TILE)) == {(250, 5, 256, 5)}
    assert _segments(mask, (TILE, 0, TILE, TILE)) == {(256, 5, 260, 5)}


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_per_tile_links_equal_the_whole_frame_links_for_random_skeletons(seed):
    mask = _random_skeleton(seed)
    whole = skeleton_links(mask)
    per_tile = [skeleton_links(mask, box) for box in _tiles(mask)]

    assert sum(len(p) for p in per_tile) == len(whole)  # every link extracted exactly once
    assert set().union(*({tuple(r) for r in p.tolist()} for p in per_tile)) == _links(mask)


def test_per_tile_links_equal_the_whole_frame_links_for_lines_crossing_tile_corners():
    mask = np.zeros((520, 530), np.uint8)
    for i in range(-6, 7):
        mask[256 + i, 256 + i] = 255  # diagonal through the corner of four tiles
        mask[255 - i, 256 + i] = 255  # anti-diagonal through the same corner
        mask[255, 250 + i] = 255  # along the last row of the top tiles
        mask[250 + i, 256] = 255  # along the first column of the right tiles
    mask[511:514, 511:514] = 255  # a blob on the next corner
    per_tile = [_links(mask, box) for box in _tiles(mask)]

    assert sum(len(p) for p in per_tile) == len(_links(mask))
    assert set().union(*per_tile) == _links(mask)


@pytest.mark.parametrize("seed", [5, 6])
def test_merged_segments_are_the_tile_links_joined_into_maximal_collinear_runs(seed):
    mask = _random_skeleton(seed)
    for box in _tiles(mask):
        segments = tile_segments(mask, box)
        expanded = [step for seg in segments for step in _unit_steps(seg)]
        assert sorted(expanded) == sorted(_links(mask, box))  # same links, none twice
        runs = [s for s in segments.tolist() if _direction(s) is not None]
        ends = {(tuple(s[2:]), _direction(s)) for s in runs}
        starts = {(tuple(s[:2]), _direction(s)) for s in runs}
        assert not ends & starts, "two collinear runs of one tile should have merged"


def _direction(seg) -> tuple[int, int] | None:
    x0, y0, x1, y1 = seg
    n = max(abs(x1 - x0), abs(y1 - y0))
    return None if n == 0 else ((x1 - x0) // n, (y1 - y0) // n)


def test_extraction_reads_the_mask_without_changing_it():
    mask = _random_skeleton(7)
    before = mask.copy()
    skeleton_links(mask)
    tile_segments(mask, (0, 0, TILE, TILE))
    assert np.array_equal(mask, before)


def test_an_empty_or_outside_box_has_no_links():
    mask = _random_skeleton(8)
    assert len(skeleton_links(np.zeros((40, 40), np.uint8))) == 0
    assert len(tile_segments(mask, (1000, 1000, TILE, TILE))) == 0


# ---------------------------------------------------------------------------- step 3: tiers
VIEW_HINTS = QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
SMOOTH_VIEW_HINTS = VIEW_HINTS | QPainter.RenderHint.SmoothPixmapTransform


def _near(rgb: np.ndarray, color: str | QColor, tol: int = 30) -> np.ndarray:
    """Pixels within ``tol`` of ``color`` on every channel."""
    return (np.abs(rgb.astype(int) - np.array(_rgb_of(color))) <= tol).all(axis=-1)


def _max_run(flags: np.ndarray) -> int:
    """Longest run of True in a 1-D array."""
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def _components(binary: np.ndarray) -> int:
    count, _ = cv2.connectedComponents(binary.astype(np.uint8), connectivity=8)
    return count - 1


def _three_curves() -> np.ndarray:
    """400x300: a circle, a diagonal and a sine curve, 8-connected, far apart."""
    mask = np.zeros((300, 400), np.uint8)
    cv2.circle(mask, (80, 80), 50, 255, 1)
    cv2.line(mask, (200, 20), (380, 150), 255, 1)
    xs = np.arange(20, 380)
    pts = np.stack([xs, (230 + 30 * np.sin(xs / 25.0)).astype(int)], axis=1)
    cv2.polylines(mask, [pts.astype(np.int32)], False, 255, 1)
    return mask


def _dots(count: int, columns: int = 50) -> np.ndarray:
    """``count`` isolated pixels on a 2 px grid (one vector segment each)."""
    rows = -(-count // columns)
    mask = np.zeros((2 * rows + 2, 2 * columns + 2), np.uint8)
    for i in range(count):
        mask[1 + 2 * (i // columns), 1 + 2 * (i % columns)] = 255
    return mask


def test_800_percent_draws_a_thin_line_not_eight_pixel_blocks(app):
    mask = np.zeros((40, 60), np.uint8)
    mask[10, 5:46] = 255  # horizontal
    for i in range(20):
        mask[15 + i, 10 + i] = 255  # diagonal
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)

    rgb = _render(item, 8.0, (480, 320), hints=VIEW_HINTS)

    assert item.mode_painted() == "vector"
    skel = _near(rgb, tokens.SKEL)
    across_horizontal = [_max_run(skel[40:130, c]) for c in range(200, 360, 7)]
    across_diagonal = [_max_run(skel[r, 60:300]) for r in range(150, 270, 7)]
    assert max(across_horizontal) < 4 and min(across_horizontal) >= 1
    assert max(across_diagonal) < 4 and min(across_diagonal) >= 1


@pytest.mark.parametrize("zoom", [0.25, 0.3, 0.45, 0.6, 0.75, 0.9])
def test_minified_lines_never_break_into_dashes(app, zoom):
    mask = _three_curves()
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)
    size = (int(np.ceil(400 * zoom)), int(np.ceil(300 * zoom)))

    rgb = _render(item, zoom, size, hints=VIEW_HINTS)

    assert item.mode_painted() == "raster-lod"
    assert _components(_near(rgb, tokens.SKEL, tol=0)) == _components(mask > 0) == 3


def test_a_one_pixel_line_on_any_row_stays_visible_when_minified(app):
    for row in (100, 101, 102, 103):
        mask = np.zeros((200, 300), np.uint8)
        mask[row, 20:280] = 255
        item = _in_scene(SkeletonLayerItem(halo=False))
        item.set_mask(mask)
        for zoom in (0.3, 0.6, 0.75):
            rgb = _render(item, zoom, (int(300 * zoom), int(200 * zoom)))
            skel = _near(rgb, tokens.SKEL, tol=0)
            covered = skel.any(axis=0)[int(25 * zoom) : int(275 * zoom)]
            assert covered.all(), (row, zoom)


@pytest.mark.parametrize(
    ("zoom", "mode"), [(0.5, "raster-lod"), (1.0, "raster"), (1.5, "raster"), (4.0, "vector")]
)
def test_the_tier_follows_the_device_scale(app, zoom, mode):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(_line_mask())
    _render(item, zoom, (int(40 * zoom), int(30 * zoom)))
    assert item.mode_painted() == mode


def test_devicepixelratio_two_at_zoom_one_is_the_vector_tier(app):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(_line_mask())
    _render(item, 1.0, (80, 60), dpr=2.0)
    assert item.mode_painted() == "vector"


@pytest.mark.parametrize(
    ("zoom", "mode"), [(0.4, "raster-lod"), (0.5, "raster"), (0.75, "raster"), (1.0, "vector")]
)
def test_tiers_count_device_pixels_at_devicepixelratio_two(app, zoom, mode):
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(_line_mask())
    _render(item, zoom, (int(80 * zoom), int(60 * zoom)), dpr=2.0)
    assert item.mode_painted() == mode


def test_the_list_fallback_for_other_qreal_layouts_draws_the_same_pixels(app, monkeypatch):
    import app.gui.canvas_layers.skeleton as module

    mask = _three_curves()
    mask[5, 5] = 255  # a dot
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))  # a busy CPU must not split the tiles
    item.set_mask(mask)
    fast = _render(item, 4.0, (1600, 1200), hints=VIEW_HINTS)
    monkeypatch.setattr(module, "_SHARES_NUMPY_LAYOUT", {QLineF: False, QPointF: False})

    assert np.array_equal(_render(item, 4.0, (1600, 1200), hints=VIEW_HINTS), fast)


@pytest.mark.parametrize(
    ("count", "mode"), [(300, "vector"), (1000, "vector-thin"), (3000, "raster")]
)
def test_raster_engine_budgets_pick_thinner_lines_then_the_pixel_overlay(app, count, mode):
    mask = _dots(count)
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)
    h, w = mask.shape
    _render(item, 4.0, (4 * w, 4 * h))
    assert item.mode_painted() == mode


@pytest.mark.parametrize("zoom", [0.5, 1.5, 4.0])
def test_an_isolated_pixel_is_drawn_at_every_tier(app, zoom):
    item = _in_scene(SkeletonLayerItem(halo=False))
    item.set_mask(_mask(20, 20, [(9, 9)]))
    rgb = _render(item, zoom, (int(20 * zoom), int(20 * zoom)))
    assert _near(rgb, tokens.SKEL, tol=60).any()


@pytest.mark.parametrize("zoom", [0.5, 1.5, 4.0])
def test_halo_false_never_darkens_the_background(app, zoom):
    item = _in_scene(SkeletonLayerItem(halo=False))
    item.set_mask(_three_curves())
    rgb = _render(item, zoom, (int(400 * zoom), int(300 * zoom)), hints=VIEW_HINTS)
    assert rgb.min() >= min(_rgb_of(GREY)) - 1  # blends of the line and grey only


def test_the_halo_darkens_both_sides_of_a_vector_line(app):
    mask = np.zeros((20, 40), np.uint8)
    mask[10, 5:35] = 255
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)
    rgb = _render(item, 8.0, (320, 160), hints=VIEW_HINTS)
    column = rgb[:, 160].astype(int).sum(axis=1)
    centre = 84  # device row of the line centre (10.5 * 8)
    grey = sum(_rgb_of(GREY))
    assert column[centre - 3 : centre - 1].min() < grey - 60  # dark rim above the line
    assert column[centre + 1 : centre + 3].min() < grey - 60  # and below it


@pytest.mark.parametrize(
    ("zoom", "mode"), [(0.5, "raster-lod"), (1.5, "raster-smooth"), (4.0, "raster-smooth")]
)
def test_library_items_never_draw_vectors(app, zoom, mode):
    item = _in_scene(SkeletonLayerItem(halo=False, vectors=False))
    item.set_mask(_line_mask())
    _render(item, zoom, (int(40 * zoom), int(30 * zoom)))
    assert item.mode_painted() == mode
    assert item.vector_tiles() == {}


def test_library_two_pixel_lines_have_soft_edges_at_4x(app):
    mask = np.zeros((30, 40), np.uint8)
    mask[10:12, 5:35] = 255  # a generated line, about 2 px wide
    item = _in_scene(SkeletonLayerItem(halo=False, vectors=False))
    item.set_mask(mask)

    rgb = _render(item, 4.0, (160, 120), hints=VIEW_HINTS)

    profile = rgb[30:60, 80, 0].astype(int)  # red channel across the line's upper edge
    grey, skel = _rgb_of(GREY)[0], _rgb_of(tokens.SKEL)[0]
    between = (profile > grey + 10) & (profile < skel - 10)
    assert between.any()  # bilinear: intermediate values, no hard pixel-block edge
    assert _max_run(_near(rgb[:, 80], tokens.SKEL, tol=4)) <= 5  # no 8 px flat block


def test_library_scale_covers_the_whole_photo(app):
    mask = np.zeros((256, 341), np.uint8)
    mask[0, :] = mask[-1, :] = 255
    mask[:, 0] = mask[:, -1] = 255
    item = _in_scene(SkeletonLayerItem(halo=False, vectors=False))
    item.set_mask(mask, scale=(640 / 341, 480 / 256))

    rgb = _render(item, 1.0, (660, 500))

    ys, xs = np.nonzero(_near(rgb, tokens.SKEL, tol=100))
    assert xs.min() <= 1 and ys.min() <= 1
    assert 638 <= xs.max() <= 640 and 478 <= ys.max() <= 480


@pytest.mark.parametrize("zoom", [0.3, 1.0, 1.37, 1.5, 1.93])
def test_nearest_tiers_never_blend_even_under_a_smoothing_view(app, zoom):
    mask = np.zeros((600, 600), np.uint8)
    cv2.line(mask, (0, 255), (599, 257), 255, 1)  # crosses the x = 256 and x = 512 borders
    cv2.line(mask, (255, 0), (257, 599), 255, 1)  # crosses the y = 256 and y = 512 borders
    cv2.circle(mask, (256, 256), 40, 255, 1)
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)

    rgb = _render(item, zoom, (int(600 * zoom), int(600 * zoom)), hints=SMOOTH_VIEW_HINTS)

    halo = np.round(_over(tokens.BG_0, HALO_ALPHA)).astype(int)
    exact = _near(rgb, tokens.SKEL, tol=0) | _near(rgb, GREY, tol=0)
    exact |= (np.abs(rgb.astype(int) - halo) <= 1).all(axis=-1)
    assert exact.all()  # line, halo or background: no blended pixel anywhere


@pytest.mark.parametrize("zoom", [1.0, 1.37, 1.93])
def test_nearest_tier_tiles_leave_no_gap_in_a_line_crossing_their_border(app, zoom):
    mask = np.zeros((520, 600), np.uint8)
    mask[300, :] = 255  # crosses both vertical tile borders
    mask[:, 300] = 255  # crosses the horizontal tile border
    item = _in_scene(SkeletonLayerItem(halo=False))
    item.set_mask(mask)

    rgb = _render(item, zoom, (int(600 * zoom), int(520 * zoom)))

    skel = _near(rgb, tokens.SKEL, tol=0)
    assert skel.any(axis=0)[: int(599 * zoom)].all()  # every device column shows the row
    assert skel.any(axis=1)[: int(519 * zoom)].all()  # every device row shows the column


@pytest.mark.parametrize(
    "scale, offset", [(1.5, 1.5), (1.5, 2.5), (1.5, 0.5), (1.5, 0.0), (1.25, 0.5), (1.75, 0.5)]
)
def test_nearest_tier_tiles_sit_on_the_device_pixels_of_the_mask_layer_tiles(app, scale, offset):
    """At a .5 tie of the device mapping (common at 125-175 % display scaling) the skeleton's
    pixel tiles meet on the same device columns and rows as the mask layer's tiles: both
    layers show the same pixels identically."""
    from app.gui.canvas_layers.raster import RasterLayerItem
    from app.gui.canvas_layers.skeleton import _argb

    mask = np.zeros((300, 600), np.uint8)
    mask[:, [254, 255, 256, 257, 511, 512]] = 255  # lines on both sides of the tile borders
    mask[[255, 256], :] = 255
    item = SkeletonLayerItem(halo=False)
    item.set_mask(mask)
    canvas = _argb(overlay_indices(mask), item._table)  # the same pixels as a mask canvas
    layer = RasterLayerItem(lambda: canvas)

    def paint(target) -> np.ndarray:
        size = (int(600 * scale + offset) + 2, int(300 * scale + offset) + 2)
        img = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(GREY)
        painter = QPainter(img)
        painter.translate(offset, offset)
        painter.scale(scale, scale)
        option = QStyleOptionGraphicsItem()
        option.exposedRect = target.boundingRect()
        target.paint(painter, option, None)
        painter.end()
        return _rgb(img)

    skeleton = paint(item)
    assert item.mode_painted() == "raster"
    assert np.array_equal(skeleton, paint(layer))


# ------------------------------------------------------- step 4: lazy tiles, O(box) updates
def _busy_mask(w: int = 1024, h: int = 768) -> np.ndarray:
    """Lines in every 256 px tile, some crossing tile borders."""
    mask = np.zeros((h, w), np.uint8)
    for y in range(40, h, 96):
        cv2.line(mask, (0, y), (w - 1, y + 30), 255, 1)
    for x in range(30, w, 128):
        cv2.line(mask, (x, 0), (x + 40, h - 1), 255, 1)
    return mask


def _edit(mask: np.ndarray, box) -> None:
    """Erase and draw inside ``box`` (x, y, w, h), in place."""
    x, y, w, h = box
    mask[y : y + h, x : x + w] = 0
    cv2.line(mask, (x, y + h // 2), (x + w - 1, y + h - 1), 255, 1)
    mask[y, x] = 255  # a pixel on the box corner: its halo reaches outside the box


EDIT_BOXES = [(300, 200, 32, 32), (250, 250, 12, 12), (0, 0, 8, 8), (1016, 760, 8, 8)]


@pytest.mark.parametrize("box", EDIT_BOXES)
@pytest.mark.parametrize("new_array", [False, True])
@pytest.mark.parametrize("zoom", [0.5, 1.5, 3.0])
def test_a_box_update_renders_the_same_pixels_as_a_full_update(app, box, new_array, zoom):
    mask = _busy_mask()
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    _render(item, zoom, (int(1024 * zoom), int(768 * zoom)))  # every cache built
    if new_array:
        mask = mask.copy()
    _edit(mask, box)

    item.set_mask(mask, changed=box)
    fresh = _unbudgeted(_in_scene(SkeletonLayerItem()))
    fresh.set_mask(mask.copy())

    size = (int(1024 * zoom), int(768 * zoom))
    assert np.array_equal(_render(item, zoom, size), _render(fresh, zoom, size))


ODD_BOXES = [(300, 200, 32, 32), (0, 0, 9, 7), (990, 770, 11, 7), (511, 255, 3, 3)]


@pytest.mark.parametrize("box", ODD_BOXES)
def test_a_box_update_patches_every_cached_level_to_a_full_rebuild(app, box):
    mask = _busy_mask(1001, 777)  # odd sides: each level replicates its last row and column
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    zooms = (0.5, 0.24, 1.0 - 1e-7)  # levels 1 and 3, and level 0 of the LOD tier
    for zoom in zooms:
        _render(item, zoom, (int(1001 * zoom), int(777 * zoom)))
    assert set(item._level_images) == {0, 1, 3}
    for step in range(3):  # edits one after the other, each patched in place
        x, y, w, h = box
        _edit(mask, (max(0, x - 5 * step), y, w, h))
        item.set_mask(mask, changed=(max(0, x - 5 * step), y, w, h))
    fresh = _unbudgeted(_in_scene(SkeletonLayerItem()))
    fresh.set_mask(mask.copy())

    for zoom in zooms:
        size = (int(1001 * zoom), int(777 * zoom))
        assert np.array_equal(_render(item, zoom, size), _render(fresh, zoom, size)), zoom


def test_a_box_update_never_rebuilds_a_whole_level_and_moves_its_cache_key(app, monkeypatch):
    import app.gui.canvas_layers.skeleton as module

    mask = _busy_mask(2048, 1536)
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    for zoom in (0.5, 0.24):
        _render(item, zoom, (int(2048 * zoom), int(1536 * zoom)))
    keys = {k: image.cacheKey() for k, image in item._level_images.items()}
    shapes = []
    monkeypatch.setattr(
        module, "overlay_indices", lambda m: shapes.append(m.shape) or overlay_indices(m)
    )

    _edit(mask, (1000, 690, 32, 32))
    item.set_mask(mask, changed=(1000, 690, 32, 32))
    _render(item, 0.5, (1024, 768))
    _render(item, 0.24, (int(2048 * 0.24), int(1536 * 0.24)))

    assert max(h * w for h, w in shapes) <= 36 * 36  # the box and its context, never a level
    assert set(item._level_images) == set(keys)  # patched, not dropped
    for k, image in item._level_images.items():
        assert image.cacheKey() != keys[k], k  # an OpenGL texture of it is uploaded again


def test_a_box_update_patches_a_cached_level_only_when_that_level_is_drawn(app, monkeypatch):
    import app.gui.canvas_layers.skeleton as module

    mask = _busy_mask(2048, 1536)
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    for zoom in (0.5, 0.24):  # levels 1 and 3 cached
        _render(item, zoom, (int(2048 * zoom), int(1536 * zoom)))
    keys = {k: image.cacheKey() for k, image in item._level_images.items()}
    shapes = []
    monkeypatch.setattr(
        module, "overlay_indices", lambda m: shapes.append(m.shape) or overlay_indices(m)
    )

    _edit(mask, (1000, 690, 32, 32))
    item.set_mask(mask, changed=(1000, 690, 32, 32))
    assert shapes == [(36, 36)]  # the level-0 overlay only: the levels wait for a draw
    assert {k: image.cacheKey() for k, image in item._level_images.items()} == keys
    _render(item, 0.5, (1024, 768))  # level 1 drawn and patched, level 3 still waits
    assert len(shapes) == 2 and shapes[1][0] * shapes[1][1] <= 20 * 20
    assert item._level_images[1].cacheKey() != keys[1]
    assert item._level_images[3].cacheKey() == keys[3]
    _render(item, 0.24, (int(2048 * 0.24), int(1536 * 0.24)))
    assert len(shapes) == 3 and item._level_images[3].cacheKey() != keys[3]


def test_a_frame_sized_box_drops_the_cached_levels_instead_of_patching_them(app, monkeypatch):
    import app.gui.canvas_layers.skeleton as module

    mask = _busy_mask(1001, 777)
    full = mask.copy()
    frame = (0, 0, 1001, 777)
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    zooms = (0.5, 0.24)  # levels 1 and 3
    for zoom in zooms:
        _render(item, zoom, (int(1001 * zoom), int(777 * zoom)))
    assert set(item._level_images) == {1, 3}
    words, patched = module.pixel_words, []
    monkeypatch.setattr(module, "pixel_words", lambda image: patched.append(image) or words(image))

    for content in (0, full):  # Clear, then its undo
        mask[:] = content
        item.set_mask(mask, changed=frame)
        assert item._level_images == {}  # rebuilt when drawn, as cheap as a patch
        fresh = _unbudgeted(_in_scene(SkeletonLayerItem()))
        fresh.set_mask(mask.copy())
        for zoom in zooms:
            size = (int(1001 * zoom), int(777 * zoom))
            assert np.array_equal(_render(item, zoom, size), _render(fresh, zoom, size)), zoom

    assert patched == []  # no level was patched


def test_a_cached_level_merges_its_stale_boxes_and_is_dropped_past_half_of_it(app):
    mask = _busy_mask(1001, 777)
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    zooms = (0.5, 0.24)  # levels 1 and 3 cached; level 3 is the one drawn below
    for zoom in zooms:
        _render(item, zoom, (int(1001 * zoom), int(777 * zoom)))

    for box in ((0, 0, 8, 8), (990, 770, 11, 7)):  # opposite corners
        _edit(mask, box)
        item.set_mask(mask, changed=box)
        _render(item, 0.24, (int(1001 * 0.24), int(777 * 0.24)))

    # level 1 waited: one stale box over both corners, the whole level, so it was dropped
    assert set(item._level_images) == {3}
    fresh = _unbudgeted(_in_scene(SkeletonLayerItem()))
    fresh.set_mask(mask.copy())
    for zoom in zooms:
        size = (int(1001 * zoom), int(777 * zoom))
        assert np.array_equal(_render(item, zoom, size), _render(fresh, zoom, size)), zoom


def test_a_box_update_recomputes_the_overlay_around_the_box_only_and_extracts_nothing(
    app, monkeypatch
):
    import app.gui.canvas_layers.skeleton as module

    mask = np.zeros((1536, 2048), np.uint8)
    mask[700, 100:1900] = 255
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)
    shapes, extracted = [], []
    monkeypatch.setattr(
        module, "overlay_indices", lambda m: shapes.append(m.shape) or overlay_indices(m)
    )
    monkeypatch.setattr(module, "tile_segments", lambda *a: extracted.append(a))

    mask[690:722, 1000:1032] = 0
    item.set_mask(mask, changed=(1000, 690, 32, 32))
    item.set_mask(mask, changed=(0, 0, 3, 3))  # clipped at the image corner

    # the overlay of the box padded by 1 needs 1 more pixel of context (the 3x3 halo)
    assert shapes == [(36, 36), (5, 5)]
    assert extracted == []


def test_a_box_update_drops_exactly_the_vector_tiles_its_padded_box_touches(app):
    mask = _busy_mask()
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(mask)
    _render(item, 2.0, (2048, 1536))  # s = 2: every tile extracted
    before = item.vector_tiles()
    assert set(before) == {(tx, ty) for tx in range(4) for ty in range(3)}

    item.set_mask(mask, changed=(300, 300, 10, 10))  # inside tile (1, 1)
    after_inside = item.vector_tiles()
    item.set_mask(mask, changed=(256, 20, 4, 4))  # padded box reaches x = 255: tile (0, 0)
    after_border = item.vector_tiles()

    assert set(before) - set(after_inside) == {(1, 1)}
    assert set(after_inside) - set(after_border) == {(0, 0), (1, 0)}
    assert set(after_border) <= set(before)  # an update never extracts


def test_a_vector_paint_extracts_only_the_exposed_tiles(app):
    item = _unbudgeted(_in_scene(SkeletonLayerItem()))
    item.set_mask(_busy_mask())

    _render(item, 4.0, (400, 400), origin=(300, 300))  # scene 300..400: inside tile (1, 1)
    assert set(item.vector_tiles()) == {(1, 1)}
    _render(item, 4.0, (800, 400), origin=(300, 300))  # scene x 300..500: still tile (1, 1)
    assert set(item.vector_tiles()) == {(1, 1)}
    _render(item, 4.0, (1200, 400), origin=(300, 300))  # scene x 300..600: into tile (2, 1)
    assert set(item.vector_tiles()) == {(1, 1), (2, 1)}


def test_without_an_extraction_budget_pending_tiles_show_the_pixel_overlay(app):
    mask = _busy_mask()
    item = _in_scene(SkeletonLayerItem())
    item.set_mask(mask)
    item.EXTRACT_BUDGET_MS = 0.0  # one tile per paint
    pixels = _in_scene(SkeletonLayerItem())
    pixels.set_mask(mask)
    pixels.VECTOR_MIN_SCALE = 100.0  # the nearest level-0 overlay at every zoom >= 1

    first = _render(item, 2.0, (1024, 1024), origin=(128, 128))  # tiles x 0..2, y 0..2
    reference = _render(pixels, 2.0, (1024, 1024), origin=(128, 128))

    assert len(item.vector_tiles()) == 1
    (done,) = item.vector_tiles()
    for tx in range(3):
        for ty in range(3):
            if (tx, ty) == done:
                continue
            x0, y0 = max(0, 2 * (tx * 256 - 128)) + 8, max(0, 2 * (ty * 256 - 128)) + 8
            x1, y1 = (
                min(1024, 2 * ((tx + 1) * 256 - 128)) - 8,
                min(1024, 2 * ((ty + 1) * 256 - 128)) - 8,
            )
            assert np.array_equal(first[y0:y1, x0:x1], reference[y0:y1, x0:x1]), (tx, ty)


@pytest.fixture
def shown_views():
    """Views a test shows, closed and deleted afterwards even when the test fails."""
    views: list[QGraphicsView] = []
    yield views
    delete_widgets(views)


def _raster_view(
    views: list[QGraphicsView],
    item: SkeletonLayerItem,
    size: tuple[int, int],
    zoom: float,
    centre: QPointF | None = None,
) -> QGraphicsView:
    """A shown raster-viewport view (partial updates) holding ``item`` at ``zoom``."""
    view = QGraphicsView()
    views.append(view)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scene = QGraphicsScene(view)
    view.setScene(scene)
    scene.addItem(item)
    view.resize(*size)
    view.scale(zoom, zoom)
    if centre is not None:
        view.centerOn(centre)
    view.show()
    for _ in range(5):
        QApplication.processEvents()
    return view


def test_pending_tiles_schedule_paints_until_the_render_equals_an_unbudgeted_one(app, shown_views):
    mask = _busy_mask()
    item = SkeletonLayerItem()
    item.set_mask(mask)
    item.EXTRACT_BUDGET_MS = 0.0  # one tile per paint
    view = _raster_view(shown_views, item, (900, 700), 2.0, QPointF(500, 380))
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    wanted = {
        (tx, ty)
        for tx in range(int(visible.left()) // 256, int(visible.right()) // 256 + 1)
        for ty in range(int(visible.top()) // 256, int(visible.bottom()) // 256 + 1)
    }
    assert len(wanted) > 3

    for _ in range(200):  # the item asks for the follow-up paints itself
        app.processEvents()
        if set(item.vector_tiles()) >= wanted:
            break
    assert set(item.vector_tiles()) >= wanted

    unbudgeted = _unbudgeted(_in_scene(SkeletonLayerItem()))
    unbudgeted.set_mask(mask)
    origin = (visible.left(), visible.top())
    assert np.array_equal(
        _render(item, 2.0, (600, 400), origin=origin),
        _render(unbudgeted, 2.0, (600, 400), origin=origin),
    )


def test_a_small_repaint_keeps_the_tier_of_the_whole_view(app, shown_views):
    item = _unbudgeted(SkeletonLayerItem())
    item.set_mask(_dots(3000))  # 3000 segments on screen: over the raster budgets
    view = _raster_view(shown_views, item, (560, 640), 4.0)
    assert item.mode_painted() == "raster"

    painted = []
    item.paint = lambda p, o, w=None: (
        painted.append(o.exposedRect) or type(item).paint(item, p, o, w)
    )
    corner = view.mapFromScene(QPointF(20.0, 20.0))
    view.viewport().update(corner.x(), corner.y(), 12, 12)  # a hover-sized repaint
    for _ in range(5):
        app.processEvents()

    assert painted and max(r.width() for r in painted) < 10  # a few dots were repainted
    assert item.mode_painted() == "raster"  # not vectors in a patch of the pixel view

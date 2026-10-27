"""Qt-free image pyramids (app/imaging/pyramid.py, contract C5, spec section 10).

Level k halves level k-1 with ceil sizes: ``area_half`` averages each 2x2 block (photos
and premultiplied edit canvases), ``max_half`` max-pools it (line overlays stay
connected). ``invalidate(rect)`` patches built levels lazily, only where level 0 changed,
and always equals a full rebuild.
"""

import subprocess
import sys

import cv2
import numpy as np
import pytest

from app.imaging.pyramid import ImagePyramid, area_half, level_sizes, max_half
from tests.render_metrics import ring_std, zone_plate

RNG_SEED = 20260929


def _random(shape, seed=RNG_SEED):
    return np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8)


def _box_mean_reference(img: np.ndarray) -> np.ndarray:
    """Hand-computed 2x2 rounded mean with the last odd row / column replicated."""
    a = img.astype(np.int64)
    if a.shape[0] % 2:
        a = np.concatenate([a, a[-1:]], axis=0)
    if a.shape[1] % 2:
        a = np.concatenate([a, a[:, -1:]], axis=1)
    s = a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]
    return ((s + 2) // 4).astype(img.dtype)


def _full_rebuild(image: np.ndarray, reducer=area_half, min_side=32) -> list[np.ndarray]:
    fresh = ImagePyramid(image.copy(), reducer=reducer, min_side=min_side)
    return [fresh.level(k) for k in range(fresh.count)]


# ------------------------------------------------------------------------------- sizes
def test_levels_are_ceil_halves_down_to_min_side():
    pyramid = ImagePyramid(np.zeros((481, 641, 3), np.uint8))
    sizes = [pyramid.level_size(k) for k in range(pyramid.count)]
    assert sizes == [(641, 481), (321, 241), (161, 121), (81, 61), (41, 31), (21, 16)]
    assert [pyramid.level(k).shape for k in range(pyramid.count)] == [
        (481, 641, 3),
        (241, 321, 3),
        (121, 161, 3),
        (61, 81, 3),
        (31, 41, 3),
        (16, 21, 3),
    ]


@pytest.mark.parametrize(
    ("shape", "min_side", "count"),
    [
        ((480, 640), 100, 4),  # 640, 320, 160, 80
        ((32, 32), 32, 1),
        ((10, 20), 32, 1),
        ((1, 33), 32, 2),  # 33 -> 17
        ((4000, 6000), 32, 9),  # ... 47 -> 24
    ],
)
def test_count_stops_at_the_first_level_within_min_side(shape, min_side, count):
    assert ImagePyramid(np.zeros(shape, np.uint8), min_side=min_side).count == count


@pytest.mark.parametrize(
    ("width", "height", "min_side"),
    [(641, 481, 32), (640, 480, 100), (32, 32, 32), (1, 33, 32), (6000, 4000, 32), (5, 3, 0)],
)
def test_level_sizes_are_the_pyramid_sizes_without_building_it(width, height, min_side):
    pyramid = ImagePyramid(np.zeros((height, width), np.uint8), min_side=min_side)
    sizes = tuple(pyramid.level_size(k) for k in range(pyramid.count))
    assert level_sizes(width, height, min_side) == sizes
    assert level_sizes(0, 0, 32) == ((0, 0),)  # no image: one empty level


def test_level_outside_the_pyramid_raises():
    pyramid = ImagePyramid(np.zeros((64, 64), np.uint8))
    with pytest.raises(IndexError):
        pyramid.level(pyramid.count)
    with pytest.raises(IndexError):
        pyramid.level(-1)


def test_level_zero_is_the_image_itself():
    image = _random((40, 50, 3))
    assert ImagePyramid(image).level(0) is image


# ----------------------------------------------------------------------------- reducers
@pytest.mark.parametrize("shape", [(480, 640, 3), (240, 320), (64, 64, 4)])
def test_area_half_of_even_sizes_is_cv2_inter_area(shape):
    image = _random(shape)
    h, w = shape[:2]
    expected = cv2.resize(image, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    assert np.array_equal(area_half(image), expected)


@pytest.mark.parametrize("shape", [(7, 9), (7, 9, 3), (8, 9, 4), (9, 8, 1), (1, 1, 3), (1, 5)])
def test_area_half_is_the_rounded_mean_of_each_2x2_block(shape):
    image = _random(shape)
    out = area_half(image)
    assert out.shape == ((shape[0] + 1) // 2, (shape[1] + 1) // 2) + shape[2:]
    assert np.array_equal(out, _box_mean_reference(image))


def test_every_level_is_area_half_of_the_level_above():
    image = _random((301, 457, 3))
    pyramid = ImagePyramid(image)
    for k in range(1, pyramid.count):
        assert np.array_equal(pyramid.level(k), _box_mean_reference(pyramid.level(k - 1)))


def test_max_half_keeps_the_maximum_of_each_block():
    image = np.array(
        [
            [0, 5, 1, 0, 9],
            [2, 0, 0, 7, 0],
            [0, 0, 3, 0, 0],
        ],
        np.uint8,
    )
    assert max_half(image).tolist() == [[5, 7, 9], [0, 3, 0]]


@pytest.mark.parametrize("shape", [(7, 9), (7, 9, 3), (8, 9, 4), (9, 8, 1), (1, 1, 3), (1, 5)])
@pytest.mark.parametrize("dtype", [np.uint8, np.int32])  # int32: the non-cv2 path
def test_max_half_is_the_maximum_of_each_2x2_block(shape, dtype):
    image = _random(shape).astype(dtype)
    h, w = shape[:2]
    expected = np.empty(((h + 1) // 2, (w + 1) // 2) + shape[2:], dtype)
    for y in range(expected.shape[0]):
        for x in range(expected.shape[1]):
            block = image[2 * y : 2 * y + 2, 2 * x : 2 * x + 2]
            expected[y, x] = block.max(axis=(0, 1))
    out = max_half(image)
    assert out.dtype == dtype and out.flags.c_contiguous
    assert np.array_equal(out, expected)


def test_premultiplied_blocks_average_to_valid_premultiplied_pixels():
    opaque = np.full((2, 2, 4), (40, 80, 120, 255), np.uint8)
    assert area_half(opaque).tolist() == [[[40, 80, 120, 255]]]
    white, clear = (255, 255, 255, 255), (0, 0, 0, 0)
    checker = np.array([[white, clear], [clear, white]], np.uint8)
    half = area_half(checker)[0, 0]
    assert half[3] in (127, 128)
    assert np.all(half[:3] == half[3])
    # colour never exceeds alpha at any level of a random premultiplied image
    rgba = _random((97, 131, 4))
    rgba[..., :3] = np.minimum(rgba[..., :3], rgba[..., 3:])
    pyramid = ImagePyramid(rgba, min_side=4)
    for k in range(pyramid.count):
        level = pyramid.level(k)
        assert np.all(level[..., :3] <= level[..., 3:])


# ----------------------------------------------------------------------- level choice
@pytest.mark.parametrize(
    ("scale", "count", "level"),
    [
        (1.0, 6, 0),
        (0.75, 6, 0),
        (0.5, 6, 1),
        (0.499, 6, 1),
        (0.25, 6, 2),
        (0.1, 6, 3),
        (2.0, 6, 0),
        (20.0, 6, 0),
        (0.01, 6, 5),  # clamped to count - 1
        (0.1, 2, 1),
        (0.1, 1, 0),
    ],
)
def test_level_for_scale(scale, count, level):
    assert ImagePyramid.level_for_scale(scale, count) == level


@pytest.mark.parametrize(
    ("scale", "count", "level"),
    [
        (1.0, 6, 0),
        (2.0, 6, 0),
        (float("inf"), 6, 0),
        (0.999, 6, 1),  # any minification: the next level (a nearest draw would drop rows)
        (0.75, 6, 1),
        (0.5, 6, 1),  # an exact power of two: that level, drawn at one device px per pixel
        (0.5 * (1 + 1e-12), 6, 1),
        (0.499, 6, 2),
        (0.25, 6, 2),
        (0.1, 6, 4),
        (0.01, 6, 5),  # clamped to count - 1
        (0.0, 6, 5),
        (-1.0, 6, 5),
        (0.1, 1, 0),
    ],
)
def test_nearest_level_for_scale_has_a_device_pixel_per_level_pixel(scale, count, level):
    assert ImagePyramid.level_for_scale(scale, count, nearest=True) == level


# ------------------------------------------------------------------------- invalidate
def _paint(image: np.ndarray, rect, rng) -> None:
    x, y, w, h = rect
    image[y : y + h, x : x + w] = rng.integers(0, 256, image[y : y + h, x : x + w].shape)


@pytest.mark.parametrize("shape", [(301, 457, 3), (256, 256, 4), (97, 131), (33, 1, 3)])
def test_invalidate_rect_equals_a_full_rebuild(shape):
    rng = np.random.default_rng(RNG_SEED)
    image = _random(shape)
    pyramid = ImagePyramid(image, min_side=4)
    for k in range(pyramid.count):
        pyramid.level(k)
    h, w = shape[:2]
    rects = [
        (0, 0, 1, 1),  # top-left corner
        (w - 1, h - 1, 1, 1),  # bottom-right corner (odd edges)
        (0, h // 2, w, 1),  # a full row
        (w // 2, 0, 1, h),  # a full column
        (max(0, w - 5), max(0, h - 7), min(w, 5), min(h, 7)),  # touching both far edges
    ]
    for _ in range(12):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        rects.append((x, y, int(rng.integers(1, w - x + 1)), int(rng.integers(1, h - y + 1))))
    for rect in rects:
        _paint(image, rect, rng)
        pyramid.invalidate(rect)
        expected = _full_rebuild(image, min_side=4)
        for k in range(pyramid.count):
            assert np.array_equal(pyramid.level(k), expected[k]), (rect, k)


def test_several_invalidations_before_the_next_access_accumulate():
    rng = np.random.default_rng(RNG_SEED)
    image = _random((200, 300, 3))
    pyramid = ImagePyramid(image, min_side=8)
    for k in range(pyramid.count):
        pyramid.level(k)
    for rect in ((3, 5, 10, 4), (250, 150, 49, 49), (101, 99, 2, 2)):
        _paint(image, rect, rng)
        pyramid.invalidate(rect)
    expected = _full_rebuild(image, min_side=8)
    for k in (pyramid.count - 1, 1, 2):  # any access order
        assert np.array_equal(pyramid.level(k), expected[k])
    for k in range(pyramid.count):
        assert np.array_equal(pyramid.level(k), expected[k])


def test_invalidate_with_only_some_levels_built():
    rng = np.random.default_rng(RNG_SEED)
    image = _random((128, 160))
    pyramid = ImagePyramid(image, min_side=4)
    pyramid.level(2)  # levels 1, 2 built; deeper ones not
    _paint(image, (40, 40, 9, 9), rng)
    pyramid.invalidate((40, 40, 9, 9))
    expected = _full_rebuild(image, min_side=4)
    for k in range(pyramid.count):
        assert np.array_equal(pyramid.level(k), expected[k])


def test_invalidate_everything_rebuilds_from_level_zero():
    image = _random((64, 80, 3))
    pyramid = ImagePyramid(image, min_side=4)
    for k in range(pyramid.count):
        pyramid.level(k)
    image[:] = 255 - image
    pyramid.invalidate()
    expected = _full_rebuild(image, min_side=4)
    for k in range(pyramid.count):
        assert np.array_equal(pyramid.level(k), expected[k])


def test_invalidate_recomputes_only_the_affected_region_lazily():
    inputs: list[tuple[int, ...]] = []

    def spy(img):
        inputs.append(img.shape)
        return area_half(img)

    image = _random((1024, 1024, 4))
    pyramid = ImagePyramid(image, reducer=spy)
    for k in range(pyramid.count):
        pyramid.level(k)
    inputs.clear()
    image[500:510, 700:710] = 7
    pyramid.invalidate((700, 500, 10, 10))
    assert inputs == []  # lazy: nothing is recomputed until a level is read
    pyramid.level(1)
    assert inputs and max(max(s[:2]) for s in inputs) <= 12
    pyramid.level(pyramid.count - 1)
    patched = sum(s[0] * s[1] for s in inputs)
    assert max(max(s[:2]) for s in inputs) <= 12
    assert patched < 0.001 * 1024 * 1024  # a full rebuild reads 1024*1024 + 512*512 + ...
    expected = _full_rebuild(image)
    for k in range(pyramid.count):
        assert np.array_equal(pyramid.level(k), expected[k])


def test_levels_never_read_are_never_built():
    inputs: list[tuple[int, ...]] = []

    def spy(img):
        inputs.append(img.shape)
        return area_half(img)

    pyramid = ImagePyramid(_random((256, 256)), reducer=spy, min_side=8)
    pyramid.level(2)
    assert inputs == [(256, 256), (128, 128)]
    pyramid.invalidate((0, 0, 256, 256))
    pyramid.level(1)
    assert len(inputs) == 3  # level 2 waits until it is read


# ------------------------------------------------------------------------ connectivity
def _components(mask: np.ndarray) -> int:
    count, _ = cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)
    return count - 1  # minus the background


def _random_walk(size: int, steps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mask = np.zeros((size, size), np.uint8)
    x = y = size // 2
    moves = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    for _ in range(steps):
        dx, dy = moves[rng.integers(0, 8)]
        x, y = min(size - 1, max(0, x + dx)), min(size - 1, max(0, y + dy))
        mask[y, x] = 255
    return mask


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_max_half_keeps_thin_lines_connected_at_every_level(seed):
    diagonal = np.zeros((256, 256), np.uint8)
    idx = np.arange(255)
    diagonal[idx + 1, idx] = 255  # a 1 px diagonal off the even grid
    walk = _random_walk(256, 3000, seed)
    for line in (diagonal, walk):
        assert _components(line) == 1
        pyramid = ImagePyramid(line, reducer=max_half, min_side=4)
        for k in range(pyramid.count):
            assert _components(pyramid.level(k)) == 1, k
    # nearest decimation loses or breaks the same lines
    assert _components(diagonal[::2, ::2]) != 1
    assert any(_components(walk[:: 2**k, :: 2**k]) != 1 for k in (1, 2, 3))


# ------------------------------------------------------------------------- aliasing
@pytest.mark.parametrize("zoom", [0.25, 0.125])  # the annulus lies above the output Nyquist
def test_zone_plate_level_placed_bilinear_is_as_alias_free_as_inter_area(zoom):
    plate = zone_plate(1200)
    size = round(1200 * zoom)
    level = ImagePyramid(plate).level(ImagePyramid.level_for_scale(zoom, 99))
    placed = cv2.resize(level, (size, size), interpolation=cv2.INTER_LINEAR)
    reference = cv2.resize(plate, (size, size), interpolation=cv2.INTER_AREA)
    nearest = cv2.resize(plate, (size, size), interpolation=cv2.INTER_NEAREST)
    ideal = ring_std(reference, zoom)
    assert ring_std(placed, zoom) <= 1.1 * ideal
    assert ring_std(nearest, zoom) > 1.5 * ideal  # the metric does see aliasing


# ------------------------------------------------------------------------------ guard
def test_pyramid_and_canvas_layers_package_import_no_qt():
    code = (
        "import sys, app.imaging.pyramid, app.gui.canvas_layers; "
        "print(any(m.startswith('PyQt6') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"

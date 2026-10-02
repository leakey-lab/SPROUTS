"""``MaskStore.read`` reads every mask file exactly as the old read did.

The old read (before any fast path) was a grayscale decode thresholded at
``MASK_THRESHOLD``, and ``MaskFileError`` where the decode returned None. For an RGBA
PNG libpng strips the alpha channel (it is not composited) and turns RGB into grey with
the luma weights; a pixel with R == G == B keeps its value. An editor-saved mask (8-bit
RGBA, R == G == B) and a model mask (8-bit grey) take a faster path that must give
the same grey plane.

These tests write many kinds of mask file (grey, RGBA with every alpha pattern, colour,
RGB, 16-bit, palette, every PNG row filter, odd sizes) and compare the store with a copy
of the old read.
"""

from __future__ import annotations

import io
import os

import cv2
import numpy as np
import pytest
from PIL import Image

import app.masks.store as store_module
from app.io_utils import imread_unicode
from app.masks import MASK_THRESHOLD, MaskFileError, MaskStore, encode_mask_png


def _old_read(path: str) -> np.ndarray:
    """``MaskStore.read`` before the fast path, copied as the reference."""
    gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise MaskFileError(f"cannot decode {path}")
    return gray > MASK_THRESHOLD


def _cv2_png(img: np.ndarray, *params: int) -> bytes:
    ok, buf = cv2.imencode(".png", img, list(params))
    assert ok
    return buf.tobytes()


def _pil_png(img: Image.Image, **kw) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", **kw)
    return out.getvalue()


def _grey_levels(h: int, w: int, seed: int) -> np.ndarray:
    """Random grey levels, half of them close to the threshold."""
    rng = np.random.default_rng(seed)
    grey = rng.integers(0, 256, (h, w), dtype=np.uint8)
    near = rng.random((h, w)) < 0.5
    grey[near] = rng.integers(MASK_THRESHOLD - 3, MASK_THRESHOLD + 4, int(near.sum()))
    return grey


def _rgba_grey(grey: np.ndarray, alpha) -> np.ndarray:
    """R == G == B == ``grey`` with the given alpha (a number or a plane)."""
    a = np.broadcast_to(np.asarray(alpha, np.uint8), grey.shape)
    return np.dstack([grey, grey, grey, a])


def _strokes(h: int, w: int, seed: int) -> np.ndarray:
    """A root-like bool mask: random thick polylines."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w), np.uint8)
    for _ in range(max(1, h * w // 8000)):
        pts = np.cumsum(rng.normal(0, max(w, 2) / 20, (8, 2)), 0) + rng.uniform(0, [w, h])
        cv2.polylines(img, [pts.astype(np.int32)], False, 255, int(rng.integers(1, 9)))
    return img > 0


_FILTERS = {
    "none": cv2.IMWRITE_PNG_FILTER_NONE,
    "sub": cv2.IMWRITE_PNG_FILTER_SUB,
    "up": cv2.IMWRITE_PNG_FILTER_UP,
    "avg": cv2.IMWRITE_PNG_FILTER_AVG,
    "paeth": cv2.IMWRITE_PNG_FILTER_PAETH,
    "all": cv2.IMWRITE_PNG_ALL_FILTERS,
}


def _cases() -> dict[str, tuple[bytes, bool]]:
    """name -> (file bytes, whether the fast path should take it)."""
    cases: dict[str, tuple[bytes, bool]] = {}
    grey = _grey_levels(37, 53, 1)
    alpha = np.random.default_rng(2).integers(0, 256, grey.shape, dtype=np.uint8)

    # Editor-saved masks, at sizes that stress row handling.
    for h, w in ((1, 1), (1, 257), (257, 1), (3, 5), (480, 640)):
        cases[f"editor_{h}x{w}"] = (encode_mask_png(_strokes(h, w, h + w)), True)
    cases["editor_empty"] = (encode_mask_png(np.zeros((40, 60), bool)), True)
    cases["editor_full"] = (encode_mask_png(np.ones((40, 60), bool)), True)

    # R == G == B with every alpha pattern: alpha is ignored, never composited.
    for name, a in {
        "opaque": 255,
        "transparent": 0,
        "half": 128,
        "random": alpha,
        "equal_to_grey": grey,
        "inverse_of_grey": 255 - grey,
    }.items():
        cases[f"rgba_grey_alpha_{name}"] = (_cv2_png(_rgba_grey(grey, a)), True)
    every_level = np.arange(256, dtype=np.uint8).reshape(16, 16)
    cases["rgba_every_grey_level"] = (_cv2_png(_rgba_grey(every_level, alpha[:16, :16])), True)

    # Every PNG row filter and several zlib settings, from OpenCV and from Pillow.
    rgba = _rgba_grey(grey, alpha)
    for name, flag in _FILTERS.items():
        cases[f"rgba_filter_{name}"] = (_cv2_png(rgba, cv2.IMWRITE_PNG_FILTER, flag), True)
    for level in (0, 1, 9):
        cases[f"rgba_cv2_level_{level}"] = (
            _cv2_png(rgba, cv2.IMWRITE_PNG_COMPRESSION, level),
            True,
        )
    for strategy in ("FILTERED", "HUFFMAN_ONLY", "RLE", "FIXED"):
        flag = getattr(cv2, f"IMWRITE_PNG_STRATEGY_{strategy}")
        cases[f"rgba_strategy_{strategy.lower()}"] = (
            _cv2_png(rgba, cv2.IMWRITE_PNG_STRATEGY, flag),
            True,
        )
    pil_rgba = Image.fromarray(rgba, "RGBA")
    for level in (0, 6, 9):
        cases[f"rgba_pillow_level_{level}"] = (_pil_png(pil_rgba, compress_level=level), True)
    cases["rgba_pillow_optimized"] = (_pil_png(pil_rgba, optimize=True), True)

    # Channels that differ: the luma decides, and one channel alone would read otherwise.
    tricky = np.array(
        [
            [255, 150, 150],  # luma about 181: off, though R alone is on
            [180, 230, 230],  # luma about 215: on, though R alone is off
            [150, 255, 150],
            [230, 180, 230],
            [150, 150, 255],
            [230, 230, 180],
        ],
        np.uint8,
    )
    block = np.repeat(np.repeat(tricky[None], 4, 0), 4, 1)  # (4, 24, 3), RGB order
    for name, a in {"opaque": 255, "transparent": 0, "random": alpha[:4, :24]}.items():
        rgba_c = np.dstack([block, np.broadcast_to(np.asarray(a, np.uint8), block.shape[:2])])
        cases[f"rgba_colour_alpha_{name}"] = (_pil_png(Image.fromarray(rgba_c, "RGBA")), False)
    rng = np.random.default_rng(3)
    cases["rgba_random_colours"] = (
        _cv2_png(rng.integers(0, 256, (30, 40, 4), dtype=np.uint8)),
        False,
    )
    for channel, (y, x) in enumerate(((0, 0), (18, 26), (36, 52))):
        nearly = rgba.copy()
        nearly[y, x, channel] ^= 1
        cases[f"rgba_one_pixel_channel_{channel}_differs"] = (_cv2_png(nearly), False)

    # 8-bit grey (model masks) takes the fast path too.
    cases["grey8_cv2"] = (_cv2_png(grey), True)
    cases["grey8_pillow_model_mask"] = (
        _pil_png(Image.fromarray(_strokes(48, 64, 9).astype(np.uint8) * 255, "L")),
        True,
    )
    cases["grey8_pillow_levels"] = (_pil_png(Image.fromarray(grey, "L")), True)

    # Other kinds: the reference decode reads them.
    cases["grey1_bilevel"] = (_pil_png(Image.fromarray(grey > 127)), False)
    cases["grey16"] = (_cv2_png(grey.astype(np.uint16) * 257), False)
    cases["grey_alpha_la"] = (_pil_png(Image.fromarray(np.dstack([grey, alpha]), "LA")), False)
    cases["rgb_grey"] = (_cv2_png(np.dstack([grey] * 3)), False)
    cases["rgb_colour"] = (_pil_png(Image.fromarray(block, "RGB")), False)
    cases["rgba16_grey"] = (_cv2_png(rgba.astype(np.uint16) * 257), False)
    cases["rgb16_grey"] = (_cv2_png(np.dstack([grey] * 3).astype(np.uint16) * 257), False)
    pal = Image.fromarray(np.dstack([grey] * 3), "RGB").quantize(32)
    cases["palette_grey"] = (_pil_png(pal), False)
    cases["palette_colour"] = (_pil_png(Image.fromarray(block, "RGB").quantize(8)), False)
    cases["palette_with_transparency"] = (_pil_png(pal, transparency=0), False)
    cases["not_a_png"] = (b"not a png", False)
    cases["empty_file"] = (b"", False)
    editor = cases["editor_480x640"][0]
    cases["editor_truncated"] = (editor[: len(editor) // 2], False)
    return cases


CASES = _cases()


def _write(tmp_path, data: bytes) -> tuple[MaskStore, str]:
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory, exist_ok=True)
    path = store.path_for("a")
    with open(path, "wb") as fh:
        fh.write(data)
    return store, path


def test_the_filter_cases_really_use_different_filters():
    filtered = {CASES[f"rgba_filter_{name}"][0] for name in _FILTERS}
    assert len(filtered) == len(_FILTERS)


@pytest.mark.parametrize("name", sorted(CASES))
def test_read_is_the_old_read(tmp_path, name):
    store, path = _write(tmp_path, CASES[name][0])
    try:
        expected = _old_read(path)
    except MaskFileError:
        with pytest.raises(MaskFileError):
            store.read("a")
        return
    got = store.read("a")
    assert got.dtype == bool and got.shape == expected.shape
    assert np.array_equal(got, expected)


@pytest.mark.parametrize("name", sorted(CASES))
def test_fast_grey_plane_is_the_reference_grey_plane(name):
    """Stronger than the thresholded mask: the fast plane equals the decoded grey plane."""
    data, fast = CASES[name]
    plane = store_module._decode_plain_png(data)
    assert (plane is not None) == fast
    if plane is not None:
        reference = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        assert plane.dtype == np.uint8 and plane.shape == reference.shape
        assert np.array_equal(plane, reference)


def test_random_editor_masks_read_back_exactly(tmp_path):
    rng = np.random.default_rng(11)
    for i in range(40):
        h, w = (int(v) for v in rng.integers(1, 300, 2))
        on = _strokes(h, w, i) ^ (rng.random((h, w)) < 0.01)
        store, path = _write(tmp_path, encode_mask_png(on))
        got = store.read("a")
        assert np.array_equal(got, on)
        assert np.array_equal(got, _old_read(path))

"""Qt-free Enhance core (app/enhancement.py): fast contrast stretch, settings validation,
decoded-source cache and the per-user defaults store (PE-08, RB-13, UI-19, PE-14, AR-13)."""

from __future__ import annotations

import json
import os
import warnings

import cv2
import numpy as np
import pytest

from app import enhancement as enh
from app.enhancement import (
    DEFAULT_SETTINGS,
    METHOD_CLAHE,
    METHOD_STRETCH,
    EnhanceDefaultsStore,
    EnhanceSettings,
    SourceImageCache,
)
from tests.real_data import real_data_dir


def _reference_stretch(img, lower_percentile=2, upper_percentile=98):
    """The pre-PE-08 float64 np.percentile implementation, kept verbatim as the golden."""
    if len(img.shape) == 3:
        out = []
        for channel in cv2.split(img):
            lower = np.percentile(channel, lower_percentile)
            upper = np.percentile(channel, upper_percentile)
            normalized = np.clip((channel - lower) * 255.0 / (upper - lower), 0, 255)
            out.append(normalized.astype(np.uint8))
        return cv2.merge(out)
    lower = np.percentile(img, lower_percentile)
    upper = np.percentile(img, upper_percentile)
    return np.clip((img - lower) * 255.0 / (upper - lower), 0, 255).astype(np.uint8)


def _images():
    rng = np.random.default_rng(7)
    random = rng.integers(0, 256, (97, 131, 3), dtype=np.uint8)
    low = (rng.normal(100, 6, (120, 90, 3)).clip(0, 255)).astype(np.uint8)
    gradient = np.tile(np.arange(0, 250, dtype=np.uint8), (33, 1))
    return {"random": random, "low_contrast": low, "gray_gradient": gradient}


PAIRS = [(0, 100), (2, 98), (5, 95), (13, 61), (49, 51), (1, 99), (0, 51), (33, 77)]


@pytest.mark.parametrize("pair", PAIRS)
@pytest.mark.parametrize("name", list(_images()))
def test_fast_stretch_is_bit_exact_with_the_float64_reference(name, pair):
    img = _images()[name]
    lo, hi = pair
    expected = _reference_stretch(img, lo, hi)
    assert np.array_equal(enh.contrast_stretch(img, lo, hi), expected)


def test_fast_stretch_matches_on_a_real_image_when_available():
    folder = real_data_dir()
    if folder is None:
        pytest.skip("real data not present")
    path = None
    for root, _dirs, files in os.walk(folder):
        jpgs = [f for f in files if f.lower().endswith((".jpg", ".png"))]
        if jpgs:
            path = os.path.join(root, jpgs[0])
            break
    if path is None:
        pytest.skip("no image found")
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    for lo, hi in PAIRS:
        assert np.array_equal(enh.contrast_stretch(img, lo, hi), _reference_stretch(img, lo, hi))


def test_flat_channel_is_returned_unchanged_without_warnings():
    flat = np.full((10, 12, 3), 128, np.uint8)
    flat[..., 0] = np.arange(12, dtype=np.uint8) * 20  # one channel has contrast
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = enh.contrast_stretch(flat, 2, 98)
    assert np.array_equal(out[..., 1], flat[..., 1])
    assert np.array_equal(out[..., 2], flat[..., 2])
    assert out[..., 0].max() == 255


def test_non_uint8_input_falls_back_without_crashing():
    img = (np.arange(64, dtype=np.uint16).reshape(8, 8) * 1000).astype(np.uint16)
    out = enh.contrast_stretch(img, 2, 98)
    assert out.dtype == np.uint8 and out.shape == img.shape


def test_enhance_dispatches_on_method():
    img = _images()["random"]
    clahe = enh.enhance(img, EnhanceSettings(method=METHOD_CLAHE, clip=30, tile=4))
    ref = enh.clahe(img, 3.0, 4)
    assert np.array_equal(clahe, ref)
    stretched = enh.enhance(img, EnhanceSettings(method=METHOD_STRETCH, lower=5, upper=95))
    assert np.array_equal(stretched, _reference_stretch(img, 5, 95))


# --------------------------------------------------------------------------- settings
def test_settings_legacy_dict_round_trip():
    s = EnhanceSettings(method=METHOD_STRETCH, clip=8, tile=14, lower=3, upper=97)
    data = s.to_dict()
    assert data == {
        "clahe_clip": 8,
        "clahe_tile": 14,
        "contrast_lower": 3,
        "contrast_upper": 97,
        "last_method": METHOD_STRETCH,
        "jpeg_deblock": False,
    }
    assert EnhanceSettings.from_dict(data)[0] == s
    assert s.clip_limit == 0.8


@pytest.mark.parametrize(
    "payload",
    [
        {"clahe_clip": 8.5},
        {"clahe_tile": "14"},
        {"clahe_clip": 10**9},
        {"contrast_lower": [1]},
        {"contrast_upper": None},
        {"last_method": "Sharpen"},
        {"clahe_tile": True},
        {"unknown": 1},
    ],
)
def test_from_dict_never_raises_and_clamps(payload):
    settings, problems = EnhanceSettings.from_dict(payload)
    for field, (lo, hi) in EnhanceSettings.RANGES.items():
        value = getattr(settings, field)
        assert type(value) is int and lo <= value <= hi
    assert settings.method in (METHOD_CLAHE, METHOD_STRETCH)
    assert problems  # every payload above is reported


def test_from_dict_rounds_floats_and_parses_numeric_strings():
    settings, _ = EnhanceSettings.from_dict({"clahe_clip": 8.6, "clahe_tile": "14"})
    assert settings.clip == 9 and settings.tile == 14
    assert EnhanceSettings.from_dict({"clahe_clip": 10**9})[0].clip == 50


def test_lower_must_stay_below_upper():
    settings, _ = EnhanceSettings.from_dict({"contrast_lower": 49, "contrast_upper": 51})
    assert settings.lower < settings.upper


# --------------------------------------------------------------------------- store
def _store(tmp_path, factory=None):
    factory_path = tmp_path / "factory.json"
    if factory is not None:
        factory_path.write_text(json.dumps(factory), encoding="utf-8")
    return EnhanceDefaultsStore(
        user_path=str(tmp_path / "user" / "normalization_defaults.json"),
        factory_path=str(factory_path),
    )


def test_store_seeds_user_file_from_factory_on_first_run(tmp_path):
    factory = {"clahe_clip": 8, "clahe_tile": 14, "last_method": "CLAHE"}
    store = _store(tmp_path, factory)
    loaded = store.load()
    assert (loaded.clip, loaded.tile) == (8, 14)
    user = json.loads((tmp_path / "user" / "normalization_defaults.json").read_text("utf-8"))
    assert user["clahe_clip"] == 8 and user["clahe_tile"] == 14


def test_store_prefers_user_file_and_never_writes_the_factory_file(tmp_path):
    store = _store(tmp_path, {"clahe_clip": 8})
    before = (tmp_path / "factory.json").read_bytes()
    mtime = os.stat(tmp_path / "factory.json").st_mtime_ns
    assert store.save(EnhanceSettings(clip=33, tile=5, method=METHOD_STRETCH))
    assert store.load() == EnhanceSettings(clip=33, tile=5, method=METHOD_STRETCH)
    assert (tmp_path / "factory.json").read_bytes() == before
    assert os.stat(tmp_path / "factory.json").st_mtime_ns == mtime


def test_store_builtin_when_nothing_on_disk(tmp_path):
    assert _store(tmp_path).load() == DEFAULT_SETTINGS


def test_store_survives_a_corrupt_user_file(tmp_path, caplog):
    store = _store(tmp_path, {"clahe_clip": 12})
    user = tmp_path / "user" / "normalization_defaults.json"
    user.parent.mkdir(parents=True)
    user.write_text("{not json", encoding="utf-8")
    assert store.load().clip == 12  # factory fallback
    assert "normalization" in caplog.text.lower()


def test_store_save_failure_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = EnhanceDefaultsStore(
        user_path=str(blocker / "sub" / "n.json"), factory_path=str(tmp_path / "none.json")
    )
    assert store.save(DEFAULT_SETTINGS) is False


def test_default_store_lives_in_the_user_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path))
    store = EnhanceDefaultsStore()
    assert os.path.dirname(store.user_path) == str(tmp_path)


# --------------------------------------------------------------------------- cache
def exif_rotated_jpeg(path, width=80, height=40, orientation=6) -> str:
    """A JPEG stored ``width`` x ``height`` whose EXIF orientation says "rotate on display"."""
    from PIL import Image

    picture = Image.fromarray(np.full((height, width, 3), 128, np.uint8))
    exif = Image.Exif()
    exif[0x0112] = orientation  # 6 = rotate 90 degrees clockwise to display
    picture.save(str(path), exif=exif.tobytes())
    return str(path)


def test_source_cache_keeps_the_stored_geometry_of_an_exif_rotated_jpeg(tmp_path):
    """Qt ignores EXIF orientation (every canvas and saved mask use the stored size), so the
    one decode must too; otherwise the photo would be transposed against the plane (C12)."""
    path = exif_rotated_jpeg(tmp_path / "rotated.jpg")
    rotated = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    assert rotated.shape == (80, 40, 3)  # the tag is real: a plain decode rotates it
    assert SourceImageCache().get(path).shape == (40, 80, 3)


def test_source_cache_hands_out_a_read_only_array(tmp_path):
    """The decode is shared by the photo, Enhance and the pyramid: nobody may edit it."""
    path = tmp_path / "img.png"
    cv2.imencode(".png", np.zeros((4, 5, 3), np.uint8))[1].tofile(str(path))
    image = SourceImageCache().get(str(path))
    assert not image.flags.writeable
    with pytest.raises(ValueError):
        image[0, 0, 0] = 1


def test_source_cache_decodes_once_and_invalidates_on_change(tmp_path, monkeypatch):
    path = tmp_path / "img.png"
    img = np.zeros((4, 5, 3), np.uint8)
    cv2.imencode(".png", img)[1].tofile(str(path))
    calls = []
    real = enh.imread_unicode

    def counting(p, flags=None):
        calls.append(p)
        return real(p, flags)

    monkeypatch.setattr(enh, "imread_unicode", counting)
    cache = SourceImageCache()
    a = cache.get(str(path))
    b = cache.get(str(path))
    assert a is b and len(calls) == 1
    img2 = np.full((6, 5, 3), 9, np.uint8)
    cv2.imencode(".png", img2)[1].tofile(str(path))
    os.utime(path, ns=(1, 1))
    assert cache.get(str(path)).shape == (6, 5, 3)
    assert len(calls) == 2
    assert cache.get(str(tmp_path / "missing.png")) is None


# --------------------------------------------------------------------------- JPEG deblock (O22)
def _gray_step(left=100, right=108, width=24, height=16):
    img = np.full((height, width), left, np.uint8)
    img[:, 8:] = right
    return img


def test_deblock_turns_a_flat_step_below_the_threshold_into_a_ramp():
    out = enh.deblock(_gray_step(100, 108))
    assert out[0, 5:11].tolist() == [101, 102, 103, 105, 106, 107]
    assert out.shape == (16, 24) and out.dtype == np.uint8  # gray in, gray out
    bgr = enh.deblock(cv2.merge([_gray_step(100, 108)] * 3))
    for channel in range(3):
        assert bgr[0, 5:11, channel].tolist() == [101, 102, 103, 105, 106, 107]


@pytest.mark.parametrize("right", [114, 120])  # the step equals / exceeds the threshold 14
def test_deblock_leaves_a_step_at_or_above_the_threshold_alone(right):
    img = _gray_step(100, right)
    assert np.array_equal(enh.deblock(img), img)


def test_deblock_leaves_a_textured_side_alone():
    img = _gray_step(100, 108)
    img[:, 6] = 92  # |p1 - p0| = 8 >= 7
    assert np.array_equal(enh.deblock(img), img)


def test_deblock_flat_image_is_unchanged_and_a_second_pass_changes_nothing():
    flat = np.full((32, 32, 3), (30, 120, 200), np.uint8)
    assert np.array_equal(enh.deblock(flat), flat)
    assert np.array_equal(enh.deblock(enh.deblock(flat)), flat)
    gray = np.full((16, 24), 77, np.uint8)
    assert np.array_equal(enh.deblock(gray), gray)


def test_deblock_filters_chroma_on_a_16_px_grid_only():
    ycc = np.full((16, 40, 3), 128, np.uint8)
    ycc[:, 16:, 1] = 136  # a Cr step of 8 on the 16 px grid
    bgr = cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)

    def cr(a):
        return cv2.cvtColor(a, cv2.COLOR_BGR2YCrCb)[0, :, 1].astype(int)

    before, after = cr(bgr), cr(enh.deblock(bgr))
    assert after[15] > before[15] and after[16] < before[16]
    ycc8 = np.full((16, 40, 3), 128, np.uint8)
    ycc8[:, 8:, 1] = 136  # the same step on the 8 px (luma) grid
    bgr8 = cv2.cvtColor(ycc8, cv2.COLOR_YCrCb2BGR)
    assert np.array_equal(enh.deblock(bgr8), bgr8)


def test_deblock_never_modifies_its_input_and_keeps_shape_and_dtype():
    img = cv2.merge([_gray_step(100, 108)] * 3)
    img.flags.writeable = False  # what SourceImageCache hands out
    out = enh.deblock(img)
    assert out is not img and out.shape == img.shape and out.dtype == np.uint8
    assert np.array_equal(img, cv2.merge([_gray_step(100, 108)] * 3))
    assert enh.deblock(np.zeros((5, 5, 3), np.uint8)).shape == (5, 5, 3)  # too small to filter


def test_deblock_returns_a_copy_of_a_non_uint8_input():
    img = np.full((16, 24), 3.5, np.float32)
    out = enh.deblock(img)
    assert out is not img and np.array_equal(out, img) and out.dtype == np.float32


def test_enhance_runs_the_deblock_first_and_tone_false_skips_clahe():
    img = cv2.merge([_gray_step(100, 108)] * 3)
    on = EnhanceSettings(deblock=True)
    assert np.array_equal(enh.enhance(img, on, tone=False), enh.deblock(img))
    assert np.array_equal(enh.enhance(img, on), enh.clahe(enh.deblock(img), 2.0, 8))
    off = EnhanceSettings()
    assert np.array_equal(enh.enhance(img, off), enh.clahe(img, 2.0, 8))  # unchanged path
    assert enh.enhance(img, off, tone=False) is not img  # always a new array


def test_is_jpeg_path_is_case_insensitive_and_jpeg_only():
    for name in ("a.jpg", "a.JPG", "a.jpeg", "A.Jpeg", "C:/x y/é.jpg"):
        assert enh.is_jpeg_path(name)
    for name in ("a.png", "a.jpg.png", "a", "", None):
        assert not enh.is_jpeg_path(name)


def test_deblock_setting_defaults_off_loads_from_an_old_file_and_round_trips():
    assert DEFAULT_SETTINGS.deblock is False
    old = {"clahe_clip": 12, "clahe_tile": 4, "last_method": "CLAHE"}  # no jpeg_deblock
    loaded, problems = EnhanceSettings.from_dict(old)
    assert loaded.deblock is False and problems == []
    s = EnhanceSettings(deblock=True)
    assert s.to_dict()["jpeg_deblock"] is True
    assert EnhanceSettings.from_dict(s.to_dict())[0] == s
    bad, problems = EnhanceSettings.from_dict({"jpeg_deblock": "yes"})
    assert bad.deblock is False and any("jpeg_deblock" in p for p in problems)
    kept, _ = EnhanceSettings.from_dict({"jpeg_deblock": 1}, base=s)  # an int is not a bool
    assert kept.deblock is True


def _reference_deblock(img, threshold=14.0):
    """The first O22 deblock (index arrays, boolean write-back), kept verbatim as the golden
    of the faster strided-view / masked-copy version (audit S09-01)."""
    if img.dtype != np.uint8 or not (img.ndim == 2 or (img.ndim == 3 and img.shape[2] == 3)):
        return img.copy()
    if img.ndim == 2:
        luma = img.astype(np.float32)
        _reference_deblock_axis(luma, 8, threshold)
        _reference_deblock_axis(luma.T, 8, threshold)
        return np.clip(luma + 0.5, 0, 255).astype(np.uint8)
    base = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    ycc = base.astype(np.float32)
    for c, block in ((0, 8), (1, 16), (2, 16)):
        ch = ycc[:, :, c]
        _reference_deblock_axis(ch, block, threshold)
        _reference_deblock_axis(ch.T, block, threshold)
    new = np.clip(ycc + 0.5, 0, 255).astype(np.uint8)
    changed = (new != base).any(axis=2)
    out = img.copy()
    if changed.any():
        out[changed] = cv2.cvtColor(new, cv2.COLOR_YCrCb2BGR)[changed]
    return out


def _reference_deblock_axis(ch, block, t):
    xs = np.arange(block, ch.shape[1] - 2, block)  # q0 columns
    if xs.size == 0:
        return
    p2, p1, p0 = ch[:, xs - 3], ch[:, xs - 2], ch[:, xs - 1]
    q0, q1, q2 = ch[:, xs], ch[:, xs + 1], ch[:, xs + 2]
    d = q0 - p0
    m = (np.abs(d) < t) & (np.abs(p1 - p0) < t / 2) & (np.abs(q1 - q0) < t / 2)
    d = np.where(m, d, np.float32(0))
    ch[:, xs - 3] = p2 + d / 8
    ch[:, xs - 2] = p1 + d / 4
    ch[:, xs - 1] = p0 + 3 * d / 8
    ch[:, xs] = q0 - 3 * d / 8
    ch[:, xs + 1] = q1 - d / 4
    ch[:, xs + 2] = q2 - d / 8


def _deblock_content(kind, h, w, rng):
    if kind == "flat":
        return np.full((h, w, 3), 97, np.uint8)
    img = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    if kind == "noise":
        return img
    if min(h, w) >= 3:
        img = cv2.GaussianBlur(img, (0, 0), 2.5)
    if kind == "blocky":  # 8 x 8 steps below the threshold: the filter's main case
        off = rng.integers(-7, 8, (-(-h // 8), -(-w // 8), 3)).astype(np.int16)
        off = np.repeat(np.repeat(off, 8, 0), 8, 1)[:h, :w]
        img = np.clip(img.astype(np.int16) + off, 0, 255).astype(np.uint8)
    elif kind == "jpeg":
        _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 30])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


def _deblock_layouts(img):
    """The same pixels as C, read-only (what SourceImageCache hands out), Fortran and strided."""
    read_only = img.copy()
    read_only.flags.writeable = False
    yield "C", img
    yield "read-only", read_only
    yield "fortran", np.asfortranarray(img)
    yield "strided", img[::-1, ::2]


def _assert_deblock_matches_reference(img, threshold, case):
    before = img.copy()
    out = enh.deblock(img, threshold)
    ref = _reference_deblock(img, threshold)
    assert out.dtype == ref.dtype and out.shape == ref.shape, case
    assert np.array_equal(out, ref), case
    assert out.flags.c_contiguous == ref.flags.c_contiguous and out.flags.writeable, case
    assert not np.shares_memory(out, img), case
    assert np.array_equal(img, before), case  # the input is never modified


@pytest.mark.parametrize("kind", ["noise", "smooth", "blocky", "jpeg", "flat"])
def test_deblock_is_bit_exact_with_the_reference(kind):
    rng = np.random.default_rng(22)
    # block + 2 (no boundary yet) and block + 3 (the first one just fits), for both grids
    sides = (10, 11, 18, 19)
    small = [(1, 1)] + [(h, w) for h in sides for w in sides]
    for h, w in small + [(480, 640)]:
        thresholds = (6.0, 14.0, 30.0, 0.0, float("inf")) if (h, w) in small else (14.0,)
        bgr = _deblock_content(kind, h, w, rng)
        for colour, img in (("bgr", bgr), ("gray", np.ascontiguousarray(bgr[..., 1]))):
            for layout, arr in _deblock_layouts(img):
                for threshold in thresholds:
                    case = (kind, h, w, colour, layout, threshold)
                    _assert_deblock_matches_reference(arr, threshold, case)


def test_deblock_is_bit_exact_with_the_reference_on_the_example_photos():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    examples = os.path.join(root, "docs", "examples")
    names = sorted(f for f in os.listdir(examples) if f.lower().endswith(".jpg"))
    assert names, "docs/examples has no photos"
    for name in names:
        img = enh.SourceImageCache().get(os.path.join(examples, name))
        for threshold in (6.0, 14.0, 30.0):
            _assert_deblock_matches_reference(img, threshold, (name, threshold))


def test_deblock_core_imports_no_torch_or_qt():
    import subprocess
    import sys

    from tests.test_layering_contract import ROOT

    code = (
        "import sys, numpy as np; from app.enhancement import deblock; "
        "deblock(np.zeros((16, 24, 3), np.uint8)); "
        "print('torch' in sys.modules, any(m.startswith('PyQt6') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == ["False", "False"]

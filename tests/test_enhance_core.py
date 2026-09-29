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
    folder = "D:/2024 EF image data"
    if not os.path.isdir(folder):
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

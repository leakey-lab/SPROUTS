"""PV-09 / PV-17: Growth-Lines hover thumbnails.

Thumbnails are small JPEGs (not base64 PNGs of ~75 KiB each), kept in a bounded
LRU, served by URL from the dashboard's own Flask server, and the image index is
parsed lazily with the shared ``app.naming`` parser.
"""

from __future__ import annotations

import io
import os

import numpy as np
import pandas as pd
import pytest
from flask import Flask
from PIL import Image

from app.visualization import dash_image_utils as diu
from tests.viz_dash_helpers import FakeImageManager

NAMES = {
    "24EFCAM1_T001_L011_2024.07.18_111618_001_SMM": (1, 11, "2024.07.18"),
    "24EFCAM1_T012_L045_2024.08.07_101500_001_SMM": (12, 45, "2024.08.07"),
    # A project prefix is not a tube token (shared app.naming rule, FR-15).
    "PLOT3_T002_L007_2024.08.26_090000": (2, 7, "2024.08.26"),
}


def _camera_jpeg(path, size=(640, 480), seed=0) -> str:
    """A camera-like 640x480 JPEG: smooth background plus a few bright 'roots'."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0 : size[1], 0 : size[0]]
    base = (60 + 40 * np.sin(x / 57.0) + 30 * np.cos(y / 41.0)).astype(np.float32)
    rgb = np.stack([base * 0.9, base * 0.75, base * 0.55], axis=-1)
    for cx in rng.integers(2, size[0] - 2, 6):
        rgb[:, cx - 2 : cx + 2, :] = 220
    rgb += rng.normal(0, 6, rgb.shape)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path, quality=92)
    return str(path)


@pytest.fixture
def images(tmp_path):
    """name -> path of real JPEG files for every NAMES entry."""
    return {name: _camera_jpeg(tmp_path / f"{name}.jpg", seed=i) for i, name in enumerate(NAMES)}


# ---- index (PV-17) -----------------------------------------------------------


def test_index_keys_follow_the_shared_name_parser(images):
    index = diu.build_available_images_map(FakeImageManager({**images, "calibration_card": "x"}))
    assert set(index) == set(NAMES.values())
    for name, key in NAMES.items():
        assert index[key] == images[name]
    assert (99, 1, "2024.07.18") not in index


def test_index_parses_names_only_when_first_used(images, monkeypatch):
    calls = []
    real = diu.parse_image_name
    monkeypatch.setattr(diu, "parse_image_name", lambda n: calls.append(n) or real(n))

    index = diu.build_available_images_map(FakeImageManager(images))
    assert calls == []  # opening the dashboard no longer parses every image name
    assert (1, 11, "2024.07.18") in index
    assert len(calls) == len(images)
    assert len(index) == len(NAMES)
    assert len(calls) == len(images)  # parsed once


def test_index_can_be_parsed_ahead_on_a_background_thread(images, monkeypatch):
    # The first Growth Lines view waited ~95 ms for 47k names; a dashboard now
    # parses them while its page loads (PKG-VIZ).
    import threading

    threads = []
    real = diu.parse_image_name

    def spy(name):
        threads.append(threading.current_thread())
        return real(name)

    monkeypatch.setattr(diu, "parse_image_name", spy)
    index = diu.build_available_images_map(FakeImageManager(images))

    worker = index.parse_in_background()
    worker.join(5)

    assert not worker.is_alive()
    assert threads and all(t is worker for t in threads)
    assert (1, 11, "2024.07.18") in index
    assert len(threads) == len(images)  # parsed once, by the background thread


def test_dashboard_parses_its_image_index_in_the_background(tmp_path, monkeypatch):
    from tests.viz_dash_helpers import build_length_app

    started = []
    monkeypatch.setattr(diu.ImageIndex, "parse_in_background", lambda self: started.append(self))

    dashboard = build_length_app(tmp_path)

    assert started == [dashboard.available_images]


def test_index_without_images_is_empty():
    assert len(diu.build_available_images_map(None)) == 0
    assert len(diu.build_available_images_map(FakeImageManager({}))) == 0


# ---- thumbnails (PV-09) ------------------------------------------------------


def test_thumbnail_is_a_small_jpeg_with_the_image_aspect(tmp_path):
    data = diu.encode_thumbnail_jpeg(_camera_jpeg(tmp_path / "a.jpg"))
    assert data[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert len(data) < 25_000
    with Image.open(io.BytesIO(data)) as thumb:
        assert thumb.size == (256, 192)


def test_png_and_grayscale_sources_are_encoded_too(tmp_path):
    png = tmp_path / "g.png"
    Image.fromarray(np.full((300, 400), 128, np.uint8), mode="L").save(png)
    with Image.open(io.BytesIO(diu.encode_thumbnail_jpeg(str(png)))) as thumb:
        assert thumb.mode == "RGB" and thumb.size == (256, 192)


@pytest.mark.parametrize("content", [b"not an image", None])
def test_unreadable_or_missing_file_gives_no_thumbnail(tmp_path, content):
    path = tmp_path / "broken.jpg"
    if content is not None:
        path.write_bytes(content)
    assert diu.encode_thumbnail_jpeg(str(path)) == b""


def test_thumbnail_cache_evicts_the_least_recently_used(tmp_path):
    paths = {(1, p, "2024.07.18"): _camera_jpeg(tmp_path / f"{p}.jpg", seed=p) for p in range(4)}
    cache = diu.ThumbnailCache(paths, capacity=2)
    a, b, c, d = sorted(paths)
    cache.jpeg(a)
    cache.jpeg(b)
    cache.jpeg(a)  # a is now the most recently used
    cache.jpeg(c)  # evicts b
    assert len(cache) == 2
    assert cache.cached_keys() == [a, c]
    cache.jpeg(d)
    assert cache.cached_keys() == [c, d]


def test_thumbnail_cache_serves_unknown_keys_as_empty(tmp_path):
    cache = diu.ThumbnailCache({}, capacity=4)
    assert cache.jpeg((1, 1, "2024.07.18")) == b""
    assert cache.data_url((1, 1, "2024.07.18")) == ""


def test_get_encoded_image_keeps_its_contract_with_jpeg_data(images):
    index = diu.build_available_images_map(FakeImageManager(images))
    url = diu.get_encoded_image(index, 1, 11, pd.Timestamp("2024-07-18"))
    assert url.startswith("data:image/jpeg;base64,")
    assert diu.get_encoded_image(index, 1, 12, pd.Timestamp("2024-07-18")) == ""
    assert diu.get_encoded_image(index, 1, 11, "not a date") == ""


# ---- thumbnail route (PV-09) ---------------------------------------------------


@pytest.fixture
def served(images):
    app = Flask(__name__)
    index = diu.build_available_images_map(FakeImageManager(images))
    server = diu.ThumbnailServer(index.thumbnails)
    server.register(app)
    return server, app.test_client()


def test_thumbnail_route_serves_cacheable_jpeg(served):
    server, client = served
    resp = client.get(server.url(12, 45, "2024.08.07"))
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data[:2] == b"\xff\xd8"
    assert "max-age" in resp.headers["Cache-Control"]


@pytest.mark.parametrize(
    "tube, position, date", [(12, 46, "2024.08.07"), (12, 45, "2024-08-07"), (12, 45, "..")]
)
def test_thumbnail_route_rejects_unknown_or_malformed_keys(served, tube, position, date):
    server, client = served
    assert client.get(server.url(tube, position, date)).status_code == 404


def test_each_dashboard_instance_gets_its_own_thumbnail_urls(images):
    """URLs carry a per-instance token, so the browser cache can never show a
    thumbnail from a previously opened project on the same port."""
    cache = diu.build_available_images_map(FakeImageManager(images)).thumbnails
    first, second = diu.ThumbnailServer(cache), diu.ThumbnailServer(cache)
    assert first.url(1, 11, "2024.07.18") != second.url(1, 11, "2024.07.18")
    assert os.path.basename(first.url(1, 11, "2024.07.18")) == "2024.07.18.jpg"

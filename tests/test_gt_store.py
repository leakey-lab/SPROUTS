"""
Tests for the GT palette-PNG store (``app/gt/store.py``).

Covers the write/read round trip (ids, ``reviewed``, ``file_classes``), the
palette + transparency written into the PNG, path derivation (multi-dot
stems, non-ASCII paths), atomic writes, error handling for the wrong PNG
mode or a corrupt metadata chunk, ``align_to_registry`` (spec §1.4), and
``present_keys``/``is_complete``.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from app.config import GT_META_CHUNK
from app.gt.registry import default_registry
from app.gt.store import (
    GtFileError,
    Tracing,
    align_to_registry,
    empty_tracing,
    gt_path,
    is_complete,
    present_keys,
    read_tracing,
    write_tracing,
)


def _ids_with_values(shape, values):
    """A deterministic (h, w) uint8 array whose pixels cycle through ``values``."""
    h, w = shape
    flat = np.array([values[i % len(values)] for i in range(h * w)], dtype=np.uint8)
    return flat.reshape(h, w)


def test_round_trip_ids_and_chunk(tmp_path):
    ids = _ids_with_values((30, 40), [0, 1, 2, 3, 255])
    tracing = Tracing(ids=ids, reviewed=frozenset({"root", "nodule"}))
    registry = default_registry()
    path = str(tmp_path / "gt" / "a.png")

    write_tracing(path, tracing, registry)
    result = read_tracing(path)

    assert result is not None
    assert np.array_equal(result.ids, ids)
    assert result.reviewed == frozenset({"root", "nodule"})
    assert result.file_classes == {1: "root", 2: "nodule", 3: "root_tip", 255: "ignore"}


def test_palette_matches_registry(tmp_path):
    ids = _ids_with_values((10, 10), [0, 1, 255])
    tracing = Tracing(ids=ids)
    registry = default_registry()
    path = str(tmp_path / "gt" / "a.png")

    write_tracing(path, tracing, registry)

    with Image.open(path) as im:
        im.load()
        assert im.mode == "P"
        palette = im.getpalette()
        assert palette[3:6] == [0x5F, 0xD6, 0xA0]  # root
        ignore = registry.by_key("ignore")
        ig_id = ignore.id
        expected_ignore_rgb = [int(ignore.color[i : i + 2], 16) for i in (1, 3, 5)]
        assert palette[3 * ig_id : 3 * ig_id + 3] == expected_ignore_rgb
        assert im.info["transparency"] == 0


def test_gt_path_multi_dot_and_unicode(tmp_path):
    src = os.path.join("somedir", "plötz.T1.L2.png")
    assert gt_path(src) == os.path.join("somedir", "gt", "plötz.T1.L2.png")

    project_dir = tmp_path / "ünï"
    project_dir.mkdir()
    image_path = str(project_dir / "a.b.png")
    path = gt_path(image_path)
    assert path == str(project_dir / "gt" / "a.b.png")

    ids = _ids_with_values((5, 5), [0, 1])
    registry = default_registry()
    write_tracing(path, Tracing(ids=ids), registry)
    result = read_tracing(path)
    assert result is not None
    assert np.array_equal(result.ids, ids)


def test_atomic_no_tmp_left(tmp_path):
    path = str(tmp_path / "gt" / "a.png")
    write_tracing(path, empty_tracing(8, 8), default_registry())
    assert (tmp_path / "gt" / "a.png").exists()
    assert not (tmp_path / "gt" / "a.png.tmp").exists()


def test_failed_write_removes_tmp_and_keeps_old_file(tmp_path, monkeypatch):
    path = str(tmp_path / "gt" / "a.png")
    write_tracing(path, empty_tracing(8, 8), default_registry())
    before = open(path, "rb").read()

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    ids = np.ones((8, 8), np.uint8)
    with pytest.raises(OSError, match="disk full"):
        write_tracing(path, Tracing(ids=ids), default_registry())
    assert not (tmp_path / "gt" / "a.png.tmp").exists()
    assert open(path, "rb").read() == before


def test_empty_tracing_still_written(tmp_path):
    path = str(tmp_path / "gt" / "a.png")
    write_tracing(path, empty_tracing(6, 7), default_registry())
    result = read_tracing(path)
    assert result is not None
    assert result.ids.shape == (6, 7)
    assert np.all(result.ids == 0)


def test_missing_returns_none(tmp_path):
    path = str(tmp_path / "gt" / "does_not_exist.png")
    assert read_tracing(path) is None


def test_rgb_file_raises(tmp_path):
    path = tmp_path / "rgb.png"
    Image.new("RGB", (5, 5), color=(1, 2, 3)).save(path, format="PNG")
    with pytest.raises(GtFileError):
        read_tracing(str(path))


def test_16bit_file_raises(tmp_path):
    path = tmp_path / "i16.png"
    Image.new("I;16", (5, 5)).save(path, format="PNG")
    with pytest.raises(GtFileError):
        read_tracing(str(path))


def test_bad_chunk_json_raises(tmp_path):
    path = tmp_path / "bad_chunk.png"
    ids = np.zeros((5, 5), dtype=np.uint8)
    img = Image.fromarray(ids, mode="P")
    img.putpalette([0, 0, 0] * 256)
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, "{not valid json")
    img.save(path, format="PNG", pnginfo=info)
    with pytest.raises(GtFileError):
        read_tracing(str(path))


def test_file_without_chunk_reads_empty_meta(tmp_path):
    path = tmp_path / "no_chunk.png"
    ids = _ids_with_values((5, 5), [0, 1])
    Image.fromarray(ids, mode="L").save(path, format="PNG")
    result = read_tracing(str(path))
    assert result is not None
    assert result.reviewed == frozenset()
    assert result.file_classes == {}


def test_align_same_project_noop(tmp_path):
    ids = _ids_with_values((10, 10), [0, 1, 2, 3, 255])
    registry = default_registry()
    path = str(tmp_path / "gt" / "a.png")
    write_tracing(path, Tracing(ids=ids), registry)
    tracing = read_tracing(path)

    aligned, warnings, changed = align_to_registry(tracing, registry)

    assert changed is False
    assert warnings == []
    assert np.array_equal(aligned.ids, ids)


def test_align_unknown_key_to_ignore():
    ids = _ids_with_values((4, 4), [1, 2])
    tracing = Tracing(ids=ids, file_classes={1: "root", 2: "weed"})
    registry = default_registry()

    aligned, warnings, changed = align_to_registry(tracing, registry)

    assert changed is True
    assert len(warnings) == 1
    assert "weed" in warnings[0]
    assert set(np.unique(aligned.ids).tolist()) == {1, 255}
    assert np.all(aligned.ids[ids == 2] == 255)
    assert np.all(aligned.ids[ids == 1] == 1)


def test_align_remaps_by_key():
    ids = _ids_with_values((4, 4), [1])
    tracing = Tracing(ids=ids, file_classes={1: "nodule"})
    registry = default_registry()

    aligned, warnings, changed = align_to_registry(tracing, registry)

    assert changed is True
    assert warnings == []
    assert np.all(aligned.ids == 2)


def test_align_id_missing_from_map():
    ids = _ids_with_values((4, 4), [1, 7])
    tracing = Tracing(ids=ids, file_classes={})
    registry = default_registry()

    aligned, warnings, changed = align_to_registry(tracing, registry)

    assert changed is True
    assert len(warnings) == 1
    assert "7" in warnings[0]
    assert np.all(aligned.ids[ids == 7] == 255)
    assert np.all(aligned.ids[ids == 1] == 1)


def test_present_keys_and_is_complete():
    ids = _ids_with_values((4, 4), [1, 255])
    tracing = Tracing(ids=ids)
    registry = default_registry()

    assert present_keys(tracing, registry) == {"root"}
    assert is_complete(tracing, registry) is False

    complete = Tracing(ids=ids, reviewed=frozenset({"root", "nodule", "root_tip"}))
    assert is_complete(complete, registry) is True

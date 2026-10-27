"""The export's id remap and pixel counts give the results of their original code.

``remap_ids`` was ``lut[ids].astype(np.uint8)`` and the manifest counts ``np.bincount``; a
class was listed in ``classes_present`` when ``present_keys`` found it. They are now a
``cv2.LUT`` lookup, the store's exact ``histogram`` and the counts themselves (about 10x
faster each, 2026-10-26). These tests pin the results against the original code, copied here
verbatim, on random planes of every shape: strided views, empty planes and planes over
``2**24`` pixels (counted by ``np.bincount``) included.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app.config import GT_BACKGROUND_ID, GT_IGNORE_ID
from app.gt import Tracing, align_to_registry, default_registry, present_keys, store
from app.gt.export import _export_stem, _pixel_counts, remap_ids
from app.gt.store import histogram

_VALUES = np.array([0, 1, 2, 3, 4, 5, 9, 200, 254, 255], np.uint8)


def _reference_remap(ids, registry):
    lut = np.full(256, GT_IGNORE_ID, dtype=np.uint8)
    lut[GT_BACKGROUND_ID] = GT_BACKGROUND_ID
    for new_id, c in enumerate(registry.labelled(), start=1):
        lut[c.id] = new_id
    return lut[ids].astype(np.uint8)


def _reference_counts(remapped, registry):
    counts = np.bincount(remapped.ravel(), minlength=GT_IGNORE_ID + 1)
    row = {"px_background": int(counts[GT_BACKGROUND_ID])}
    for new_id, c in enumerate(registry.labelled(), start=1):
        row[f"px_{c.key}"] = int(counts[new_id])
    row["px_ignore"] = int(counts[GT_IGNORE_ID])
    return row


def _registries():
    base = default_registry()
    five = base.add(key="hypha", name="Hypha", color="#3fa7ff").add(
        key="dead", name="Dead", color="#111111"
    )
    return [base, five, five.retire("nodule"), base.retire("root").retire("root_tip")]


def _planes(rng):
    """Random id planes: C-ordered, strided, transposed, tiny and empty ones."""
    big = rng.choice(_VALUES, size=(97, 131))
    yield big
    yield big[::3, 1::2]  # a strided view
    yield big.T  # Fortran-ordered
    yield rng.choice(_VALUES, size=(1, 1))
    yield rng.choice(_VALUES, size=(1, 9))
    yield rng.choice(_VALUES, size=(9, 1))
    yield np.zeros((0, 5), np.uint8)
    yield np.zeros((480, 640), np.uint8)


@pytest.mark.parametrize("seed", range(6))
def test_remap_and_counts_match_the_original(seed):
    rng = np.random.default_rng(seed)
    for registry in _registries():
        for ids in _planes(rng):
            expected = _reference_remap(ids, registry)
            remapped = remap_ids(ids, registry)
            assert remapped.dtype == np.uint8 and remapped.shape == ids.shape
            assert np.array_equal(remapped, expected)
            assert not np.shares_memory(remapped, ids)
            assert _pixel_counts(remapped, registry) == _reference_counts(expected, registry)


def test_other_shapes_and_types_take_the_original_lookup():
    registry = default_registry()
    rng = np.random.default_rng(7)
    for ids in (
        rng.choice(_VALUES, size=50),  # 1-D
        rng.choice(_VALUES, size=(6, 7, 2)),  # 3-D
        rng.choice(_VALUES, size=(6, 7)).astype(np.int64),  # not uint8
    ):
        remapped = remap_ids(ids, registry)
        assert remapped.dtype == np.uint8 and remapped.shape == ids.shape
        assert np.array_equal(remapped, _reference_remap(ids, registry))


def test_counts_over_two_to_the_24_pixels_stay_exact():
    """Over 2**24 pixels cv2.calcHist's float32 counts would round: np.bincount counts them."""
    ids = np.zeros((4097, 4097), np.uint8)  # 16,785,409 px
    ids[:2000, :] = 1
    ids[-3:, :17] = GT_IGNORE_ID
    registry = default_registry()
    assert ids.size > store._EXACT_HIST_MAX
    assert _pixel_counts(remap_ids(ids, registry), registry) == _reference_counts(ids, registry)


@pytest.mark.parametrize("limit", [0, 50, 1 << 24])
def test_histogram_is_exact_on_both_paths(monkeypatch, limit):
    monkeypatch.setattr(store, "_EXACT_HIST_MAX", limit)
    rng = np.random.default_rng(limit)
    for ids in _planes(rng):
        expected = np.bincount(ids.ravel(), minlength=256)
        counts = histogram(ids)
        assert counts.dtype == np.int64 and counts.shape == (256,)
        assert np.array_equal(counts, expected)


@pytest.mark.parametrize("seed", range(4))
def test_the_manifest_row_lists_the_classes_present_as_before(seed, tmp_path):
    """``classes_present`` from the counts equals the original ``present_keys`` test, for
    files with unknown, retired and Ignore ids."""
    rng = np.random.default_rng(100 + seed)
    h, w = 41, 57
    photo = str(tmp_path / "photo.png")
    Image.new("RGB", (w, h)).save(photo)
    for i, registry in enumerate(_registries()):
        for _ in range(3):
            values = rng.choice(_VALUES, size=int(rng.integers(1, len(_VALUES))), replace=False)
            ids = rng.choice(values, size=(h, w)).astype(np.uint8)
            aligned, _warnings, _changed = align_to_registry(Tracing(ids), registry)
            folder = tmp_path / f"out{i}_{rng.integers(1 << 30)}"
            (folder / "masks").mkdir(parents=True)
            (folder / "images").mkdir()
            row = _export_stem(
                "plot_T1_L1",
                photo,
                aligned.ids,
                registry,
                str(folder / "masks"),
                str(folder / "images"),
            )
            present = present_keys(aligned, registry)
            expected = ";".join(c.key for c in registry.labelled() if c.key in present)
            assert row["classes_present"] == expected
            remapped = _reference_remap(aligned.ids, registry)
            counts = _reference_counts(remapped, registry)
            assert {k: v for k, v in row.items() if k.startswith("px_")} == counts

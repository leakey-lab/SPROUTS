"""``app.gt.store`` gives the results of its original ``np.unique`` code (a guard for speedups).

Trace GT opens a tracing through ``align_to_registry`` and saves it through
``write_tracing``; the dataset export calls both and ``present_keys`` for every image. At
2048 x 1536 their ``np.unique`` sorts cost about 20 ms each (PKG-GT, 2026-09-30), so they
may be replaced by histograms: these tests pin the results (ids, warnings, the changed flag,
present keys and the written PNG bytes) against the original code, copied here verbatim.
"""

import io
import json

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from app.config import GT_BACKGROUND_ID, GT_IGNORE_ID, GT_META_CHUNK, GT_UNKNOWN_COLOR
from app.gt import Tracing, align_to_registry, default_registry, present_keys, write_tracing
from app.gt.store import _hex_to_rgb


def _reference_align(tracing, registry):
    warnings = []
    lut = np.arange(256, dtype=np.uint8)
    for old_id in np.unique(tracing.ids).tolist():
        if old_id == GT_BACKGROUND_ID:
            continue
        key = tracing.file_classes.get(old_id)
        if key is not None:
            try:
                lut[old_id] = registry.by_key(key).id
            except KeyError:
                lut[old_id] = GT_IGNORE_ID
                warnings.append(f"File id {old_id} has unknown key {key!r}; mapped to Ignore")
        elif registry.by_id(old_id) is not None:
            lut[old_id] = old_id
        else:
            lut[old_id] = GT_IGNORE_ID
            warnings.append(
                f"File id {old_id} has no key mapping and is unknown to the registry; "
                "mapped to Ignore"
            )
    new_ids = lut[tracing.ids]
    changed = not np.array_equal(new_ids, tracing.ids)
    return new_ids.astype(np.uint8), warnings, changed


def _reference_png(tracing, registry):
    ids = np.ascontiguousarray(tracing.ids, dtype=np.uint8)
    palette = [0, 0, 0] * 256
    unknown = _hex_to_rgb(GT_UNKNOWN_COLOR)
    for i in np.unique(ids).tolist():
        if i != GT_BACKGROUND_ID:
            palette[3 * i : 3 * i + 3] = unknown
    for c in registry.classes:
        palette[3 * c.id : 3 * c.id + 3] = _hex_to_rgb(c.color)
    image = Image.fromarray(ids, mode="P")
    image.putpalette(palette)
    meta = {
        "v": 1,
        "classes": {str(c.id): c.key for c in registry.classes},
        "reviewed": sorted(tracing.reviewed),
    }
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, json.dumps(meta))
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info, transparency=GT_BACKGROUND_ID)
    return buf.getvalue()


def _registry():
    return default_registry().add(key="hypha", name="Hypha", color="#3fa7ff").retire("nodule")


def _random_tracing(rng, h=41, w=57):
    values = np.array([0, 1, 2, 3, 4, 5, 9, 200, 255], np.uint8)
    ids = rng.choice(values, size=(h, w)).astype(np.uint8)
    keys = ["root", "nodule", "root_tip", "hypha", "weed", "ignore", None]
    file_classes = {}
    for v in values.tolist():
        key = keys[int(rng.integers(0, len(keys)))]
        if v and key is not None:
            file_classes[v] = key
    return Tracing(ids, frozenset({"root"}), file_classes)


@pytest.mark.parametrize("seed", range(12))
def test_align_to_registry_matches_the_original(seed):
    rng = np.random.default_rng(seed)
    registry = _registry()
    tracing = _random_tracing(rng)
    if seed % 3 == 0:
        tracing = Tracing(tracing.ids, tracing.reviewed, {})  # no id -> key map
    before = tracing.ids.copy()
    expected_ids, expected_warnings, expected_changed = _reference_align(tracing, registry)
    aligned, warnings, changed = align_to_registry(tracing, registry)
    assert np.array_equal(aligned.ids, expected_ids) and aligned.ids.dtype == np.uint8
    assert warnings == expected_warnings and changed == expected_changed
    assert not np.shares_memory(aligned.ids, tracing.ids)  # a new array either way
    assert np.array_equal(tracing.ids, before)  # the input is left alone


def test_align_to_registry_of_an_aligned_tracing_changes_nothing():
    registry = default_registry()
    ids = np.zeros((30, 40), np.uint8)
    ids[5:9, 5:9] = 2
    ids[20:25, 3:30] = 255
    aligned, warnings, changed = align_to_registry(
        Tracing(ids, frozenset(), {2: "nodule"}), registry
    )
    assert not changed and warnings == [] and np.array_equal(aligned.ids, ids)


@pytest.mark.parametrize("seed", range(8))
def test_present_keys_matches_the_original(seed):
    rng = np.random.default_rng(100 + seed)
    registry = _registry()
    tracing = _random_tracing(rng)
    present = set(np.unique(tracing.ids).tolist())
    expected = {c.key for c in registry.classes if not c.is_ignore and c.id in present}
    assert present_keys(tracing, registry) == expected
    assert present_keys(Tracing(np.zeros((0, 0), np.uint8)), registry) == set()


@pytest.mark.parametrize("seed", range(6))
def test_write_tracing_writes_the_original_bytes(seed, tmp_path):
    rng = np.random.default_rng(200 + seed)
    registry = _registry()
    tracing = _random_tracing(rng)
    path = tmp_path / "gt" / "plot.png"
    write_tracing(str(path), tracing, registry)
    assert path.read_bytes() == _reference_png(tracing, registry)

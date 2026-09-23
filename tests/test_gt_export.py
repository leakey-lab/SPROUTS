"""
Tests for the dataset exporter (``app/gt/export.py``).

Covers ``remap_ids`` (background/active/ignore/retired/unknown mapping),
``assign_splits`` (determinism, tube grouping, non-empty splits, val_fraction
0.0), and ``export_dataset`` (only-complete filtering, output layout,
``classes.json``/``manifest.csv`` contents, non-empty ``out_dir`` guard, a
corrupt GT file landing in ``errors`` without aborting the run, and source
files being left untouched).
"""

from __future__ import annotations

import csv
import json
import os

import numpy as np
import pytest
from PIL import Image

from app.config import GT_IGNORE_ID
from app.gt.export import ExportError, ExportOptions, assign_splits, export_dataset, remap_ids
from app.gt.registry import default_registry
from app.gt.store import Tracing, gt_path, write_tracing

# Six stems: three tubes (T1-T3), two positions each (L1/L2).
_STEMS = [
    "plot_T1_L1_2024.01.01_120000",
    "plot_T1_L2_2024.01.01_120000",
    "plot_T2_L1_2024.01.01_120000",
    "plot_T2_L2_2024.01.01_120000",
    "plot_T3_L1_2024.01.01_120000",
    "plot_T3_L2_2024.01.01_120000",
]


def _make_ids(shape, values):
    h, w = shape
    flat = np.array([values[i % len(values)] for i in range(h * w)], dtype=np.uint8)
    return flat.reshape(h, w)


def _build_project(tmp_path, *, incomplete_stem=None, dead_root_stem=None):
    """Six 20x20 RGB images + GT files (registry has an added-then-retired class).

    Returns (project_dir, images, write_registry, export_registry).
    ``write_registry`` still has ``dead_root`` active (used to write the GT
    file that paints it); ``export_registry`` has it retired, and is what
    callers pass to ``export_dataset``.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    write_registry = default_registry().add(key="dead_root", name="Dead root", color="#111111")
    export_registry = write_registry.retire("dead_root")

    images: dict[str, str] = {}
    complete_keys = frozenset({"root", "nodule", "root_tip"})
    incomplete_keys = frozenset({"root", "nodule"})

    for stem in _STEMS:
        image_path = project_dir / f"{stem}.png"
        Image.new("RGB", (20, 20), color=(10, 20, 30)).save(image_path, format="PNG")
        images[stem] = str(image_path)

        if stem == dead_root_stem:
            ids = _make_ids((20, 20), [0, 1, 2, 4])
        else:
            ids = _make_ids((20, 20), [0, 1, 2, 3, 255])

        reviewed = incomplete_keys if stem == incomplete_stem else complete_keys
        tracing = Tracing(ids=ids, reviewed=reviewed)
        write_tracing(gt_path(image_path), tracing, write_registry)

    return str(project_dir), images, write_registry, export_registry


# --------------------------- remap_ids --------------------------------------


def test_remap_ids_defaults():
    registry = default_registry()
    ids = _make_ids((4, 4), [0, 1, 2, 3, 255])

    remapped = remap_ids(ids, registry)

    assert np.all(remapped[ids == 0] == 0)
    assert np.all(remapped[ids == 1] == 1)  # root
    assert np.all(remapped[ids == 2] == 2)  # nodule
    assert np.all(remapped[ids == 3] == 3)  # root_tip
    assert np.all(remapped[ids == 255] == GT_IGNORE_ID)


def test_remap_ids_retired_and_unknown_to_ignore():
    registry = default_registry().add(key="dead_root", name="Dead root", color="#111111")
    registry = registry.retire("dead_root")
    ids = _make_ids((4, 4), [1, 4, 9])

    remapped = remap_ids(ids, registry)

    assert np.all(remapped[ids == 1] == 1)
    assert np.all(remapped[ids == 4] == GT_IGNORE_ID)  # retired
    assert np.all(remapped[ids == 9] == GT_IGNORE_ID)  # unknown


# --------------------------- assign_splits -----------------------------------


def test_assign_splits_deterministic_same_seed():
    a = assign_splits(list(_STEMS), 0.34, True, seed=7)
    b = assign_splits(list(_STEMS), 0.34, True, seed=7)
    assert a == b


def test_assign_splits_different_seed_can_differ():
    results = {seed: assign_splits(list(_STEMS), 0.5, True, seed=seed) for seed in range(8)}
    distinct = {tuple(sorted(r.items())) for r in results.values()}
    assert len(distinct) > 1, "expected at least two seeds to disagree"


def test_assign_splits_group_by_tube_never_splits_a_tube():
    splits = assign_splits(list(_STEMS), 0.5, True, seed=3)
    for tube in ("T1", "T2", "T3"):
        tube_splits = {splits[s] for s in _STEMS if f"_{tube}_" in s}
        assert len(tube_splits) == 1


def test_assign_splits_both_non_empty_with_two_or_more_groups():
    splits = assign_splits(list(_STEMS), 0.5, True, seed=1)
    values = set(splits.values())
    assert "train" in values
    assert "val" in values


def test_assign_splits_zero_val_fraction_is_all_train():
    splits = assign_splits(list(_STEMS), 0.0, True, seed=0)
    assert set(splits.values()) == {"train"}


# --------------------------- export_dataset ----------------------------------


def test_export_only_complete_true_skips_incomplete(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(
        tmp_path, incomplete_stem=_STEMS[0]
    )
    options = ExportOptions(out_dir=str(tmp_path / "out"), only_complete=True)

    result = export_dataset(project_dir, images, export_registry, options)

    assert result.exported == 5
    assert result.skipped_incomplete == 1


def test_export_only_complete_false_exports_all(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(
        tmp_path, incomplete_stem=_STEMS[0]
    )
    options = ExportOptions(out_dir=str(tmp_path / "out"), only_complete=False)

    result = export_dataset(project_dir, images, export_registry, options)

    assert result.exported == 6
    assert result.skipped_incomplete == 0


def test_export_output_layout(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(
        tmp_path, incomplete_stem=_STEMS[0], dead_root_stem=_STEMS[1]
    )
    out_dir = tmp_path / "out"
    options = ExportOptions(out_dir=str(out_dir), only_complete=False, val_fraction=0.34, seed=2)

    result = export_dataset(project_dir, images, export_registry, options)

    exported_stems = set(_STEMS)  # only_complete=False -> all 6 exported
    assert result.exported == 6

    for stem in exported_stems:
        image_out = out_dir / "images" / f"{stem}.png"
        assert image_out.exists()
        assert image_out.read_bytes() == (tmp_path / "project" / f"{stem}.png").read_bytes()

        mask_out = out_dir / "masks" / f"{stem}.png"
        assert mask_out.exists()
        with Image.open(mask_out) as im:
            assert im.mode == "L"

    with Image.open(out_dir / "masks" / f"{_STEMS[1]}.png") as im:
        remapped_vals = set(np.array(im).tolist()[0])
    assert GT_IGNORE_ID in remapped_vals  # the dead_root (id 4) pixels

    with open(out_dir / "classes.json", encoding="utf-8") as fh:
        classes_json = json.load(fh)
    assert classes_json["ignore_index"] == GT_IGNORE_ID
    ids_in_json = sorted(c["id"] for c in classes_json["classes"])
    assert ids_in_json == [0, 1, 2, 3]

    with open(out_dir / "splits" / "train.txt", encoding="utf-8") as fh:
        train_stems = {line.strip() for line in fh if line.strip()}
    with open(out_dir / "splits" / "val.txt", encoding="utf-8") as fh:
        val_stems = {line.strip() for line in fh if line.strip()}
    assert train_stems | val_stems == exported_stems
    assert train_stems & val_stems == set()

    with open(out_dir / "manifest.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    assert list(rows[0].keys()) == [
        "stem",
        "source_image",
        "split",
        "tube",
        "position",
        "date",
        "classes_present",
    ]
    assert {row["stem"] for row in rows} == exported_stems
    for row in rows:
        assert row["split"] in ("train", "val")


def test_export_non_empty_out_dir_raises(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("x")

    options = ExportOptions(out_dir=str(out_dir))
    with pytest.raises(ExportError):
        export_dataset(project_dir, images, export_registry, options)


@pytest.mark.parametrize(
    "sub",
    [
        "gt",
        os.path.join("gt", "export"),
        os.path.join("mask", "export"),
        os.path.join("skeletons", "export"),
        os.path.join("skeleton_corrections", "deep", "export"),
    ],
)
def test_export_refuses_out_dir_inside_project_data_folders(tmp_path, sub):
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    out_dir = os.path.join(project_dir, sub)
    gt_before = sorted(os.listdir(os.path.join(project_dir, "gt")))

    with pytest.raises(ExportError, match="inside"):
        export_dataset(project_dir, images, export_registry, ExportOptions(out_dir=out_dir))
    if sub != "gt":
        assert not os.path.exists(out_dir)
    assert sorted(os.listdir(os.path.join(project_dir, "gt"))) == gt_before


def test_export_allows_sibling_folder_with_similar_name(tmp_path):
    """``gt_export`` next to ``gt/`` is not inside it (no prefix matching)."""
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    out_dir = os.path.join(project_dir, "gt_export")
    result = export_dataset(project_dir, images, export_registry, ExportOptions(out_dir=out_dir))
    assert result.exported == 6


def test_export_writes_errors_txt_only_when_there_are_errors(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    clean_out = tmp_path / "clean"
    export_dataset(project_dir, images, export_registry, ExportOptions(out_dir=str(clean_out)))
    assert not (clean_out / "errors.txt").exists()

    corrupt_stem = _STEMS[2]
    with open(gt_path(images[corrupt_stem]), "wb") as fh:
        fh.write(b"not a png")
    out_dir = tmp_path / "out"
    result = export_dataset(
        project_dir, images, export_registry, ExportOptions(out_dir=str(out_dir))
    )
    assert [stem for stem, _ in result.errors] == [corrupt_stem]
    lines = (out_dir / "errors.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    stem, message = lines[0].split("\t", 1)
    assert stem == corrupt_stem and message == " ".join(result.errors[0][1].split())


def test_export_missing_out_dir_is_created(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    out_dir = tmp_path / "nested" / "out"

    options = ExportOptions(out_dir=str(out_dir))
    export_dataset(project_dir, images, export_registry, options)

    assert out_dir.exists()


def test_export_corrupt_gt_file_lands_in_errors_others_continue(tmp_path):
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    corrupt_stem = _STEMS[0]
    corrupt_path = gt_path(images[corrupt_stem])
    original_contents = {stem: open(gt_path(images[stem]), "rb").read() for stem in _STEMS}
    with open(corrupt_path, "wb") as fh:
        fh.write(b"not a png")
    corrupted_contents = open(corrupt_path, "rb").read()

    options = ExportOptions(out_dir=str(tmp_path / "out"), only_complete=False)
    result = export_dataset(project_dir, images, export_registry, options)

    assert len(result.errors) == 1
    assert result.errors[0][0] == corrupt_stem
    assert result.exported == 5

    # Source GT files (including the corrupted one) are left untouched.
    for stem in _STEMS:
        current = open(gt_path(images[stem]), "rb").read()
        if stem == corrupt_stem:
            assert current == corrupted_contents
        else:
            assert current == original_contents[stem]


def test_export_missing_source_image_lands_in_errors_others_continue(tmp_path):
    """A GT-traced image whose source file went missing must not abort the run.

    remap_ids/mask-save only depends on the GT tracing, so the mask for the
    missing-source stem would be written before ``shutil.copy2`` raises;
    that partial mask must not survive, and the stem must not appear in the
    split files or the manifest.
    """
    project_dir, images, _write_reg, export_registry = _build_project(tmp_path)
    missing_stem = _STEMS[0]
    os.remove(images[missing_stem])  # source image gone; its GT file remains

    options = ExportOptions(out_dir=str(tmp_path / "out"), only_complete=False)
    result = export_dataset(project_dir, images, export_registry, options)

    assert len(result.errors) == 1
    assert result.errors[0][0] == missing_stem
    assert result.exported == 5
    assert result.train + result.val == 5

    out_dir = tmp_path / "out"
    assert not (out_dir / "images" / f"{missing_stem}.png").exists()
    assert not (out_dir / "masks" / f"{missing_stem}.png").exists()

    with open(out_dir / "splits" / "train.txt", encoding="utf-8") as fh:
        train_stems = {line.strip() for line in fh if line.strip()}
    with open(out_dir / "splits" / "val.txt", encoding="utf-8") as fh:
        val_stems = {line.strip() for line in fh if line.strip()}
    assert missing_stem not in train_stems
    assert missing_stem not in val_stems
    assert len(train_stems | val_stems) == 5

    with open(out_dir / "manifest.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert missing_stem not in {row["stem"] for row in rows}
    assert len(rows) == 5

    # Other images still exported normally.
    other_stem = _STEMS[1]
    assert (out_dir / "images" / f"{other_stem}.png").exists()
    assert (out_dir / "masks" / f"{other_stem}.png").exists()

"""Foundation-core: ProjectLayout pins every on-disk naming convention of the app.

Each expected path below is the formula the current writer/reader uses (file
references in the comments), so a drift between ProjectLayout and the code it
replaces fails here.
"""

from __future__ import annotations

import os

import pytest

import app.project_layout as pl
from app.gt import store as gt_store
from app.project_layout import ProjectLayout, find_test_latest_dir

STEM = "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM"


def _touch(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x")
    return str(path)


@pytest.fixture
def layout(tmp_path) -> ProjectLayout:
    return ProjectLayout(str(tmp_path))


@pytest.fixture
def walk_inside_tmp(tmp_path, monkeypatch):
    """Keep the legacy test_latest walk inside tmp_path (its upward search would
    otherwise inspect whatever sits next to the pytest temp folder)."""
    real = pl.find_test_latest_dir

    def contained(start_path, max_depth=3):
        found = real(start_path, max_depth)
        inside = found and os.path.commonpath([found, str(tmp_path)]) == str(tmp_path)
        return found if inside else None

    monkeypatch.setattr(pl, "find_test_latest_dir", contained)


# ------------------------------------------------------------------ identity / stems


def test_root_is_normalised_and_layout_is_hashable(tmp_path):
    a = ProjectLayout(str(tmp_path) + "/")
    b = ProjectLayout(tmp_path)  # PathLike accepted
    assert a == b and hash(a) == hash(b)
    assert a.root == os.path.normpath(str(tmp_path))
    with pytest.raises(AttributeError):
        a.root = "elsewhere"  # frozen


def test_for_image_uses_the_image_folder(tmp_path):
    image = os.path.join(str(tmp_path), f"{STEM}.jpg")
    assert ProjectLayout.for_image(image) == ProjectLayout(str(tmp_path))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"D:/data/{STEM}.jpg", STEM),
        (f"{STEM}.JPG", STEM),
        (STEM, STEM),  # a bare stem whose date contains dots stays whole
        ("a.PNG", "a"),
        ("x.jpeg", "x"),
        ("scan.tif", "scan"),
        (f"{STEM}_fake.png", f"{STEM}_fake"),
    ],
)
def test_stem(value, expected):
    assert ProjectLayout.stem(value) == expected


def test_folders(layout, tmp_path):
    root = str(tmp_path)
    assert layout.mask_dir == os.path.join(root, "mask")
    assert layout.skeletons_dir == os.path.join(root, "skeletons")
    assert layout.corrections_dir == os.path.join(root, "skeleton_corrections")
    assert layout.gt_dir == os.path.join(root, "gt")


# ------------------------------------------------------------------ masks


def test_mask_path_matches_mask_editor_save_path(tmp_path, layout):
    # mask_tracing_interface._mask_save_path: normpath(join(dirname(image)/"mask", stem + ".png"))
    image = os.path.join(str(tmp_path), f"{STEM}.jpg")
    editor = os.path.normpath(
        os.path.join(os.path.join(os.path.dirname(image), "mask"), STEM + ".png")
    )
    assert layout.mask_path(STEM) == editor
    assert layout.mask_path(image) == editor  # a path or file name works too


def test_mask_path_for_relative_root_matches_editor():
    image = f"{STEM}.jpg"  # dirname == "" (image in the working directory)
    editor = os.path.normpath(os.path.join(os.path.join("", "mask"), STEM + ".png"))
    assert ProjectLayout.for_image(image).mask_path(STEM) == editor


def test_find_mask_prefers_png_then_legacy_extensions(layout):
    assert layout.find_mask(STEM) is None
    jpg = _touch(os.path.join(layout.mask_dir, STEM + ".jpg"))
    assert os.path.normcase(layout.find_mask(STEM)) == os.path.normcase(jpg)
    png = _touch(os.path.join(layout.mask_dir, STEM + ".png"))
    assert layout.find_mask(STEM) == png


def test_find_mask_ignores_directories(layout):
    os.makedirs(os.path.join(layout.mask_dir, STEM + ".png"))
    assert layout.find_mask(STEM) is None


def test_scan_masks_matches_library_scanner(layout):
    # image_manager.ImageLoaderThread: files in mask/ whose lower-case name ends
    # with .png/.jpg/.jpeg, keyed by splitext stem.
    for name in ("a.png", "b.JPG", "c.jpeg", "d.tif", "root_areas.csv", "a.png.provenance.json"):
        _touch(os.path.join(layout.mask_dir, name))
    _touch(os.path.join(layout.mask_dir, ".e.png.1a2b3c4d.tmp"))
    os.makedirs(os.path.join(layout.mask_dir, "f.png"))
    assert layout.scan_mask_stems() == {"a", "b", "c"}
    masks = layout.scan_masks()
    assert set(masks) == {"a", "b", "c"}
    assert masks["b"] == os.path.join(layout.mask_dir, "b.JPG")


def test_scan_masks_prefers_png_when_stems_collide(layout):
    _touch(os.path.join(layout.mask_dir, "a.jpg"))
    _touch(os.path.join(layout.mask_dir, "a.png"))
    _touch(os.path.join(layout.mask_dir, "a.jpeg"))
    assert layout.scan_masks() == {"a": os.path.join(layout.mask_dir, "a.png")}


def test_scan_masks_png_only_filter(layout):
    # skeleton_handler.calculate_root_area measures only *.png masks.
    _touch(os.path.join(layout.mask_dir, "a.png"))
    _touch(os.path.join(layout.mask_dir, "b.jpg"))
    assert set(layout.scan_masks(extensions=(".png",))) == {"a"}


def test_scans_of_missing_folders_are_empty(layout):
    assert layout.scan_masks() == {}
    assert layout.scan_mask_stems() == set()
    assert layout.scan_skeleton_stems() == set()
    assert layout.scan_corrected_stems() == set()


# ------------------------------------------------------------------ skeletons


def test_skeleton_path_matches_inference_output(tmp_path, layout):
    # generate_skeleton_handler: output_dir = normpath(join(input_dir, "skeletons"));
    # skeleton_inference.run_inference: join(output_dir, f"{img_name}_fake.png").
    output_dir = os.path.normpath(os.path.join(str(tmp_path), "skeletons"))
    assert layout.skeleton_path(STEM) == os.path.join(output_dir, f"{STEM}_fake.png")
    assert layout.skeleton_path(f"{STEM}.jpg") == layout.skeleton_path(STEM)


def test_find_and_scan_skeletons(layout):
    assert layout.find_skeleton(STEM) is None
    path = _touch(layout.skeleton_path(STEM))
    _touch(os.path.join(layout.skeletons_dir, "root_lengths.csv"))
    _touch(os.path.join(layout.skeletons_dir, "other_real.png"))
    assert layout.find_skeleton(STEM) == path
    # skeleton_handler.calculate_root_length: filename.endswith("_fake.png")
    assert layout.scan_skeleton_stems() == {STEM}
    assert layout.scan_skeletons() == {STEM: path}


def test_corrected_skeleton_path_matches_skeleton_editor(tmp_path, layout):
    # skeleton_correction_interface._default_correction_path:
    # join(images_base_folder, "skeleton_corrections", f"{current_image_name}_skeleton.png")
    expected = os.path.join(str(tmp_path), "skeleton_corrections", f"{STEM}_skeleton.png")
    assert layout.corrected_skeleton_path(STEM) == expected
    assert layout.find_corrected_skeleton(STEM) is None
    _touch(expected)
    _touch(os.path.join(layout.corrections_dir, "notes.txt"))
    assert layout.find_corrected_skeleton(STEM) == expected
    assert layout.scan_corrected_stems() == {STEM}
    assert layout.scan_corrected_skeletons() == {STEM: expected}


def test_gt_path_matches_gt_store(tmp_path, layout):
    image = os.path.join(str(tmp_path), f"{STEM}.jpg")
    assert layout.gt_path(STEM) == gt_store.gt_path(image)
    assert layout.gt_dir == gt_store.gt_dir(str(tmp_path))


def test_gt_mask_and_skeleton_paths_sit_under_gt(tmp_path, layout):
    root = str(tmp_path)
    assert layout.gt_mask_dir == os.path.join(root, "gt", "mask")
    assert layout.gt_skeletons_dir == os.path.join(root, "gt", "skeletons")
    assert layout.gt_mask_path(STEM) == os.path.join(root, "gt", "mask", f"{STEM}.png")
    assert layout.gt_skeleton_path(f"{STEM}.jpg") == os.path.join(
        root, "gt", "skeletons", f"{STEM}.png"
    )


# ------------------------------------------------------------------ legacy folders


def test_resolve_masks_dir_keeps_todays_priority(layout, tmp_path):
    # skeleton_handler.calculate_root_area / visualization_manager: output/mask first.
    assert layout.resolve_masks_dir() is None
    os.makedirs(layout.mask_dir)
    assert layout.resolve_masks_dir() == layout.mask_dir
    legacy = os.path.join(str(tmp_path), "output", "mask")
    os.makedirs(legacy)
    assert layout.mask_dir_candidates == (legacy, layout.mask_dir)
    assert layout.resolve_masks_dir() == legacy


def test_resolve_skeletons_dir_priority_and_legacy_layouts(layout, tmp_path, walk_inside_tmp):
    root = str(tmp_path)
    assert layout.resolve_skeletons_dir() is None
    nested = os.path.join(root, "results", "run1", "test_latest", "images")
    os.makedirs(nested)
    assert layout.resolve_skeletons_dir() == nested
    assert layout.resolve_skeletons_dir(search_test_latest=False) is None
    legacy2 = os.path.join(root, "skeletonizer", "test_latest", "images")
    os.makedirs(legacy2)
    assert layout.resolve_skeletons_dir() == legacy2
    legacy1 = os.path.join(root, "output", "skeletonizer", "test_latest", "images")
    os.makedirs(legacy1)
    assert layout.resolve_skeletons_dir() == legacy1
    os.makedirs(layout.skeletons_dir)
    assert layout.resolve_skeletons_dir() == layout.skeletons_dir
    assert layout.skeleton_dir_candidates == (layout.skeletons_dir, legacy1, legacy2)


def test_find_lengths_csv_primary_then_legacy(layout, tmp_path, walk_inside_tmp, monkeypatch):
    root = str(tmp_path)
    assert layout.find_lengths_csv() is None
    legacy = _touch(os.path.join(root, "output", "x", "test_latest", "root_lengths.csv"))
    assert layout.find_lengths_csv() == legacy
    base = _touch(os.path.join(root, "root_lengths.csv"))
    assert layout.find_lengths_csv() == base
    primary = _touch(os.path.join(layout.skeletons_dir, "root_lengths.csv"))

    def no_walk(*a, **k):
        raise AssertionError("legacy walk must not run when a primary CSV exists")

    monkeypatch.setattr(pl, "find_test_latest_dir", no_walk)
    assert layout.find_lengths_csv() == primary


def test_find_areas_csv_lives_in_the_resolved_masks_dir(layout, tmp_path):
    assert layout.find_areas_csv() is None
    os.makedirs(layout.mask_dir)
    assert layout.find_areas_csv() is None
    csv_path = _touch(os.path.join(layout.mask_dir, "root_areas.csv"))
    assert layout.find_areas_csv() == csv_path
    os.makedirs(os.path.join(str(tmp_path), "output", "mask"))
    assert layout.find_areas_csv() is None  # output/mask wins, as today


def test_find_test_latest_dir_searches_down_then_up(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sibling = tmp_path / "results" / "test_latest"
    sibling.mkdir(parents=True)
    assert find_test_latest_dir(str(project)) == str(sibling)
    below = project / "output" / "skeletonizer" / "test_latest"
    below.mkdir(parents=True)
    assert find_test_latest_dir(str(project)) == str(below)
    assert find_test_latest_dir(str(below)) == str(below)


def test_find_test_latest_dir_respects_max_depth(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "test_latest"  # depth 4 below tmp_path
    deep.mkdir(parents=True)
    assert find_test_latest_dir(str(tmp_path), max_depth=3) != str(deep)
    assert find_test_latest_dir(str(tmp_path), max_depth=4) == str(deep)


def test_find_lengths_csv_finds_measure_output_of_legacy_layouts(layout, tmp_path, walk_inside_tmp):
    # Measure writes root_lengths.csv into the folder it read skeletons from,
    # which for the legacy layout is .../test_latest/images.
    images = os.path.join(str(tmp_path), "output", "skeletonizer", "test_latest", "images")
    written = _touch(os.path.join(images, "root_lengths.csv"))
    assert layout.resolve_skeletons_dir() == images
    assert layout.find_lengths_csv() == written
    assert layout.find_lengths_csv(search_test_latest=False) is None


def test_mask_candidates_follow_the_editor_lookup_order(layout):
    # mask_tracing_interface: MASK_EXTENSIONS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")
    names = [os.path.basename(p) for p in layout.mask_candidates(f"{STEM}.jpg")]
    assert names == [STEM + e for e in (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")]
    assert layout.mask_candidates(STEM)[0] == layout.mask_path(STEM)


def test_mask_path_matches_mask_generation_output(tmp_path, layout):
    # mask_generation_handler: output_dir = join(input_dir, "mask");
    # MaskGenerationThread saves join(output_dir, splitext(filename)[0] + ".png").
    filename = f"{STEM}.jpg"
    output_dir = os.path.join(str(tmp_path), "mask")
    generated = os.path.join(output_dir, os.path.splitext(filename)[0] + ".png")
    assert layout.mask_path(filename) == os.path.normpath(generated)

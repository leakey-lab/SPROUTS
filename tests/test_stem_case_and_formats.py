"""R4 / R3 (O7, O6): one rule for "the same image", and the Library's formats everywhere.

Stems compare without case on every platform (``app.naming.stem_key``): ``A.png`` and
``a.jpg`` are one image, the lowest ``(extension rank, file name)`` wins, and the hidden
one gets no mask, skeleton or Library row. Before, a case-insensitive disk gave three
"written" for two output files with a random winner, and *Keep existing masks* replaced a
hand-corrected mask whose name differed in case. Every reader takes PNG and JPEG only
(``config.IMAGE_EXTENSIONS``): a mixed folder gave 3 Library images but 7 skeletons. The
load toast names what is not listed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.handlers.deps import HandlerDependencies
from app.inference import mask_inference as mi
from app.inference import skeleton_inference as si
from app.inference.mask_origin import OverwritePolicy, scan_existing_masks
from app.naming import image_stem, stem_key
from app.project_layout import ImageScan, ProjectLayout
from tests.qt_wait import wait_until as _wait
from tests.test_library_main_window import mw  # noqa: F401 - fixture
from tests.test_robust_generation_stem_clash import _Brightness, _mean, _Skeleton
from tests.test_shell_info_measurement_index import _row, _write_lengths

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


def _images(folder, spec):
    """``spec``: ``{file name: grey level}``; every image 64x48 RGB."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, level in spec.items():
        Image.new("RGB", (64, 48), (level, level, level)).save(folder / name)
    return str(folder)


def _pngs(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".png"))


def _case_sensitive(folder) -> bool:
    (folder / "Probe").write_bytes(b"")
    try:
        return not (folder / "probe").exists()
    finally:
        (folder / "Probe").unlink()


def test_stem_key_lowers_and_does_not_casefold():
    assert stem_key("A_T001") == "a_t001" and stem_key("straße") != stem_key("strasse")


@pytest.mark.parametrize("run", range(5))
def test_case_variant_stems_give_one_image_and_one_output_every_run(
    tmp_path,
    cpu_runtime,
    run,
):
    src = _images(tmp_path / "imgs", {"A.png": 255, "a.jpg": 0, "b.jpg": 120})
    out = str(tmp_path / "mask")
    skel = str(tmp_path / "skel")

    scan = ProjectLayout(src).scan_image_folder()
    assert sorted(scan.images) == ["A", "b"] and scan.hidden == (("a.jpg", "A.png"),)

    report = mi.generate_masks(src, out, model=_Brightness(), batch_size=8)
    assert sorted(report.written) == ["A.png", "b.jpg"] and report.total == 2
    assert len(_pngs(out)) == 2 and _mean(os.path.join(out, "A.png")) == 255

    skeletons = si.run_skeleton_generation(src, skel, model=_Skeleton(), batch_size=8)
    assert sorted(skeletons.written) == ["A.png", "b.jpg"]


@pytest.mark.parametrize("mask_name", ["A.png", "a.png"])
def test_a_manual_mask_survives_keep_existing_whatever_its_case(
    tmp_path,
    cpu_runtime,
    mask_name,
):
    src = _images(tmp_path / "imgs", {"A.png": 255, "a.jpg": 0, "b.jpg": 120})
    layout = ProjectLayout(src)
    os.makedirs(layout.mask_dir)
    # A hand-corrected mask, as the Correct editor writes it: RGBA, white on transparent.
    pixels = np.zeros((48, 64, 4), np.uint8)
    pixels[10:30, 10:30] = 255
    hand_mask = os.path.join(layout.mask_dir, mask_name)
    Image.fromarray(pixels, "RGBA").save(hand_mask)
    before = Path(hand_mask).read_bytes()

    existing = scan_existing_masks(layout, layout.scan_images())
    assert existing.stems == frozenset({"A"})

    report = mi.generate_masks(
        src,
        layout.mask_dir,
        model=_Brightness(),
        batch_size=8,
        skip_stems=OverwritePolicy.KEEP_EXISTING.skip_stems(existing),
    )
    assert report.written == ["b.jpg"] and Path(hand_mask).read_bytes() == before


def test_equal_extensions_pick_the_name_that_sorts_first(tmp_path):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system (runs on the ubuntu CI)")
    src = _images(tmp_path / "imgs", {"a.png": 0, "A.png": 255})
    scan = ProjectLayout(src).scan_image_folder()
    assert scan.images == {"A": os.path.join(src, "A.png")}
    assert scan.hidden == (("a.png", "A.png"),)


def test_mixed_formats_give_the_same_inputs_to_library_masks_and_skeletons(tmp_path):
    names = ("a.png", "b.jpg", "c.JPEG", "d.tif", "e.bmp", "f.ppm", "g.TIFF")
    src = _images(tmp_path / "imgs", dict.fromkeys(names, 90))

    scan = ProjectLayout(src).scan_image_folder()
    stems = sorted(scan.images)
    assert stems == ["a", "b", "c"]
    assert sorted(os.path.splitext(n)[0] for n in mi.list_inputs(src)[0]) == stems
    assert sorted(image_stem(p) for p in si.make_dataset(src)) == stems
    assert scan.unsupported == ("d.tif", "e.bmp", "f.ppm", "g.TIFF")


def test_the_scan_keeps_listing_once(tmp_path):
    src = _images(tmp_path / "imgs", {"a.png": 0, "b.jpg": 0})
    layout = ProjectLayout(src)
    assert layout.scan_images() == layout.scan_image_folder().images
    assert layout.scan_image_folder().hidden == () == layout.scan_image_folder().unsupported


def test_lookup_finds_a_row_whose_stem_differs_in_case(tmp_path):
    _write_lengths(tmp_path, [_row("a", LENGTH_CSV_HEADERS[5], 18.77)])
    assert MeasurementIndex(str(tmp_path)).lookup("A") == Measurement(18.77, None, None)


def test_the_library_finds_a_mask_whose_name_differs_in_case(mw, tmp_path):  # noqa: F811 - fixture
    folder = tmp_path / "p"
    _images(folder, {"A.png": 200, "b.png": 100})
    (folder / "mask").mkdir()
    Image.new("L", (64, 48), 255).save(folder / "mask" / "a.png")

    mw.open_path(str(folder))
    assert _wait(lambda: mw.image_manager.images and mw.library_index.leaves())
    assert mw.image_manager.has_mask("A")


def test_the_load_toast_names_hidden_and_unsupported_files(mw, tmp_path):  # noqa: F811 - fixture
    folder = tmp_path / "p"
    _images(folder, {"A.png": 200, "a.jpg": 0, "b.png": 100, "t.tif": 50})

    mw.open_path(str(folder))
    assert _wait(lambda: any(kind == "warn" for kind, _ in mw.toasts_seen))
    assert (
        "warn",
        "1 image is not listed: a.jpg has the same name as A.png. 1 file (.tif) is not "
        "supported and is not listed; convert it to PNG or JPEG",
    ) in mw.toasts_seen
    assert [m for k, m in mw.toasts_seen if k == "success"] == ["Loaded 2 images"]
    assert len(mw._image_leaves()) == 2


def test_a_folder_of_tiffs_says_why_nothing_loaded(mw, tmp_path):  # noqa: F811 - fixture
    _images(tmp_path / "tifs", {"x.tif": 0, "y.bmp": 0})

    mw.open_path(str(tmp_path / "tifs"))
    assert _wait(lambda: mw.toasts_seen and mw.toasts_seen[-1][0] == "warn")
    assert mw.toasts_seen[-1] == (
        "warn",
        "No .png/.jpg/.jpeg images in tifs — nothing loaded. 2 files (.bmp, .tif) are not "
        "supported and are not listed; convert them to PNG or JPEG",
    )


def _boom(*_args, **_kwargs):
    raise RuntimeError("adoption failed")


@pytest.mark.parametrize("where", ["manager", "tree"])
def test_a_failed_adoption_loads_nothing_and_leaves_jobs_startable(
    mw,  # noqa: F811 - fixture
    tmp_path,
    monkeypatch,
    where,
):
    # Since W1-T1 a folder load refuses every Generate / Measure run until it ends; an
    # exception while the manager adopts the scan, or while the tree is built, must still
    # end it, and never leave the Library on one project and the jobs' folder on another.
    from app.gui.image_manager import ImageManager, project_layout_of

    _images(tmp_path / "old", {"a.png": 90, "b.png": 90})
    mw.open_path(str(tmp_path / "old"))
    assert _wait(lambda: len(mw._image_leaves()) == 2)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    if where == "manager":
        monkeypatch.setattr(ImageManager, "on_loading_finished", _boom)
    else:  # the new folder's tree fails; an empty one still builds
        real = mw.populate_file_list
        monkeypatch.setattr(
            mw, "populate_file_list", lambda: _boom() if mw.image_manager.images else real()
        )
    _images(tmp_path / "new", {"c.png": 90})

    mw.open_path(str(tmp_path / "new"))
    assert _wait(lambda: any(kind == "danger" for kind, _ in mw.toasts_seen))
    assert project_layout_of(mw.image_manager) is None and mw.image_manager.images == {}
    assert mw._image_leaves() == [] and mw.metrics.index is None
    assert mw.app_stack.currentIndex() == 0  # nothing loaded: back to Welcome
    assert mw._loading_project is False
    assert HandlerDependencies(mw).refuse_while_loading() is False
    assert errors == []


def test_a_failure_after_the_library_is_shown_keeps_the_folder_loaded(
    mw,  # noqa: F811 - fixture
    tmp_path,
    monkeypatch,
):
    from app.gui.image_manager import project_layout_of

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    monkeypatch.setattr(mw.metrics, "prewarm", _boom)
    folder = tmp_path / "p"
    _images(folder, {"a.png": 90})

    mw.open_path(str(folder))
    assert _wait(lambda: any(kind == "success" for kind, _ in mw.toasts_seen))
    assert [m for k, m in mw.toasts_seen if k == "success"] == ["Loaded 1 image"]
    assert not any(kind == "danger" for kind, _ in mw.toasts_seen)
    layout = project_layout_of(mw.image_manager)
    assert os.path.normcase(layout.root) == os.path.normcase(str(folder))
    assert len(mw._image_leaves()) == 1 and not mw.loading_overlay.isVisible()
    assert mw._loading_project is False and errors == []


def test_a_lookup_prefers_the_row_of_the_exact_stem(tmp_path):
    column = LENGTH_CSV_HEADERS[5]
    _write_lengths(tmp_path, [_row("A", column, 1.5), _row("a", column, 2.5)])
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup("A").length_mm == 1.5 and index.lookup("a").length_mm == 2.5


def test_a_lookup_never_guesses_between_case_variant_rows(tmp_path):
    column = LENGTH_CSV_HEADERS[5]
    _write_lengths(tmp_path, [_row("Ab", column, 1.5), _row("aB", column, 2.5)])
    assert MeasurementIndex(str(tmp_path)).lookup("AB") == Measurement(None, None, None)


def test_a_trailing_separator_still_finds_a_case_insensitive_folder(tmp_path):
    if _case_sensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system (Windows, macOS)")
    from app import project_layout

    folder = tmp_path / "Mask"
    folder.mkdir()
    assert project_layout._ignores_case(str(folder) + os.sep)


def test_the_overlay_lists_the_skeleton_folder_once_per_miss(tmp_path, monkeypatch):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system (runs on the ubuntu CI)")
    from app.gui.image_manager import ImageManager

    layout = ProjectLayout(_images(tmp_path / "p", {"A.png": 90}))
    os.makedirs(layout.skeletons_dir)
    manager = ImageManager()
    manager.original_folder = layout.root
    manager.on_loading_finished(layout.scan_images(), {}, {}, False)
    manager.processed_base_path = layout.skeletons_dir
    manager.current_view_mode = "Overlay"
    listings = []
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda d: listings.append(d) or real(d))

    assert manager.get_fake_image_path("A") is None
    assert manager.get_fake_image_path("A") is None
    assert len(listings) == 1
    skeleton = _touch(os.path.join(layout.skeletons_dir, "a_fake.png"))
    manager.refresh_outputs()
    manager.processed_base_path = layout.skeletons_dir
    assert manager.get_fake_image_path("A") == skeleton


def test_the_skipped_text_names_at_most_three_hidden_files():
    from app.gui.window.project_loader import skipped_text

    three = (("a.jpg", "A.png"), ("b.JPG", "B.png"), ("c.jpeg", "C.png"))
    assert skipped_text(ImageScan({}, hidden=three)) == (
        "3 images are not listed: each has the same name as another image (a.jpg, b.JPG, c.jpeg)"
    )
    four = (*three, ("d.jpg", "D.png"))
    assert skipped_text(ImageScan({}, hidden=four)).endswith("(a.jpg, b.JPG, c.jpeg, …)")
    assert skipped_text(ImageScan({}, unsupported=("a.TIF", "b.bmp", "c.tif"))) == (
        "3 files (.bmp, .tif) are not supported and are not listed; convert them to PNG or JPEG"
    )
    assert skipped_text(ImageScan({"a": "a.png"})) == ""


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("L", (8, 6)).save(path, "PNG")
    return path


def test_single_stem_lookups_find_a_file_whose_name_differs_in_case(tmp_path):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system (runs on the ubuntu CI)")
    from app.gui.image_manager import ImageManager

    layout = ProjectLayout(_images(tmp_path / "p", {"A.png": 90}))
    mask = _touch(os.path.join(layout.mask_dir, "a.PNG"))
    skeleton = _touch(os.path.join(layout.skeletons_dir, "a_fake.png"))
    gt_skeleton = _touch(os.path.join(layout.gt_skeletons_dir, "a.png"))

    assert layout.find_mask("A") == mask
    assert layout.find_skeleton("A") == skeleton
    assert layout.find_gt_skeleton("A") == gt_skeleton
    assert layout.find_mask("b") is None and layout.find_skeleton("b") is None

    manager = ImageManager()
    manager.original_folder = layout.root
    manager.on_loading_finished(layout.scan_images(), {}, {}, False)
    assert manager.has_gt_skeleton("A") and manager.gt_skeleton_path_for("A") == gt_skeleton
    manager.processed_base_path = layout.skeletons_dir
    manager.current_view_mode = "Overlay"
    assert manager.get_fake_image_path("A") == skeleton


def test_the_exact_case_file_wins_a_single_stem_lookup(tmp_path):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system (runs on the ubuntu CI)")
    layout = ProjectLayout(_images(tmp_path / "p", {"A.png": 90}))
    _touch(os.path.join(layout.mask_dir, "a.png"))
    exact = _touch(os.path.join(layout.mask_dir, "A.jpg"))
    _touch(os.path.join(layout.skeletons_dir, "a_fake.png"))
    exact_skeleton = _touch(os.path.join(layout.skeletons_dir, "A_fake.png"))

    assert layout.find_mask("A") == exact
    assert layout.find_skeleton("A") == exact_skeleton
    # Without an exact-case file, the case variants rank as the scan does.
    os.remove(exact)
    _touch(os.path.join(layout.mask_dir, "a.jpg"))
    assert layout.find_mask("A") == os.path.join(layout.mask_dir, "a.png")


def test_a_miss_on_a_case_insensitive_disk_lists_no_folder(tmp_path, monkeypatch):
    if _case_sensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system (Windows, macOS)")
    from app import project_layout

    layout = ProjectLayout(_images(tmp_path / "p", {"A.png": 90}))
    _touch(os.path.join(layout.mask_dir, "other.png"))
    os.makedirs(layout.skeletons_dir)

    def no_listing(*_args):
        raise AssertionError("a lookup listed a folder")

    monkeypatch.setattr(project_layout.os, "scandir", no_listing)
    assert layout.find_mask("A") is None and layout.find_skeleton("A") is None
    _touch(os.path.join(layout.mask_dir, "a.png"))
    assert os.path.normcase(layout.find_mask("A")) == os.path.normcase(
        os.path.join(layout.mask_dir, "a.png")
    )

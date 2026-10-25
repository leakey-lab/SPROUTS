"""O37: Measure skips, and records, the masks and skeletons whose image is gone.

A mask or skeleton whose image is no longer in the project gets no CSV row (for both
metrics); the run's provenance lists the files, the end notice counts them, and the files
stay on disk. Several files sharing one image's name without case (a case-sensitive disk)
are measured once: the file named exactly like the image, else the first by name.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

from app.gui import stage_summary
from app.gui.stage_summary import Freshness, input_freshness, measure_text
from app.handlers import skeleton_handler
from app.inference import measure_run
from app.inference.input_split import split_by_image, split_inputs
from app.project_layout import ProjectLayout
from tests.test_measure_handler import _handler, _project, _wait_idle, qapp  # noqa: F401

ORPHAN = "orphan_T9_L2_2024.01.02_010209_001"


def _rows(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _sidecar(csv_path) -> dict:
    with open(str(csv_path) + ".provenance.json", encoding="utf-8") as fh:
        return json.load(fh)


def _notices(mw) -> list[tuple[str, str]]:
    return [c.args for c in mw.feedback.calls if c.name == "notify"]


def _write_png(path, seed=0) -> bytes:
    img = np.zeros((64, 80), np.uint8)
    cv2.line(img, (0, seed), (79, 40 + seed), 255, 2)
    cv2.imwrite(str(path), img)
    return path.read_bytes()


# ------------------------------------------------------------------ the split
def test_split_by_image_keeps_inputs_with_an_image():
    inputs = {"a": "m/a.png", "B": "m/B.png", "z": "m/z.png"}
    assert split_by_image(inputs, ["A", "b"]) == (
        {"a": "m/a.png", "B": "m/B.png"},
        ["z.png"],
    )


def test_split_inputs_measures_one_file_per_image():
    inputs = {"A": "m/A_fake.png", "a": "m/a_fake.png", "Z": "m/Z_fake.png", "z": "m/z_fake.png"}
    split = split_inputs(inputs, ["a", "Z", "gone"])
    # the file named exactly like the image wins; "gone" has no file at all
    assert split.kept == {"a": "m/a_fake.png", "Z": "m/Z_fake.png"}
    assert split.duplicates == ["A_fake.png", "z_fake.png"]
    assert split.without_image == []


def test_split_inputs_without_an_exact_name_takes_the_first_by_file_name():
    split = split_inputs({"Ab": "m/Ab.png", "aB": "m/aB.png"}, ["AB"])
    assert split.kept == {"Ab": "m/Ab.png"}  # "Ab.png" sorts before "aB.png"
    assert split.duplicates == ["aB.png"]


def test_split_inputs_names_every_file_of_an_image_that_is_gone():
    split = split_inputs({"B": "m/B.png", "b": "m/b.png"}, ["x"])
    assert split.kept == {} and split.duplicates == []
    assert split.without_image == ["B.png", "b.png"]


# ------------------------------------------------------------------ the handler
def test_area_skips_a_mask_without_an_image(tmp_path, qapp):  # noqa: F811
    _project(tmp_path, n=4, skeletons=False)
    orphan = tmp_path / "mask" / f"{ORPHAN}.png"
    kept_bytes = _write_png(orphan, 7)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    csv_path = tmp_path / "mask" / "root_areas.csv"
    assert len(_rows(csv_path)) == 4
    assert orphan.read_bytes() == kept_bytes  # never deleted or touched
    inputs = _sidecar(csv_path)["inputs"]
    assert inputs["n_without_image"] == 1
    assert inputs["without_image"] == [f"{ORPHAN}.png"]
    assert (
        "1 mask skipped: its image is no longer in the project "
        "(named in root_areas.csv.provenance.json)",
        "warn",
    ) in _notices(mw)


def test_length_skips_a_skeleton_without_an_image(tmp_path, qapp):  # noqa: F811
    _project(tmp_path, n=4, masks=False)
    orphan = tmp_path / "skeletons" / f"{ORPHAN}_fake.png"
    kept_bytes = _write_png(orphan, 7)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_length()
    _wait_idle(handler, qapp)

    csv_path = tmp_path / "skeletons" / "root_lengths.csv"
    assert len(_rows(csv_path)) == 4
    assert orphan.read_bytes() == kept_bytes
    inputs = _sidecar(csv_path)["inputs"]
    assert inputs["n_without_image"] == 1
    assert inputs["without_image"] == [f"{ORPHAN}_fake.png"]
    assert (
        "1 skeleton skipped: its image is no longer in the project "
        "(named in root_lengths.csv.provenance.json)",
        "warn",
    ) in _notices(mw)


def test_a_project_where_every_input_has_its_image_records_none(tmp_path, qapp):  # noqa: F811
    _project(tmp_path, n=3)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    inputs = _sidecar(tmp_path / "mask" / "root_areas.csv")["inputs"]
    assert inputs["n_without_image"] == 0 and inputs["without_image"] == []
    assert inputs["n_duplicate"] == 0 and inputs["duplicates"] == []
    assert not [m for m, kind in _notices(mw) if "skipped" in m]


def test_a_case_variant_stem_is_not_skipped(tmp_path, qapp):  # noqa: F811
    _write_png(tmp_path / "x_T1_L2_2024.01.02_010200_001.jpg")
    (tmp_path / "mask").mkdir()
    _write_png(tmp_path / "mask" / "X_T1_L2_2024.01.02_010200_001.png")
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    csv_path = tmp_path / "mask" / "root_areas.csv"
    assert len(_rows(csv_path)) == 1
    assert _sidecar(csv_path)["inputs"]["n_without_image"] == 0
    assert not [m for m, kind in _notices(mw) if "skipped" in m]


def test_only_orphans_starts_no_job(tmp_path, qapp):  # noqa: F811
    (tmp_path / "mask").mkdir()
    for i in range(2):
        _write_png(tmp_path / "mask" / f"{ORPHAN[:-1]}{i}.png", i)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()

    assert mw.jobs.running_name() is None
    assert handler.area_calculator_thread is None
    assert _notices(mw) == [
        (
            f"No images to measure in {tmp_path / 'mask'}: its 2 masks have no image in the project.",
            "warn",
        )
    ]


def test_one_orphan_alone_says_has(tmp_path, qapp):  # noqa: F811
    (tmp_path / "skeletons").mkdir()
    _write_png(tmp_path / "skeletons" / f"{ORPHAN}_fake.png")
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_length()

    assert mw.jobs.running_name() is None
    assert _notices(mw) == [
        (
            f"No images to measure in {tmp_path / 'skeletons'}: "
            "its 1 skeleton has no image in the project.",
            "warn",
        )
    ]


def test_several_files_for_one_image_are_measured_once(tmp_path, qapp, monkeypatch):  # noqa: F811
    """A case-sensitive disk, simulated: the scan lists A_fake.png and a_fake.png."""
    _project(tmp_path, n=1, masks=False)
    skeletons = tmp_path / "skeletons"
    (skeletons / "other").mkdir()
    exact = skeletons / "x_T1_L2_2024.01.02_010200_001_fake.png"
    upper = skeletons / "other" / "X_T1_L2_2024.01.02_010200_001_fake.png"
    shutil.copyfile(exact, upper)
    listing = {
        "X_T1_L2_2024.01.02_010200_001": str(upper),
        "x_T1_L2_2024.01.02_010200_001": str(exact),
    }
    monkeypatch.setattr(ProjectLayout, "scan_skeletons", lambda self, directory=None: listing)
    before = upper.read_bytes()
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_length()
    _wait_idle(handler, qapp)

    csv_path = skeletons / "root_lengths.csv"
    assert [r["Image"] for r in _rows(csv_path)] == ["x_T1_L2_2024.01.02_010200_001"]
    inputs = _sidecar(csv_path)["inputs"]
    assert inputs["n_without_image"] == 0 and inputs["n_duplicate"] == 1
    assert inputs["without_image"] == []
    assert inputs["duplicates"] == ["X_T1_L2_2024.01.02_010200_001_fake.png"]
    assert upper.read_bytes() == before
    skipped = [m for m, kind in _notices(mw) if "skipped" in m]
    assert skipped == [
        "1 skeleton skipped: a file with the same name apart from letter case is measured "
        "instead (named in root_lengths.csv.provenance.json)"
    ]


def test_the_png_mask_scan_lists_every_case_variant(tmp_path):
    (tmp_path / "mask").mkdir()
    for name in ("a.png", "B.PNG", ".hidden.tmp", "c.jpg", "notes.txt"):
        (tmp_path / "mask" / name).write_bytes(b"x")
    (tmp_path / "mask" / "sub.png").mkdir()
    found, shadowed = measure_run.scan_png_files(str(tmp_path / "mask"))
    assert sorted(found) == ["B", "a"] and shadowed == []
    assert measure_run.scan_png_files(str(tmp_path / "missing")) == ({}, [])


def test_provenance_lists_each_kind_apart(tmp_path):
    run = measure_run.MeasureRun(
        measure_run.AREA,
        {},
        str(tmp_path / "mask"),
        project_root=str(tmp_path),
        without_image=["gone.png"],
        duplicates=["B.png", "A.png"],
    )
    run.rows = []
    os.makedirs(run.output_dir)
    run.write()
    inputs = _sidecar(run.csv_path)["inputs"]
    assert (inputs["n_without_image"], inputs["without_image"]) == (1, ["gone.png"])
    assert (inputs["n_duplicate"], inputs["duplicates"]) == (2, ["A.png", "B.png"])


def test_provenance_names_stop_at_the_limit(tmp_path):
    names = [f"n{i:03d}.png" for i in range(measure_run.WITHOUT_IMAGE_NAMES_LIMIT + 5)]
    run = measure_run.MeasureRun(
        measure_run.AREA,
        {},
        str(tmp_path / "mask"),
        project_root=str(tmp_path),
        without_image=names,
    )
    run.rows = []
    os.makedirs(run.output_dir)
    run.write()
    inputs = _sidecar(run.csv_path)["inputs"]
    assert inputs["n_without_image"] == len(names)
    assert inputs["without_image"] == names[: measure_run.WITHOUT_IMAGE_NAMES_LIMIT]
    assert inputs["duplicates"] == [] and inputs["n_duplicate"] == 0


# ------------------------------------------------------------------ the status line
def test_the_measure_status_counts_only_inputs_with_an_image(tmp_path):
    folder = tmp_path / "mask"
    folder.mkdir()
    for name in ("a.png", "b.png"):
        (folder / name).write_bytes(b"x")

    fresh = input_freshness(str(folder), ".png", None, images=["a"])

    assert fresh == Freshness(1, False, without_image=1)
    assert measure_text(0, 0, area=fresh).endswith(
        "1 mask: not measured yet (1 without an image: skipped)"
    )


def test_the_status_without_images_counts_every_file(tmp_path):
    folder = tmp_path / "mask"
    folder.mkdir()
    (folder / "a.png").write_bytes(b"x")
    assert input_freshness(str(folder), ".png", None) == Freshness(1, False)
    assert input_freshness(str(folder), ".png", None, images=[]) == Freshness(
        0, False, without_image=1
    )


class _Entry:
    def __init__(self, folder, name):
        self.name, self.path = name, os.path.join(folder, name)

    def is_file(self):
        return True

    def stat(self):
        return os.stat_result((0, 0, 0, 0, 0, 0, 1, 0, 1, 0))


class _Scan:
    def __init__(self, folder, names):
        self.entries = [_Entry(folder, n) for n in names]

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False


def test_the_status_counts_a_second_file_of_an_image_as_a_duplicate(tmp_path, monkeypatch):
    """Two files of one image exist only on a case-sensitive disk: list them by hand."""
    folder = str(tmp_path / "skeletons")
    monkeypatch.setattr(
        stage_summary.os, "scandir", lambda f: _Scan(f, ["A_fake.png", "a_fake.png", "z_fake.png"])
    )

    fresh = input_freshness(folder, "_fake.png", None, images=["a"])

    assert fresh == Freshness(1, False, without_image=1, duplicates=1)
    assert measure_text(0, 0, length=fresh).startswith(
        "1 skeleton: not measured yet (1 without an image: skipped) (1 duplicate not counted)"
    )
    assert measure_text(0, 0, length=Freshness(3, False, duplicates=2)).startswith(
        "3 skeletons: not measured yet (2 duplicates not counted)"
    )


def test_the_status_text_module_imports_no_image_libraries():
    code = (
        "import sys, app.gui.stage_summary;"
        "print(sorted(m for m in ('cv2', 'numpy', 'torch') if m in sys.modules))"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_files_sharing_a_whole_stem_are_all_accounted_for(tmp_path, monkeypatch):
    """a.png and a.PNG: the lowest name (a.PNG) is listed, the other is shadowed."""
    folder = str(tmp_path / "mask")
    monkeypatch.setattr(measure_run.os, "scandir", lambda f: _Scan(f, ["a.png", "a.PNG", "b.png"]))
    found, shadowed = measure_run.scan_png_files(folder)
    assert found == {"a": os.path.join(folder, "a.PNG"), "b": os.path.join(folder, "b.png")}
    assert shadowed == [("a", os.path.join(folder, "a.png"))]

    split = split_inputs(found, ["a"], shadowed)
    assert split.kept == {"a": os.path.join(folder, "a.PNG")}
    assert split.duplicates == ["a.png"] and split.without_image == ["b.png"]
    # no image for the stem: the shadowed file is without one, not a duplicate
    split = split_inputs(found, ["b"], shadowed)
    assert split.duplicates == [] and split.without_image == ["a.PNG", "a.png"]


def test_a_shadowed_mask_is_recorded_as_a_duplicate(tmp_path, qapp, monkeypatch):  # noqa: F811
    _project(tmp_path, n=1, skeletons=False)
    mask = tmp_path / "mask" / "x_T1_L2_2024.01.02_010200_001.png"
    twin = tmp_path / "x_T1_L2_2024.01.02_010200_001.PNG.copy"
    shutil.copyfile(mask, twin)
    monkeypatch.setattr(
        skeleton_handler,
        "scan_png_files",
        lambda folder: (
            {"x_T1_L2_2024.01.02_010200_001": str(mask)},
            [("x_T1_L2_2024.01.02_010200_001", str(twin))],
        ),
    )
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    csv_path = tmp_path / "mask" / "root_areas.csv"
    assert len(_rows(csv_path)) == 1
    inputs = _sidecar(csv_path)["inputs"]
    assert inputs["n_duplicate"] == 1 and inputs["duplicates"] == [twin.name]
    assert inputs["n_without_image"] == 0
    assert twin.exists()


def test_the_status_counts_a_shadowed_file_as_a_duplicate(tmp_path, monkeypatch):
    folder = str(tmp_path / "mask")
    monkeypatch.setattr(stage_summary.os, "scandir", lambda f: _Scan(f, ["a.png", "a.PNG"]))
    assert input_freshness(folder, ".png", None, images=["a"]) == Freshness(1, False, duplicates=1)


def test_an_unreadable_image_folder_names_the_project_not_the_mask_folder(
    tmp_path,
    qapp,  # noqa: F811
    monkeypatch,
):
    _project(tmp_path, n=1, skeletons=False)
    shown = []
    monkeypatch.setattr(
        skeleton_handler.QMessageBox, "critical", lambda parent, title, text: shown.append(text)
    )

    def refuse(self):
        raise PermissionError("denied")

    monkeypatch.setattr(ProjectLayout, "scan_images", refuse)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()

    assert len(shown) == 1
    assert shown[0].splitlines()[1] == os.path.normpath(str(tmp_path))  # the project root
    assert mw.jobs.running_name() is None

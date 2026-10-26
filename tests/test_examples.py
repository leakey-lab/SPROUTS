"""``docs/examples``: three real photos with their model outputs, the Measure CSVs and a graph.

The folder is a project users open in SPROUTS, and the input of the real-image golden gate
(``tests/test_correct_goldens_real.py``). These tests keep it complete, small, free of local
paths, and consistent with the app: the committed CSVs are what Measure writes for the folder
today, and the committed graph is what ``scripts/make_examples.py`` draws from them.

CSV and SVG text is compared with line endings normalised: a checkout may turn the writer's
``\\r\\n`` into ``\\n`` (git's autocrlf) and the content must still match.
"""

from __future__ import annotations

import csv
import importlib.util
import os
import re
import shutil
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO_ROOT, "docs", "examples")

from app.feedback import RecordingFeedback  # noqa: E402
from app.handlers.job_registry import JobRegistry  # noqa: E402
from app.handlers.skeleton_handler import SkeletonHandler  # noqa: E402
from app.io_utils import imread_unicode, imwrite_unicode  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402

# scripts/ is not a package: load the script by path, as tests/test_fetch_models.py does.
_spec = importlib.util.spec_from_file_location(
    "make_examples", os.path.join(REPO_ROOT, "scripts", "make_examples.py")
)
make_examples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_examples)

LENGTH_CSV = os.path.join("skeletons", "root_lengths.csv")
AREA_CSV = os.path.join("mask", "root_areas.csv")
GRAPH = os.path.join("results", "length_and_area.svg")
REGENERATE = "regenerate with scripts/make_examples.py"
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/")
TEXT_SUFFIXES = {".md", ".csv", ".svg", ".json", ".txt"}
SIZE_LIMIT = 200 * 1024


def photos() -> list[str]:
    return sorted(n for n in os.listdir(EXAMPLES) if n.lower().endswith(".jpg"))


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def rows_of(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_every_example_photo_has_its_model_mask_and_skeleton():
    names = photos()
    assert len(names) == 3
    for name in names:
        stem = os.path.splitext(name)[0]
        mask = imread_unicode(os.path.join(EXAMPLES, "mask", f"{stem}.png"), cv2.IMREAD_UNCHANGED)
        assert mask is not None, f"mask/{stem}.png"
        assert mask.ndim == 2 and mask.shape == (480, 640), f"mask/{stem}.png is not 8-bit L"
        skeleton = imread_unicode(
            os.path.join(EXAMPLES, "skeletons", f"{stem}_fake.png"), cv2.IMREAD_UNCHANGED
        )
        assert skeleton is not None, f"skeletons/{stem}_fake.png"
        assert skeleton.shape[:2] == (256, 341)


def test_the_examples_stay_small_and_free_of_local_paths():
    total = 0
    for folder, _dirs, names in os.walk(EXAMPLES):
        for name in names:
            path = os.path.join(folder, name)
            total += os.path.getsize(path)
            if os.path.splitext(name)[1].lower() in TEXT_SUFFIXES:
                found = LOCAL_PATH.search(read_text(path))
                assert not found, f"{path} names a local path ({found.group(0)!r})"
    assert total < SIZE_LIMIT, f"{total} bytes"


def test_measure_reproduces_the_committed_csvs(tmp_path):
    copy = str(tmp_path / "examples")
    shutil.copytree(EXAMPLES, copy)
    for relative in (LENGTH_CSV, AREA_CSV):
        os.remove(os.path.join(copy, relative))

    make_examples.measure(copy)

    for relative in (LENGTH_CSV, AREA_CSV):
        assert read_text(os.path.join(copy, relative)) == read_text(
            os.path.join(EXAMPLES, relative)
        ), f"{relative}: {REGENERATE}"
    leftovers = sorted(
        n for _f, _d, names in os.walk(copy) for n in names if n.endswith((".json", ".tmp"))
    )
    assert leftovers == [], (
        "Measure's sidecars must be deleted from docs/examples: copy the folder somewhere "
        "before opening it in SPROUTS (the app writes them next to the CSVs)"
    )


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_measure_is_gt_first_like_the_apps_measure(tmp_path, qapp):
    """A project with a GT mask is measured from it, as ``SkeletonHandler`` does, so the
    script and the app write the same area CSV (``Source = gt`` on that row)."""
    stem = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
    by_app, by_script = str(tmp_path / "by_app"), str(tmp_path / "by_script")
    for copy in (by_app, by_script):
        shutil.copytree(EXAMPLES, copy)
        gt = np.zeros((480, 640), np.uint8)
        gt[100:200, 100:250] = 255  # 15,000 px: not what the model mask holds
        gt_path = ProjectLayout(copy).gt_mask_path(stem)
        os.makedirs(os.path.dirname(gt_path))
        imwrite_unicode(gt_path, gt)
        os.remove(os.path.join(copy, AREA_CSV))

    window = SimpleNamespace(
        image_manager=SimpleNamespace(original_folder=by_app, images={}),
        feedback=RecordingFeedback(),
        jobs=JobRegistry(),
    )
    handler = SkeletonHandler(window, generation=SimpleNamespace(is_busy=lambda: False))
    handler.calculate_root_area()
    end = time.monotonic() + 20
    while handler.is_busy() or window.jobs.is_busy():
        assert time.monotonic() < end, "the app's Measure did not finish"
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()

    make_examples.measure(by_script)

    assert read_text(os.path.join(by_script, AREA_CSV)) == read_text(os.path.join(by_app, AREA_CSV))
    sources = {row["Image"]: row["Source"] for row in rows_of(os.path.join(by_script, AREA_CSV))}
    assert sources.pop(stem) == "gt"
    assert set(sources.values()) == {"prediction"}
    area = {r["Image"]: r["Area (mm²)"] for r in rows_of(os.path.join(by_script, AREA_CSV))}
    assert (
        area[stem]
        != {r["Image"]: r["Area (mm²)"] for r in rows_of(os.path.join(EXAMPLES, AREA_CSV))}[stem]
    )


def test_the_graph_is_the_one_the_csvs_draw():
    length_rows = rows_of(os.path.join(EXAMPLES, LENGTH_CSV))
    area_rows = rows_of(os.path.join(EXAMPLES, AREA_CSV))
    drawn = make_examples.build_svg(length_rows, area_rows)

    assert drawn == read_text(os.path.join(EXAMPLES, GRAPH)), f"the graph: {REGENERATE}"
    for row in length_rows:
        assert row["Length (mm)"] in drawn
    for row in area_rows:
        assert row["Area (mm²)"] in drawn
    assert make_examples.build_svg(length_rows, area_rows) == drawn  # deterministic


def test_the_example_csvs_follow_the_current_schema():
    for relative, value in ((LENGTH_CSV, "Length (mm)"), (AREA_CSV, "Area (mm²)")):
        rows = rows_of(os.path.join(EXAMPLES, relative))
        assert len(rows) == 3
        assert list(rows[0]) == [
            "Image",
            "Tube",
            "Position",
            "Date",
            "Time",
            value,
            "Error",
            "Source",
        ]
        assert {row["Source"] for row in rows} == {"prediction"}


def test_the_examples_readme_names_every_shipped_file():
    text = read_text(os.path.join(EXAMPLES, "README.md"))
    for photo in photos():
        assert photo in text
    assert "results/length_and_area.svg" in text
    assert "inputs.json" in text and "provenance.json" in text

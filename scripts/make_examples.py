"""Build ``docs/examples``: copy the three example images with their outputs, measure them
and draw the graph (owner decision O35).

    ./venv/Scripts/python.exe scripts/make_examples.py --source "<folder of the 46k project>"
    ./venv/Scripts/python.exe scripts/make_examples.py

``--source DIR`` first copies nine named files from ``DIR`` (the three photos, their masks
and their skeletons: the production outputs of the full project, copied unchanged, not
regenerated). ``DIR`` is read by direct path, never listed, and never written. Without it
the script only measures the folder as it is and redraws the graph, which is what to run
after a change of the calibration or the length estimator.

Measure runs through the same entry as the app (:class:`app.inference.measure_run.MeasureRun`
with the inputs ``SkeletonHandler`` gives it) and writes ``skeletons/root_lengths.csv`` and
``mask/root_areas.csv``. Its ``*.provenance.json`` and ``*.inputs.json`` files hold clock
time, host details and file times, so they are deleted: the folder stays deterministic.

Standard library and ``app`` only: no Qt, no torch.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from collections.abc import Sequence
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app import provenance  # noqa: E402
from app.calibration import load_project_calibration  # noqa: E402
from app.config import AREA_VALUE_COLUMN, LENGTH_VALUE_COLUMN  # noqa: E402
from app.design import tokens  # noqa: E402
from app.inference import measure_inputs  # noqa: E402
from app.inference.input_split import split_inputs  # noqa: E402
from app.inference.measure_run import AREA, LENGTH, MeasureRun, scan_png_files  # noqa: E402
from app.io_utils import sha256_file  # noqa: E402
from app.project_layout import SKELETON_SUFFIX, ProjectLayout  # noqa: E402

EXAMPLES_DIR = os.path.join(REPO_ROOT, "docs", "examples")
GRAPH_PATH = os.path.join("results", "length_and_area.svg")

#: The three example images (the audit's): tube 1 position 44 (roots), tube 1 position 50
#: (empty skeleton), tube 12 position 47.
STEMS = (
    "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM",
    "24EFCAM1_T001_L050_2024.07.18_111522_001_SMM",
    "24EFCAM1_T012_L047_2024.07.18_101909_001_SMM",
)

WIDTH, HEIGHT = 720, 360
FONT = "sans-serif"


def input_files(stem: str) -> list[str]:
    """The three files of one example, relative to a project folder (``/`` separators)."""
    return [f"{stem}.jpg", f"mask/{stem}.png", f"skeletons/{stem}{SKELETON_SUFFIX}"]


def copy_inputs(source: str, dest: str) -> list[str]:
    """Copy the nine example files from ``source`` into ``dest``, unchanged.

    Each is read by its path under ``source`` (the folder is never listed or written). If
    any is missing, nothing is copied and ``FileNotFoundError`` names them all. Returns the
    copied files, relative to ``dest`` with ``/`` separators.
    """
    wanted = [relative for stem in STEMS for relative in input_files(stem)]
    missing = [r for r in wanted if not os.path.isfile(os.path.join(source, *r.split("/")))]
    if missing:
        raise FileNotFoundError(f"{source} lacks: {', '.join(missing)}")
    for relative in wanted:
        target = os.path.join(dest, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(source, *relative.split("/")), target)
    return wanted


def measure(dest: str) -> None:
    """Measure root length and root area of the project in ``dest``; write its two CSVs.

    The inputs are those of ``SkeletonHandler._measure``: the predictions and the GT files
    (``gt/skeletons``, ``gt/mask``) each split by image, the project's field of view, and
    GT first (O1), so a project with GT files gets ``Source = gt`` on those rows. The
    committed examples have no ``gt/``: every row is ``prediction``. The provenance and
    inputs sidecars are deleted afterwards.
    """
    layout = ProjectLayout(dest)
    image_stems = layout.scan_images()
    calibration = load_project_calibration(layout.root)
    jobs = (
        (
            LENGTH,
            layout.skeletons_dir,
            (layout.scan_skeletons(layout.skeletons_dir), []),
            layout.gt_skeletons_dir,
        ),
        (AREA, layout.mask_dir, scan_png_files(layout.mask_dir), layout.gt_mask_dir),
    )
    for spec, folder, (images, shadowed), gt_folder in jobs:
        split = split_inputs(images, image_stems, shadowed)
        gt_files, gt_shadowed = scan_png_files(gt_folder)
        gt_split = split_inputs(gt_files, image_stems, gt_shadowed)
        gt_prefix = os.path.relpath(gt_folder, layout.root).replace(os.sep, "/") + "/"
        run = MeasureRun(
            spec,
            split.kept,
            folder,
            project_root=layout.root,
            without_image=[*split.without_image, *(gt_prefix + n for n in gt_split.without_image)],
            duplicates=[*split.duplicates, *(gt_prefix + n for n in gt_split.duplicates)],
            gt_files=gt_split.kept,
            photos=image_stems,
            calibration=calibration,
        )
        run.compute()
        csv_path = run.write()
        for sidecar in (
            provenance.sidecar_path(csv_path),
            measure_inputs.inputs_record_path(csv_path),
        ):
            if os.path.exists(sidecar):
                os.remove(sidecar)


def rows_of(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def label_of(row: dict) -> str:
    """``T001 L044`` from the tube and the position, else the image name."""
    try:
        return f"T{int(row['Tube']):03d} L{int(row['Position']):03d}"
    except (KeyError, TypeError, ValueError):
        return row.get("Image", "")


def number(row: dict, column: str) -> float:
    try:
        return float(row[column])
    except (KeyError, TypeError, ValueError):
        return 0.0


def chart(
    rows: list[dict], column: str, unit: str, heading: str, colour: str, top: int
) -> list[str]:
    """One horizontal bar chart in the band that starts at ``top`` (a bar per row, a
    ``<title>`` with the CSV value on each bar)."""
    left, right, bar_h, gap = 130, 120, 28, 12
    scale = (WIDTH - left - right) / (max((number(r, column) for r in rows), default=0) or 1.0)
    parts = [
        f'<text x="20" y="{top + 18}" font-size="15" font-weight="bold" fill="#222222">'
        f"{escape(heading)}</text>"
    ]
    for index, row in enumerate(rows):
        y = top + 34 + index * (bar_h + gap)
        value = row.get(column, "")
        width = number(row, column) * scale
        parts.append(
            f'<text x="{left - 10}" y="{y + 19}" font-size="13" text-anchor="end" '
            f'fill="#222222">{escape(label_of(row))}</text>'
        )
        parts.append(
            f'<rect x="{left}" y="{y}" width="{width:.2f}" height="{bar_h}" fill="{colour}">'
            f"<title>{escape(label_of(row))}: {escape(value)} {escape(unit)}</title></rect>"
        )
        parts.append(
            f'<text x="{left + width + 8:.2f}" y="{y + 19}" font-size="13" fill="#222222">'
            f"{escape(value)} {escape(unit)}</text>"
        )
    bottom = top + 34 + len(rows) * (bar_h + gap) - gap
    parts.append(
        f'<line x1="{left}" y1="{top + 28}" x2="{left}" y2="{bottom + 6}" '
        'stroke="#888888" stroke-width="1"/>'
    )
    return parts


def build_svg(length_rows: list[dict], area_rows: list[dict]) -> str:
    """The graph: root length (skeleton colour) above root area (mask colour), one bar per
    image, drawn from the CSV rows exactly as printed. Deterministic: a fixed 720 x 360
    canvas, no clock, no random."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}">',
        "<title>Root length and root area of the three example images</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
        *chart(length_rows, LENGTH_VALUE_COLUMN, "mm", "Root length (mm)", tokens.SKEL, 8),
        *chart(area_rows, AREA_VALUE_COLUMN, "mm²", "Root area (mm²)", tokens.MASK, 184),
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


def write_graph(dest: str) -> str:
    """Draw the graph of the CSVs in ``dest`` into ``results/length_and_area.svg``."""
    svg = build_svg(
        rows_of(os.path.join(dest, "skeletons", LENGTH.csv_name)),
        rows_of(os.path.join(dest, "mask", AREA.csv_name)),
    )
    path = os.path.join(dest, GRAPH_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build docs/examples (copy, measure, draw).")
    parser.add_argument("--source", help="folder of the full project to copy the nine files from")
    args = parser.parse_args(argv)
    dest = EXAMPLES_DIR
    if args.source:
        for relative in copy_inputs(args.source, dest):
            digest = sha256_file(os.path.join(dest, *relative.split("/")))
            print(f"{digest}  {relative}")
    measure(dest)
    print(f"wrote {write_graph(dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

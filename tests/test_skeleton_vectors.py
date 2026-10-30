"""The Qt-free vector extraction of the skeleton item (app/imaging/skeleton_vectors.py).

The extraction itself (links, merged runs, tiles) is pinned in
tests/test_canvas_layer_skeleton.py, step 2; here: the module loads no Qt, the item draws
with these very functions, and ``clip_box`` clips like the item expects.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.imaging.skeleton_vectors import clip_box

ROOT = Path(__file__).resolve().parents[1]


def test_the_vector_module_loads_no_qt_and_no_cv2():
    code = (
        "import sys, app.imaging.skeleton_vectors; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'PyQt6', 'cv2', 'torch'}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_the_item_draws_with_the_qt_free_functions():
    import app.gui.canvas_layers.skeleton as item_module
    import app.imaging.skeleton_vectors as vectors

    # A from-import: monkeypatching the item module's name reaches the item's extraction.
    assert item_module.tile_segments is vectors.tile_segments
    assert item_module.clip_box is vectors.clip_box
    assert item_module.Box is vectors.Box


@pytest.mark.parametrize(
    ("box", "clipped"),
    [
        (None, (0, 0, 40, 30)),
        ((5, 6, 10, 8), (5, 6, 15, 14)),
        ((-4, -3, 10, 8), (0, 0, 6, 5)),
        ((35, 25, 10, 10), (35, 25, 40, 30)),
        ((50, 40, 10, 10), (50, 40, 40, 30)),  # outside: an empty box (end <= start)
        ((2.9, 3.1, 4.0, 5.0), (2, 3, 6, 8)),  # truncated to int, as the item passes them
    ],
)
def test_clip_box_gives_end_exclusive_edges_inside_the_mask(box, clipped):
    assert clip_box(box, (30, 40)) == clipped
    assert clip_box(box, (30, 40, 4)) == clipped  # extra dimensions are ignored

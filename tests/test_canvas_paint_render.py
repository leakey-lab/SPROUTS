"""PaintCanvas.render_rect (undo, redo and fill repaint their box through it): the box is
written through the premultiplied colour table straight into the canvas pixels, the same
path as ``PaintCanvas.rebuild``, pixel for pixel what Qt's own premultiply of
``lut[plane]`` gives, in place, and fast enough that undo of a whole 2048 x 1536 edit stays
well inside a frame.

Wall-clock: an always-on smoke with about 10x headroom; the strict budget (undo or redo of a
whole-plane patch at 2048 x 1536 <= 3 ms, median) only with ``SPROUTS_PERF_TESTS=1``.
"""

import os
import statistics
import time

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage

from app.gui.paint_canvas import (
    PaintCanvas,
    argb_array_to_image,
    pixmap_to_argb_array,
)
from app.gui.patch_history import PatchHistory

STRICT = os.environ.get("SPROUTS_PERF_TESTS") == "1"


def make_canvas(plane, lut):
    history = PatchHistory()
    history.reset(plane)
    regions: list[QRect] = []
    canvas = PaintCanvas(
        history,
        lut=lambda: lut,
        to_plane=lambda argb, rect: (argb[:, :, 3] > 0).astype(np.uint8),
        fill_source=lambda p: ((p > 0).astype(np.uint8) * 255, 1),
        on_region=lambda rect: regions.append(QRect(rect)),
        on_replaced=lambda: None,
    )
    canvas.rebuild()
    return canvas, regions


def qt_premultiplied(lut: np.ndarray, plane: np.ndarray) -> np.ndarray:
    """Today's canvas pixels: Qt's premultiply of the ARGB32 image ``lut[plane]``."""
    image = argb_array_to_image(lut[plane])
    return pixmap_to_argb_array(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))


@pytest.mark.parametrize("box", [(0, 0, 53, 37), (7, 3, 29, 21), (52, 36, 1, 1), (11, 0, 1, 37)])
def test_render_rect_writes_the_premultiplied_lut_colours_of_its_box(app, box):
    rng = np.random.default_rng(11)
    lut = rng.integers(0, 256, (256, 4), dtype=np.uint8)  # translucent colours included
    plane = rng.integers(0, 256, (37, 53), dtype=np.uint8)
    canvas, regions = make_canvas(plane, lut)
    before = pixmap_to_argb_array(canvas.image)
    plane[:] = rng.integers(0, 256, plane.shape, dtype=np.uint8)  # behind the canvas's back

    canvas.render_rect(box)

    x, y, w, h = box
    expected = before.copy()
    expected[y : y + h, x : x + w] = qt_premultiplied(lut, plane)[y : y + h, x : x + w]
    assert np.array_equal(pixmap_to_argb_array(canvas.image), expected)
    assert regions == [QRect(*box)]


def test_render_rect_paints_the_canvas_in_place(app):
    lut = np.zeros((256, 4), np.uint8)
    lut[1:] = (160, 214, 95, 255)
    plane = np.zeros((480, 640), np.uint8)
    canvas, _ = make_canvas(plane, lut)
    image = canvas.image
    serial = image.cacheKey() >> 32
    plane[100:300, 50:400] = 1

    canvas.render_rect((40, 90, 400, 250))

    assert canvas.image is image and image.cacheKey() >> 32 == serial
    assert np.array_equal(pixmap_to_argb_array(image), qt_premultiplied(lut, plane))


def _undo_redo_ms(canvas: PaintCanvas, rounds: int) -> float:
    times = []
    for _ in range(rounds):
        for step in (canvas.undo, canvas.redo):
            t0 = time.perf_counter()
            assert step() is not None
            times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def _whole_plane_edit_2048():
    lut = np.zeros((256, 4), np.uint8)
    lut[1:] = (160, 214, 95, 255)
    rng = np.random.default_rng(5)
    plane = (rng.random((1536, 2048)) < 0.4).astype(np.uint8)
    canvas, _ = make_canvas(plane, lut)
    canvas.clear()  # one whole-plane patch
    return canvas


def test_undo_of_a_whole_plane_edit_at_2048_is_well_inside_a_frame(app):
    canvas = _whole_plane_edit_2048()
    assert _undo_redo_ms(canvas, 3) < 15.0


@pytest.mark.skipif(not STRICT, reason="wall-clock budget: set SPROUTS_PERF_TESTS=1")
def test_undo_of_a_whole_plane_edit_at_2048_budget(app):
    canvas = _whole_plane_edit_2048()
    assert _undo_redo_ms(canvas, 10) <= 3.0

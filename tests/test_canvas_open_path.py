"""The image-open path of the raster layers: fewer full-size allocations, same pixels.

* ``PaintCanvas.rebuild`` writes the plane into the canvas it already has when the size is
  unchanged (opening the next image of a folder), instead of a new 12 MiB image at 2048 x 1536;
  a new size gets a new canvas.
* ``MaskDocument.open`` takes the saved mask's 0/1 plane from the boolean mask as it is
  (``uint8`` view), instead of filling a second array.
"""

import os

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QImage

from app.gui.correct.mask_document import MaskDocument
from app.gui.correct.mask_tools import argb_to_plane, fill_source, mask_lut
from app.gui.paint_canvas import (
    PaintCanvas,
    argb_array_to_image,
    pixmap_to_argb_array,
)
from app.gui.patch_history import PatchHistory
from app.masks import MaskStore

pytestmark = pytest.mark.usefixtures("app")


def make_canvas(lut: np.ndarray):
    history = PatchHistory()
    replaced = []
    canvas = PaintCanvas(
        history,
        lut=lambda: lut,
        to_plane=argb_to_plane,
        fill_source=fill_source,
        on_region=lambda rect: None,
        on_replaced=lambda: replaced.append(1),
    )
    return canvas, history, replaced


def premultiplied(lut: np.ndarray, plane: np.ndarray) -> np.ndarray:
    image = argb_array_to_image(lut[plane]).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    return pixmap_to_argb_array(image)


def test_rebuild_reuses_the_canvas_of_the_same_size():
    rng = np.random.default_rng(1)
    lut = rng.integers(0, 256, (256, 4), dtype=np.uint8)
    canvas, history, replaced = make_canvas(lut)
    history.reset(rng.integers(0, 4, (48, 64), dtype=np.uint8))
    canvas.rebuild()
    first = canvas.image
    canvas.begin(QPoint(5, 5), QColor(255, 255, 255), 9, erase=False)  # never committed

    second_plane = rng.integers(0, 4, (48, 64), dtype=np.uint8)
    history.reset(second_plane)
    canvas.rebuild()

    assert canvas.image is first and not canvas.in_stroke()
    assert np.array_equal(pixmap_to_argb_array(canvas.image), premultiplied(lut, second_plane))
    assert replaced == [1, 1]


def test_rebuild_makes_a_new_canvas_for_a_new_size_and_none_without_a_plane():
    lut = mask_lut()
    canvas, history, _ = make_canvas(lut)
    history.reset(np.ones((48, 64), np.uint8))
    canvas.rebuild()
    first = canvas.image
    plane = np.zeros((30, 40), np.uint8)
    plane[3:9, 4:20] = 1
    history.reset(plane)
    canvas.rebuild()
    assert canvas.image is not first
    assert (canvas.image.width(), canvas.image.height()) == (40, 30)
    assert canvas.image.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert np.array_equal(pixmap_to_argb_array(canvas.image), premultiplied(lut, plane))
    history.reset(None)
    canvas.rebuild()
    assert canvas.image is None


def _project_with_mask(tmp_path, mask: np.ndarray) -> str:
    image = tmp_path / "SYNTH_T001_L001_2024.01.01_000000_001_SMM.png"
    cv2.imwrite(str(image), np.full((*mask.shape, 3), 90, np.uint8))
    os.makedirs(tmp_path / "mask", exist_ok=True)
    cv2.imwrite(str(tmp_path / "mask" / image.name), mask)
    return str(image)


def test_open_takes_the_saved_masks_0_1_plane_and_it_edits_like_any_plane(tmp_path):
    rng = np.random.default_rng(2)
    saved = rng.integers(0, 256, (61, 83), dtype=np.uint8)  # grey values around the threshold
    image = _project_with_mask(tmp_path, saved)
    document = MaskDocument()

    assert document.open(image, (83, 61)) == []

    plane = document.history.plane
    assert plane.dtype == np.uint8 and plane.flags.c_contiguous and plane.flags.writeable
    assert np.array_equal(plane, (saved > 127).astype(np.uint8))
    assert np.array_equal(plane, MaskStore.for_image(image).read(image).astype(np.uint8))
    assert not document.is_dirty()

    before = plane.copy()
    document.history.commit((0, 0, 10, 10), np.ones((10, 10), np.uint8))
    document.touch()
    assert document.is_dirty() and plane[:10, :10].all()
    document.history.undo()
    document.touch()
    assert np.array_equal(document.history.plane, before) and not document.is_dirty()
    document.history.commit((20, 20, 5, 5), np.zeros((5, 5), np.uint8))
    document.touch()
    document.revert()
    assert np.array_equal(document.history.plane, before) and not document.is_dirty()

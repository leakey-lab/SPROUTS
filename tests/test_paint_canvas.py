"""PaintCanvas (C9): today's MaskDrawingMixin painting, moved onto a QImage canvas that sits
over a PatchHistory plane (the Correct mask layer and Trace GT share it).

The op-by-op identity with the deleted mask editor is carried by the pixel goldens
(``tests/data/correct_goldens.json``, replayed by ``test_correct_mask_goldens.py`` and
``test_correct_goldens.py``).
"""

import hashlib
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gui.paint_canvas import (  # noqa: E402
    PaintCanvas,
    argb_array_to_image,
    pixmap_to_argb_array,
)
from app.gui.patch_history import PatchHistory  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402

MASK = QColor(tokens.MASK)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


# --------------------------------------------------------------------------- helpers
def mask_lut() -> np.ndarray:
    """Plane value 1 drawn opaque in the mask colour, 0 transparent (B, G, R, A rows)."""
    lut = np.zeros((256, 4), np.uint8)
    lut[1:] = (MASK.blue(), MASK.green(), MASK.red(), 255)
    return lut


def mask_to_plane(argb: np.ndarray, rect) -> np.ndarray:
    return (argb[:, :, 3] > 0).astype(np.uint8)


def mask_fill_source(plane: np.ndarray) -> tuple[np.ndarray, int]:
    return (plane > 0).astype(np.uint8) * 255, 1


class Recorder:
    """The canvas callbacks, recorded."""

    def __init__(self) -> None:
        self.regions: list[QRect] = []
        self.replaced = 0
        self.patches: list[tuple[object, bool]] = []

    def region(self, rect: QRect) -> None:
        self.regions.append(QRect(rect))

    def whole(self) -> None:
        self.replaced += 1

    def patch(self, patch, forward: bool) -> None:
        self.patches.append((patch, forward))


def make_canvas(plane, *, lut=mask_lut, on_patch=True):
    history = PatchHistory()
    history.reset(plane)
    rec = Recorder()
    canvas = PaintCanvas(
        history,
        lut=lut,
        to_plane=mask_to_plane,
        fill_source=mask_fill_source,
        on_region=rec.region,
        on_replaced=rec.whole,
        on_patch=rec.patch if on_patch else None,
    )
    canvas.rebuild()
    return canvas, rec


def shown(canvas) -> np.ndarray:
    return pixmap_to_argb_array(canvas.image)


def stroke(canvas, *points, size=6, erase=False):
    canvas.begin(QPoint(*points[0]), MASK, size, erase=erase)
    for p in points[1:]:
        canvas.extend(QPoint(*p))
    return canvas.commit()


def square_outline(w=120, h=90) -> np.ndarray:
    plane = np.zeros((h, w), np.uint8)
    plane[20:60, 30:80] = 1
    plane[22:58, 32:78] = 0  # a closed 2 px outline around (32..77, 22..57)
    return plane


# --------------------------------------------------------------------------- rebuild
@pytest.mark.parametrize("size", [(640, 480), (81, 61)])
def test_rebuild_draws_exactly_the_lut_colour_of_every_plane_value(app, size):
    w, h = size
    plane = (np.random.default_rng(1).random((h, w)) < 0.3).astype(np.uint8)
    canvas, rec = make_canvas(plane)
    assert canvas.image.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert (canvas.image.width(), canvas.image.height()) == (w, h)
    assert np.array_equal(shown(canvas), mask_lut()[plane])
    assert rec.replaced == 1


def test_rebuild_draws_a_class_id_plane_over_a_transparent_background(app):
    lut = np.zeros((256, 4), np.uint8)
    lut[:] = (128, 0, 255, 255)  # unknown ids: magenta
    lut[1] = (57, 214, 95, 255)
    lut[2] = (104, 168, 240, 255)
    lut[3] = (246, 154, 195, 255)
    lut[0] = 0  # background: transparent
    plane = np.random.default_rng(2).integers(0, 5, (61, 83), dtype=np.uint8)
    canvas, _ = make_canvas(plane, lut=lambda: lut)
    assert np.array_equal(shown(canvas), lut[plane])


def test_rebuild_premultiplies_translucent_colours_exactly_as_qt_does(app):
    """Today's canvas was ``QPixmap.fromImage(ARGB32 lut[plane])``: Qt's own premultiply."""
    rng = np.random.default_rng(4)
    lut = rng.integers(0, 256, (256, 4), dtype=np.uint8)
    plane = rng.integers(0, 256, (37, 53), dtype=np.uint8)
    canvas, _ = make_canvas(plane, lut=lambda: lut)
    todays = argb_array_to_image(lut[plane]).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )
    assert np.array_equal(shown(canvas), pixmap_to_argb_array(todays))


def test_a_lookup_table_of_the_wrong_shape_is_refused(app):
    with pytest.raises(ValueError, match="256, 4"):
        make_canvas(np.zeros((4, 4), np.uint8), lut=lambda: np.zeros((2, 4), np.uint8))


def test_rebuild_without_a_plane_empties_the_canvas(app):
    canvas, rec = make_canvas(np.zeros((10, 10), np.uint8))
    canvas.history.reset(None)
    canvas.rebuild()
    assert canvas.image is None and rec.replaced == 2
    canvas.begin(QPoint(1, 1), MASK, 3, erase=False)
    assert not canvas.in_stroke()
    assert canvas.commit() is None
    assert canvas.fill(QPoint(1, 1)) is None
    assert canvas.clear() is None
    assert canvas.undo() is None and canvas.redo() is None
    assert rec.regions == [] and rec.patches == []


# --------------------------------------------------------------------------- strokes
def test_a_stroke_is_one_history_entry_and_the_canvas_shows_the_plane(app):
    canvas, rec = make_canvas(np.zeros((90, 120), np.uint8))
    assert not canvas.in_stroke()
    canvas.begin(QPoint(10, 10), MASK, 6, erase=False)
    assert canvas.in_stroke()
    canvas.extend(QPoint(40, 30))
    assert canvas.history.plane[30, 40] == 0  # painted on the canvas, not committed yet
    patch = canvas.commit()
    assert not canvas.in_stroke()
    assert patch is not None and len(canvas.history.undo_stack) == 1
    assert canvas.history.plane[30, 40] == 1 and canvas.history.plane[10, 10] == 1
    assert rec.patches == [(patch, True)]
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])


def test_commit_without_paint_is_none_and_records_nothing(app):
    canvas, rec = make_canvas(np.zeros((20, 20), np.uint8))
    assert canvas.commit() is None
    stroke(canvas, (5, 5))
    assert canvas.commit() is None  # the stroke was already committed
    assert len(canvas.history.undo_stack) == 1 and len(rec.patches) == 1


def test_extend_without_begin_paints_nothing(app):
    canvas, rec = make_canvas(np.zeros((20, 20), np.uint8))
    canvas.extend(QPoint(5, 5))
    assert rec.regions == [] and not shown(canvas).any()


def test_the_eraser_clears_to_transparent(app):
    canvas, _ = make_canvas(np.ones((20, 30), np.uint8))
    stroke(canvas, (2, 10), (27, 10), size=3, erase=True)
    assert canvas.history.plane[10, 2:28].sum() == 0
    assert canvas.history.plane[5].all()
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])


def test_segments_repaint_their_box_padded_by_radius_plus_two(app):
    """Today's dirty rects: segment box padded by r + 2, aligned, clipped to the canvas."""
    canvas, rec = make_canvas(np.zeros((100, 100), np.uint8))
    canvas.begin(QPoint(10, 10), MASK, 6, erase=False)  # centre 10.5, pad 3 + 2
    canvas.extend(QPoint(20, 10))
    canvas.commit()
    canvas.begin(QPoint(0, 0), MASK, 6, erase=False)  # clipped at the top-left corner
    canvas.commit()
    assert rec.regions == [QRect(5, 5, 11, 11), QRect(5, 5, 21, 11), QRect(0, 0, 6, 6)]


def test_a_new_stroke_commits_the_one_in_progress_first(app):
    """No abort (C2): pixels on the canvas always reach the plane."""
    canvas, rec = make_canvas(np.zeros((40, 40), np.uint8))
    canvas.begin(QPoint(5, 5), MASK, 4, erase=False)
    canvas.begin(QPoint(30, 30), MASK, 4, erase=False)
    assert len(canvas.history.undo_stack) == 1 and canvas.history.plane[5, 5] == 1
    canvas.commit()
    assert len(canvas.history.undo_stack) == 2 and canvas.history.plane[30, 30] == 1
    assert [forward for _patch, forward in rec.patches] == [True, True]
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])


def test_rebuild_mid_stroke_drops_the_uncommitted_pixels_with_the_old_canvas(app):
    canvas, _ = make_canvas(np.zeros((40, 40), np.uint8))
    canvas.begin(QPoint(5, 5), MASK, 4, erase=False)
    canvas.rebuild()
    assert not canvas.in_stroke()
    canvas.extend(QPoint(20, 20))
    assert canvas.commit() is None and len(canvas.history.undo_stack) == 0
    assert not shown(canvas).any()


def test_painting_never_copies_the_canvas(app):
    """PE-04: a segment paints in place (the image serial never changes)."""
    canvas, _ = make_canvas(np.zeros((480, 640), np.uint8))
    serial = canvas.image.cacheKey() >> 32
    canvas.begin(QPoint(10, 10), MASK, 12, erase=False)
    for i in range(100):
        canvas.extend(QPoint(10 + i * 5, 10 + i * 4))
        assert canvas.image.cacheKey() >> 32 == serial
    canvas.commit()
    assert canvas.image.cacheKey() >> 32 == serial


# --------------------------------------------------------------------------- fill / clear
def test_fill_inside_a_closed_outline_is_one_entry(app):
    canvas, rec = make_canvas(square_outline())
    patch = canvas.fill(QPoint(50, 40))
    assert patch is not None and len(canvas.history.undo_stack) == 1
    assert canvas.history.plane[22:58, 32:78].all()
    assert rec.patches == [(patch, True)]
    assert rec.regions == [QRect(30, 20, 50, 40)]  # the outline's bounding box
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])


def test_fill_outside_every_outline_is_none_with_no_history_entry(app):
    canvas, rec = make_canvas(square_outline())
    before = canvas.history.plane.copy()
    assert canvas.fill(QPoint(5, 5)) is None
    assert len(canvas.history.undo_stack) == 0 and rec.patches == [] and rec.regions == []
    assert np.array_equal(canvas.history.plane, before)


def test_clear_zeros_the_plane_as_one_entry_and_empties_the_canvas(app):
    canvas, rec = make_canvas(square_outline())
    patch = canvas.clear()
    assert patch is not None and not canvas.history.plane.any()
    assert len(canvas.history.undo_stack) == 1 and rec.patches == [(patch, True)]
    assert rec.replaced == 2 and not shown(canvas).any()


def test_clear_commits_a_stroke_in_progress_first(app):
    canvas, _ = make_canvas(np.zeros((40, 40), np.uint8))
    canvas.begin(QPoint(5, 5), MASK, 4, erase=False)
    canvas.clear()
    assert len(canvas.history.undo_stack) == 2  # the stroke, then the clear
    canvas.undo()
    assert canvas.history.plane[5, 5] == 1
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])


# --------------------------------------------------------------------------- history
def test_undo_and_redo_repaint_the_patch_and_report_the_direction(app):
    canvas, rec = make_canvas(np.zeros((60, 80), np.uint8))
    stroke(canvas, (10, 10), (50, 40), size=5)
    painted = shown(canvas)
    rec.regions.clear()
    rec.patches.clear()
    undone = canvas.undo()
    assert undone is not None and not canvas.history.plane.any() and not shown(canvas).any()
    assert rec.regions == [QRect(*undone.rect)] and rec.patches == [(undone, False)]
    redone = canvas.redo()
    assert redone is undone and rec.patches[-1] == (redone, True)
    assert np.array_equal(shown(canvas), painted)
    assert canvas.redo() is None


def test_undo_waits_for_the_stroke_in_progress(app):
    """Today's guard: a stroke not yet committed is not in the history."""
    canvas, _ = make_canvas(np.zeros((40, 40), np.uint8))
    stroke(canvas, (5, 5))
    canvas.begin(QPoint(30, 30), MASK, 4, erase=False)
    assert canvas.undo() is None and canvas.redo() is None
    assert len(canvas.history.undo_stack) == 1 and canvas.in_stroke()


def test_render_rect_repaints_from_the_plane(app):
    canvas, rec = make_canvas(np.zeros((30, 40), np.uint8))
    canvas.history.plane[5:10, 5:15] = 1  # changed behind the canvas's back
    canvas.render_rect((0, 0, 20, 12))
    assert rec.regions == [QRect(0, 0, 20, 12)]
    assert np.array_equal(shown(canvas), mask_lut()[canvas.history.plane])
    canvas.render_rect((35, 25, 10, 10))  # clipped to the plane
    assert rec.regions[-1] == QRect(35, 25, 5, 5)


# --------------------------------------------------------------------------- array helpers
def test_argb_arrays_read_the_same_from_an_image_and_a_pixmap(app):
    arr = np.zeros((7, 9, 4), np.uint8)
    arr[..., 3] = 255
    arr[2:5, 3:8] = (95, 214, 160, 255)
    image = argb_array_to_image(arr)
    assert image.format() == QImage.Format.Format_ARGB32
    assert np.array_equal(pixmap_to_argb_array(image), arr)
    assert np.array_equal(pixmap_to_argb_array(QPixmap.fromImage(image)), arr)


def test_argb_array_to_image_owns_its_pixels(app):
    arr = np.full((4, 6, 4), 200, np.uint8)
    image = argb_array_to_image(arr)
    arr[:] = 0
    assert pixmap_to_argb_array(image)[0, 0].tolist() == [200, 200, 200, 200]
    out = pixmap_to_argb_array(image)
    out[:] = 1  # a copy: the image is untouched
    assert pixmap_to_argb_array(image)[0, 0].tolist() == [200, 200, 200, 200]


# --------------------------------------------------------------------------- 1 px dabs (O11)
# sha256[:16] of the plane after one dab at (19, 17) on a 40 x 40 plane, captured before W3-T5:
# sizes 2 and above must stay exactly as they were.
DAB_SHA = {
    (2, False): "04ad7f8a2be67837",
    (3, False): "770b8e77db9ac324",
    (4, False): "a47d91a5771d664b",
    (5, False): "7f1ff7ffcfa1ba9c",
    (6, False): "f7fbc55836f16aa5",
    (7, False): "f0d6c30a9a45804b",
    (8, False): "57768f883ee3dea4",
    (9, False): "e6f941c821a27364",
    (2, True): "a6f194924716dabe",
    (3, True): "743a8c4629f12bb1",
    (4, True): "9ad6bfdce6b94e98",
    (5, True): "495d81834eea816e",
    (6, True): "6a3067d00cc76a00",
    (7, True): "68a49e42c1536a52",
    (8, True): "f94b31cd517c936d",
    (9, True): "b4f54e25e8a491a0",
}
# The (x, y) pixels a 1 px drag (3, 8) -> (8, 3) painted before W3-T5 (audit E4).
ANTI_DIAGONAL_TODAY = {(3, 9), (4, 8), (5, 7), (6, 6), (7, 5), (8, 4)}


def test_a_one_pixel_click_paints_exactly_its_pixel(app):
    canvas, _ = make_canvas(np.zeros((20, 30), np.uint8))
    patch = stroke(canvas, (7, 5), size=1)
    plane = canvas.history.plane
    assert plane.sum() == 1 and plane[5, 7] == 1
    assert patch is not None and len(canvas.history.undo_stack) == 1
    assert np.array_equal(shown(canvas), mask_lut()[plane])


def test_a_one_pixel_eraser_click_clears_exactly_its_pixel(app):
    canvas, _ = make_canvas(np.ones((20, 30), np.uint8))
    stroke(canvas, (7, 5), size=1, erase=True)
    plane = canvas.history.plane
    assert plane.sum() == 599 and plane[5, 7] == 0
    assert np.array_equal(shown(canvas), mask_lut()[plane])


@pytest.mark.parametrize("erase", [False, True])
@pytest.mark.parametrize("size", range(2, 10))
def test_dabs_of_sizes_2_to_9_are_unchanged(app, size, erase):
    base = np.ones((40, 40), np.uint8) if erase else np.zeros((40, 40), np.uint8)
    canvas, _ = make_canvas(base)
    stroke(canvas, (19, 17), size=size, erase=erase)
    digest = hashlib.sha256(canvas.history.plane.tobytes()).hexdigest()[:16]
    assert digest == DAB_SHA[(size, erase)]


def test_a_one_pixel_anti_diagonal_drag_gains_its_pressed_pixel(app):
    canvas, _ = make_canvas(np.zeros((20, 30), np.uint8))
    stroke(canvas, (3, 8), (8, 3), size=1)
    ys, xs = np.nonzero(canvas.history.plane)
    painted = set(zip(xs.tolist(), ys.tolist(), strict=True))
    assert canvas.history.plane[8, 3] == 1  # the pressed pixel, unpainted before
    assert ANTI_DIAGONAL_TODAY <= painted

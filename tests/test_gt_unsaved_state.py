"""Trace GT: unsaved means "differs from the tracing as saved or opened" (V7, 2026-09-30).

Every edit, undo and redo used to set an "unsaved" flag that only a save or Discard
cleared. Painting and then undoing every stroke left Trace GT "Unsaved", so leaving the
image asked "Save changes to the GT for ...?" although nothing had changed, while Correct
went back to "Saved" in the same situation. Now the editor compares the tracing with the
one saved (or opened) after each action: the class ids pixel by pixel (one ``cv2.norm``
pass, about 0.05 ms at 2048 x 1536) and the "checked" ticks. A stroke in progress counts
as unsaved; a tracing remapped on open (unknown classes became Ignore) stays unsaved until
it is saved or discarded.
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gt import Tracing, default_registry, gt_path, read_tracing, write_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

H, W = 60, 80
STRICT = os.environ.get("SPROUTS_PERF_TESTS") == "1"
strict = pytest.mark.skipif(not STRICT, reason="wall-clock budget: set SPROUTS_PERF_TESTS=1")
_DISCARD = QMessageBox.StandardButton.Discard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt (one fails the test), no GL probing, no write to the user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def editor(app):
    """A GT editor (on ``image_path`` when given), closed and deleted after the test."""
    made = []

    def _make(image_path=None):
        iface = GtTracingInterface()
        made.append(iface)
        iface.resize(900, 700)
        if image_path:
            iface.load_image(image_path)
        return iface

    yield _make
    delete_widgets(made)


def _photo(folder, name="plot_001.png", w=W, h=H) -> str:
    path = str(folder / name)
    cv2.imwrite(path, np.full((h, w, 3), 90, np.uint8))
    return path


@pytest.fixture
def image(tmp_path):
    return _photo(tmp_path)


def _stroke(iface, *points, size=6):
    iface.size_slider.setValue(size)
    iface.begin_stroke(QPoint(*points[0]))
    for p in points[1:]:
        iface.continue_stroke(QPoint(*p))
    iface.end_stroke()


def _chip(iface) -> str:
    return iface.state_label.text()


def _record(signal) -> list:
    seen = []
    signal.connect(seen.append)
    return seen


def _tick(iface, key, checked=True):
    iface.class_panel._rows[key].checkbox.setChecked(checked)


# ------------------------------------------------------------------ undo / redo
def test_undoing_every_stroke_leaves_nothing_to_save(editor, image):
    iface = editor(image)
    changes = _record(iface.dirty_changed)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30))
    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55))

    iface.undo()
    assert iface.has_unsaved_changes()  # the root stroke is still there
    iface.undo()

    assert not iface.has_unsaved_changes()
    assert changes == [True, False]
    assert "No GT" in _chip(iface)  # the chip as opened, not "Unsaved"
    assert iface.maybe_save_changes() is True  # no prompt (the guard fails on one)
    iface.redo()
    assert iface.has_unsaved_changes() and changes == [True, False, True]


def test_undo_back_to_the_saved_tracing_is_saved_again(editor, image):
    iface = editor(image)
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30))
    assert iface.save_mask()
    iface.set_active_class("nodule")
    _stroke(iface, (40, 5), (40, 55))
    changes = _record(iface.dirty_changed)

    iface.undo()
    assert not iface.has_unsaved_changes()
    assert "Saved · 1/3 checked" in _chip(iface)
    iface.redo()
    assert iface.has_unsaved_changes() and "Unsaved" in _chip(iface)
    iface.undo()
    assert not iface.has_unsaved_changes()
    assert changes == [False, True, False]


def test_leaving_after_undoing_everything_asks_nothing_and_writes_nothing(editor, tmp_path):
    first, second = _photo(tmp_path), _photo(tmp_path, "plot_002.png")
    iface = editor(first)
    _stroke(iface, (10, 30), (70, 30))
    iface.undo()

    iface.load_image(second)  # the guard fails on a prompt

    assert iface.current_image_path == second
    assert not os.path.exists(gt_path(first))


def test_pgdn_after_undoing_everything_moves_on_without_saving(editor, image):
    iface = editor(image)
    moves = _record(iface.navigate_requested)
    _stroke(iface, (10, 30), (70, 30))
    iface.undo()

    iface.navigate(1)

    assert moves == [1]
    assert not os.path.exists(gt_path(image))  # an unchanged tracing is not written


# ------------------------------------------------------------------ edits that change nothing
def test_a_stroke_in_progress_is_unsaved_and_one_that_changed_nothing_is_not(editor, image):
    iface = editor(image)
    iface.eraser_button.setChecked(True)  # erasing an empty tracing changes no pixel
    iface.size_slider.setValue(6)
    iface.begin_stroke(QPoint(10, 30))
    iface.continue_stroke(QPoint(70, 30))
    assert iface.has_unsaved_changes()  # the stroke is not committed yet

    iface.end_stroke()

    assert not iface.has_unsaved_changes()
    assert "No GT" in _chip(iface)


def test_a_click_outside_the_image_leaves_the_tracing_saved(editor, image):
    iface = editor(image)
    _stroke(iface, (W + 40, H + 40))  # a dab entirely off the canvas
    assert not iface.has_unsaved_changes()


def test_clearing_back_to_the_opened_tracing_is_not_a_change(editor, image):
    """Pixels decide, not the history: two actions later the tracing is as opened."""
    iface = editor(image)
    _stroke(iface, (10, 30), (70, 30))
    iface.clear_mask()

    assert not iface.has_unsaved_changes()
    assert len(iface.undo_stack) == 2
    iface.undo()  # the stroke is back
    assert iface.has_unsaved_changes()


def test_erasing_a_stroke_by_hand_is_saved_again(editor, image):
    iface = editor(image)
    assert iface.save_mask()  # an empty tracing on disk
    iface.set_active_class("root")
    _stroke(iface, (10, 30), (70, 30))
    assert iface.has_unsaved_changes()

    iface.eraser_button.setChecked(True)
    _stroke(iface, (5, 30), (75, 30), size=20)

    assert not iface.edits.plane.any()
    assert not iface.has_unsaved_changes()
    assert "Saved" in _chip(iface)


def test_clearing_an_empty_tracing_changes_nothing(editor, image):
    iface = editor(image)
    changes = _record(iface.dirty_changed)
    iface.clear_mask()
    assert not iface.has_unsaved_changes() and changes == []


# ------------------------------------------------------------------ the "checked" ticks
def test_ticking_and_unticking_a_class_leaves_the_tracing_saved(editor, image):
    iface = editor(image)
    changes = _record(iface.dirty_changed)

    _tick(iface, "nodule")
    assert iface.has_unsaved_changes()
    _tick(iface, "nodule", False)

    assert not iface.has_unsaved_changes()
    assert changes == [True, False]


def test_a_tick_still_differs_after_its_stroke_is_undone(editor, image):
    iface = editor(image)
    _tick(iface, "root_tip")
    _stroke(iface, (10, 30), (70, 30))
    iface.undo()
    assert iface.has_unsaved_changes()  # the tick is still new
    assert iface.save_mask()
    assert read_tracing(gt_path(image)).reviewed == {"root_tip"}


# ------------------------------------------------------------------ unsaved as opened
def _write_with_unknown_class(image_path):
    """A GT file from another project: id 4 = 'hypha', unknown here (becomes Ignore)."""
    other = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    ids = np.zeros((H, W), np.uint8)
    ids[5:15, 5:15] = 4
    write_tracing(gt_path(image_path), Tracing(ids, frozenset({"root"})), other)


def test_a_remapped_tracing_stays_unsaved_after_undoing_every_stroke(editor, image, monkeypatch):
    _write_with_unknown_class(image)
    iface = editor(image)
    assert iface.has_unsaved_changes() and "remapped" in _chip(iface)

    _stroke(iface, (10, 40), (70, 40))
    iface.undo()

    assert iface.has_unsaved_changes()  # the remap itself is still unsaved
    assert "remapped" in _chip(iface)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: _DISCARD)
    assert iface.maybe_save_changes() is True  # Discard: the file stays as it is
    assert not iface.has_unsaved_changes()
    _stroke(iface, (10, 40), (70, 40))
    iface.undo()
    assert not iface.has_unsaved_changes()  # back at the tracing as Discard left it


# ------------------------------------------------------------------ cost
def test_a_class_count_change_needs_no_pixel_compare(editor, image, monkeypatch):
    """GT keeps pixel counts per class: while they differ from the saved ones the tracing
    is unsaved without a full pass; the pixels are compared only when they match."""
    import app.gui.raster_editor_base as base

    calls = []
    real = base._same_plane
    monkeypatch.setattr(base, "_same_plane", lambda a, b: calls.append(1) or real(a, b))
    iface = editor(image)
    _stroke(iface, (10, 30), (70, 30))
    _stroke(iface, (10, 40), (70, 40))
    iface.undo()
    assert calls == [] and iface.has_unsaved_changes()

    iface.undo()  # every class count is back: one pass decides

    assert calls == [1] and not iface.has_unsaved_changes()


def test_the_same_class_counts_elsewhere_are_still_a_change(editor, image):
    iface = editor(image)
    iface.set_active_class("root")
    _stroke(iface, (15, 15))  # one dab
    assert iface.save_mask()
    iface.eraser_button.setChecked(True)
    _stroke(iface, (15, 15), size=30)  # erased
    iface.brush_button.setChecked(True)

    _stroke(iface, (50, 40))  # the same dab elsewhere: the same pixel count

    assert np.array_equal(iface._counts, iface._saved_counts)
    assert iface.has_unsaved_changes()


def _big_editor(editor, tmp_path):
    iface = editor(_photo(tmp_path, "big.png", 2048, 1536))
    iface.set_active_class("root")
    _stroke(iface, (100, 100), (1900, 1400), size=40)
    iface.undo()  # equal planes: the comparison runs to the last pixel
    return iface


def _median_check_ms(iface, count=30) -> float:
    times = []
    for _ in range(count):
        t0 = time.perf_counter()
        iface._differs_from_saved()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def test_the_check_is_cheap_on_a_2048_tracing(editor, tmp_path):
    """About 0.05 ms (cv2.norm over 3.1 MB); 2 ms leaves 40x headroom on a busy machine."""
    iface = _big_editor(editor, tmp_path)
    assert not iface.has_unsaved_changes()
    assert _median_check_ms(iface) < 2.0


@strict
def test_the_check_is_within_0_25_ms_on_a_2048_tracing(editor, tmp_path):
    assert _median_check_ms(_big_editor(editor, tmp_path), 200) <= 0.25

"""Trace GT's fast paths give exactly the results of the slow ones they replaced (G3).

- ``_ids_from_argb`` matches only the pixels a stroke changed (the rest keep their
  committed id) instead of testing every class colour on the whole stroke box;
- class counts come from a histogram of the new values in the plane and the old values of
  the patch (``cv2.calcHist`` up to 2**24 pixels, ``np.bincount`` above), not two
  ``np.bincount`` passes over two unpacked boxes;
- Save takes the present classes from a histogram, not ``np.unique``.

Each is compared with the code it replaced, on random data and random editing sessions.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import app.gui.gt_tracing_interface as gti  # noqa: E402
from app.gt import Tracing, default_registry, gt_path, present_keys, read_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402

H, W = 90, 120


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def editor(app, tmp_path):
    made = []

    def _make():
        path = str(tmp_path / "plot_001.png")
        cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
        iface = GtTracingInterface()
        made.append(iface)
        iface.resize(900, 700)
        iface.load_image(path)
        return iface

    yield _make
    for iface in made:
        iface.close()
        iface.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _reference_ids_from_argb(arr, registry, fallback=None):
    """The implementation before G3, verbatim."""
    arr = np.ascontiguousarray(arr)
    packed = arr.view("<u4")[:, :, 0]
    ids = np.zeros(packed.shape, np.uint8)
    known = arr[:, :, 3] == 0
    for c in registry.classes:
        hit = packed == gti._packed(gti._bgra(c.color))
        ids[hit] = c.id
        known |= hit
    other = ~known
    if fallback is not None and other.any():
        ids[other] = fallback[other]
    return ids


def _registry_with_a_retired_class():
    return default_registry().add(key="hypha", name="Hypha", color="#3fa7ff").retire("nodule")


@pytest.mark.parametrize("seed", range(6))
def test_ids_from_argb_matches_the_reference(seed):
    """Random canvases: class colours, transparent, colours no class owns, noise alpha."""
    rng = np.random.default_rng(seed)
    registry = _registry_with_a_retired_class()
    lut = GtTracingInterface._lut(registry)
    h, w = 37, 53
    plane_ids = np.array([0, 1, 2, 3, 4, 255, 7, 200], np.uint8)  # 7, 200: no class owns them
    fallback = rng.choice(plane_ids, size=(h, w)).astype(np.uint8)
    canvas = lut[fallback].copy()  # what the committed plane shows
    painted = rng.random((h, w)) < 0.3
    colours = np.vstack(
        [lut[[0, 1, 2, 3, 4, 255]], [[1, 2, 3, 255], [10, 20, 30, 128], [0, 0, 0, 255]]]
    ).astype(np.uint8)
    canvas[painted] = colours[rng.integers(0, len(colours), painted.sum())]
    for fb in (fallback, None, fallback[3:30, 5:40]):
        arr = canvas if fb is None or fb is fallback else canvas[3:30, 5:40]
        expected = _reference_ids_from_argb(arr, registry, fb)
        got = GtTracingInterface._ids_from_argb(arr, registry, fb)
        assert got.dtype == np.uint8 and got.shape == expected.shape
        assert np.array_equal(got, expected)
        if fb is not None:
            assert not np.shares_memory(got, fb)  # a new array: the history compares it


def test_ids_from_argb_matches_the_reference_with_a_grey_class():
    """A class in the grey shown for ids no class owns: those pixels still match like before."""
    registry = default_registry().update("nodule", color=gti.GT_UNKNOWN_COLOR)
    lut = GtTracingInterface._lut(registry)
    fallback = np.array([[0, 1, 7, 7], [2, 200, 255, 3]], np.uint8)
    canvas = lut[fallback].copy()
    canvas[0, 0] = lut[1]
    expected = _reference_ids_from_argb(canvas, registry, fallback)
    assert expected[0, 2] == 2 and expected[1, 1] == 2  # the grey is the nodule's colour now
    assert np.array_equal(GtTracingInterface._ids_from_argb(canvas, registry, fallback), expected)


def test_histogram_is_exact_above_the_float32_range():
    """cv2.calcHist counts in float32: 2**24 + 3 would read 2**24 + 4 (np.bincount there)."""
    ids = np.zeros(2**24 + 8, np.uint8)
    ids[:5] = 7
    hist = gti._histogram(ids)
    assert hist[0] == 2**24 + 3 and hist[7] == 5


def test_histogram_is_exact_on_both_paths(monkeypatch):
    rng = np.random.default_rng(1)
    ids = rng.integers(0, 256, size=(301, 257)).astype(np.uint8)
    ids[:50] = 3
    expected = np.bincount(ids.ravel(), minlength=256)
    assert np.array_equal(gti._histogram(ids), expected)
    assert np.array_equal(
        gti._histogram(ids[7:200, 9:100]), np.bincount(ids[7:200, 9:100].ravel(), minlength=256)
    )
    assert np.array_equal(
        gti._histogram(ids.ravel()[::3]), np.bincount(ids.ravel()[::3], minlength=256)
    )
    assert np.array_equal(gti._histogram(np.zeros((0, 5), np.uint8)), np.zeros(256))
    monkeypatch.setattr(gti, "_EXACT_HIST_MAX", 100)  # above it: the np.bincount path
    assert np.array_equal(gti._histogram(ids), expected)
    assert gti._histogram(ids).dtype == np.int64


def _random_session(iface, rng, steps):
    classes = [c.key for c in iface.registry.active()]
    for _ in range(steps):
        op = rng.integers(0, 7)
        if op == 0 and iface.undo_stack:
            iface.undo()
        elif op == 1 and iface.redo_stack:
            iface.redo()
        elif op == 2:
            iface.clear_mask()
        elif op == 3:
            iface.fill_button.setChecked(True)
            iface.begin_stroke(QPoint(int(rng.integers(0, W)), int(rng.integers(0, H))))
            iface.end_stroke()
            iface.brush_button.setChecked(True)
        else:
            iface.set_active_class(classes[int(rng.integers(0, len(classes)))])
            (iface.eraser_button if op == 4 else iface.brush_button).setChecked(True)
            iface.size_slider.setValue(int(rng.integers(1, 30)))
            pts = rng.integers(0, [W, H], size=(int(rng.integers(1, 6)), 2))
            iface.begin_stroke(QPoint(int(pts[0][0]), int(pts[0][1])))
            for x, y in pts[1:]:
                iface.continue_stroke(QPoint(int(x), int(y)))
            iface.end_stroke()
        yield


@pytest.mark.parametrize("seed", range(3))
def test_class_counts_stay_exact_through_a_random_session(editor, seed):
    iface = editor()
    rng = np.random.default_rng(seed)
    for _ in _random_session(iface, rng, 80):
        plane = iface.edits.plane
        assert np.array_equal(iface._counts, np.bincount(plane.ravel(), minlength=256))
        assert iface._present == present_keys(Tracing(plane), iface.registry)
        assert np.array_equal(iface._pixmap_to_ids(), plane)  # the canvas shows the plane


def test_class_counts_stay_exact_on_the_bincount_path(editor, monkeypatch):
    monkeypatch.setattr(gti, "_EXACT_HIST_MAX", 50)
    iface = editor()
    rng = np.random.default_rng(9)
    for _ in _random_session(iface, rng, 40):
        assert np.array_equal(iface._counts, np.bincount(iface.edits.plane.ravel(), minlength=256))


def test_the_status_chip_is_updated_only_when_it_changes(editor):
    """Each chip update re-places every overlay: an edit that changes nothing shown skips it."""
    iface = editor()
    chip, calls = iface.status_chip, []
    real = chip.set_state
    chip.set_state = lambda *a, **k: (calls.append(a), real(*a, **k))
    iface.begin_stroke(QPoint(10, 10))
    iface.end_stroke()
    assert [c[0] for c in calls] == ["dirty"] and "Unsaved" in iface.state_label.text()
    for x in (20, 30, 40):
        iface.begin_stroke(QPoint(x, 10))
        iface.end_stroke()
    iface.undo()
    assert len(calls) == 1  # still "Unsaved": nothing to redraw
    assert iface.save_mask()
    assert [c[0] for c in calls] == ["dirty", "clean"] and "Saved" in iface.state_label.text()


def test_save_writes_the_same_checks_as_present_keys(editor):
    iface = editor()
    rng = np.random.default_rng(4)
    for i, _ in enumerate(_random_session(iface, rng, 30)):
        if i % 5 == 4:
            plane = iface.edits.plane.copy()
            expected = iface._reviewed | present_keys(Tracing(plane), iface.registry)
            assert iface.save_mask()
            tracing = read_tracing(gt_path(iface.current_image_path))
            assert np.array_equal(tracing.ids, plane)
            assert tracing.reviewed == expected

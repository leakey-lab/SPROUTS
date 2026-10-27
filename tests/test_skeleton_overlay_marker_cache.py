"""The endpoint markers' cache mode (X2-03, app/gui/skeleton_overlay.py).

The markers are device-cached (a blit per frame), except where Qt would keep only a
viewport-size piece of that cache: once the markers' bounds are more than 1.2 x the viewport,
Qt reallocates and copies the piece on every pan step and rebuilds it on every zoom (about
10 ms of a 400 % pan frame at devicePixelRatio 2). There the markers are drawn directly, which
paints what a fresh cache paints:

- ``partial_device_cache`` is Qt's own rule: a 1 px scroll repaints a piece of the cache
  exactly where the rule says so, across a dense zoom sweep over the 1.2 x threshold;
- the mode follows the zoom, the viewport size and the markers, and keeps the cache where two
  markers could overlap (they would composite with other rounding) or where a view does not
  report its changes;
- pan frames of direct markers equal those of a cache rebuilt where it stands (no scroll
  history: Qt's scroll copies misplace markers by a device pixel after the markers' bounds
  cross the viewport's left or top edge), at devicePixelRatio 1 here and 2 and 1.5 in child
  processes (at 1.5 the copies also drift half a device pixel per odd step).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QRectF
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui import canvas_gl
from app.gui.editor_canvas import EditorCanvasView
from app.gui.skeleton_overlay import (
    _SPACING_PASSES,
    DotsItem,
    SkeletonDecorations,
    _min_spacing,
    partial_device_cache,
)
from tests.qt_teardown import delete_widgets

CACHE = QGraphicsItem.CacheMode.DeviceCoordinateCache
DIRECT = QGraphicsItem.CacheMode.NoCache
W, H = 640, 480


@pytest.fixture
def made(app, monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")  # raster viewports, as offscreen
    views: list = []
    yield views
    for view in views:
        view.setScene(None)
    delete_widgets(views)


# ------------------------------------------------------------------ spacing
def _brute_min(xy: np.ndarray) -> float:
    if len(xy) < 2:
        return math.inf
    d = np.hypot(*(xy[:, None, :] - xy[None, :, :]).transpose(2, 0, 1))
    return float(d[np.triu_indices(len(xy), 1)].min())


@pytest.mark.parametrize("count", [0, 1, 2, 3, 17, 300, 1500])
def test_the_spacing_is_the_smallest_distance_between_two_dots(count):
    rng = np.random.default_rng(count)
    for xy in (
        rng.uniform(0, 2000, (count, 2)),
        rng.integers(0, 400, (count, 2)) + 0.5,  # pixel centres, duplicates likely
    ):
        assert _min_spacing(xy) == pytest.approx(_brute_min(xy), abs=0)


def test_dots_crowded_in_one_column_count_as_touching():
    column = np.column_stack(
        (np.full(_SPACING_PASSES * 3, 10.5), np.arange(_SPACING_PASSES * 3) * 9)
    )
    assert _brute_min(column) == 9
    assert _min_spacing(column) == 0.0  # unknown after the passes: as if two touched
    item = DotsItem(4)
    item.set_points(column[:5])
    assert item.min_spacing() == 9
    item.set_points(column[:4])  # changed: found again
    assert item.min_spacing() == 9
    item.set_points([])
    assert item.min_spacing() == math.inf


# ------------------------------------------------------------------ Qt's rule
class _CountingDots(DotsItem):
    paints = 0

    def paint(self, painter, option, widget=None):
        _CountingDots.paints += 1
        super().paint(painter, option, widget)


def _qt_keeps_a_piece(view: QGraphicsView) -> bool:
    """A 1 px scroll repaints part of the item into its cache only for a partial cache."""
    view.viewport().repaint()
    before = _CountingDots.paints
    for bar in (view.horizontalScrollBar(), view.verticalScrollBar()):
        bar.setValue(bar.value() + 1)
    view.viewport().repaint()
    return _CountingDots.paints > before


@pytest.mark.parametrize(
    ("viewport", "image"), [((400, 300), (640, 480)), ((401, 299), (333, 517))]
)
def test_the_partial_rule_is_the_one_qt_applies(made, viewport, image):
    iw, ih = image
    rng = np.random.default_rng(iw)
    dots = _CountingDots(4)
    dots.set_points(
        np.vstack((rng.uniform(5, iw - 5, (30, 2)) * [1, ih / iw], [[5, 5], [iw - 5, ih - 5]]))
    )
    dots.setCacheMode(CACHE)
    scene = QGraphicsScene()
    scene.addItem(dots)
    view = EditorCanvasView()
    made.append(view)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, iw, ih))
    view.resize(*viewport)
    view.show()
    QApplication.processEvents()
    bounds = dots.boundingRect()
    threshold = min(1.2 * viewport[0] / bounds.width(), 1.2 * viewport[1] / bounds.height())
    seen = set()
    for zoom in np.linspace(0.97 * threshold, 1.03 * threshold, 25):
        view.set_zoom(float(zoom))
        view.centerOn(bounds.center())
        rule = partial_device_cache(dots, view)
        assert _qt_keeps_a_piece(view) == rule, zoom
        seen.add(rule)
    assert seen == {True, False}  # the sweep crossed the threshold


# ------------------------------------------------------------------ the mode
def _markers(made, points, size=(400, 300)):
    view = EditorCanvasView()
    made.append(view)
    scene = QGraphicsScene()
    view.setScene(scene)  # the view first, as the Correct editor builds it
    view.set_content_rect(QRectF(0, 0, W, H))
    view.resize(*size)
    view.show()
    deco = SkeletonDecorations(scene)
    deco.set_endpoints(points)
    view.fit_to_view()
    QApplication.processEvents()
    return view, deco, deco.endpoints_item


SPREAD = [(x, y) for x in range(20, W, 60) for y in range(15, H, 50)]


def test_the_markers_are_drawn_directly_only_where_qt_would_rebuild_a_piece_of_the_cache(made):
    view, deco, markers = _markers(made, SPREAD)
    assert markers.cacheMode() == CACHE  # fitted: the whole cache, one blit a frame
    view.set_zoom(4.0)
    assert partial_device_cache(markers, view) and markers.cacheMode() == DIRECT
    view.set_zoom(0.6)
    assert markers.cacheMode() == CACHE
    view.set_zoom(4.0)
    view.resize(2200, 1600)  # the markers fit in 1.2 x the viewport again
    QApplication.processEvents()
    assert not partial_device_cache(markers, view) and markers.cacheMode() == CACHE
    view.resize(400, 300)
    QApplication.processEvents()
    assert markers.cacheMode() == DIRECT


def test_markers_that_could_overlap_keep_the_cache(made):
    view, deco, markers = _markers(made, SPREAD)
    view.set_zoom(4.0)
    assert markers.cacheMode() == DIRECT
    deco.set_endpoints([*SPREAD, (300, 200), (301, 201)])  # 1.4 px apart: 5.7 device px
    # (at a fractional ratio the cache's scroll copies drift: direct even then)
    whole = float(view.viewport().devicePixelRatioF()).is_integer()
    assert markers.cacheMode() == (CACHE if whole else DIRECT)
    view.set_zoom(16.0)  # 23 device px apart: no sprite (12 px) can reach the other
    assert markers.cacheMode() == DIRECT
    deco.set_endpoints(SPREAD)
    view.set_zoom(4.0)
    assert markers.cacheMode() == DIRECT


def test_a_new_device_pixel_ratio_is_reported(made):
    view, deco, markers = _markers(made, SPREAD)
    reports = []
    view.viewport_changed.connect(lambda: reports.append(True))
    QApplication.sendEvent(view.viewport(), QEvent(QEvent.Type.DevicePixelRatioChange))
    assert reports == [True]


def test_a_view_that_does_not_report_its_changes_keeps_the_cache(made):
    scene = QGraphicsScene()
    plain = QGraphicsView(scene)
    made.append(plain)
    plain.resize(400, 300)
    plain.scale(4.0, 4.0)
    plain.show()
    deco = SkeletonDecorations(scene)
    deco.set_endpoints(SPREAD)
    assert partial_device_cache(deco.endpoints_item, plain)
    assert deco.endpoints_item.cacheMode() == CACHE


# ------------------------------------------------------------------ pixels
def _photo_scene() -> QGraphicsScene:
    rng = np.random.default_rng(3)
    rgb = np.ascontiguousarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8))
    image = QImage(rgb.data, W, H, W * 3, QImage.Format.Format_RGB888).copy()
    scene = QGraphicsScene()
    scene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(image)))
    return scene


def pan_check(zooms=(3.0, 4.0, 8.0)) -> tuple[int, int]:
    """(pan frames compared, frames where direct markers differ from a fresh cache).

    Two canvases over the same photo and markers: one with the markers' mode as decided, one
    whose cache is rebuilt where it stands before every comparison, panned from the markers'
    centre, left edge and top-left corner. Runs in this process's devicePixelRatio (the child
    processes set QT_SCALE_FACTOR); at a fractional one from the centre only: a fresh cache
    starts on a whole device pixel only where the markers overflow the viewport's left and top
    (its piece then starts at the viewport's corner).
    """
    whole = float(QApplication.instance().devicePixelRatio()).is_integer()
    starts = ("centre", "left-edge", "corner") if whole else ("centre",)
    points = [*SPREAD, (77, 401), (523, 33)]
    views, decos = [], []
    for _ in range(2):
        scene = _photo_scene()
        view = EditorCanvasView()
        view.setScene(scene)
        view.set_content_rect(QRectF(0, 0, W, H))
        view.resize(420, 310)
        view.show()
        deco = SkeletonDecorations(scene)
        deco.set_endpoints(points)
        views.append(view)
        decos.append(deco)
    fresh = decos[1].endpoints_item
    fresh.skip_device_cache = lambda view: False  # always cached
    compared = differing = 0
    try:
        for zoom in zooms:
            for start in starts:
                for view, deco in zip(views, decos, strict=True):
                    r, vp = deco.endpoints_item.sceneBoundingRect(), view.viewport()
                    view.set_zoom(zoom)
                    if start == "centre":
                        view.centerOn(r.center())
                    elif start == "left-edge":
                        view.centerOn(r.left() + vp.width() / (2.5 * zoom), r.center().y())
                    else:
                        view.centerOn(
                            r.left() + vp.width() / (2.1 * zoom),
                            r.top() + vp.height() / (2.1 * zoom),
                        )
                for step in range(12):
                    dx, dy = (7, 5) if step % 2 == 0 else (-3, -2)
                    for view in views:
                        view._scroll_by(dx, dy)
                        view.viewport().repaint()  # a real frame: a cache piece is scroll-copied
                    fresh.setCacheMode(DIRECT)
                    fresh.setCacheMode(CACHE)
                    shown, rebuilt = (view.viewport().grab().toImage() for view in views)
                    compared += 1
                    differing += shown != rebuilt
                assert decos[0].endpoints_item.cacheMode() == DIRECT, (zoom, start)
    finally:
        for view in views:
            view.setScene(None)
        delete_widgets(views)
    return compared, differing


def test_direct_markers_paint_what_a_fresh_cache_paints(made):
    whole = float(QApplication.instance().devicePixelRatio()).is_integer()
    assert pan_check() == (108 if whole else 36, 0)


_AT_SCALE = """
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import tests.test_skeleton_overlay_marker_cache as t
compared, differing = t.pan_check()
print(app.devicePixelRatio(), compared, differing)
"""


@pytest.mark.parametrize(("scale", "frames"), [("2", 108), ("1.5", 36)])
def test_direct_markers_paint_what_a_fresh_cache_paints_on_high_dpi_screens(scale, frames):
    root = Path(__file__).resolve().parents[1]
    env = dict(
        os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale, **{canvas_gl.ENV_VAR: "0"}
    )
    run = subprocess.run(
        [sys.executable, "-c", _AT_SCALE], cwd=root, env=env, capture_output=True, text=True,
        timeout=300,
    )  # fmt: skip

    assert run.returncode == 0, run.stderr
    dpr, compared, differing = run.stdout.split()[-3:]
    assert float(dpr) == float(scale)
    assert (int(compared), int(differing)) == (frames, 0)

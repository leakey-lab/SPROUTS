"""Regression test: zoomed-out image views must paint the canvas colour outside the image.

The global SPROUTS stylesheet sets ``QWidget { background-color: transparent; }``.
Qt's style-sheet style then turns off ``autoFillBackground`` on each graphics
view's viewport, so nothing clears it between frames. A view with no background
brush of its own paints nothing outside the scene. Once the zoomed-out image is
smaller than the viewport, the OpenGL viewport keeps pixels from earlier frames,
and each zoom step draws a smaller copy on top of the old ones ("hall of
mirrors").

The OpenGL path cannot be pixel-checked offscreen, so the test pins two things:
- each view owns an opaque canvas-colour background brush, which is the thing
  that repaints the area outside the scene on every backend, and keeps it
  across OpenGL <-> QWidget viewport swaps;
- after a swap to the QWidget viewport and a zoom-out to the minimum zoom,
  the pixels outside the scene really are the canvas colour.
"""

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap, QTransform  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui.brush_preview import BrushPreview  # noqa: E402
from app.gui.display_controller import MagnifyingGraphicsView  # noqa: E402
from app.gui.mask_graphics_view import MaskTracingGraphicsView  # noqa: E402
from app.gui.skeleton_correction_graphics_view import (  # noqa: E402
    SkeletonCorrectionGraphicsView,
)
from app.gui.widgets import tokens  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_QSS = REPO_ROOT / "resources" / "themes" / "dark_theme.qss"

VIEW_CLASSES = [MagnifyingGraphicsView, SkeletonCorrectionGraphicsView, MaskTracingGraphicsView]
CANVAS = QColor(tokens.BG_0)


@pytest.fixture(scope="module")
def app():
    """QApplication with the real app stylesheet (as ``main.apply_stylesheet`` loads it)."""
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


def _min_zoom(view: QGraphicsView) -> float:
    return getattr(view, "MIN_ZOOM", None) or view.min_zoom


def _image_scene(w: int, h: int) -> QGraphicsScene:
    pm = QPixmap(w, h)
    pm.fill(QColor(255, 0, 255))  # nothing like the canvas colour
    scene = QGraphicsScene()
    scene.addItem(QGraphicsPixmapItem(pm))
    scene.setSceneRect(QRectF(0, 0, w, h))
    return scene


def _assert_canvas_brush(view: QGraphicsView) -> None:
    brush = view.backgroundBrush()
    assert brush.style() == Qt.BrushStyle.SolidPattern, "view has no background brush"
    assert brush.isOpaque(), "view background brush is not opaque"
    assert brush.color().name() == CANVAS.name()


@pytest.mark.parametrize("view_cls", VIEW_CLASSES, ids=lambda c: c.__name__)
def test_view_owns_opaque_canvas_brush_across_viewport_swaps(app, view_cls):
    view = view_cls(None)
    _assert_canvas_brush(view)

    view.set_opengl_viewport_enabled(True)
    _assert_canvas_brush(view)
    view.set_opengl_viewport_enabled(False)
    _assert_canvas_brush(view)


@pytest.mark.parametrize("view_cls", VIEW_CLASSES, ids=lambda c: c.__name__)
def test_zoomed_out_view_paints_canvas_colour_outside_scene(app, view_cls):
    view = view_cls(None)
    # Swap to the OpenGL viewport and back, so the check runs on a freshly
    # created QWidget viewport (the path used while visualizations are open).
    view.set_opengl_viewport_enabled(True)
    view.set_opengl_viewport_enabled(False)
    assert type(view.viewport()).__name__ == "QWidget"

    view.setScene(_image_scene(800, 600))
    view.resize(400, 300)
    view.show()
    app.processEvents()

    # Seed: at 100 % the image covers the whole viewport.
    view.setTransform(QTransform())
    view.centerOn(400, 300)
    view.viewport().grab()

    zoom = _min_zoom(view)
    view.setTransform(QTransform.fromScale(zoom, zoom))
    view.centerOn(400, 300)
    app.processEvents()

    img = view.viewport().grab().toImage()
    dpr = img.width() / view.viewport().width()
    scene_in_vp = view.mapFromScene(view.sceneRect()).boundingRect()
    assert scene_in_vp.width() < view.viewport().width() / 2  # really zoomed out

    vw, vh = view.viewport().width(), view.viewport().height()
    samples = [QPoint(2, 2), QPoint(vw - 3, 2), QPoint(2, vh - 3), QPoint(vw - 3, vh - 3)]
    samples += [QPoint(vw // 2, 2), QPoint(2, vh // 2)]
    for p in samples:
        assert not scene_in_vp.contains(p)
        c = img.pixelColor(int(p.x() * dpr), int(p.y() * dpr))
        assert (c.red(), c.green(), c.blue(), c.alpha()) == (
            CANVAS.red(),
            CANVAS.green(),
            CANVAS.blue(),
            255,
        ), f"pixel {p.x()},{p.y()} outside the scene is {c.name(QColor.NameFormat.HexArgb)}"

    view.close()


def _frame_after_interaction(app, view_cls, mode=None):
    """The window's backing store (what is on screen) after zooming out and then
    sweeping the brush ring across the zoomed-out image."""
    view = view_cls(None)
    if mode is not None:
        view.setViewportUpdateMode(mode)
    scene = _image_scene(800, 600)
    view.setScene(scene)
    view.resize(400, 300)
    view.show()
    app.processEvents()
    view.set_zoom(3.0)
    app.processEvents()
    for _ in range(12):
        view.zoom_by(0.8)
        app.processEvents()
    ring = BrushPreview(scene)
    ring.set_diameter(40)
    ring.set_visible(True)
    for i in range(30):
        ring.move_to_pixel(QPoint(100 + 19 * i, 150 + 7 * i))
        app.processEvents()
    frame = app.primaryScreen().grabWindow(view.winId()).toImage()
    view.close()
    return frame


@pytest.mark.parametrize("view_cls", VIEW_CLASSES, ids=lambda c: c.__name__)
def test_partial_raster_updates_leave_no_stale_pixels(app, monkeypatch, view_cls):
    """PE-05: the raster viewport repaints only dirty regions. That must put the
    same pixels on screen as full-viewport updates: no stale copies around the
    zoomed-out image and no trails behind the moving brush ring."""
    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    partial = _frame_after_interaction(app, view_cls)
    full = _frame_after_interaction(
        app, view_cls, QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    )
    assert not partial.isNull()
    assert partial == full


def test_every_graphics_view_subclass_is_covered():
    """A new QGraphicsView subclass in app/ must be added to VIEW_CLASSES above."""
    found = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                getattr(b, "id", getattr(b, "attr", None)) == "QGraphicsView" for b in node.bases
            ):
                found.add(node.name)
    assert found == {c.__name__ for c in VIEW_CLASSES}

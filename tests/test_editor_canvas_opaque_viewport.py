"""The Correct canvas's opaque raster viewport (X2-04).

The theme's ``QWidget { background-color: transparent }`` leaves every canvas viewport
non-opaque, so Qt painted the window's background under the whole canvas on every frame
before the view filled it again. ``EditorCanvasView`` marks its raster viewport opaque
(``WA_OpaquePaintEvent``) and then owes every device pixel of each repaint itself:

- its background is filled in viewport pixels, so the pixel straddling a .5 device edge at
  125-175 % display scaling is painted too (QGraphicsView's own fill, in scene coordinates,
  can leave it out: on the backing store it would keep the previous frame's pixel, the
  "hall of mirrors" kind of stale copy). The suite runs at devicePixelRatio 1, so the check
  renders the viewport into poisoned images of other ratios at odd offsets;
- only while it has a scene (without one the view paints nothing at all), never on the
  OpenGL viewport, and the Library canvas is left as it was;
- a pan repaints the whole canvas, as before: Qt would otherwise move an opaque viewport's
  pixels when nothing floats over it.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsScene  # noqa: E402

from app.gui import canvas_gl  # noqa: E402
from app.gui.display_controller import MagnifyingGraphicsView  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

OPAQUE = Qt.WidgetAttribute.WA_OpaquePaintEvent
POISON = QColor(255, 0, 255)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def made(app, monkeypatch):
    """Views made by a test, deleted after it (raster viewports, as offscreen)."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    views: list = []
    yield views
    for view in views:
        view.setScene(None)
    delete_widgets(views)


def _image_scene(w: int = 640, h: int = 480) -> QGraphicsScene:
    pixmap = QPixmap(w, h)
    pixmap.fill(QColor(90, 120, 60))
    scene = QGraphicsScene()
    scene.addItem(QGraphicsPixmapItem(pixmap))
    return scene


def _canvas(made, size=(401, 301)) -> EditorCanvasView:
    view = EditorCanvasView()
    made.append(view)
    view.setScene(_image_scene())
    view.set_content_rect(QRectF(0, 0, 640, 480))
    view.resize(*size)
    view.show()
    QApplication.processEvents()
    return view


def test_the_raster_viewport_is_opaque_only_while_there_is_a_scene(made):
    view = EditorCanvasView()
    made.append(view)
    assert not view.viewport().testAttribute(OPAQUE)  # nothing to paint yet
    scene = _image_scene()
    view.setScene(scene)
    assert view.viewport().testAttribute(OPAQUE)
    view.setScene(None)
    assert not view.viewport().testAttribute(OPAQUE)
    view.setScene(scene)
    assert view.viewport().testAttribute(OPAQUE)


def test_a_new_raster_viewport_is_opaque_again_and_the_opengl_one_never(made, monkeypatch):
    view = _canvas(made)
    monkeypatch.setattr(canvas_gl, "opengl_viewports_allowed", lambda: True)
    view.set_opengl_viewport_enabled(True)
    assert view.viewport().inherits("QOpenGLWidget")
    assert not view.viewport().testAttribute(OPAQUE)
    view.set_opengl_viewport_enabled(False)
    assert not view.viewport().inherits("QOpenGLWidget")
    assert view.viewport().testAttribute(OPAQUE)


def test_the_library_canvas_keeps_its_viewport(made):
    view = MagnifyingGraphicsView()
    made.append(view)
    view.setScene(_image_scene())
    assert not view.viewport().testAttribute(OPAQUE)


def _poison_left(view: EditorCanvasView, ratio: float, offset: QPoint) -> int:
    """Poison pixels left inside the device rect the viewport is rendered to."""
    viewport = view.viewport()
    w, h = viewport.width(), viewport.height()
    image = QImage(
        int((offset.x() + w) * ratio) + 4,
        int((offset.y() + h) * ratio) + 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(POISON)
    viewport.render(image, offset)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    pixels = np.frombuffer(bits, np.uint32).reshape(image.height(), image.bytesPerLine() // 4)
    # Qt clips the widget to its rect mapped to device pixels, corners rounded half up.
    x0, y0 = int(offset.x() * ratio + 0.5), int(offset.y() * ratio + 0.5)
    x1, y1 = int((offset.x() + w) * ratio + 0.5), int((offset.y() + h) * ratio + 0.5)
    return int((pixels[y0:y1, x0:x1] == POISON.rgba()).sum())


@pytest.mark.parametrize("ratio", [1.25, 1.5, 1.75])
def test_every_device_pixel_of_the_canvas_is_painted_at_fractional_scaling(made, ratio):
    view = _canvas(made)
    assert view.viewport().testAttribute(OPAQUE)
    left = {}
    for zoom in (view.zoom_out_limit, 0.413, 0.613, 1.0, 1.5):
        view.set_zoom(zoom)
        for scroll in ((0, 0), (17, 9)):
            view._scroll_by(*scroll)
            for offset in (QPoint(0, 0), QPoint(1, 1), QPoint(0, 1), QPoint(3, 5)):
                count = _poison_left(view, ratio, offset)
                if count:
                    left[(round(view.zoom_factor, 4), scroll, offset.x(), offset.y())] = count
    assert left == {}


def _rendered(view: EditorCanvasView) -> QImage:
    image = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(POISON)
    painter = QPainter(image)
    view.render(painter)  # scaled onto the image, not the viewport's own paint
    painter.end()
    return image


def test_rendering_the_view_onto_another_device_fills_as_qt_does(made):
    view = _canvas(made, size=(200, 150))
    view.set_zoom(0.25)  # the image smaller than the viewport: background around it
    opaque = _rendered(view)
    view.OPAQUE_RASTER_VIEWPORT = False
    view._sync_opaque_viewport()
    assert not view.viewport().testAttribute(OPAQUE)
    assert _rendered(view) == opaque


class _PaintRegions(QObject):
    """The regions of the paint events a widget receives."""

    def __init__(self, widget) -> None:
        super().__init__(widget)
        self.regions = []
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Paint:
            self.regions.append(event.region().boundingRect())
        return False


def test_a_pan_repaints_the_whole_canvas_even_with_nothing_over_it(made):
    view = _canvas(made)
    view.set_zoom(2.0)
    QApplication.processEvents()
    spy = _PaintRegions(view.viewport())
    for dx, dy in ((7, 5), (-3, -2), (0, 11)):
        spy.regions.clear()
        view._scroll_by(dx, dy)
        QApplication.processEvents()
        assert spy.regions and spy.regions[-1] == view.viewport().rect(), (dx, dy, spy.regions)

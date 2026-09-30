"""The pixel grid of a canvas view that sits inside a window (app/gui/canvas_view.py).

A raster viewport paints into its window's backing store, moved there by its offset in the
window (Qt's paint redirection). The grid lines must still sit on the device columns where
the exact-pixel layer starts each pixel block, at every devicePixelRatio: the grid computes
its device columns with that offset included and must not add it a second time when it
draws them (it did: every line landed ``offset x dpr`` device px to the right and below,
mid-pixel, with no grid over the top-left band).
"""

import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_layers.raster import RasterLayerItem  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402
from tests.render_metrics import qimage_to_rgb  # noqa: E402

W, H = 64, 48
OFFSET = (37, 23)  # the view's position in its window
VIEW = (320, 240)


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_canvas()
        self.setFrameShape(QFrame.Shape.NoFrame)


def _checkerboard_canvas() -> QImage:
    yy, xx = np.mgrid[0:H, 0:W]
    on = (xx + yy) % 2 == 0
    bgra = np.zeros((H, W, 4), np.uint8)
    bgra[on] = (40, 200, 40, 255)
    bgra[~on] = (200, 40, 200, 255)
    return QImage(bgra.data, W, H, W * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    """A window holding the view at ``OFFSET``, the checkerboard in exact pixels."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    canvas = _checkerboard_canvas()
    host = QWidget()
    host.resize(500, 400)
    view = _View(host)
    view.setGeometry(*OFFSET, *VIEW)
    scene = QGraphicsScene(view)
    item = RasterLayerItem(lambda: canvas)
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, W, H))
    host.show()
    app.processEvents()
    yield host, view
    scene.removeItem(item)
    host.close()
    host.deleteLater()
    app.processEvents()


def _render(app, host, view, dpr: float, zoom: float, grid: bool) -> np.ndarray:
    """The whole window at ``dpr`` (the view painted through the window's redirection)."""
    view.set_pixel_grid(grid)
    view.set_zoom(zoom)
    view.centerOn(W / 2 + 0.3, H / 2 + 0.2)
    app.processEvents()
    image = QImage(
        round(host.width() * dpr), round(host.height() * dpr), QImage.Format.Format_ARGB32
    )
    image.setDevicePixelRatio(dpr)
    image.fill(QColor(0, 0, 0))
    host.render(image)
    return qimage_to_rgb(image).astype(int)


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("zoom", [8.0, 10.0, 12.0])
def test_grid_lines_sit_on_the_block_edges_inside_a_window(app, window, dpr, zoom):
    host, view = window
    plain = _render(app, host, view, dpr, zoom, grid=False)
    lined = _render(app, host, view, dpr, zoom, grid=True)

    # A device row through the middle of image row 20; the view's columns, 2 px in.
    row_y = math.floor((OFFSET[1] + view.mapFromScene(0.0, 20.5).y() + 0.25) * dpr)
    first = math.ceil((OFFSET[0] + 2) * dpr)
    last = math.floor((OFFSET[0] + VIEW[0] - 2) * dpr)
    row, gridded = plain[row_y], lined[row_y]
    edges = {x for x in range(first, last) if (row[x] != row[x - 1]).any()}
    lines = {x for x in range(first, last) if np.abs(gridded[x] - row[x]).sum() > 6}

    assert len(edges) > 10
    if (zoom * dpr).is_integer():
        assert lines == edges
    else:  # the raster engine's nearest walk may move a block edge by one column
        assert len(lines) == len(edges)
        assert all(min(abs(x - e) for e in edges) <= 1 for x in lines)


def test_the_grid_covers_the_top_left_of_the_view_inside_a_window(app, window):
    """No band without grid lines at the view's top-left (the doubled offset clipped it)."""
    host, view = window
    lined = _render(app, host, view, 1.0, 10.0, grid=True)
    plain = _render(app, host, view, 1.0, 10.0, grid=False)
    changed = np.abs(lined - plain).sum(axis=2) > 6
    x0, y0 = OFFSET
    band = changed[y0 : y0 + 20, x0 : x0 + VIEW[0]]  # the view's top 20 device rows
    assert band.any(axis=1).all()  # every row of the band crosses vertical grid lines

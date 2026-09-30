"""The photo's idle upload frames come wherever the view is (app/gui/canvas_layers/photo.py).

On OpenGL the tiles the idle warm-up made are uploaded by frames the photo asks for once
the warm-up is done. A view repaints only for an update that meets the viewport, so the
request must name a part of the photo that is on screen: the top-left pixel it named
(0, 0, 1, 1) is off screen as soon as the view is zoomed into any other part, and then no
frame came and every tile made ahead waited for the first frame that showed it.
"""

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFrame, QGraphicsScene, QGraphicsView  # noqa: E402

from app.gui import canvas_gl  # noqa: E402
from app.gui.canvas_layers import photo as photo_module  # noqa: E402
from app.gui.canvas_layers.photo import PhotoLayerItem  # noqa: E402
from app.gui.canvas_view import CanvasViewMixin  # noqa: E402

W, H = 1400, 1000


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self):
        super().__init__(None)
        self._init_canvas()
        self.setFrameShape(QFrame.Shape.NoFrame)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, monkeypatch):
    """A 400 x 300 view over a 1400 x 1000 photo tiled at 64 px, updated as a GL viewport
    is (the whole viewport per frame); engines count as GL."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setattr(photo_module, "_is_gl", lambda painter: True)
    rng = np.random.default_rng(3)
    item = PhotoLayerItem()
    item.TILE = 64
    item.set_image(rng.integers(0, 256, (H, W, 3), dtype=np.uint8))
    view = _View()
    view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    scene = QGraphicsScene(view)
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, W, H))
    view.resize(400, 300)
    yield view, item
    scene.removeItem(item)
    view.close()
    view.deleteLater()
    app.processEvents()


def _show(app, view, zoom: float, centre: tuple[float, float]) -> None:
    view.set_zoom(zoom)
    view.centerOn(QPointF(*centre))
    view.show()
    app.processEvents()
    view.centerOn(QPointF(*centre))
    view.viewport().repaint()


def _settle(item: PhotoLayerItem, shown: int, timeout_s: float = 5.0) -> None:
    """Wait until the warm-up made tiles beyond the ``shown`` ones and none waits."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        QTest.qWait(20)
        if len(item.built_tiles()) > shown and item._levels.pending_uploads() == 0:
            return


def test_tiles_made_ahead_are_uploaded_when_the_image_corner_is_off_screen(app, page):
    view, item = page
    _show(app, view, 0.9, (W / 2, H / 2))  # the corner (0, 0) is far off screen
    assert not view.mapToScene(view.viewport().rect()).boundingRect().contains(0.5, 0.5)
    shown = len(item.built_tiles())
    _settle(item, shown)
    assert len(item.built_tiles()) > shown  # the warm-up made tiles ahead
    assert item._levels.pending_uploads() == 0  # and idle frames uploaded them


def test_tiles_made_ahead_are_uploaded_when_the_corner_is_on_screen(app, page):
    view, item = page
    _show(app, view, 0.25, (W / 2, H / 2))  # the whole photo in view
    shown = len(item.built_tiles())
    _settle(item, shown)
    assert len(item.built_tiles()) > shown
    assert item._levels.pending_uploads() == 0

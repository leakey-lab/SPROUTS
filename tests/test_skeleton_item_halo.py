"""The skeleton's dark halo does not darken where its segments meet (KNOWN_ISSUES #6, S6).

At vector zoom the halo (BG_0 at HALO_ALPHA, 4 px) was stroked segment by segment, so the
round caps of two segments meeting at a pixel stacked their alpha: joints and sharp bends
read darker. It is now composited once: on the raster engine an opaque halo is drawn into
its own image and blended at HALO_ALPHA; on OpenGL the visible tiles' segments are stroked
as one path (the engine covers a translucent stroke once). Display only.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QLineF, QPointF, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QGraphicsScene

import app.gui.canvas_layers.skeleton as sk
from app.gui.skeleton_graph_model import _skeletonize_uint8
from app.gui.skeleton_overlay import HALO_ALPHA
from app.gui.widgets import tokens

W, H = 160, 120


def _bent() -> np.ndarray:
    """A sharp V and an arc: joints at every step, a 30 degree bend."""
    mask = np.zeros((H, W), np.uint8)
    cv2.line(mask, (5, 5), (25, 60), 255, 1)
    cv2.line(mask, (25, 60), (30, 5), 255, 1)
    cv2.ellipse(mask, (90, 60), (30, 40), 0, 0, 270, 255, 1)
    return _skeletonize_uint8(mask)


def _rgb(image: QImage) -> np.ndarray:
    data = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    return data.reshape(image.height(), image.width(), 4)[:, :, :3].astype(int)


def _render_item(mask: np.ndarray, zoom: float, scenes: list) -> tuple[np.ndarray, str]:
    scene = QGraphicsScene()
    scenes.append(scene)
    item = sk.SkeletonLayerItem()
    item.EXTRACT_BUDGET_MS = float("inf")
    scene.addItem(item)
    item.set_mask(mask)
    image = QImage(int(W * zoom), int(H * zoom), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(painter, QRectF(image.rect()), QRectF(0, 0, W, H))
    painter.end()
    return _rgb(image), item.mode_painted()


def _once_composited(mask: np.ndarray, zoom: float) -> np.ndarray:
    """The intended look: an opaque halo in its own layer, blended once, then the line."""
    links = sk.tile_segments(mask, (0, 0, W, H))
    centres = (links.astype(np.float64) + 0.5) * zoom
    dot = (links[:, 0] == links[:, 2]) & (links[:, 1] == links[:, 3])
    lines = [QLineF(*row) for row in centres[~dot].tolist()]
    dots = [QPointF(*row) for row in centres[dot, :2].tolist()]
    size = (int(W * zoom), int(H * zoom))
    layer = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(0)
    painter = QPainter(layer)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(sk._cosmetic_pen(QColor(tokens.BG_0), 4.0))
    painter.drawLines(lines)
    painter.drawPoints(dots)
    painter.end()
    out = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor(255, 255, 255))
    painter = QPainter(out)
    painter.setOpacity(HALO_ALPHA / 255.0)
    painter.drawImage(0, 0, layer)
    painter.setOpacity(1.0)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(sk._cosmetic_pen(QColor(tokens.SKEL), 2.0))
    painter.drawLines(lines)
    painter.drawPoints(dots)
    painter.end()
    return _rgb(out)


@pytest.mark.parametrize("zoom", [2.0, 4.0, 8.0])
def test_the_vector_halo_is_composited_once(app, zoom):
    scenes: list = []
    rendered, mode = _render_item(_bent(), zoom, scenes)
    assert mode == "vector"

    darker = _once_composited(_bent(), zoom).mean(axis=2) - rendered.mean(axis=2)

    assert (darker > 3).sum() == 0, int(darker.max())  # no joint reads darker
    assert (darker < -3).sum() == 0, int(darker.min())  # and nothing lost its halo


def test_the_opengl_branch_strokes_the_halo_as_one_path(app, monkeypatch):
    """The OpenGL branch (forced here on the raster engine) strokes one path per paint."""
    monkeypatch.setattr(sk, "_is_gl", lambda painter: True)
    paths = []
    real = QPainter.drawPath

    class Spy(QPainter):
        def drawPath(self, path):  # noqa: N802 - Qt name
            paths.append(path.elementCount())
            real(self, path)

    scenes: list = []
    scene = QGraphicsScene()
    scenes.append(scene)
    item = sk.SkeletonLayerItem()
    item.EXTRACT_BUDGET_MS = float("inf")
    scene.addItem(item)
    item.set_mask(_bent())
    image = QImage(int(W * 4), int(H * 4), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))
    painter = Spy(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(painter, QRectF(image.rect()), QRectF(0, 0, W, H))
    painter.end()

    assert item.mode_painted() == "vector"
    links = sk.tile_segments(_bent(), (0, 0, W, H))
    lines = int(((links[:, 0] != links[:, 2]) | (links[:, 1] != links[:, 3])).sum())
    assert paths == [2 * lines]  # one path: a move and a line per segment
    # On OpenGL this equals the once-composited halo exactly (checked on the owner's GPU);
    # the raster engine's stroker only approximates it (joints stacked up to 62 before).
    darker = _once_composited(_bent(), 4.0).mean(axis=2) - _rgb(image).mean(axis=2)
    assert darker.max() <= 20 and (darker < -3).sum() == 0


def test_a_halo_layer_too_large_for_memory_draws_the_halo_directly(app, monkeypatch):
    monkeypatch.setattr(sk.SkeletonLayerItem, "HALO_LAYER_MAX_PX", 100)
    scenes: list = []
    rendered, mode = _render_item(_bent(), 4.0, scenes)
    assert mode == "vector"
    reference = _once_composited(_bent(), 4.0)
    halo = np.abs(reference.mean(axis=2) - 255) > 30  # where the halo darkens the white
    assert (np.abs(rendered.mean(axis=2) - 255) > 30)[halo].mean() > 0.95  # still drawn

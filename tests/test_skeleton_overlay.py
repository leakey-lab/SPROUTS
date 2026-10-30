"""Skeleton overlay helpers that outlive the old skeleton editor.

``overlay_indices`` (the line plus its one-pixel halo, UXE-13) feeds the skeleton layer
item, and ``SkeletonDecorations`` draws the endpoint markers of the Correct workspace.
Copied from ``tests/test_skeleton_editor_tools.py``, which goes with that editor.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QPointF


def test_overlay_halo_surrounds_the_line_only():
    from app.gui.skeleton_overlay import HALO_INDEX, LINE_INDEX, overlay_indices

    mask = np.zeros((9, 9), np.uint8)
    mask[4, 2:7] = 255
    idx = overlay_indices(mask)
    assert (idx == LINE_INDEX).sum() == 5 and np.array_equal(idx == LINE_INDEX, mask > 0)
    halo = idx == HALO_INDEX
    ring = cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0
    assert np.array_equal(halo, ring & (mask == 0))
    assert mask[4, 2] == 255  # input untouched


def test_touching_endpoint_markers_stay_filled(app):
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.skeleton_overlay import SkeletonDecorations

    deco = SkeletonDecorations(QGraphicsScene())
    deco.set_endpoints([(10, 10), (12, 10)])
    assert deco.endpoints_item.path().contains(QPointF(11, 10))


def _painted(item, scale: float) -> int:
    """How many device pixels ``item`` covers when painted at zoom ``scale`` (centred)."""
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QStyleOptionGraphicsItem

    image = QImage(400, 400, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.translate(200, 200)
    painter.scale(scale, scale)
    painter.translate(-10, -10)  # the dot at (10, 10) lands in the middle
    item.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    alpha = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    return int((alpha.reshape(400, 400, 4)[:, :, 3] > 0).sum())


@pytest.mark.parametrize("which", ["endpoints", "highlight", "handles"])
def test_markers_and_handles_stop_growing_past_100_percent(app, which):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.skeleton_overlay import SkeletonDecorations

    deco = SkeletonDecorations(QGraphicsScene())
    deco.set_endpoints([(10, 10)])
    deco.highlight_endpoint(QPoint(10, 10))
    deco.show_polyline([QPoint(10, 10)], None, smooth=False)
    item = getattr(deco, f"{which}_item")
    at_100, at_1600 = _painted(item, 1.0), _painted(item, 16.0)
    at_50 = _painted(item, 0.5)

    assert at_100 > 0 and at_1600 <= at_100 * 1.2  # a 128 px disc at 1600 % before
    assert at_50 < at_100  # below 100 % the dots shrink with the image, as before
    assert item.boundingRect().contains(item.path().boundingRect())

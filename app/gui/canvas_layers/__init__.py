"""Render items shared by the image canvases (Correct, Library).

Submodules (import them directly, e.g.
``from app.gui.canvas_layers.raster import RasterLayerItem``):

* :mod:`~app.gui.canvas_layers.photo` - :class:`PhotoLayerItem`, the photo, smooth at
  every zoom (area pyramid + bilinear).
* :mod:`~app.gui.canvas_layers.raster` - :class:`RasterLayerItem`, an editable ARGB canvas
  (exact pixels when magnified, area coverage when minified, 256 px tiles).
* :mod:`~app.gui.canvas_layers.skeleton` - :class:`SkeletonLayerItem`, a 1 px skeleton
  drawn as a line at every zoom.

Every item chooses its level of detail from :func:`device_scale`, which counts device
pixels per item pixel (view zoom times devicePixelRatio). This module imports nothing heavy.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtGui import QPainter

__all__ = ["device_scale"]


def device_scale(painter: QPainter) -> float:
    """Device pixels per item pixel of ``painter`` (includes the devicePixelRatio).

    ``hypot(m11, m12)`` of ``painter.deviceTransform()``: the length of a unit x step in
    device pixels, also under rotation.
    """
    t = painter.deviceTransform()
    return math.hypot(t.m11(), t.m12())

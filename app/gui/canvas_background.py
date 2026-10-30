"""Shared canvas background for every image ``QGraphicsView`` in the app.

Why this exists: the global SPROUTS stylesheet sets
``QWidget { background-color: transparent; }``. Qt's style-sheet style turns off
``autoFillBackground`` on every widget such a rule matches, which includes each
graphics view's viewport. A ``QOpenGLWidget`` viewport then never clears its
framebuffer between frames. A view with no background brush of its own paints
nothing outside the scene. So once a zoomed-out image is smaller than the
viewport, pixels from earlier frames stay on screen, and each zoom step draws a
smaller copy on top of the old ones (the "hall of mirrors" artifact).

The fix is an opaque background brush on the view itself.
``QGraphicsView.drawBackground`` fills every exposed area with it on every paint,
on both the OpenGL and the QWidget viewport. The brush belongs to the view, not
to the viewport widget, so it survives ``setViewport`` swaps. A QSS rule on the
viewport does not help: it cannot make the OpenGL framebuffer clear.
"""

from __future__ import annotations

from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QGraphicsView

from app.gui.widgets import tokens


def configure_canvas_background(view: QGraphicsView) -> None:
    """Give ``view`` the opaque SPROUTS canvas background (``tokens.BG_0``)."""
    view.setBackgroundBrush(QBrush(QColor(tokens.BG_0)))

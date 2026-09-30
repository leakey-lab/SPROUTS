"""Depth helpers: QSS can't do box-shadow, so the floating panels get a drop-shadow effect.

:class:`CachedDropShadow` looks like ``QGraphicsDropShadowEffect`` (same blur, offset and
colour; Qt's own filter makes its pixels) but costs a blit per paint. The stock effect
renders its widget into an offscreen pixmap and blurs it on every paint, and a floating
panel repaints with every canvas frame under it (on the raster canvas, about 1.8 ms a frame
for the five panels of the Correct page at 400 %). A panel's shadow depends only on its
size and outline, so it is made once per size and device pixel ratio (a stand-in panel
rendered with an offline ``QGraphicsDropShadowEffect``), kept in a small cache, and blitted;
the widget then paints straight into the window.
"""

from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QGraphicsEffect, QWidget

from . import tokens

__all__ = ["CachedDropShadow", "drop_shadow", "pop_shadow"]

PANEL_RADIUS = 13.0  # the floating panels' QSS border-radius (px)
_CACHE_SIZE = 24  # shadows kept: a few panels, a few sizes each (about 0.3 MB a shadow)
_Key = tuple[int, int, float, float, float, float, float, int]  # w, h, dpr, style
_cache: OrderedDict[_Key, tuple[QPixmap, QPointF]] = OrderedDict()
# A stand-in with the floating panels' QSS (overlays._FloatingPanel): the shadow's shape.
_STAND_IN_QSS = (
    f"QFrame {{{{ background-color: {tokens.BG_1}; border: 1px solid {tokens.BORDER_STRONG};"
    " border-radius: {radius}px; }}"
)


def _shadow_colour(alpha: int) -> QColor:
    """The drop shadows' colour: black at ``alpha``."""
    return QColor(0, 0, 0, alpha)


def _margins(blur: float, dx: float, dy: float) -> tuple[int, int, int, int]:
    """Logical px the shadow reaches beyond the outline, (left, top, right, bottom): what
    ``QGraphicsDropShadowEffect.boundingRectFor`` adds, rounded up."""
    left, top = math.ceil(max(0.0, blur - dx)), math.ceil(max(0.0, blur - dy))
    return left, top, math.ceil(max(0.0, blur + dx)), math.ceil(max(0.0, blur + dy))


def _shadow_image(width: int, height: int, dpr: float, radius: float, blur, dx, dy, alpha):
    """The shadow alone of a ``width`` x ``height`` rounded panel at ``dpr``, as the stock
    effect draws it, with ``_margins`` around the panel (an image of ``dpr``).

    A stand-in panel (the floating panels' outline) with a ``QGraphicsDropShadowEffect`` is
    rendered twice, as the window renders it: the outline over its shadow, then the outline
    alone. Where the outline is transparent or partly so, ``a = o + (1 - o) s`` gives the
    shadow back; under the opaque inside of the outline the panel hides it: left clear.
    """
    left, top, right, bottom = _margins(blur, dx, dy)
    size = (width + left + right, height + top + bottom)
    host = QWidget()
    host.resize(*size)
    panel = QFrame(host)
    panel.setStyleSheet(_STAND_IN_QSS.format(radius=radius))
    panel.setGeometry(left, top, width, height)
    effect = QGraphicsDropShadowEffect(panel)
    effect.setBlurRadius(blur)
    effect.setOffset(dx, dy)
    effect.setColor(_shadow_colour(alpha))
    panel.setGraphicsEffect(effect)
    device = (math.ceil(size[0] * dpr), math.ceil(size[1] * dpr))
    pictures = []
    for enabled in (True, False):
        effect.setEnabled(enabled)
        image = QImage(*device, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(0)
        host.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        pictures.append(_alpha(image).copy())  # the view dies with the image
    both, outline = (plane.astype(np.int32) for plane in pictures)
    shadow = np.zeros_like(both)
    open_ = outline < 255
    shadow[open_] = ((both - outline)[open_] * 255 + (255 - outline[open_]) // 2) // (
        255 - outline[open_]
    )
    out = QImage(*device, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    _alpha(out)[:] = np.clip(shadow, 0, 255).astype(np.uint8)  # black: colour channels stay 0
    return out


def _alpha(image: QImage) -> np.ndarray:
    """A writable view of ``image``'s alpha channel (ARGB32, little-endian: byte 3)."""
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, 3]


def shadow_pixmap(
    width: float, height: float, dpr: float, radius: float, blur, dx, dy, alpha
) -> tuple[QPixmap, QPointF]:
    """The cached shadow of a ``width`` x ``height`` (logical px) panel at ``dpr``, and where
    its top-left goes relative to the panel's (logical px)."""
    size = (max(1, math.ceil(width)), max(1, math.ceil(height)))
    key = (*size, round(dpr, 4), float(radius), float(blur), float(dx), float(dy), int(alpha))
    found = _cache.get(key)
    if found is not None:
        _cache.move_to_end(key)
        return found
    pixmap = QPixmap.fromImage(_shadow_image(*size, dpr, radius, blur, dx, dy, alpha))
    pixmap.setDevicePixelRatio(dpr)
    left, top, _right, _bottom = _margins(blur, dx, dy)
    found = (pixmap, QPointF(-left, -top))
    _cache[key] = found
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return found


class CachedDropShadow(QGraphicsEffect):
    """A drop shadow blitted from a cached pixmap under the widget, which paints itself.

    ``blur``, ``dx``, ``dy`` and ``alpha`` mean what they mean for
    ``QGraphicsDropShadowEffect`` (a black shadow); ``radius`` is the widget's rounded outline.
    """

    def __init__(
        self,
        parent=None,
        *,
        blur: float = 24,
        dx: float = 0,
        dy: float = 8,
        alpha: int = 140,
        radius: float = PANEL_RADIUS,
    ) -> None:
        super().__init__(parent)
        self._blur, self._dx, self._dy = float(blur), float(dx), float(dy)
        self._alpha, self._radius = int(alpha), float(radius)

    def blurRadius(self) -> float:  # noqa: N802 - the QGraphicsDropShadowEffect names
        return self._blur

    def xOffset(self) -> float:  # noqa: N802
        return self._dx

    def yOffset(self) -> float:  # noqa: N802
        return self._dy

    def color(self) -> QColor:
        return _shadow_colour(self._alpha)

    def boundingRectFor(self, rect: QRectF) -> QRectF:  # noqa: N802 - Qt override
        """The source rect and its shadow (as ``QGraphicsDropShadowEffect`` reports it)."""
        blur = self._blur
        shadow = rect.translated(self._dx, self._dy).adjusted(-blur, -blur, blur, blur)
        return rect.united(shadow)

    def draw(self, painter: QPainter) -> None:
        """Blit the shadow for the source's size, then let the source paint itself."""
        rect = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates)
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        if rect.width() > 0 and rect.height() > 0:
            pixmap, origin = shadow_pixmap(
                rect.width(), rect.height(), dpr, self._radius,
                self._blur, self._dx, self._dy, self._alpha,
            )  # fmt: skip
            painter.drawPixmap(rect.topLeft() + origin, pixmap)
        self.drawSource(painter)


def drop_shadow(
    widget: QWidget,
    *,
    blur: int = 24,
    dy: int = 8,
    dx: int = 0,
    alpha: int = 140,
    radius: float = PANEL_RADIUS,
) -> CachedDropShadow:
    """Attach a soft drop shadow to ``widget`` (a rounded panel) and return the effect."""
    effect = CachedDropShadow(widget, blur=blur, dx=dx, dy=dy, alpha=alpha, radius=radius)
    widget.setGraphicsEffect(effect)
    return effect


def pop_shadow(widget: QWidget, *, radius: float = PANEL_RADIUS) -> CachedDropShadow:
    """Stronger shadow for floating popovers/docks (--shadow-pop)."""
    return drop_shadow(widget, blur=50, dy=18, alpha=180, radius=radius)

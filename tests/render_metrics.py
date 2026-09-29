"""Render-quality metrics and helpers for the canvas tests (test support, not a test module).

Ported from the zoom-render and contracts scouts (``scratch/zoom-render/zr_common.py``,
``bench_proto_raster.py``, ``scratch/contracts-tests/render/render_metrics.py`` and
``render_design.py``). Import as ``from tests.render_metrics import ...``.

* Patterns: :func:`sine_image`, :func:`stripes_image`, :func:`chirp_image`, :func:`zone_plate`.
* Array metrics (Qt-free): :func:`psnr`, :func:`equal_neighbour_fraction`, :func:`mean_run`,
  :func:`sine_step_ratio`, :func:`chirp_alias_std`, :func:`chirp_pan_delta`, :func:`ring_std`.
* References: :func:`remap_reference` (what an ideal nearest / bilinear renderer shows) and
  :func:`area_reference` (ideal zoom-out: area average, then bilinear placement).
* Qt helpers (PyQt6 is imported when they are called): :func:`qimage_to_rgb`, :func:`grab`,
  :func:`view_mapping`, :func:`render_view`.

Device mapping convention: a view shows scene point ``p`` at device ``p * zoom + (dx, dy)``;
device pixel ``x`` covers ``[x, x + 1)`` and is sampled at its centre ``x + 0.5``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np

# --------------------------------------------------------------------------- patterns


def sine_image(w: int = 640, h: int = 480, period: float = 7.0) -> np.ndarray:
    """Gray ``(h, w)`` uint8: ``127 + 100 sin(2 pi x / period)``, constant down each column."""
    row = (127 + 100 * np.sin(2 * np.pi * np.arange(w) / period)).astype(np.uint8)
    return np.tile(row, (h, 1))


def stripes_image(w: int = 640, h: int = 480) -> np.ndarray:
    """Gray ``(h, w)`` uint8 of 1 px black / white columns (ideal zoom-out: mean 127.5)."""
    row = ((np.arange(w) % 2) * 255).astype(np.uint8)
    return np.tile(row, (h, 1))


def chirp_image(w: int = 640, h: int = 64, shift: int = 0) -> np.ndarray:
    """Gray ``(h, w)`` uint8 horizontal chirp from 0 to 0.5 cycles / px (``shift``: pan probe)."""
    x = np.arange(w, dtype=np.float64) + shift
    row = 127.5 + 127.5 * np.cos(np.pi * x * x / (2 * w))
    return np.tile(row, (h, 1)).astype(np.uint8)


def zone_plate(n: int = 1200) -> np.ndarray:
    """Gray ``(n, n)`` uint8 radial chirp (full contrast, reaches Nyquist at the edge)."""
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64)
    r2 = (xx - n / 2) ** 2 + (yy - n / 2) ** 2
    return (127.5 + 127.5 * np.cos(np.pi * r2 / (2 * n))).astype(np.uint8)


# ----------------------------------------------------------------------- array metrics


def _gray(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float64) if a.ndim == 2 else a.astype(np.float64).mean(axis=2)


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    """Peak signal-to-noise ratio of ``a`` against ``b`` in dB (``inf`` when equal)."""
    d = a.astype(np.float64) - b.astype(np.float64)
    mse = float(np.mean(d * d))
    return float("inf") if mse == 0 else float(10 * np.log10(255.0**2 / mse))


def _same_as_left(a: np.ndarray) -> np.ndarray:
    same = a[:, 1:] == a[:, :-1]
    return same if a.ndim == 2 else np.all(same, axis=-1)


def equal_neighbour_fraction(a: np.ndarray) -> float:
    """Fraction of horizontally adjacent pixel pairs that are identical (blockiness)."""
    return float(_same_as_left(a).mean())


def mean_run(a: np.ndarray) -> float:
    """Mean length of horizontal runs of identical pixels (1.0 = every pixel differs)."""
    same = _same_as_left(a)
    total = a.shape[0] * a.shape[1]
    runs = total - int(same.sum())
    return total / max(1, runs)


def sine_step_ratio(region: np.ndarray, source: np.ndarray) -> float:
    """Largest step between adjacent screen pixels over the largest between image pixels.

    About ``1 / zoom`` for a smooth magnification, about 1.0 for nearest (hard blocks).
    """
    src_step = float(np.abs(np.diff(_gray(source), axis=1)).max())
    scr_step = float(np.abs(np.diff(_gray(region), axis=1)).max())
    return scr_step / src_step


def chirp_band(out: np.ndarray, zoom: float, w: int = 640) -> np.ndarray:
    """The part of a rendered chirp above the output Nyquist (+15 %): only aliasing survives."""
    cut = w * zoom * 1.15  # source x where the chirp passes the output Nyquist
    x0, x1 = int(cut * zoom), int(0.97 * w * zoom)
    return _gray(out)[:, x0:x1] if x1 - x0 >= 4 else np.zeros((1, 0))


def chirp_alias_std(out: np.ndarray, zoom: float, w: int = 640) -> float | None:
    """Std of the aliased band of a chirp rendered at ``zoom`` (0 = perfectly filtered)."""
    band = chirp_band(out, zoom, w)
    return None if band.size == 0 else float(band.std())


def chirp_pan_delta(a: np.ndarray, b: np.ndarray, zoom: float, w: int = 640) -> float | None:
    """Mean |difference| of the aliased band between chirps shifted by 1 source px (shimmer)."""
    ba, bb = chirp_band(a, zoom, w), chirp_band(b, zoom, w)
    return None if ba.size == 0 else float(np.abs(ba - bb).mean())


def ring_std(
    img: np.ndarray, zoom: float, origin: tuple[float, float] = (0.0, 0.0), n: int = 1200
) -> float | None:
    """Std of a zone plate's outer annulus (0.30 n .. 0.48 n) as rendered: aliasing energy.

    ``origin`` is the device position of the plate's top-left corner, ``zoom`` its scale.
    ``None`` when fewer than 50 pixels of the annulus are inside ``img``.
    """
    cx, cy = origin[0] + (n / 2) * zoom, origin[1] + (n / 2) * zoom
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    rr = np.hypot(xx - cx, yy - cy)
    ring = (rr >= 0.30 * n * zoom) & (rr <= 0.48 * n * zoom)
    if ring.sum() < 50:
        return None
    return float(_gray(img)[ring].std())


# -------------------------------------------------------------------------- references


def remap_reference(
    src: np.ndarray, zoom: float, dx: float, dy: float, out_w: int, out_h: int, interp: int
) -> np.ndarray:
    """What an ideal renderer with ``interp`` shows for ``src`` placed at ``(dx, dy)``.

    Device pixel centre ``x + 0.5`` shows image coordinate ``u = (x + 0.5 - dx) / zoom``;
    nearest takes pixel ``floor(u)``, bilinear samples at ``u - 0.5`` (cv2 convention).
    Outside the image: 0.
    """
    xs = (np.arange(out_w, dtype=np.float64) + 0.5 - dx) / zoom
    ys = (np.arange(out_h, dtype=np.float64) + 0.5 - dy) / zoom
    if interp == cv2.INTER_NEAREST:
        xs, ys = np.floor(xs), np.floor(ys)
    else:
        xs, ys = xs - 0.5, ys - 0.5
    mx, my = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32))
    return cv2.remap(src, mx, my, interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def area_reference(
    src: np.ndarray, zoom: float, dx: float, dy: float, out_w: int, out_h: int
) -> np.ndarray:
    """Ideal zoom-out of ``src``: area average to the zoom, then bilinear placement."""
    h, w = src.shape[:2]
    small = cv2.resize(
        src, (max(1, round(w * zoom)), max(1, round(h * zoom))), interpolation=cv2.INTER_AREA
    )
    zs = small.shape[1] / w
    xs = (np.arange(out_w, dtype=np.float32) + 0.5 - dx) * (zs / zoom) - 0.5
    ys = (np.arange(out_h, dtype=np.float32) + 0.5 - dy) * (zs / zoom) - 0.5
    mx, my = np.meshgrid(xs, ys)
    return cv2.remap(small, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


# --------------------------------------------------------------------------- Qt helpers


def qimage_to_rgb(image) -> np.ndarray:
    """``(h, w, 3)`` uint8 RGB copy of a ``QImage``."""
    from PyQt6.QtGui import QImage

    image = image.convertToFormat(QImage.Format.Format_RGB888)
    w, h = image.width(), image.height()
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 3].reshape(h, w, 3).copy()


def grab(view) -> np.ndarray:
    """RGB array of what ``view``'s viewport shows (``viewport().grab()``)."""
    return qimage_to_rgb(view.viewport().grab().toImage())


def view_mapping(view) -> tuple[float, float, float]:
    """``(zoom, dx, dy)``: device = scene * zoom + (dx, dy) for a scale + translate view."""
    t = view.viewportTransform()
    return t.m11(), t.dx(), t.dy()


@dataclass(frozen=True)
class Render:
    """One grabbed frame: RGB pixels and the scene-to-device mapping it was drawn with."""

    rgb: np.ndarray
    zoom: float
    dx: float
    dy: float


def render_view(
    items: Iterable,
    zoom: float,
    size: tuple[int, int] = (640, 480),
    *,
    centre: tuple[float, float] | None = None,
    antialias: bool = True,
    background=None,
) -> Render:
    """Grab ``items`` in a raster canvas view (``size`` viewport px) at ``zoom``.

    The view is a plain ``CanvasViewMixin`` view with ``Antialiasing | TextAntialiasing``
    (as ``EditorCanvasView`` sets them) unless ``antialias`` is False, on a black
    background unless ``background`` (a ``QColor`` / global colour) is given, centred on
    ``centre`` (scene coordinates; default: the items' bounds centre). The items are
    taken out of the scene again before the view is deleted, so callers may reuse them.
    """
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QPainter
    from PyQt6.QtWidgets import QApplication, QFrame, QGraphicsScene, QGraphicsView

    from app.gui import canvas_gl
    from app.gui.canvas_view import CanvasViewMixin

    class _RenderView(CanvasViewMixin, QGraphicsView):
        def __init__(self) -> None:
            super().__init__(None)
            self._init_canvas()

    # Raster viewport, without probing GL and without leaking the env opt-out.
    saved = os.environ.get(canvas_gl.ENV_VAR)
    os.environ[canvas_gl.ENV_VAR] = "0"
    try:
        view = _RenderView()
    finally:
        if saved is None:
            del os.environ[canvas_gl.ENV_VAR]
        else:
            os.environ[canvas_gl.ENV_VAR] = saved
    view.setFrameShape(QFrame.Shape.NoFrame)
    hints = QPainter.RenderHint.TextAntialiasing
    if antialias:
        hints |= QPainter.RenderHint.Antialiasing
    view.setRenderHints(hints)
    view.setBackgroundBrush(Qt.GlobalColor.black if background is None else background)
    scene = QGraphicsScene(view)
    items = list(items)
    bounds = QRectF()
    for item in items:
        scene.addItem(item)
        bounds = bounds.united(item.sceneBoundingRect())
    view.setScene(scene)
    view.set_content_rect(bounds)
    view.resize(*size)
    c = centre if centre is not None else (bounds.center().x(), bounds.center().y())
    view.set_zoom(zoom)  # before the first paint: items only ever see this zoom
    view.centerOn(QPointF(*c))
    view.show()
    QApplication.processEvents()
    view.centerOn(QPointF(*c))  # again, now that the viewport has its final size
    QApplication.processEvents()
    rgb = grab(view)
    z, dx, dy = view_mapping(view)
    for item in items:
        scene.removeItem(item)
    view.close()
    view.deleteLater()
    QApplication.processEvents()
    return Render(rgb, z, dx, dy)

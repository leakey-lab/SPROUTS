"""The skeleton layer item: a 1 px skeleton raster drawn as a line at every zoom.

:class:`SkeletonLayerItem` (spec section 10.3) draws a skeleton mask (uint8 0/255, kept by
reference, never modified) in the tier the painter's device scale ``s`` asks for
(:func:`app.gui.canvas_layers.device_scale`: view zoom times devicePixelRatio times the
item's own ``scale``):

* ``s < 1``, "raster-lod": ``skeleton_overlay.overlay_indices`` (the line plus a dark
  one-pixel halo) through a max-pool pyramid, drawn nearest from the finest level that is
  not minified, so a 1 px line never breaks into dashes.
* ``1 <= s < 2``, "raster": that overlay at level 0 in 256 px tiles, drawn nearest.
* ``s >= 2``, "vector": m-adjacency links between pixel centres, a 2 px cosmetic line over
  a 4 px halo composited once (joints do not darken), antialiased; "vector-thin" (1 px) or
  back to "raster" when the visible segments exceed the paint engine's budget. Idle time
  extracts the tiles ahead of the first vector frame.
* ``vectors=False`` (the Library's generated file, lines about 2 px wide): "raster-lod"
  below 1, else "raster-smooth", the overlay drawn bilinear (soft edges, no pixel blocks).

The vector extraction is the Qt-free :mod:`app.imaging.skeleton_vectors`.
Colours come from :mod:`app.gui.widgets.tokens`.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPaintEngine, QPainter, QPainterPath, QPen, QTransform
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from app.gui.canvas_layers import device_scale
from app.gui.canvas_layers.raster import snapped_rect
from app.gui.paint_canvas import pixel_words
from app.gui.skeleton_overlay import HALO_ALPHA, HALO_INDEX, LINE_INDEX, overlay_indices
from app.gui.widgets import tokens
from app.imaging.pyramid import ImagePyramid, max_half
from app.imaging.skeleton_vectors import Box, clip_box, tile_segments

Edges = tuple[float, float, float, float]  # (left, top, right, bottom) in skeleton pixels
TileKey = tuple[int, int]  # (tx, ty)

_GL_ENGINES = (QPaintEngine.Type.OpenGL, QPaintEngine.Type.OpenGL2)
_PEN_REACH = 3.0  # device px a vector pen reaches beyond a segment (halo half-width + AA)
_LEVEL_PATCH_SHARE = 0.5  # a larger stale part of a LOD level image: rebuilt, not patched


# ------------------------------------------------------------------------------ the item
class _TileVectors:
    """The merged links of one tile in pixel-centre coordinates: lines ``(n, 4)`` with
    their bounds, and dots ``(m, 2)`` (a zero-length line would draw nothing)."""

    __slots__ = ("revision", "lines", "lo", "hi", "dots", "reach", "_path")

    def __init__(self, revision: int, links: np.ndarray, reach: tuple[float, ...]) -> None:
        self.revision = revision
        centres = links.astype(np.float64) + 0.5
        dot = (links[:, 0] == links[:, 2]) & (links[:, 1] == links[:, 3])
        self.lines = centres[~dot]
        self.dots = centres[dot, :2]
        self.lo = np.minimum(self.lines[:, :2], self.lines[:, 2:])
        self.hi = np.maximum(self.lines[:, :2], self.lines[:, 2:])
        self.reach = reach  # (x0, y0, x1, y1) holding every line and dot of the tile
        self._path: QPainterPath | None = None

    def path(self) -> QPainterPath:
        """Every line of the tile as one path, a move and a line each (built once)."""
        if self._path is None:
            path = QPainterPath()
            for x0, y0, x1, y1 in self.lines.tolist():
                path.moveTo(x0, y0)
                path.lineTo(x1, y1)
            self._path = path
        return self._path

    def visible(self, rect: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
        """The lines and dots whose bounds meet ``rect`` (left, top, right, bottom)."""
        picked = self._pick(rect)
        if picked is None:
            return self.lines, self.dots
        return self.lines[picked[0]], self.dots[picked[1]]

    def count_in(self, rect: tuple[float, ...]) -> int:
        """How many lines and dots :meth:`visible` returns for ``rect``."""
        picked = self._pick(rect)
        if picked is None:
            return len(self.lines) + len(self.dots)
        return int(np.count_nonzero(picked[0])) + int(np.count_nonzero(picked[1]))

    def _pick(self, rect: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray] | None:
        """Line and dot masks of ``rect``; None when it holds every line and dot."""
        left, top, right, bottom = rect
        x0, y0, x1, y1 = self.reach
        if left <= x0 and top <= y0 and right >= x1 and bottom >= y1:
            return None
        lo, hi, dots = self.lo, self.hi, self.dots
        lines = (hi[:, 0] >= left) & (lo[:, 0] <= right) & (hi[:, 1] >= top) & (lo[:, 1] <= bottom)
        near = (dots[:, 0] >= left) & (dots[:, 0] <= right)
        near &= (dots[:, 1] >= top) & (dots[:, 1] <= bottom)
        return lines, near


class SkeletonLayerItem(QGraphicsItem):
    """A 1 px skeleton raster drawn as a line at every zoom (spec section 10.3).

    ``color`` is the line colour (a token), ``halo`` adds the dark halo, and
    ``vectors=False`` never draws pixels or vectors magnified: the overlay is drawn
    bilinear from device scale 1 (the Library). Opacity and visibility use the standard
    item API; the mask is only read.
    """

    VECTOR_MIN_SCALE = 2.0
    TILE = 256  # vector extraction tile, image px
    EXTRACT_BUDGET_MS = 4.0  # vector extraction per paint
    WARM_DELAY_MS = 150  # idle time after the last set_mask before tiles are warmed
    WARM_SLICE_MS = 2.0  # vector extraction per warm-up slice (one event-loop turn)
    GL_SEGMENT_BUDGET = 6000
    RASTER_HALO_BUDGET = 400
    RASTER_THIN_BUDGET = 2500
    HALO_LAYER_MAX_PX = 4096 * 4096  # a larger raster halo layer draws the halo directly

    def __init__(
        self,
        parent: QGraphicsItem | None = None,
        *,
        color: str = tokens.SKEL,
        halo: bool = True,
        vectors: bool = True,
    ) -> None:
        """An empty item; :meth:`set_mask` gives it a skeleton."""
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
        self._vectors = vectors
        line = QColor(color)
        dark = QColor(tokens.BG_0)
        dark.setAlpha(HALO_ALPHA)
        self._table = _color_table(line, dark if halo else None)
        self._line_pen = _cosmetic_pen(line, 2.0)
        self._thin_pen = _cosmetic_pen(line, 1.0)
        self._halo_pen = _cosmetic_pen(dark, 4.0) if halo else None
        self._halo_solid = _cosmetic_pen(QColor(tokens.BG_0), 4.0)  # a halo layer, blended once
        self._mask: np.ndarray | None = None
        self._indices: np.ndarray | None = None  # overlay_indices of the mask
        self._width = self._height = 0
        self._scale = (1.0, 1.0)
        self._revision = 0
        self._mode = ""
        self._base_revision = 0  # every tile's revision after the last full change
        self._tile_revisions: dict[TileKey, int] = {}
        self._tile_vectors: dict[TileKey, _TileVectors] = {}
        self._tile_images: dict[TileKey, QImage] = {}
        self._full_image: QImage | None = None  # vectors=False: one image, bilinear
        self._pyramid: ImagePyramid | None = None  # max-pool pyramid of the mask
        self._level_images: dict[int, QImage] = {}
        # Per cached level image: the level px box (x0, y0, x1, y1) the mask changed since.
        self._level_stale: dict[int, tuple[int, int, int, int]] = {}
        self._warm_timer: QTimer | None = None  # idle vector extraction (see _warm_later)

    # ------------------------------------------------------------------ public API
    def set_mask(
        self,
        mask: np.ndarray | None,
        *,
        changed: Box | None = None,
        scale: tuple[float, float] = (1.0, 1.0),
    ) -> None:
        """Show ``mask`` (uint8 0/255, kept by reference and only read).

        ``changed`` is the ``(x, y, w, h)`` box of the pixels that may differ from the
        previous mask (None: everything changed). ``scale`` is the item transform, e.g.
        image size / skeleton size for a skeleton raster smaller than the photo. Never
        extracts vectors: a paint does, lazily, and idle time does once the changes
        settle (:meth:`_warm_later`).
        """
        self._revision += 1
        self._set_scale(scale)
        previous, self._mask = self._mask, mask
        self._warm_later()
        if mask is None:
            self._indices = None
            self._resize(0, 0)
        elif (
            changed is None
            or self._indices is None
            or (mask.shape[1], mask.shape[0]) != (self._width, self._height)
        ):
            self._resize(mask.shape[1], mask.shape[0])
            self._indices = np.ascontiguousarray(overlay_indices(mask))
        else:
            if mask is not previous:
                self._pyramid = None  # it reads its level 0 by reference
            self._update_box(changed)
            return
        self._drop_caches()
        self.update()

    def revision(self) -> int:
        """Number of ``set_mask`` calls so far (test hook)."""
        return self._revision

    def mode_painted(self) -> str:
        """The tier the last paint used: "raster-lod", "raster", "raster-smooth", "vector",
        "vector-thin", or "" before any paint and without a mask (test hook)."""
        return self._mode

    def vector_tiles(self) -> dict[TileKey, int]:
        """``(tx, ty) -> tile revision`` of the cached vector segments (test hook)."""
        return {key: tile.revision for key, tile in self._tile_vectors.items()}

    # ------------------------------------------------------------------ QGraphicsItem
    def boundingRect(self) -> QRectF:
        """The skeleton raster in its own pixels: ``(0, 0, w, h)``."""
        return QRectF(0.0, 0.0, float(self._width), float(self._height))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Draw ``option.exposedRect`` in the tier of the painter's device scale."""
        if self._indices is None or not self._width or not self._height:
            self._mode = ""
            return
        exposed = option.exposedRect.intersected(self.boundingRect())
        if painter.hasClipping():  # QGraphicsScene.render exposes the whole item: clip it
            exposed = exposed.intersected(painter.clipBoundingRect())
        if exposed.isEmpty():
            return
        scale = device_scale(painter)
        painter.save()  # the Library view sets DontSavePainterState
        try:
            # Hints off before the clip: an antialiased clip would blend its edges.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.setClipRect(exposed, Qt.ClipOperation.IntersectClip)
            if scale < 1.0:
                self._mode = "raster-lod"
                self._paint_lod(painter, scale)
            elif not self._vectors:
                self._mode = "raster-smooth"
                self._paint_smooth(painter)
            elif scale < self.VECTOR_MIN_SCALE:
                self._mode = "raster"
                self._paint_tiles(painter, self._tiles_in(_edges(exposed), reach=0.0))
            else:
                seen = self._on_screen(painter, widget, exposed)
                self._mode = self._paint_vectors(painter, exposed, seen, scale)
        finally:
            painter.restore()

    # ------------------------------------------------------------------ tiers
    def _paint_lod(self, painter: QPainter, scale: float) -> None:
        """Max-pool level with at least one device px per level px, drawn nearest.

        The pyramid pools the skeleton (lines stay connected) and each level gets its own
        overlay, so the halo is one level pixel on both sides at every zoom.
        """
        if self._pyramid is None:  # down to 1 px: small skeletons need levels too (lazy)
            self._pyramid = ImagePyramid(self._mask, reducer=max_half, min_side=1)
        k = ImagePyramid.level_for_scale(scale, self._pyramid.count, nearest=True)
        image = self._level_image(k)
        f = float(1 << k)
        w, h = float(self._width), float(self._height)
        painter.drawImage(QRectF(0.0, 0.0, w, h), image, QRectF(0.0, 0.0, w / f, h / f))

    def _level_image(self, k: int) -> QImage:
        """The overlay image of LOD level ``k``: built once, then patched in place over the
        part the mask changed since (:meth:`_stale_levels`), as a full rebuild writes it
        there (the image's ``cacheKey`` moves)."""
        image = self._level_images.get(k)
        if image is None:
            level = self._pyramid.level(k)
            indices = self._indices if k == 0 else overlay_indices(level)
            image = self._level_images[k] = _argb(indices, self._table)
            return image
        stale = self._level_stale.pop(k, None)
        if stale is not None:
            x0, y0, x1, y1 = stale
            if k == 0:  # the level-0 overlay is up to date already
                indices = self._indices[y0:y1, x0:x1]
            else:  # the overlay there reads one more level px around it (the 3x3 halo)
                lw, lh = self._pyramid.level_size(k)
                ix0, iy0 = max(x0 - 1, 0), max(y0 - 1, 0)
                ix1, iy1 = min(x1 + 1, lw), min(y1 + 1, lh)
                fresh = overlay_indices(self._pyramid.level(k)[iy0:iy1, ix0:ix1])
                indices = fresh[y0 - iy0 : y1 - iy0, x0 - ix0 : x1 - ix0]
            patch = _argb(indices, self._table)  # Qt's conversion: the words of a rebuild
            pixel_words(image)[y0:y1, x0:x1] = pixel_words(patch)
        return image

    def _paint_smooth(self, painter: QPainter) -> None:
        """The whole level-0 overlay, bilinear (one image: tiles would seam)."""
        if self._full_image is None:
            self._full_image = _argb(self._indices, self._table)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self.boundingRect(), self._full_image)

    def _paint_tiles(self, painter: QPainter, keys: list[TileKey]) -> None:
        """Level-0 overlay tiles, nearest, with edges on device pixels (no seams), where the
        mask layer's tiles put theirs."""
        transform = painter.deviceTransform()
        for key in keys:
            x0, y0, x1, y1 = self._tile_bounds(key)
            target = snapped_rect(x0, y0, x1 - x0, y1 - y0, transform)
            painter.drawImage(target, self._tile_image(key))

    def _paint_vectors(self, painter: QPainter, exposed: QRectF, seen: QRectF, scale: float) -> str:
        """Vector links within the paint engine's budget; returns the tier painted.

        The tier is decided by the segments of the whole on-screen part (``seen``), so a
        small repaint (a hover on the raster viewport) draws the tier of the full frame;
        only ``exposed`` is drawn. The 2 px line is given the lines of all of ``seen`` too:
        ``drawLines`` strokes 16 lines at a time and a translucent joint blends once inside
        a batch, twice across one, so only the full frame's list keeps a repainted joint as
        the full frame drew it (the 1 px line does not depend on its batches). A tile not
        extracted yet (extraction stops after EXTRACT_BUDGET_MS per paint) draws the pixel
        overlay, and the item asks for another paint until every tile on screen is done.
        """
        gl = _is_gl(painter)
        limit = self.GL_SEGMENT_BUDGET if gl else self.RASTER_THIN_BUDGET
        reach = _PEN_REACH / scale
        count, pending = self._count_on_screen(_edges(seen, reach), limit)
        pixels = self._tiles_in(_edges(exposed), reach=0.0)
        if count > limit:
            self._paint_tiles(painter, pixels)
            return "raster"
        waiting = [key for key in pixels if key not in self._tile_vectors]
        if waiting:
            self._paint_tiles(painter, waiting)
        if pending:
            self.update(seen)  # extract the rest in the next frames
        thin = not gl and count > self.RASTER_HALO_BUDGET
        drawn = _edges(exposed if thin else seen, reach)
        tiles = [self._tile_vectors.get(key) for key in self._tiles_in(drawn)]
        tiles = [tile for tile in tiles if tile is not None]
        if not tiles:
            return "vector-thin" if thin else "vector"
        parts = [tile.visible(drawn) for tile in tiles]
        line_rows = np.concatenate([part[0] for part in parts])
        dot_rows = np.concatenate([part[1] for part in parts])
        lines, dots = _qt_array(QLineF, line_rows, 4), _qt_array(QPointF, dot_rows, 2)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if thin:
            _draw(painter, self._thin_pen, lines, dots)
            return "vector-thin"
        if self._halo_pen is not None and gl:
            self._halo_path(painter, tiles, dots)
        elif self._halo_pen is not None:
            area = _extent(line_rows, dot_rows, reach).intersected(
                exposed.adjusted(-reach, -reach, reach, reach)
            )
            self._halo_layer(painter, area, lines, dots)
        _draw(painter, self._line_pen, lines, dots)
        return "vector"

    def _halo_path(self, painter: QPainter, tiles: list[_TileVectors], dots) -> None:
        """OpenGL: the halo of the visible tiles as one stroked path (S6). The engine
        covers a translucent stroke once, so joints do not stack alpha (drawLines strokes
        16 lines per call and did); it is also 2.7x faster than drawLines."""
        path = QPainterPath()
        for tile in tiles:
            path.addPath(tile.path())
        painter.setPen(self._halo_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        if len(dots):
            painter.drawPoints(dots)  # isolated pixels: their halos never overlap

    def _halo_layer(self, painter: QPainter, area: QRectF, lines, dots) -> None:
        """Raster engine: the halo drawn opaque into its own image over ``area`` (item px),
        blended once at HALO_ALPHA (S6): joints do not stack alpha. (A stroked path is
        8x slower than drawLines on this engine.)"""
        world = painter.worldTransform()
        rect = world.mapRect(area).toAlignedRect()
        if rect.isEmpty():
            return
        device = painter.device()
        ratio = device.devicePixelRatioF() if device is not None else 1.0
        if rect.width() * rect.height() * ratio * ratio > self.HALO_LAYER_MAX_PX:
            _draw(painter, self._halo_pen, lines, dots)  # a huge render: memory first
            return
        layer = QImage(
            max(1, math.ceil(rect.width() * ratio)),
            max(1, math.ceil(rect.height() * ratio)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        layer.setDevicePixelRatio(ratio)
        layer.fill(0)
        inner = QPainter(layer)
        inner.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inner.setTransform(world * QTransform.fromTranslate(-rect.x(), -rect.y()))
        _draw(inner, self._halo_solid, lines, dots)
        inner.end()
        painter.save()
        painter.resetTransform()
        painter.setOpacity(painter.opacity() * HALO_ALPHA / 255.0)
        painter.drawImage(QPointF(rect.x(), rect.y()), layer)
        painter.restore()

    def _count_on_screen(self, rect: Edges, limit: int) -> tuple[int, bool]:
        """Segments meeting ``rect`` and whether on-screen tiles are still pending.

        Missing tiles are extracted centre first, within EXTRACT_BUDGET_MS (at least one
        tile per call, so a zero budget still progresses); counting stops past ``limit``,
        where the answer is the pixel overlay whatever the pending tiles hold.
        """
        keys = self._tiles_in(rect)
        cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
        half = self.TILE / 2.0
        keys.sort(
            key=lambda k: (k[0] * self.TILE + half - cx) ** 2 + (k[1] * self.TILE + half - cy) ** 2
        )
        budget = self.EXTRACT_BUDGET_MS / 1000.0
        start = time.perf_counter()
        count, extracted, pending = 0, 0, False
        for key in keys:
            tile = self._tile_vectors.get(key)
            if tile is None:
                if extracted and time.perf_counter() - start >= budget:
                    pending = True
                    continue
                tile = self._extract(key)
                extracted += 1
            count += tile.count_in(rect)
            if count > limit:
                return count, False
        return count, pending

    def _on_screen(self, painter: QPainter, widget: QWidget | None, exposed: QRectF) -> QRectF:
        """The item part inside ``widget`` (the view's viewport), in item px; just
        ``exposed`` without a widget (``QGraphicsScene.render``)."""
        if widget is None:
            return exposed
        inverse, invertible = painter.transform().inverted()
        if not invertible:
            return exposed
        seen = inverse.mapRect(QRectF(widget.rect())).intersected(self.boundingRect())
        return seen.united(exposed)

    # ------------------------------------------------------------------ warm-up
    def _warm_later(self) -> None:
        """Extract every vector tile in idle slices once the changes settle (Q5).

        The first frame of a zoom into the vector tier (200 % and up) then finds its tiles
        ready instead of drawing pixel tiles and extracting under EXTRACT_BUDGET_MS. Each
        set_mask restarts the WARM_DELAY_MS wait, so a drag never warms tiles it is about
        to change; only an item shown in a view warms (renders alone stay lazy).
        """
        timer = self._warm_timer
        if timer is not None and sip.isdeleted(timer):  # its scene went
            timer = self._warm_timer = None
        if not self._vectors or self._mask is None:
            if timer is not None:
                timer.stop()
            return
        if timer is None:  # parented like the layer's timers: nothing fires once it is gone
            timer = self._warm_timer = QTimer(self.scene())
            timer.setSingleShot(True)
            timer.timeout.connect(self._warm_slice)
        timer.start(self.WARM_DELAY_MS)

    def _warm_slice(self) -> None:
        """Extract missing tiles for WARM_SLICE_MS, nearest the view first; then yield."""
        if sip.isdeleted(self) or self._mask is None or not self._vectors:
            return
        scene = self.scene()
        views = scene.views() if scene is not None else []
        if not views:
            return
        t = self.TILE
        cols, rows = math.ceil(self._width / t), math.ceil(self._height / t)
        missing = [
            (tx, ty)
            for ty in range(rows)
            for tx in range(cols)
            if (tx, ty) not in self._tile_vectors
        ]
        if not missing:
            return
        view = views[0]
        centre = self.mapFromScene(view.mapToScene(view.viewport().rect().center()))
        missing.sort(
            key=lambda k: (
                (k[0] * t + t / 2 - centre.x()) ** 2 + (k[1] * t + t / 2 - centre.y()) ** 2
            )
        )
        budget = self.WARM_SLICE_MS / 1000.0
        start = time.perf_counter()
        for done, key in enumerate(missing, 1):
            self._extract(key)
            if time.perf_counter() - start >= budget:
                timer = self._warm_timer
                if done < len(missing) and timer is not None and not sip.isdeleted(timer):
                    timer.start(0)  # the rest after the pending events
                return

    # ------------------------------------------------------------------ caches
    def _extract(self, key: TileKey) -> _TileVectors:
        """Extract and cache the vectors of tile ``key`` with its current revision."""
        x0, y0, x1, y1 = self._tile_bounds(key)
        links = tile_segments(self._mask, (x0, y0, x1 - x0, y1 - y0))
        revision = self._tile_revisions.get(key, self._base_revision)
        tile = _TileVectors(revision, links, (x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0))
        self._tile_vectors[key] = tile
        return tile

    def _tile_image(self, key: TileKey) -> QImage:
        image = self._tile_images.get(key)
        if image is None:
            x0, y0, x1, y1 = self._tile_bounds(key)
            image = self._tile_images[key] = _argb(self._indices[y0:y1, x0:x1], self._table)
        return image

    def _tile_bounds(self, key: TileKey) -> tuple[int, int, int, int]:
        t = self.TILE
        x0, y0 = key[0] * t, key[1] * t
        return x0, y0, min(self._width, x0 + t), min(self._height, y0 + t)

    def _tiles_in(self, rect: Edges, reach: float = 1.0) -> list[TileKey]:
        """The tiles meeting ``rect`` (item px) grown by ``reach``, row-major (a tile's
        links reach one pixel beyond it)."""
        left, top = max(0.0, rect[0] - reach), max(0.0, rect[1] - reach)
        right = min(float(self._width), rect[2] + reach)
        bottom = min(float(self._height), rect[3] + reach)
        if right <= left or bottom <= top:
            return []
        t = self.TILE
        tx0, ty0 = int(left) // t, int(top) // t
        tx1, ty1 = (math.ceil(right) - 1) // t, (math.ceil(bottom) - 1) // t
        return [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]

    def _update_box(self, changed: Box) -> None:
        """The mask changed inside ``changed`` only: O(box) overlay patch and invalidation.

        The overlay (line + 3x3 halo) changes on the box padded by 1 px and is recomputed
        there from the box padded by 2 px; tiles meeting the padded box lose their vectors
        (a link reads a one-pixel margin) and overlay images; the cached LOD level images
        are marked stale there and patched when drawn (:meth:`_stale_levels`). Nothing is
        extracted.
        """
        w, h = self._width, self._height
        x0, y0, x1, y1 = clip_box(changed, (h, w))
        if x1 <= x0 or y1 <= y0:
            return
        ox0, oy0, ox1, oy1 = max(x0 - 1, 0), max(y0 - 1, 0), min(x1 + 1, w), min(y1 + 1, h)
        ix0, iy0, ix1, iy1 = max(x0 - 2, 0), max(y0 - 2, 0), min(x1 + 2, w), min(y1 + 2, h)
        fresh = overlay_indices(self._mask[iy0:iy1, ix0:ix1])
        self._indices[oy0:oy1, ox0:ox1] = fresh[oy0 - iy0 : oy1 - iy0, ox0 - ix0 : ox1 - ix0]
        t = self.TILE
        for ty in range(oy0 // t, (oy1 - 1) // t + 1):
            for tx in range(ox0 // t, (ox1 - 1) // t + 1):
                self._tile_revisions[(tx, ty)] = self._revision
                self._tile_vectors.pop((tx, ty), None)
                self._tile_images.pop((tx, ty), None)
        self._full_image = None
        if self._pyramid is not None:
            self._pyramid.invalidate((x0, y0, x1 - x0, y1 - y0))
            self._stale_levels(x0, y0, x1, y1)
        else:  # a new array: its pyramid is built again, and its levels
            self._level_images.clear()
            self._level_stale.clear()
        self.update(QRectF(ox0, oy0, ox1 - ox0, oy1 - oy0))

    def _stale_levels(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Mark what the mask change in ``[x0, x1) x [y0, y1)`` (level-0 px) reaches in each
        cached LOD level image: the level px over the box and their 1 px halo, merged into
        one stale box per level, patched only when that level is drawn (:meth:`_level_image`).
        A level whose stale box passes ``_LEVEL_PATCH_SHARE`` of it is dropped instead: its
        rebuild when drawn is then about as cheap as the patch."""
        for k in list(self._level_images):
            lw, lh = self._pyramid.level_size(k)
            sx0, sy0 = max((x0 >> k) - 1, 0), max((y0 >> k) - 1, 0)
            sx1, sy1 = min(((x1 - 1) >> k) + 2, lw), min(((y1 - 1) >> k) + 2, lh)
            stale = self._level_stale.get(k)
            if stale is not None:
                sx0, sy0 = min(sx0, stale[0]), min(sy0, stale[1])
                sx1, sy1 = max(sx1, stale[2]), max(sy1, stale[3])
            if (sx1 - sx0) * (sy1 - sy0) > _LEVEL_PATCH_SHARE * lw * lh:
                del self._level_images[k]
                self._level_stale.pop(k, None)
            else:
                self._level_stale[k] = (sx0, sy0, sx1, sy1)

    def _drop_caches(self) -> None:
        self._base_revision = self._revision
        self._tile_revisions.clear()
        self._tile_vectors.clear()
        self._tile_images.clear()
        self._full_image = None
        self._pyramid = None
        self._level_images.clear()
        self._level_stale.clear()

    def _resize(self, width: int, height: int) -> None:
        if (width, height) != (self._width, self._height):
            self.prepareGeometryChange()
            self._width, self._height = width, height

    def _set_scale(self, scale: tuple[float, float]) -> None:
        sx, sy = float(scale[0]), float(scale[1])
        if (sx, sy) != self._scale:
            self._scale = (sx, sy)
            self.setTransform(QTransform.fromScale(sx, sy))


# ------------------------------------------------------------------------------ helpers
def _edges(rect: QRectF, grow: float = 0.0) -> Edges:
    """``(left, top, right, bottom)`` of ``rect`` grown by ``grow`` on every side."""
    return (rect.left() - grow, rect.top() - grow, rect.right() + grow, rect.bottom() + grow)


def _extent(lines: np.ndarray, dots: np.ndarray, grow: float) -> QRectF:
    """The rect holding every line ``(n, 4)`` and dot ``(m, 2)`` (item px), grown by ``grow``."""
    xs = np.concatenate((lines[:, 0], lines[:, 2], dots[:, 0]))
    ys = np.concatenate((lines[:, 1], lines[:, 3], dots[:, 1]))
    if not len(xs):
        return QRectF()
    x0, y0, x1, y1 = xs.min() - grow, ys.min() - grow, xs.max() + grow, ys.max() + grow
    return QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0))


def _draw(painter: QPainter, pen: QPen, lines, dots) -> None:
    """Every line and dot (``_qt_array`` rows) with ``pen``."""
    painter.setPen(pen)
    if len(lines):
        painter.drawLines(lines)
    if len(dots):
        painter.drawPoints(dots)


def _is_gl(painter: QPainter) -> bool:
    engine = painter.paintEngine()
    return engine is not None and engine.type() in _GL_ENGINES


def _argb(indices: np.ndarray, table: list[int]) -> QImage:
    """An owning ARGB32 premultiplied image of the palette ``indices``."""
    data = np.ascontiguousarray(indices)
    h, w = data.shape
    image = QImage(data.data, w, h, w, QImage.Format.Format_Indexed8)
    image.setColorTable(table)
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _color_table(line: QColor, halo: QColor | None) -> list[int]:
    """Indexed8 colour table: ``line`` on LINE_INDEX, ``halo`` on HALO_INDEX (transparent
    without one), transparent elsewhere."""
    table = [0] * 256
    table[LINE_INDEX] = line.rgba()
    if halo is not None:
        table[HALO_INDEX] = halo.rgba()
    return table


def _cosmetic_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCosmetic(True)
    return pen


_SHARES_NUMPY_LAYOUT: dict[type, bool] = {}


def _qt_array(cls: type, values: np.ndarray, width: int) -> sip.array | list:
    """Rows of ``values`` (n, ``width`` doubles) as ``cls`` (QLineF / QPointF) for
    ``drawLines`` / ``drawPoints``: a ``sip.array`` filled straight from numpy (0.01 ms
    for 6000 lines, against 3 ms for a list of QLineF), or a list where qreal is not
    double."""
    shares = _SHARES_NUMPY_LAYOUT.get(cls)
    if shares is None:
        shares = _SHARES_NUMPY_LAYOUT[cls] = _probe_layout(cls, width)
    if not shares:
        return [cls(*row) for row in values.tolist()]
    array = sip.array(cls, len(values))
    if len(values):
        np.frombuffer(memoryview(array), np.float64).reshape(-1, width)[:] = values
    return array


def _probe_layout(cls: type, width: int) -> bool:
    """Whether a ``sip.array`` of ``cls`` is ``width`` contiguous doubles per element."""
    try:
        probe = sip.array(cls, 1)
        view = np.frombuffer(memoryview(probe), np.float64)
        if view.size != width:
            return False
        view[:] = np.arange(1.0, width + 1.0)
        return np.array_equal(_values(probe[0]), view)
    except (TypeError, ValueError, BufferError):
        return False


def _values(element: QLineF | QPointF) -> list[float]:
    if isinstance(element, QLineF):
        return [element.x1(), element.y1(), element.x2(), element.y2()]
    return [element.x(), element.y()]

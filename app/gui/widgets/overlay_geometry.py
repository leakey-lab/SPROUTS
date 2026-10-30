"""Collision-free placement of the floating canvas overlays: pure geometry, no Qt.

An editor floats up to seven overlays over its canvas: a tool rail (left), a dock (bottom
centre), a zoom cluster (bottom right), a file/status chip (top left), the Enhance popover
(top right), a side panel (right, the GT class panel) and a prompt (top centre, the skeleton
polyline prompt). Each one used to place itself, so they collided at common canvas sizes
(audit UXE-09, UI-15, UI-07). :func:`plan_overlays` places them together from their measured
sizes:

- top row: the popover keeps the top-right corner; the prompt is centred but moves left of an
  open popover; the chip keeps the top-left corner, fitted to the width left beside the
  prompt and popover, or drops below the prompt when that is too narrow; a popover that would
  still touch the prompt or the chip drops below them;
- the rail is vertically centred on the left, below any top item above it;
- bottom row: the dock is centred in the free span between the rail (when the rail reaches
  the bottom row) and the zoom cluster. When it does not fit there, the zoom cluster is lifted
  above the dock; when it still does not fit, the dock turns compact;
- the side panel is centred on the right, below the popover, prompt and chip and above the
  bottom row, and shrinks to fit between them (the class panel scrolls).

:attr:`OverlayPlan.insets` is the canvas area the overlays leave clear, for fit-to-view
(UXE-08). :class:`app.gui.widgets.overlay_layout.OverlayLayout` is the Qt adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

Size = tuple[int, int]
#: ``measure(max_width)`` -> the chip's (width, height) once fitted into ``max_width``
#: (``None``: its natural size). A chip that cannot shrink that far returns a wider size.
ChipMeasure = Callable[[int | None], Size]

ROLES = ("rail", "dock", "zoom_cluster", "top_chip", "popover", "side_panel", "prompt")


@dataclass(frozen=True)
class Rect:
    """Integer rectangle; ``right`` and ``bottom`` are exclusive (x + w, y + h)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def overlaps_x(self, other: Rect) -> bool:
        return self.x < other.right and other.x < self.right

    def overlaps_y(self, other: Rect) -> bool:
        return self.y < other.bottom and other.y < self.bottom

    def intersects(self, other: Rect) -> bool:
        """Same rule as ``QRect.intersects``: empty rectangles never intersect."""
        if self.is_empty() or other.is_empty():
            return False
        return self.overlaps_x(other) and self.overlaps_y(other)

    def grown(self, by: int) -> Rect:
        return Rect(self.x - by, self.y - by, self.w + 2 * by, self.h + 2 * by)


@dataclass(frozen=True)
class Insets:
    """Margins of the canvas area left clear by the overlays."""

    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class LayoutMetrics:
    margin: int = 14  # distance from the host's left, top and right edges
    bottom_margin: int = 18  # distance of the bottom row from the host's bottom edge
    gap: int = 10  # smallest space kept between two overlays
    fit_padding: int = 8  # space kept between a fitted image and an overlay
    default_inset: int = 24  # inset on a side without an overlay


@dataclass(frozen=True)
class OverlaySpec:
    """Measured sizes (width, height) of the visible overlays; ``None`` = absent."""

    host: Size
    rail: Size | None = None
    dock: Size | None = None
    dock_compact_width: int | None = None  # the dock's width in compact mode, if it has one
    zoom: Size | None = None
    chip: ChipMeasure | None = None
    prompt: Size | None = None
    popover: Size | None = None
    side: Size | None = None  # (width, preferred height)


@dataclass(frozen=True)
class OverlayPlan:
    """Where each visible overlay goes (keys from :data:`ROLES`) and how it is sized."""

    rects: dict[str, Rect]
    insets: Insets
    dock_compact: bool = False
    zoom_lifted: bool = False
    #: Width the chip must be fitted into; ``None`` = its natural width.
    chip_width: int | None = None


def plan_overlays(spec: OverlaySpec, metrics: LayoutMetrics | None = None) -> OverlayPlan:
    """Place the overlays described by ``spec`` inside ``spec.host`` without collisions."""
    return _Planner(spec, metrics or LayoutMetrics()).plan()


def _spans_overlap(a_start: int, a_len: int, b_start: int, b_len: int) -> bool:
    return a_start < b_start + b_len and b_start < a_start + a_len


class _Planner:
    """One planning pass; the steps run in dependency order (top row -> rail -> bottom
    row -> side panel), each placing its overlays around the ones already placed."""

    def __init__(self, spec: OverlaySpec, metrics: LayoutMetrics):
        self.spec = spec
        self.m = metrics
        self.width, self.height = spec.host
        self.rects: dict[str, Rect] = {}
        self.dock_compact = False
        self.zoom_lifted = False
        self.chip_width: int | None = None

    def plan(self) -> OverlayPlan:
        self._top_row()
        self._rail()
        self._bottom_row()
        self._side_panel()
        return OverlayPlan(
            rects=dict(self.rects),
            insets=self._insets(),
            dock_compact=self.dock_compact,
            zoom_lifted=self.zoom_lifted,
            chip_width=self.chip_width,
        )

    # ------------------------------------------------------------------ top row
    def _top_row(self) -> None:
        spec, m = self.spec, self.m
        pop_x = None if spec.popover is None else self.width - m.margin - spec.popover[0]
        if spec.prompt is not None:
            width, height = spec.prompt
            x = (self.width - width) // 2
            if pop_x is not None:
                x = min(x, pop_x - m.gap - width)
            self.rects["prompt"] = Rect(max(m.margin, x), m.margin, width, height)
        if spec.chip is not None:
            self._place_chip(pop_x)
        if spec.popover is not None:
            width, height = spec.popover
            popover = Rect(pop_x, m.margin, width, height)
            for role in ("prompt", "top_chip"):  # still in the way: drop below it
                other = self.rects.get(role)
                if other is not None and popover.intersects(other.grown(m.gap - 1)):
                    popover = Rect(pop_x, other.bottom + m.gap, width, height)
            self.rects["popover"] = popover

    def _place_chip(self, pop_x: int | None) -> None:
        m, measure = self.m, self.spec.chip
        prompt = self.rects.get("prompt")
        natural = measure(None)
        rows = [m.margin] if prompt is None else [m.margin, prompt.bottom + m.gap]
        for y in rows:
            limit = self._chip_right_limit(y, natural[1], pop_x, prompt)
            size, fitted_width = self._fit_chip(natural, limit - m.margin)
            if size is not None:
                break
        else:
            # Nowhere wide enough: the last row at the chip's smallest size; the popover
            # then yields to it.
            fitted_width = max(0, limit - m.margin)
            size = measure(fitted_width)
        self.rects["top_chip"] = Rect(m.margin, y, *size)
        self.chip_width = fitted_width

    def _chip_right_limit(self, y: int, height: int, pop_x: int | None, prompt) -> int:
        limit = self.width - self.m.margin
        if prompt is not None and _spans_overlap(y, height, prompt.y, prompt.h):
            limit = min(limit, prompt.x - self.m.gap)
        if pop_x is not None and _spans_overlap(y, height, self.m.margin, self.spec.popover[1]):
            limit = min(limit, pop_x - self.m.gap)
        return limit

    def _fit_chip(self, natural: Size, available: int) -> tuple[Size | None, int | None]:
        if natural[0] <= available:
            return natural, None
        if available <= 0:
            return None, None
        size = self.spec.chip(available)
        return (size, available) if size[0] <= available else (None, None)

    # --------------------------------------------------------------------- rail
    def _rail(self) -> None:
        if self.spec.rail is None:
            return
        m = self.m
        width, height = self.spec.rail
        column = Rect(m.margin, 0, width, self.height)
        top = m.margin
        for role in ("top_chip", "prompt", "popover"):
            other = self.rects.get(role)
            if other is not None and other.overlaps_x(column):
                top = max(top, other.bottom + m.gap)
        self.rects["rail"] = Rect(m.margin, max(top, (self.height - height) // 2), width, height)

    # --------------------------------------------------------------- bottom row
    def _bottom_row(self) -> None:
        spec, m = self.spec, self.m
        zoom = None
        if spec.zoom is not None:
            width, height = spec.zoom
            zoom = Rect(
                self.width - m.margin - width, self.height - m.bottom_margin - height, width, height
            )
        if spec.dock is not None:
            dock = self._place_dock(zoom)
            self.rects["dock"] = dock
            if zoom is not None and self.zoom_lifted:
                zoom = Rect(zoom.x, dock.y - m.gap - zoom.h, zoom.w, zoom.h)
        if zoom is not None:
            self.rects["zoom_cluster"] = zoom

    def _place_dock(self, zoom: Rect | None) -> Rect:
        """Full width beside the zoom cluster, then with the zoom cluster lifted, then the
        same two with the compact dock. Sets ``dock_compact`` / ``zoom_lifted``."""
        spec, m = self.spec, self.m
        width, height = spec.dock
        y = self.height - m.bottom_margin - height
        left = m.margin
        rail = self.rects.get("rail")
        if rail is not None and rail.bottom + m.gap > y:
            left = rail.right + m.gap
        widths = [(width, False)]
        if spec.dock_compact_width is not None and spec.dock_compact_width < width:
            widths.append((spec.dock_compact_width, True))
        for dock_width, compact in widths:
            for lifted in (False, True) if zoom is not None else (False,):
                right = self.width - m.margin if lifted or zoom is None else zoom.x - m.gap
                x = self._centred_in(dock_width, left, right)
                if x is not None:
                    self.dock_compact, self.zoom_lifted = compact, lifted
                    return Rect(x, y, dock_width, height)
        # Too narrow for any variant: the narrowest dock, centred, zoom cluster out of its way.
        dock_width, self.dock_compact = widths[-1]
        self.zoom_lifted = zoom is not None
        return Rect((self.width - dock_width) // 2, y, dock_width, height)

    def _centred_in(self, width: int, left: int, right: int) -> int | None:
        """x of a ``width``-wide box centred on the host but kept inside [left, right)."""
        if right - left < width:
            return None
        return min(max((self.width - width) // 2, left), right - width)

    # --------------------------------------------------------------- side panel
    def _side_panel(self) -> None:
        if self.spec.side is None:
            return
        m = self.m
        width, preferred = self.spec.side
        x = self.width - m.margin - width
        column = Rect(x, 0, width, self.height)
        top, bottom = m.margin, self.height - m.margin
        for role in ("popover", "prompt", "top_chip"):
            other = self.rects.get(role)
            if other is not None and other.overlaps_x(column):
                top = max(top, other.bottom + m.gap)
        for role in ("dock", "zoom_cluster"):
            other = self.rects.get(role)
            if other is not None and other.overlaps_x(column):
                bottom = min(bottom, other.y - m.gap)
        height = max(0, min(preferred, bottom - top))
        y = min(max(top, (self.height - height) // 2), bottom - height)
        self.rects["side_panel"] = Rect(x, y, width, height)

    # ------------------------------------------------------------------- insets
    def _insets(self) -> Insets:
        pad, default = self.m.fit_padding, self.m.default_inset
        rects = self.rects
        left = rects["rail"].right + pad if "rail" in rects else default
        top = rects["top_chip"].bottom + pad if "top_chip" in rects else default
        right = self.width - rects["side_panel"].x + pad if "side_panel" in rects else default
        bottom_row = [rects["dock"]] if "dock" in rects else []
        if "zoom_cluster" in rects and not self.zoom_lifted:
            bottom_row.append(rects["zoom_cluster"])
        bottom = self.height - min(r.y for r in bottom_row) + pad if bottom_row else default
        return Insets(
            max(default, left), max(default, top), max(default, right), max(default, bottom)
        )

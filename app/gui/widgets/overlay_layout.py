"""Qt adapter that lays out an editor's floating overlays together (audit UXE-09).

:class:`OverlayLayout` measures the overlays registered on a host (the canvas), asks the
pure :func:`~app.gui.widgets.overlay_geometry.plan_overlays` where each one goes, and applies
the plan: positions, the dock's compact mode, the chip's fitted width and the side panel's
height. It re-runs by itself when the host is resized or a registered overlay is shown,
hidden or changes size, and a managed panel's own ``reposition()`` delegates to it, so the
editors' existing calls stay correct. :meth:`OverlayLayout.insets` is the canvas area the
overlays leave clear, for fit-to-view (UXE-08).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QMargins, QObject, QTimer
from PyQt6.QtWidgets import QWidget

from .overlay_geometry import (
    ROLES,
    ChipMeasure,
    LayoutMetrics,
    OverlayPlan,
    OverlaySpec,
    plan_overlays,
)

# Roles whose content can change size while shown (dock sliders, popover content,
# prompt buttons, rail tools): their LayoutRequest events re-run the layout. The chip
# and the side panel re-run it explicitly (their setters call reposition()).
_WATCH_CONTENT = ("rail", "dock", "popover", "prompt")
# Bottom-to-top stacking order of the overlays above the canvas.
_Z_ORDER = ("side_panel", "rail", "dock", "zoom_cluster", "top_chip", "prompt", "popover")


class OverlayLayout(QObject):
    """Collision-free placement of the floating overlays over ``host``.

    ``register(rail=..., dock=..., zoom_cluster=..., top_chip=..., popover=...,
    side_panel=..., prompt=...)`` hands overlays to the layout; call it again to add or
    replace roles (``None`` releases one). Only overlays visible on the host take part.
    Optional duck-typed hooks: ``dock.natural_width(compact)`` / ``dock.set_compact(bool)``,
    ``top_chip.fit_to_width(px | None)``, ``side_panel.preferred_height(host_h)`` /
    ``side_panel.fit_height(px)``, ``overlay.set_overlay_layout(layout)``.
    """

    def __init__(self, host: QWidget, *, metrics: LayoutMetrics | None = None):
        super().__init__(host)
        self._host = host
        self._metrics = metrics or LayoutMetrics()
        self._widgets: dict[str, QWidget] = {}
        self._busy = False
        self._pending = False
        self._last_plan: OverlayPlan | None = None
        host.installEventFilter(self)

    # ------------------------------------------------------------------ API
    def register(self, **widgets: QWidget | None) -> None:
        """Manage ``widgets`` by role (see :data:`ROLES`); ``None`` releases a role."""
        unknown = sorted(set(widgets) - set(ROLES))
        if unknown:
            raise ValueError(f"unknown overlay role(s) {unknown}; expected one of {ROLES}")
        for role, widget in widgets.items():
            previous = self._widgets.pop(role, None)
            if previous is not None:
                previous.removeEventFilter(self)
                self._attach(previous, None)
            if widget is not None:
                self._widgets[role] = widget
                widget.installEventFilter(self)
                self._attach(widget, self)
        self._last_plan = None
        self.relayout()

    def relayout(self) -> None:
        """Measure the visible overlays, plan and apply their placement."""
        if self._busy:
            return
        self._busy = True
        self._pending = False
        try:
            plan = plan_overlays(self._spec(), self._metrics)
            if plan != self._last_plan or not self._matches(plan):
                self._apply(plan)
                self._last_plan = plan
        finally:
            self._busy = False

    def insets(self) -> QMargins:
        """Margins of the canvas area the overlays leave clear, for the host's current size.

        Planned afresh from the overlays' sizes, so it is right even while the host is being
        resized (a view asks for it from its own resize event, before the relayout).
        """
        if self._busy:
            plan = self._last_plan or plan_overlays(
                OverlaySpec(host=self._host_size()), self._metrics
            )
        else:
            self._busy = True
            try:
                plan = plan_overlays(self._spec(), self._metrics)
            finally:
                self._busy = False
        i = plan.insets
        return QMargins(i.left, i.top, i.right, i.bottom)

    # ------------------------------------------------------------ Qt events
    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        kind = event.type()
        if obj is self._host:
            if kind == QEvent.Type.Resize:
                self.relayout()
        elif kind in (QEvent.Type.Show, QEvent.Type.Hide):
            self._schedule()
        elif kind == QEvent.Type.LayoutRequest and self._role_of(obj) in _WATCH_CONTENT:
            self._schedule()
        return False

    def _schedule(self) -> None:
        """Coalesce show/hide/content changes into one relayout on the next loop pass."""
        if self._pending or self._busy:
            return
        self._pending = True
        QTimer.singleShot(0, self._run_pending)

    def _run_pending(self) -> None:
        if self._pending:
            self.relayout()

    # ------------------------------------------------------------ measuring
    def _host_size(self) -> tuple[int, int]:
        return self._host.width(), self._host.height()

    def _visible(self, role: str) -> QWidget | None:
        widget = self._widgets.get(role)
        return widget if widget is not None and widget.isVisibleTo(self._host) else None

    def _role_of(self, widget) -> str | None:
        return next((role for role, w in self._widgets.items() if w is widget), None)

    @staticmethod
    def _natural_size(widget: QWidget) -> tuple[int, int]:
        _adjust_size(widget)
        return widget.width(), widget.height()

    def _spec(self) -> OverlaySpec:
        host_w, host_h = self._host_size()
        sizes: dict[str, tuple[int, int] | None] = {}
        for role in ("rail", "zoom_cluster", "prompt", "popover"):
            widget = self._visible(role)
            sizes[role] = None if widget is None else self._natural_size(widget)

        dock, dock_size, compact_width = self._visible("dock"), None, None
        if dock is not None:
            dock_size = self._natural_size(dock)
            if hasattr(dock, "natural_width"):
                full = dock.natural_width(compact=False)
                compact = dock.natural_width(compact=True)
                dock_size = (full, dock_size[1])
                compact_width = compact if compact < full else None

        panel, side = self._visible("side_panel"), None
        if panel is not None:
            width, height = self._natural_size(panel)
            if hasattr(panel, "preferred_height"):
                height = panel.preferred_height(host_h)
            side = (width, height)

        chip = self._visible("top_chip")
        return OverlaySpec(
            host=(host_w, host_h),
            rail=sizes["rail"],
            dock=dock_size,
            dock_compact_width=compact_width,
            zoom=sizes["zoom_cluster"],
            chip=None if chip is None else self._chip_measure(chip),
            prompt=sizes["prompt"],
            popover=sizes["popover"],
            side=side,
        )

    @staticmethod
    def _chip_measure(chip: QWidget) -> ChipMeasure:
        fit = getattr(chip, "fit_to_width", None)

        def measure(max_width: int | None) -> tuple[int, int]:
            if fit is not None:
                fit(max_width)
            _adjust_size(chip)
            return chip.width(), chip.height()

        return measure

    # -------------------------------------------------------------- applying
    def _matches(self, plan: OverlayPlan) -> bool:
        """True when every overlay already sits where ``plan`` puts it."""
        for role, rect in plan.rects.items():
            widget = self._widgets.get(role)
            geo = widget.geometry() if widget is not None else None
            if geo is None or (geo.x(), geo.y(), geo.width(), geo.height()) != (
                rect.x,
                rect.y,
                rect.w,
                rect.h,
            ):
                return False
        return True

    def _apply(self, plan: OverlayPlan) -> None:
        dock = self._visible("dock")
        if dock is not None and hasattr(dock, "set_compact"):
            dock.set_compact(plan.dock_compact)
        chip = self._visible("top_chip")
        if chip is not None and hasattr(chip, "fit_to_width"):
            chip.fit_to_width(plan.chip_width)
        for role in _Z_ORDER:
            widget, rect = self._visible(role), plan.rects.get(role)
            if widget is None or rect is None:
                continue
            if role == "side_panel" and hasattr(widget, "fit_height"):
                widget.fit_height(rect.h)
            else:
                _adjust_size(widget)
            widget.move(rect.x, rect.y)
            widget.raise_()

    @staticmethod
    def _attach(widget: QWidget, layout: OverlayLayout | None) -> None:
        attach = getattr(widget, "set_overlay_layout", None)
        if attach is not None:
            attach(layout)


def _adjust_size(widget: QWidget) -> None:
    """``adjustSize()`` from fresh size hints.

    ``QWidget.adjustSize`` computes the size before it activates the layout, and nested
    layouts keep cached hints until then, so a label that just changed would be measured
    at its old size. Activating first refreshes every nested hint.
    """
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    widget.adjustSize()

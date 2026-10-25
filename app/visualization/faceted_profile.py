"""
Depth Profile Grid (faceted depth profile): pure data preparation + one-pass figure assembly.

``build_faceted_profile`` turns DataCache interval tables into plain data (no
Plotly): one :class:`FacetCell` per (date row, tube column), each holding the
tube's per-interval means next to the field average (all tubes) of the same
intervals. ``faceted_profile_figure`` renders that grid.

The figure is assembled as plain dicts on top of a ``make_subplots`` skeleton
and validated ONCE by ``go.Figure`` (PV-08). Adding one shape per subplot with
``fig.add_shape`` re-validates the whole shapes tuple on every call, which made
the build O(subplots²): 3 s for 6 tubes x 24 dates. The skeleton only depends on
the grid's shape, so it is built once per shape (``make_subplots`` validates a
whole figure: 80 ms of a 140 ms build for 11 dates x 6 tubes, R-VIZ X4).
"""

from __future__ import annotations

import copy
import functools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import theme

#: Depth positions per bar (L1-L10, L11-L20, ...).
INTERVAL_SIZE = 10
#: Most tube columns the grid is designed for.
MAX_TUBES = 6
TITLE = "Depth Profile Grid - Field Average (Left) vs Tube (Right)"
DEPTH_AXIS_TITLE = "Vertical Depth (cm)"


@dataclass(frozen=True)
class FacetCell:
    """One tube on one date: the measured depth intervals, ready to draw.

    All tuples run in parallel over the intervals that have tube rows, shallow
    to deep. ``field_means``/``field_stds`` pool every tube of the date (not only
    the selected ones); ``depth_fractions`` place each interval between the
    tube's first (0.0) and last (1.0) interval of that date.
    """

    tube: Any
    date: pd.Timestamp
    interval_ends: tuple[int, ...] = ()
    interval_starts: tuple[int, ...] = ()
    tube_means: tuple[float, ...] = ()
    tube_counts: tuple[int, ...] = ()
    field_means: tuple[float, ...] = ()
    field_stds: tuple[float, ...] = ()
    depth_fractions: tuple[float, ...] = ()

    @property
    def has_data(self) -> bool:
        """True when the tube has at least one measured interval on this date."""
        return bool(self.interval_ends)


@dataclass(frozen=True)
class FacetedProfile:
    """The whole grid: ``cells[row][col]`` for ``dates[row]`` x ``tubes[col]``."""

    tubes: tuple[Any, ...]
    dates: tuple[pd.Timestamp, ...]
    cells: tuple[tuple[FacetCell, ...], ...]
    #: largest bar (tube or field mean) over every cell; 0 when nothing is drawn
    max_value: float
    #: deepest interval end drawn; 0 when nothing is drawn
    max_depth: int


def _is_measured(stats: dict) -> bool:
    return stats["count"] > 0 and not math.isnan(stats["avg"])


def _build_cell(cache, tube, date, field_table: dict, interval_size: int) -> FacetCell:
    """Pair a tube's measured intervals with the field statistics of the same intervals."""
    table = cache.get_interval_data(tube, date, interval_size)
    measured = [end for end in sorted(table) if _is_measured(table[end])]
    if not measured:
        return FacetCell(tube=tube, date=date)

    first, last = min(table), max(table)
    field_means, field_stds = [], []
    for end in measured:
        field = field_table.get(end)
        if field is not None and field["count"] > 0:
            field_means.append(field["avg"])
            field_stds.append(0 if math.isnan(field["std"]) else field["std"])
        else:  # cannot happen for consistent data (tube rows are field rows)
            field_means.append(0)
            field_stds.append(0)
    return FacetCell(
        tube=tube,
        date=date,
        interval_ends=tuple(measured),
        interval_starts=tuple(table[end]["interval_start"] for end in measured),
        tube_means=tuple(table[end]["avg"] for end in measured),
        tube_counts=tuple(table[end]["count"] for end in measured),
        field_means=tuple(field_means),
        field_stds=tuple(field_stds),
        depth_fractions=tuple(
            (end - first) / (last - first) if last > first else 0.5 for end in measured
        ),
    )


def build_faceted_profile(
    cache,
    tubes: Sequence,
    dates: Iterable[pd.Timestamp] | None = None,
    interval_size: int = INTERVAL_SIZE,
) -> FacetedProfile:
    """Prepare the faceted grid from a :class:`~.dash_data_cache.DataCache` (no Plotly).

    Args:
        cache: DataCache over the metric frame.
        tubes: tube columns, in display order.
        dates: date rows to consider (default: every date); they are sorted and
            only dates where at least one of ``tubes`` has data are kept.
        interval_size: depth positions per interval.
    """
    candidates = sorted(cache.dates if dates is None else dates)
    availability = cache.get_tube_date_availability()
    kept = [d for d in candidates if any(availability.get((t, d), False) for t in tubes)]

    rows = []
    max_value, max_depth = 0, 0
    for date in kept:
        field_table = cache.get_field_interval_data(date, interval_size)
        row = tuple(_build_cell(cache, tube, date, field_table, interval_size) for tube in tubes)
        for cell in row:
            if cell.has_data:
                max_value = max(max_value, max(cell.field_means + cell.tube_means))
                max_depth = max(max_depth, max(cell.interval_ends))
        rows.append(row)
    return FacetedProfile(
        tubes=tuple(tubes),
        dates=tuple(kept),
        cells=tuple(rows),
        max_value=max_value,
        max_depth=max_depth,
    )


def _merge(target: dict, updates: dict) -> None:
    """Recursive dict update with Plotly ``update`` semantics (nested dicts merge)."""
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _axis_suffix(subplot: int) -> str:
    """Plotly axis-id suffix of the n-th subplot (row-major): '' for 1, else 'n'."""
    return "" if subplot == 1 else str(subplot)


def _column_placeholder(col: int) -> str:
    return f"\x00column {col}\x00"


def _row_placeholder(row: int) -> str:
    return f"\x00row {row}\x00"


@functools.lru_cache(maxsize=16)
def _grid_skeleton(n_rows: int, n_cols: int) -> dict:
    """``make_subplots``' layout of an ``n_rows`` x ``n_cols`` grid, titles as placeholders.

    Read-only: :func:`grid_layout` copies it. The template is left out (the figure
    sets its own).
    """
    skeleton = make_subplots(
        rows=n_rows,
        cols=n_cols,
        shared_xaxes=False,
        shared_yaxes=True,
        vertical_spacing=0.005,
        horizontal_spacing=0.008,
        subplot_titles=[
            _column_placeholder(col) if row == 0 else ""
            for row in range(n_rows)
            for col in range(n_cols)
        ],
        row_titles=[_row_placeholder(row) for row in range(n_rows)],
    )
    layout = skeleton.layout.to_plotly_json()
    layout.pop("template", None)
    return layout


def grid_layout(column_titles: Sequence[str], row_titles: Sequence[str]) -> dict:
    """The layout ``make_subplots`` gives a grid titled ``column_titles`` (on top) and
    ``row_titles`` (right), from the cached skeleton of its shape: axes, domains and
    title annotations, template excluded. A fresh dict, free to change."""
    layout = copy.deepcopy(_grid_skeleton(len(row_titles), len(column_titles)))
    titles = {_column_placeholder(col): text for col, text in enumerate(column_titles)}
    titles.update({_row_placeholder(row): text for row, text in enumerate(row_titles)})
    for annotation in layout.get("annotations", []):
        annotation["text"] = titles[annotation["text"]]
    return layout


def _x_range(max_value: float) -> int:
    """Symmetric x half-range: 10% headroom, rounded up to a multiple of 10 (min 10)."""
    if max_value <= 0:
        return 10
    return int(math.ceil(max_value * 1.1 / 10.0) * 10)


def _x_tick_step(half_range: int) -> int:
    """Tick spacing that keeps 2-6 ticks per side of the zero line.

    The outermost ticks are unlabelled (they would collide with the next
    panel's), so a +-10 range needs a 5-step to show any scale at all; large
    (e.g. area) ranges step 1-2-5 x 10^k instead of crowding the panel. Ranges
    of 20-60 keep the original 10-step.
    """
    if half_range <= 10:
        return 5
    magnitude = 10
    while True:
        for multiple in (1, 2, 5):
            step = multiple * magnitude
            if half_range / step <= 6:
                return step
        magnitude *= 10


class _FacetedFigureAssembler:
    """Collects the traces, panels and axis settings of a profile as plain dicts."""

    def __init__(self, profile: FacetedProfile, label: str, unit: str):
        self.profile = profile
        self.label = label
        self.unit = unit
        self.n_rows = len(profile.dates)
        self.n_cols = len(profile.tubes)

    def figure(self) -> go.Figure:
        layout = grid_layout(
            [f"Tube {int(tube)}" for tube in self.profile.tubes],
            [date.strftime("%Y-%m-%d") for date in self.profile.dates],
        )
        _merge(layout, self._layout_updates())
        for annotation in layout.get("annotations", []):
            _merge(annotation, {"font": {"size": 12}})
        return go.Figure(data=self._traces(), layout=layout)

    # -- traces -----------------------------------------------------------
    def _traces(self) -> list[dict]:
        traces: list[dict] = []
        legend_shown = False
        for row, cells in enumerate(self.profile.cells):
            for col, cell in enumerate(cells):
                suffix = _axis_suffix(row * self.n_cols + col + 1)
                refs = {"xaxis": f"x{suffix}", "yaxis": f"y{suffix}"}
                if not cell.has_data:
                    traces.append({**self._no_data_trace(), **refs})
                    continue
                show_legend = not legend_shown
                legend_shown = True
                traces.append({**self._field_bars(cell, show_legend), **refs})
                traces.append({**self._tube_bars(cell, show_legend), **refs})
        return traces

    @staticmethod
    def _no_data_trace() -> dict:
        return {
            "type": "scatter",
            "x": [0],
            "y": [50],  # middle of the typical depth range
            "mode": "text",
            "text": ["No Data"],
            "textfont": {"size": 40, "color": theme.TEXT_MUTED},
            "showlegend": False,
            "hoverinfo": "skip",
        }

    def _field_bars(self, cell: FacetCell, show_legend: bool) -> dict:
        """Field-average bars, drawn leftwards from the zero line."""
        return {
            "type": "bar",
            "x": [-value for value in cell.field_means],
            "y": list(cell.interval_ends),
            "orientation": "h",
            "width": 8,
            "base": 0,
            "marker": {"color": theme.TEXT_MUTED, "line": {"color": theme.BORDER, "width": 1}},
            "name": "Field Avg",
            "showlegend": show_legend,
            "legendgroup": "field_avg",
            "hovertemplate": (
                f"<b>Field Average - {cell.date.strftime('%Y-%m-%d')}</b><br>"
                "Interval: L%{customdata[0]}-%{customdata[1]}<br>"
                f"Avg {self.label}: %{{customdata[2]:.2f}} {self.unit}<br>"
                "<extra></extra>"
            ),
            "customdata": [
                [start, end, mean, std]
                for start, end, mean, std in zip(
                    cell.interval_starts,
                    cell.interval_ends,
                    cell.field_means,
                    cell.field_stds,
                    strict=True,
                )
            ],
        }

    def _tube_bars(self, cell: FacetCell, show_legend: bool) -> dict:
        """The tube's own bars, drawn rightwards and coloured by relative depth."""
        return {
            "type": "bar",
            "x": list(cell.tube_means),
            "y": list(cell.interval_ends),
            "orientation": "h",
            "width": 8,
            "base": 0,
            "marker": {
                "color": [theme.sequential_color(f) for f in cell.depth_fractions],
                "line": {"color": theme.BORDER, "width": 1},
            },
            "name": "Tube",
            "showlegend": show_legend,
            "legendgroup": "tube",
            "hovertemplate": (
                f"<b>Tube {int(cell.tube)} - {cell.date.strftime('%Y-%m-%d')}</b><br>"
                "Interval: L%{customdata[0]}-%{customdata[1]}<br>"
                f"Avg {self.label}: %{{x:.2f}} {self.unit}<br>"
                "Count: %{customdata[2]}<br>"
                "<extra></extra>"
            ),
            "customdata": [
                [start, end, count]
                for start, end, count in zip(
                    cell.interval_starts, cell.interval_ends, cell.tube_counts, strict=True
                )
            ],
        }

    # -- layout -----------------------------------------------------------
    def _layout_updates(self) -> dict:
        x_half = _x_range(self.profile.max_value)
        x_step = _x_tick_step(x_half)
        x_max_tick = (x_half // x_step) * x_step
        x_tickvals = list(range(-x_max_tick, x_max_tick + x_step, x_step))
        # absolute values either side of zero; the outermost labels are hidden
        x_ticktext = [
            "" if i in (0, len(x_tickvals) - 1) else f"{abs(v):.0f}"
            for i, v in enumerate(x_tickvals)
        ]
        y_max = self.profile.max_depth + 10 if self.profile.max_depth > 0 else 120
        y_tickvals = list(range(0, y_max + 1, 20))
        if y_tickvals[-1] != y_max:
            y_tickvals.append(y_max)
        # hide the 0 and max depth labels to reduce clutter
        y_ticktext = ["" if v in (0, y_max) else str(v) for v in y_tickvals]

        updates: dict[str, Any] = {
            "template": "sprouts",
            "title": {"text": TITLE, "y": 0.985, "yanchor": "top", "font": {"size": 16}},
            "showlegend": True,
            "autosize": True,
            # Smaller per-row height so few-row selections roughly fill the
            # viewport instead of a giant internal scroll; many rows still
            # scroll but with readable, non-overlapping text.
            "height": max(560, self.n_rows * 300),
            # Wider top margin separates the main title from the "Tube N"
            # column titles; trim the oversized right margin.
            "margin": {"l": 64, "r": 60, "t": 118, "b": 52},
            "hovermode": "closest",
            "barmode": "overlay",  # field and tube bars share each depth row
            "shapes": [],
        }
        for row in range(1, self.n_rows + 1):
            for col in range(1, self.n_cols + 1):
                suffix = _axis_suffix((row - 1) * self.n_cols + col)
                bottom = row == self.n_rows
                updates["shapes"].append(self._panel(suffix))
                updates[f"xaxis{suffix}"] = {
                    "tickmode": "array",
                    "tickvals": x_tickvals,
                    "ticktext": x_ticktext,
                    "range": [-x_half, x_half],
                    "showticklabels": bottom,
                    "showgrid": True,
                    "zeroline": True,
                    "zerolinecolor": theme.BORDER_STRONG,
                    "zerolinewidth": 2,
                    "showline": bottom,
                    "linecolor": theme.BORDER_STRONG,
                    "linewidth": 2,
                    "matches": "x",
                    "side": "bottom",
                    "ticks": "outside" if bottom else "",
                    "ticklen": 6 if bottom else 0,
                    "tickfont": {"size": 11},
                }
                depth_title = col == 1 and row == (self.n_rows + 1) // 2
                updates[f"yaxis{suffix}"] = {
                    "range": [y_max, 0],  # depth grows downwards
                    "title": {
                        "text": DEPTH_AXIS_TITLE if depth_title else "",
                        "font": {"size": 13},
                    },
                    "showgrid": True,
                    "tickfont": {"size": 11},
                    "showline": True,
                    "linecolor": theme.BORDER_STRONG,
                    "linewidth": 2,
                    "matches": "y",
                    "tickmode": "array",
                    "tickvals": y_tickvals,
                    "ticktext": y_ticktext,
                }
        return updates

    @staticmethod
    def _panel(suffix: str) -> dict:
        """Background rectangle behind one subplot, in that subplot's domain."""
        return {
            "type": "rect",
            "xref": f"x{suffix} domain",
            "yref": f"y{suffix} domain",
            "x0": 0,
            "y0": 0,
            "x1": 1,
            "y1": 1,
            "fillcolor": theme.rgba(theme.BG_3, 0.55),
            "line": {"color": theme.BORDER, "width": 1},
            "layer": "below",
        }


def faceted_profile_figure(
    profile: FacetedProfile, label: str = "Length", unit: str = "mm"
) -> go.Figure:
    """Render a non-empty :class:`FacetedProfile` (rows = dates, columns = tubes).

    Args:
        profile: output of :func:`build_faceted_profile` with at least one date.
        label: metric name for hover text (e.g. "Length", "Area").
        unit: metric unit for hover text (e.g. "mm", "mm²").
    """
    return _FacetedFigureAssembler(profile, label, unit).figure()

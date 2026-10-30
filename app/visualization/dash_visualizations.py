"""
Figure builders for the Dash root-metric dashboards.

``DashVisualizations`` holds every chart of the length dashboard. It is
metric-agnostic (AR-15): the metric label and unit come from the processor, and
every aggregate comes from the DataCache, so the same builders can chart root
area. Figures are memoized per instance because the data is immutable while a
dashboard is open (see DataCache); ``data_cache.refresh()`` invalidates them.
"""

import logging
import math
from collections.abc import Callable, Collection, Hashable

import pandas as pd
import plotly.graph_objects as go

from . import theme
from .faceted_profile import MAX_TUBES, build_faceted_profile, faceted_profile_figure
from .figure_memo import FigureMemo

logger = logging.getLogger(__name__)

#: Depth positions per point in the Depth Profile view (L1-L10, L11-L20, ...).
GROWTH_LINES_INTERVAL = 10
#: Figures kept per dashboard instance.
FIGURE_MEMO_SIZE = 16
#: The Growth Over Time legend is shown by default up to this many tubes. A 160-entry
#: legend was ~200 ms of a ~670 ms draw (QtWebEngine, 46,970-row trial, median of 8)
#: and ten colours cannot tell that many tubes apart; the hover names each one.
#: "Toggle Legend" still shows it.
AUTO_LEGEND_MAX_TUBES = 30


def auto_legend(tubes) -> bool:
    """Whether the Growth Over Time legend is shown for ``tubes`` when nobody chose."""
    return len(tubes or ()) <= AUTO_LEGEND_MAX_TUBES


def _scatter(x, y, **properties) -> dict:
    """A scatter trace as a plain dict (its figure is built without validation)."""
    return {"type": "scatter", "x": x, "y": y, **properties}


def parse_tube_selection(text: str, within: Collection[int] | None = None) -> list[int]:
    """
    Parse tube selection string like '1,3,5,10-15' into list [1,3,5,10,11,12,13,14,15].

    Malformed parts are skipped individually rather than discarding the whole
    selection, so e.g. "1,foo,3" yields [1, 3] (F-037).

    Args:
        text: Comma-separated string containing tube numbers and ranges
        within: When given, only these tubes are kept, and a range is matched
            against them instead of expanded (a typed "1-100000000" listed every number)

    Returns:
        Sorted list of unique tube numbers
    """
    if not text or not text.strip():
        return []

    tubes = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                # Handle range like "10-15"
                range_parts = part.split("-")
                if len(range_parts) == 2:
                    start, end = int(range_parts[0].strip()), int(range_parts[1].strip())
                    if start <= end:
                        if within is None:
                            tubes.extend(range(start, end + 1))
                        else:
                            tubes.extend(tube for tube in within if start <= tube <= end)
            else:
                # Handle single number
                number = int(part)
                if within is None or number in within:
                    tubes.append(number)
        except ValueError, AttributeError:
            # Skip this malformed part but keep any valid ones already parsed.
            logger.debug("Skipping unparseable tube-selection part: %r", part)
            continue

    return sorted(set(tubes))


def stacked_bar_figure(cache, label: str, unit: str) -> go.Figure:
    """Totals by Tube of either dashboard: each tube's total per date, stacked by date.

    Args:
        cache: the dashboard's DataCache.
        label: the metric's name, e.g. "Length" or "Area".
        unit: its unit, e.g. "mm" or "mm²".
    """
    tubes, dates = list(cache.tubes), list(cache.dates)
    x = [f"Tube {int(tube)}" for tube in tubes]
    traces = []
    if tubes and dates:
        # tube x date totals in one pivot. NaN (no bar, left out of the hover)
        # where a tube was not measured: a 0.00 read as "no roots that day".
        grid = cache.tube_date_groups.unstack("Date").reindex(index=tubes, columns=dates)
        for date, colour in zip(dates, theme.date_colors(len(dates)), strict=True):
            # A list, so the browser gets null where a tube was not measured: plotly.js
            # leaves null out of the stack, while a NaN in a typed array (a numpy y) is
            # stacked and restarts the tube's later dates at 0.
            values = grid[date].tolist()
            traces.append(
                go.Bar(
                    name=date.strftime("%Y-%m-%d"),
                    marker_color=colour,
                    x=x,
                    y=values,
                    **theme.bar_labels(theme.value_labels(values), len(tubes), colour),
                    hovertemplate=theme.hover(
                        "%{x}",
                        [
                            ("Date", date.strftime("%Y-%m-%d")),
                            (label, f"%{{y:.2f}} {unit}"),
                        ],
                    ),
                )
            )

    fig = go.Figure(data=traces)
    fig.update_layout(
        barmode="stack",
        title_text=f"Root {label} by Tube and Date",
        xaxis_title_text="Tube",
        yaxis_title_text=f"Total {label} ({unit})",
        legend_title_text="Measurement date",
        hovermode="x unified",
    )
    return fig


class DashVisualizations:
    """
    Mixin class providing visualization methods for DashApp.
    Should be used as a mixin with a class that has:
    - self.data_cache: DataCache instance
    - self.data_processor: MetricDataProcessor instance (its ``metric_label`` and
      ``unit`` label the charts; "Length"/"mm" when absent)
    - self.available_images: dict of available images

    Returned figures may be shared memo entries: treat them as read-only.
    """

    # -- plumbing ---------------------------------------------------------
    def _memoized(self, key: tuple, build: Callable[[], go.Figure]) -> go.Figure:
        """Serve ``build()`` from the per-instance memo (keyed with the data version)."""
        memo = getattr(self, "_figure_memo", None)
        if memo is None:
            memo = self._figure_memo = FigureMemo(FIGURE_MEMO_SIZE)
        full_key: Hashable = (self.data_cache.generation, *key)
        return memo.get_or_build(full_key, build)

    def _metric_text(self) -> tuple[str, str]:
        """(label, unit) of the charted metric, e.g. ("Length", "mm") or ("Area", "mm²")."""
        processor = self.data_processor
        return getattr(processor, "metric_label", "Length"), getattr(processor, "unit", "mm")

    def get_tube_date_availability(self) -> dict:
        """
        Get information about which tubes have data for which dates.

        Returns:
            dict: {(tube, date): has_data_bool} (shared; treat as read-only)
        """
        return self.data_cache.get_tube_date_availability()

    # -- Depth Profile ----------------------------------------------------
    def show_growth_lines(self, selected_tube: int) -> go.Figure:
        """Generate growth lines figure for selected tube."""
        try:
            return self._memoized(
                ("lines", selected_tube), lambda: self._growth_lines_figure(selected_tube)
            )
        except Exception:
            logger.exception("Error generating growth lines")
            return go.Figure()

    def _growth_lines_figure(self, tube: int) -> go.Figure:
        label, unit = self._metric_text()
        size = GROWTH_LINES_INTERVAL
        traces = []
        all_positions: set[int] = set()
        max_length = 0

        dates = self.data_cache.dates
        ramp = theme.date_colors(len(dates))  # the date's place among ALL dates of the trial
        for date, colour in zip(dates, ramp, strict=True):
            table = self.data_cache.get_interval_data(tube, date, size)
            if not table:
                continue

            interval_ends = sorted(table)
            all_positions.update(interval_ends)
            date_str = date.strftime("%Y.%m.%d")
            averages, hover_info = [], []
            for pos in interval_ends:
                stats = table[pos]
                averages.append(stats["avg"])
                if stats["count"] > 0:
                    max_length = max(max_length, stats["max"])
                    has_image = (tube, pos, date_str) in self.available_images
                    hover_info.append(self._interval_hover(pos, date_str, stats, unit, has_image))
                else:
                    hover_info.append(f"No Data  Interval L{pos - size + 1}-L{pos}")

            traces.append(
                go.Scatter(
                    x=averages,
                    y=interval_ends,
                    mode="lines+markers",
                    name=date.strftime("%Y-%m-%d"),
                    line={
                        "color": colour,
                        "width": 2,
                        "shape": "spline",
                        "smoothing": 0.3,
                    },
                    marker={"size": 8, "opacity": 0.8},
                    hovertemplate="%{text}<extra></extra>",
                    text=hover_info,
                    customdata=[date_str] * len(interval_ends),  # date for the hover callback
                )
            )

        fig = go.Figure(data=traces)
        fig.update_layout(
            title_text=f"Root {label} Depth Profile - Tube {int(tube)}",
            xaxis_title_text=f"Root {label} ({unit})",
            yaxis_title_text="Position (L)",
            legend_title_text="Measurement date",
            hovermode="closest",
        )
        fig.update_xaxes(
            range=[-1, max_length * 1.1] if max_length > 0 else [-1, 10],
            tickformat=".1f",
        )
        # Tick every interval drawn on ANY date (not just the last date's).
        positions = sorted(all_positions)
        fig.update_yaxes(
            autorange="reversed",
            tickmode="array",
            ticktext=[f"L{pos}" for pos in positions],
            tickvals=positions,
            dtick=10,
        )
        if positions:  # zero reference line over the drawn depth range
            fig.add_shape(
                type="line",
                x0=0,
                x1=0,
                y0=positions[0],
                y1=positions[-1],
                line={"color": theme.BORDER_STRONG, "width": 2, "dash": "dot"},
            )
        return fig

    @staticmethod
    def _interval_hover(pos: int, date_str: str, stats: dict, unit: str, has_image: bool) -> str:
        """Hover text of one Depth Profile point (a measured depth interval)."""
        std = stats["std"]
        std_text = "n/a (one image)" if math.isnan(std) else f"+/-{std:.2f}"
        start = pos - GROWTH_LINES_INTERVAL + 1
        return (
            f"Interval L{start}-L{pos}  <b>{date_str}</b><br>"
            f"Average  <b>{stats['avg']:.2f} {unit}</b><br>"
            f"Range  <b>{stats['min']:.2f} - {stats['max']:.2f} {unit}</b><br>"
            f"Std Dev  <b>{std_text}</b><br>"
            f"Measurements  <b>{stats['count']}</b><br>"
            f"Image  <b>{'Available' if has_image else 'Not Available'}</b>"
        )

    # -- Growth Over Time -------------------------------------------------
    def show_growth_over_time(
        self,
        selected_tubes: list[int] | None = None,
        show_field_average: bool = True,
        show_field_variance: bool = True,
        show_legend: bool | None = True,
    ) -> go.Figure:
        """
        Generate growth over time figure with flexible tube selection and variance options.

        Args:
            selected_tubes: List of tube numbers to display. If None or empty, no individual tubes shown.
            show_field_average: Whether to show the field average line
            show_field_variance: Whether to show shaded variance for field average
            show_legend: Whether to display the legend; None: only up to
                :data:`AUTO_LEGEND_MAX_TUBES` tubes (see :func:`auto_legend`)

        Returns:
            Plotly figure object
        """
        tubes = tuple(selected_tubes or ())
        legend = auto_legend(tubes) if show_legend is None else bool(show_legend)
        flags = (bool(show_field_average), bool(show_field_variance), legend)
        try:
            return self._memoized(
                ("time", tubes, *flags), lambda: self._growth_over_time_figure(tubes, *flags)
            )
        except Exception:
            logger.exception("Error generating growth over time")
            return go.Figure()

    def _growth_over_time_figure(
        self, tubes: tuple, show_field_average: bool, show_field_variance: bool, show_legend: bool
    ) -> go.Figure:
        # Plain dicts, not validated (_validate=False): one go.Scatter per tube, validated
        # again by go.Figure(data=...), took 80 ms for 160 tubes, validating the dicts once
        # 29 ms, now 8 ms, on the server thread and holding the GIL. The browser gets the
        # same JSON (tests/test_viz_data_time_view.py compares it).
        label, unit = self._metric_text()
        traces = []
        if show_field_average:
            traces += self._field_average_traces(show_field_variance, label, unit)

        # Individual tubes: totals per measurement date, NO date merging.
        for idx, tube in enumerate(tubes):
            totals = self.data_cache.get_tube_totals(tube)
            if totals.empty:
                continue
            traces.append(
                _scatter(
                    totals.index.to_numpy(),
                    totals.to_numpy(),
                    mode="lines+markers",
                    name=f"Tube {int(tube)}",
                    line={"color": theme.color(idx), "width": 2},
                    marker={"size": 6},
                    hovertemplate=theme.hover(
                        f"Tube {int(tube)}",
                        [
                            ("Date", "%{x|%Y-%m-%d}"),
                            (f"Total {label}", f"%{{y:.2f}} {unit}"),
                        ],
                    ),
                )
            )

        layout = {
            "title": {"text": f"Root {label} Growth Over Time"},
            "xaxis": {"title": {"text": "Date"}},
            "yaxis": {"title": {"text": f"Total {label} ({unit})"}},
            "showlegend": show_legend,
            # a hidden legend leaves no empty band beside the plot
            "margin": {"r": theme.margin_right(show_legend)},
            "hovermode": "closest",
        }
        return go.Figure(data=traces, layout=layout, _validate=False)

    def _field_average_traces(self, show_variance: bool, label: str, unit: str) -> list[dict]:
        """Field-average line (+ optional +-1 SD band) across tubes, dates merged (F-034)."""
        field = self.data_cache.get_field_growth()
        dates = field["Date"].to_numpy()
        mean, std = field["mean"].to_numpy(), field["std"].to_numpy()
        traces = []
        if show_variance and field["std"].notna().any():
            # Upper bound (invisible edge) then lower bound filled up to it.
            traces.append(
                _scatter(
                    dates,
                    mean + std,
                    mode="lines",
                    line={"color": "rgba(0,0,0,0)"},
                    showlegend=False,
                    hoverinfo="skip",
                    legendgroup="field_avg",
                )
            )
            traces.append(
                _scatter(
                    dates,
                    mean - std,
                    mode="lines",
                    fill="tonexty",
                    fillcolor=theme.rgba(theme.TEXT, 0.10),
                    line={"color": "rgba(0,0,0,0)"},
                    name="Field Variance (±1 SD)",
                    legendgroup="field_avg",
                    hovertemplate=theme.hover(
                        "Field Variance Range",
                        [
                            ("Date", "%{x|%Y-%m-%d}"),
                            ("Range", "+-1 Standard Deviation"),
                        ],
                    ),
                )
            )
        traces.append(
            _scatter(
                dates,
                mean,
                mode="lines+markers",
                name="Field Average",
                line={"color": theme.TEXT, "width": 3},
                marker={"size": 8, "color": theme.TEXT},
                legendgroup="field_avg",
                hovertemplate=theme.hover(
                    "Field Average",
                    [
                        ("Date", "%{x|%Y-%m-%d}"),
                        (f"Avg {label}", f"%{{y:.2f}} {unit}"),
                        ("Std Dev", f"+/-%{{customdata[0]:.2f}} {unit}"),
                        ("Tubes", "%{customdata[1]:.0f}"),
                    ],
                ),
                customdata=field[["std", "count"]].values,
            )
        )
        return traces

    # -- Totals by Tube ---------------------------------------------------
    def create_stacked_bar_chart(self) -> go.Figure:
        """Create an optimized stacked bar chart."""
        try:
            return self._memoized(("stacked",), self._stacked_bar_figure)
        except Exception:
            logger.exception("Error generating stacked bar chart")
            return go.Figure()

    def _stacked_bar_figure(self) -> go.Figure:
        return stacked_bar_figure(self.data_cache, *self._metric_text())

    # -- Depth Profile Grid -----------------------------------------------
    def create_faceted_depth_profile(
        self, selected_tubes: list[int], selected_dates: list[pd.Timestamp] | None = None
    ) -> go.Figure:
        """
        Create a faceted depth profile visualization showing horizontal bar charts
        of root length by depth for each date-tube combination with 10-position binning.

        Args:
            selected_tubes: List of tube numbers to display (up to 6)
            selected_dates: List of dates to display (if None, uses all available dates)

        Returns:
            Plotly figure with subplots in a grid (rows=dates, cols=tubes)
        """
        if not selected_tubes or len(selected_tubes) > MAX_TUBES:
            return theme.message_figure(
                f"Please select between 1 and {MAX_TUBES} tubes for the faceted view"
            )
        tubes = tuple(selected_tubes)
        dates = None if selected_dates is None else tuple(selected_dates)
        try:
            return self._memoized(
                ("faceted", tubes, dates), lambda: self._faceted_figure(tubes, dates)
            )
        except Exception as e:
            logger.exception("Error generating faceted depth profile")
            return theme.message_figure(f"Error: {e}", color=theme.DANGER, size=16)

    def _faceted_figure(self, tubes: tuple, dates: tuple | None) -> go.Figure:
        profile = build_faceted_profile(self.data_cache, tubes, dates)
        if not profile.dates:
            return theme.message_figure("No dates with data for selected tubes")
        label, unit = self._metric_text()
        return faceted_profile_figure(profile, label=label, unit=unit)

"""
Root-area dashboard (Dash): layout, callbacks and figures (F-014 hardened).

Shares its chrome with the length dashboard (:mod:`.dash_shell`) and its
aggregates with it (:class:`.dash_data_cache.DataCache` over the area column), so
every figure is built from cached per-tube / per-position tables and memoized
(PV-17: the area app used to re-filter the whole frame on every callback).
"""

import logging

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, dcc
from dash.exceptions import PreventUpdate

from app.config import DASH_AREA_PORT, DASH_HOST

from . import dash_export, theme
from .dash_data_cache import DataCache
from .dash_shell import (
    NO_OPTIONS_SUMMARY,
    DashboardShell,
    ShellSpec,
    ViewOption,
    create_dash_app,
    tube_options,
    with_uirevision,
)
from .figure_memo import FigureMemo

theme.use("sprouts")

logger = logging.getLogger(__name__)

AREA_SHELL = ShellSpec(
    title="Root Area — trial overview",
    controls_label="Tube selection",
    views=(
        ViewOption("Stacked Bar View", "stacked"),
        ViewOption("Growth Over Time", "time"),
        ViewOption("Area Profile by Position", "profile", has_controls=True),
    ),
    plot_filename="root_area_plot",
    tube_view="profile",
)

# Fill the chart card completely (card is flex:1 of the 100vh page).
_GRAPH_STYLE = {"height": "100%", "width": "100%", "overflow": "hidden"}
_SHOWN = {"display": "block"}
_HIDDEN = {"display": "none"}
_FIGURE_MEMO_SIZE = 16


class DashAppArea:
    """The root-area dashboard: one Dash app over one root_areas.csv."""

    def __init__(self, data_processor, save_directory):
        self.data_processor = data_processor
        self.save_directory = save_directory
        self.data_cache = DataCache(data_processor)
        self._figures = FigureMemo(_FIGURE_MEMO_SIZE)

        self.shell = DashboardShell(AREA_SHELL)
        self.app = create_dash_app(__name__, title="SPROUTS — Root Area")
        self._setup_layout()
        self._setup_callbacks()

    def _setup_layout(self):
        """Define the Dash app layout."""
        self.app.layout = self.shell.page(
            controls=[
                # Options are static: blanking them made Dash 4's Dropdown drop the
                # selected tube whenever another view was shown.
                dcc.Dropdown(
                    id="tube-selector",
                    options=tube_options(self.data_cache.tubes),
                    placeholder="Select Tube",
                    clearable=False,
                    className="viz-tube-dropdown",
                    style=_HIDDEN,
                ),
            ],
            chart=self.shell.chart_graph(),
        )

    def _profile_tube(self, selected_tube):
        """The tube the profile view plots: the dropdown's, else the first."""
        tubes = [int(t) for t in self.data_cache.tubes]
        if selected_tube in tubes:
            return int(selected_tube)
        return tubes[0] if tubes else None

    def _controls_summary(self, view, tube) -> str:
        """One line for the collapsed control strip (PV-15)."""
        if view == "profile":
            plotted = self._profile_tube(tube)
            return f"Tube {plotted}" if plotted is not None else "No tubes"
        return NO_OPTIONS_SUMMARY

    def _export_table(self, view, tube) -> dash_export.ExportTable | None:
        """The rows behind ``view`` (PV-16), from the same cached aggregates."""
        if view in ("stacked", "time"):
            return dash_export.ExportTable(
                dash_export.tube_date_totals(self.data_cache),
                "Total root area per tube and measurement date (sum over positions)",
                {"tubes": "all", "dates": "all"},
            )
        if view == "profile":
            plotted = self._profile_tube(tube)
            if plotted is None:
                return None
            return dash_export.ExportTable(
                dash_export.position_means(self.data_cache, plotted),
                "Mean root area per position and measurement date for one tube",
                {"tube": str(plotted), "dates": "all"},
            )
        return None

    def _setup_callbacks(self):
        """Define Dash callbacks."""
        self.shell.register_callbacks(
            self.app,
            tubes=lambda: self.data_cache.tubes,
            summary_inputs=[Input("tube-selector", "value")],
            summarize=self._controls_summary,
        )
        self.shell.register_export(
            self.app,
            states=[State("tube-selector", "value")],
            build_table=self._export_table,
            source_csv=self.data_processor.csv_path,
            metric_column=self.data_cache.value_column,
            metric_slug="root_area",
        )

        @self.app.callback(
            [
                Output("main-graph", "figure"),
                Output("click-data", "children"),
                Output("tube-selector", "style"),
                Output("tube-selector", "options"),
                Output("main-graph", "style"),
            ],
            [
                Input("view-selector", "value"),
                Input("tube-selector", "value"),
            ],
        )
        def update_visualization(view_type, selected_tube):
            keep = dash.no_update  # tube-selector options are static (see layout)
            try:
                if view_type == "profile":
                    tube = self._profile_tube(selected_tube)
                    if tube is None:
                        logger.warning("profile view: no tubes available")
                        raise PreventUpdate
                    figure = with_uirevision(self.show_area_profile(tube), "profile", tube)
                    return figure, "", _SHOWN, keep, _GRAPH_STYLE
                if view_type == "time":
                    figure = with_uirevision(self.show_growth_over_time(), "time")
                    return figure, "", _HIDDEN, keep, _GRAPH_STYLE
                figure = with_uirevision(self.create_stacked_bar_chart(), "stacked")
                return figure, "", _HIDDEN, keep, _GRAPH_STYLE
            except PreventUpdate:
                raise
            except Exception as exc:  # noqa: BLE001 -- show the failure instead of a stale chart
                logger.exception("update_visualization(%s): unexpected error", view_type)
                figure = theme.message_figure(
                    f"Error generating visualization: {exc}", color=theme.DANGER, size=16
                )
                return (
                    figure,
                    "An error occurred while rendering this view.",
                    _HIDDEN,
                    keep,
                    _GRAPH_STYLE,
                )

    # ---- figures (memoized; treat returned figures as read-only) ---------------

    def _memoized(self, key: tuple, build):
        return self._figures.get_or_build((self.data_cache.generation, *key), build)

    def create_stacked_bar_chart(self) -> go.Figure:
        """Stacked bars: total root area per tube, one bar segment per date."""
        return self._memoized(("stacked",), self._stacked_bar_figure)

    def _stacked_bar_figure(self) -> go.Figure:
        cache = self.data_cache
        tubes = list(cache.tubes)
        pivot = cache.tube_date_groups.unstack("Date").reindex(index=tubes).fillna(0)
        labels = [f"Tube {int(tube)}" for tube in tubes]
        traces = []
        for date in cache.dates:
            values = pivot[date].to_numpy() if date in pivot.columns else [0.0] * len(tubes)
            day = date.strftime("%Y-%m-%d")
            traces.append(
                go.Bar(
                    name=day,
                    x=labels,
                    y=values,
                    text=[f"{v:.2f}" for v in values],
                    textposition="auto",
                    hovertemplate=theme.hover("%{x}", [("Date", day), ("Area", "%{y:.2f} mm²")]),
                )
            )
        fig = go.Figure(data=traces)
        fig.update_layout(
            barmode="stack",
            title_text="Root Area Growth by Tube and Date",
            xaxis_title_text="Tube",
            yaxis_title_text="Total Area (mm²)",
            legend_title_text="Measurement Date",
            hovermode="x unified",
        )
        return fig

    def show_area_profile(self, selected_tube) -> go.Figure:
        """Mean root area per position for one tube, one bar group per date."""
        return self._memoized(
            ("profile", selected_tube), lambda: self._area_profile_figure(selected_tube)
        )

    def _area_profile_figure(self, tube) -> go.Figure:
        means = dash_export.position_means(self.data_cache, tube)
        value = f"Mean {self.data_cache.value_column}"
        fig = go.Figure()
        for index, (day, rows) in enumerate(means.groupby("Date", sort=True)):
            fig.add_trace(
                go.Bar(
                    x=[f"L{int(pos)}" for pos in rows["Position"]],
                    y=rows[value],
                    name=day,
                    marker_color=theme.color(index),
                    text=rows[value].round(2),
                    textposition="auto",
                    hovertemplate=theme.hover(
                        "Position: %{x}", [("Area", "%{y:.2f} mm²"), ("Date", day)]
                    ),
                )
            )
        fig.update_layout(
            title_text=f"Root Area Profile - Tube {int(tube)}",
            xaxis_title_text="Position",
            yaxis_title_text="Root Area (mm²)",
            legend_title_text="Measurement Dates",
            barmode="group",
        )
        return fig

    def show_growth_over_time(self) -> go.Figure:
        """Total root area per tube over time, one line per tube."""
        return self._memoized(("time",), self._growth_over_time_figure)

    def _growth_over_time_figure(self) -> go.Figure:
        fig = go.Figure()
        for tube in self.data_cache.tubes:
            totals = self.data_cache.get_tube_totals(tube)
            fig.add_trace(
                go.Scatter(
                    x=totals.index,
                    y=totals.to_numpy(),
                    mode="lines+markers",
                    name=f"Tube {int(tube)}",
                    hovertemplate=theme.hover(
                        "Tube %{fullData.name}",
                        [("Date", "%{x|%Y-%m-%d}"), ("Total Area", "%{y:.2f} mm²")],
                    ),
                )
            )
        fig.update_layout(
            title_text="Root Area Growth Over Time",
            xaxis_title_text="Date",
            yaxis_title_text="Total Area (mm²)",
        )
        return fig

    def run_server(self):
        """Run the Dash server (legacy entry point — use DashServerThreadArea in production)."""
        try:
            self.app.run(debug=False, port=DASH_AREA_PORT, host=DASH_HOST, threaded=True)
        except OSError as exc:
            logger.error("run_server: port %d unavailable: %s", DASH_AREA_PORT, exc)
        except Exception:  # noqa: BLE001
            logger.exception("run_server: unexpected error")

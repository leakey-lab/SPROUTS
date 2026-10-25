"""
Root-area dashboard (Dash): layout, callbacks and figures (F-014 hardened).

Shares its chrome with the length dashboard (:mod:`.dash_shell`) and its
aggregates with it (:class:`.dash_data_cache.DataCache` over the area column), so
every figure is built from cached per-tube / per-position tables and memoized
(PV-17: the area app used to re-filter the whole frame on every callback).

The Growth Over Time view draws one line per tube and shows its legend as the length
view does: automatically up to :data:`~.dash_visualizations.AUTO_LEGEND_MAX_TUBES` tubes
(a 160-entry legend was about 200 ms of each ~660 ms draw, and the hover names each
tube), and "Toggle Legend" shows or hides it; the choice is kept across views. The button
floats in the chart's top-left margin on that view only: the view keeps no control strip,
because a strip opening or closing resizes the chart on screen, a full redraw (measured:
it took back most of what the hidden legend saves).
"""

import logging

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from app.config import DASH_AREA_PORT, DASH_HOST

from . import dash_export, theme
from .dash_data_cache import DataCache
from .dash_shell import (
    LEGEND_STORE_ID,
    LEGEND_TOGGLE_ID,
    NO_OPTIONS_SUMMARY,
    DashboardShell,
    ShellSpec,
    ViewOption,
    clientside_js,
    create_dash_app,
    exclusions_note,
    legend_patch,
    legend_shown,
    legend_store,
    tube_options,
    with_uirevision,
)
from .dash_visualizations import AUTO_LEGEND_MAX_TUBES, auto_legend
from .figure_memo import FigureMemo

theme.use("sprouts")

logger = logging.getLogger(__name__)

AREA_SHELL = ShellSpec(
    title="Root Area — trial overview",
    controls_label="Tube selection",
    views=(
        ViewOption("Totals by Tube", "stacked"),
        ViewOption("Growth Over Time", "time"),
        ViewOption("Depth Profile", "profile", has_controls=True),
    ),
    plot_filename="root_area_plot",
    tube_view="profile",
)

# Fill the chart card completely (card is flex:1 of the 100vh page).
_GRAPH_STYLE = {"height": "100%", "width": "100%", "overflow": "hidden"}
_SHOWN = {"display": "block"}
_HIDDEN = {"display": "none"}
_FIGURE_MEMO_SIZE = 16

# The chart's box: fills the chart card as the card's own flex child does (.viz-loading),
# and places "Toggle Legend" over the chart's top-left margin (above the plot, left of the
# centred title), so showing or hiding the button never resizes the chart.
_CHART_BOX = {
    "flex": "1 1 0%",
    "minHeight": 0,
    "display": "flex",
    "flexDirection": "column",
    "position": "relative",
}
_LEGEND_BUTTON = {"position": "absolute", "top": "14px", "left": "16px", "zIndex": 5}
_LEGEND_BUTTON_SHOWN = {**_LEGEND_BUTTON, "display": "inline-block"}
_LEGEND_BUTTON_HIDDEN = {**_LEGEND_BUTTON, "display": "none"}
_LEGEND_TIP = (
    f"Show or hide the legend (hidden at first for more than {AUTO_LEGEND_MAX_TUBES} "
    "tubes; the hover names a line's tube)"
)
# The button shows with its view, in the browser, in the click's own frame.
_LEGEND_BUTTON_JS = """
function(view) {
    return view === 'time' ? __SHOWN__ : __HIDDEN__;
}
"""


def _triggered_ids() -> set[str]:
    """The component ids whose change fired the running callback."""
    triggered = dash.callback_context.triggered
    return {t["prop_id"].split(".")[0] for t in triggered} if triggered else set()


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
            chart=html.Div(
                style=_CHART_BOX,
                children=[
                    # Growth Over Time only: shows or hides its legend (as the length view's).
                    html.Button(
                        "Toggle Legend",
                        id=LEGEND_TOGGLE_ID,
                        n_clicks=0,
                        className="btn btn-secondary btn-sm",
                        title=_LEGEND_TIP,
                        style=(
                            _LEGEND_BUTTON_SHOWN
                            if AREA_SHELL.default_view == "time"
                            else _LEGEND_BUTTON_HIDDEN
                        ),
                    ),
                    self.shell.chart_graph(),
                ],
            ),
            stores=[legend_store()],
            notice=exclusions_note(self.data_processor),
        )

    def _legend_shown(self, choice) -> bool:
        """Whether the time view shows its legend: the user's choice, else automatic (up to
        ``AUTO_LEGEND_MAX_TUBES`` tubes, all of which the view draws)."""
        return legend_shown(choice, auto_legend(self.data_cache.tubes))

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
        self.shell.register_legend_toggle(
            self.app, automatic=lambda: auto_legend(self.data_cache.tubes)
        )
        self.app.clientside_callback(
            clientside_js(
                _LEGEND_BUTTON_JS, shown=_LEGEND_BUTTON_SHOWN, hidden=_LEGEND_BUTTON_HIDDEN
            ),
            Output(LEGEND_TOGGLE_ID, "style"),
            Input("view-selector", "value"),
            prevent_initial_call=True,
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
                Input(LEGEND_STORE_ID, "data"),
            ],
        )
        def update_visualization(view_type, selected_tube, legend=None):
            keep = dash.no_update  # tube-selector options are static (see layout)
            if _triggered_ids() == {LEGEND_STORE_ID}:
                # "Toggle Legend": flip the legend of the time view in place.
                if view_type != "time":
                    raise PreventUpdate
                return legend_patch(self._legend_shown(legend)), keep, keep, keep, keep
            try:
                if view_type == "profile":
                    tube = self._profile_tube(selected_tube)
                    if tube is None:
                        logger.warning("profile view: no tubes available")
                        raise PreventUpdate
                    figure = with_uirevision(self.show_area_profile(tube), "profile", tube)
                    return figure, "", _SHOWN, keep, _GRAPH_STYLE
                if view_type == "time":
                    figure = with_uirevision(self.show_growth_over_time(legend), "time")
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
        # NaN (no bar, left out of the hover) where a tube was not measured.
        pivot = cache.tube_date_groups.unstack("Date").reindex(index=tubes, columns=cache.dates)
        labels = [f"Tube {int(tube)}" for tube in tubes]
        ramp = theme.date_colors(len(cache.dates))
        traces = []
        for date, colour in zip(cache.dates, ramp, strict=True):
            values = pivot[date].to_numpy()
            day = date.strftime("%Y-%m-%d")
            traces.append(
                go.Bar(
                    name=day,
                    marker_color=colour,
                    x=labels,
                    y=values,
                    **theme.bar_labels(theme.value_labels(values), len(tubes)),
                    hovertemplate=theme.hover("%{x}", [("Date", day), ("Area", "%{y:.2f} mm²")]),
                )
            )
        fig = go.Figure(data=traces)
        fig.update_layout(
            barmode="stack",
            title_text="Root Area by Tube and Date",
            xaxis_title_text="Tube",
            yaxis_title_text="Total Area (mm²)",
            legend_title_text="Measurement date",
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
        positions = sorted(means["Position"].unique())
        # grouped: every date's bar of a position stands side by side
        bars = len(positions) * means["Date"].nunique()
        # a date's colour is its place among ALL dates of the trial, not among this tube's
        days = [date.strftime("%Y-%m-%d") for date in self.data_cache.dates]
        ramp = theme.date_colors(len(days))
        fig = go.Figure()
        for day, rows in means.groupby("Date", sort=True):
            fig.add_trace(
                go.Bar(
                    x=[f"L{int(pos)}" for pos in rows["Position"]],
                    y=rows[value],
                    name=day,
                    marker_color=ramp[days.index(day)],
                    **theme.bar_labels(rows[value].round(2), bars),
                    hovertemplate=theme.hover(
                        "Position: %{x}", [("Area", "%{y:.2f} mm²"), ("Date", day)]
                    ),
                )
            )
        fig.update_layout(
            title_text=f"Root Area Depth Profile - Tube {int(tube)}",
            xaxis_title_text="Position (L)",
            yaxis_title_text="Root Area (mm²)",
            legend_title_text="Measurement date",
            barmode="group",
        )
        # Shallow to deep. Plotly's default category order is first appearance, so
        # a later date that starts higher (L5 after L10...) put L5 at the deep end.
        fig.update_xaxes(categoryorder="array", categoryarray=[f"L{int(p)}" for p in positions])
        return fig

    def show_growth_over_time(self, show_legend: bool | None = None) -> go.Figure:
        """Total root area per tube over time, one line per tube. ``show_legend``: the
        user's choice; None: automatic (shown up to ``AUTO_LEGEND_MAX_TUBES`` tubes)."""
        legend = self._legend_shown(show_legend)
        return self._memoized(("time", legend), lambda: self._growth_over_time_figure(legend))

    def _growth_over_time_figure(self, show_legend: bool = True) -> go.Figure:
        """The time view's figure; shown, the legend is the theme's (the figure as before
        the toggle), hidden it leaves no empty band beside the plot."""
        # Plain dicts validated once by go.Figure (add_trace validated every trace
        # and then the figure again: 57 -> ~20 ms for 160 tubes); the same JSON.
        hovertemplate = theme.hover(
            "Tube %{fullData.name}",
            [("Date", "%{x|%Y-%m-%d}"), ("Total Area", "%{y:.2f} mm²")],
        )
        traces = []
        for tube in self.data_cache.tubes:
            totals = self.data_cache.get_tube_totals(tube)
            traces.append(
                {
                    "type": "scatter",
                    "x": totals.index.to_numpy(),
                    "y": totals.to_numpy(),
                    "mode": "lines+markers",
                    "name": f"Tube {int(tube)}",
                    "hovertemplate": hovertemplate,
                }
            )
        layout = {
            "title": {"text": "Root Area Growth Over Time"},
            "xaxis": {"title": {"text": "Date"}},
            "yaxis": {"title": {"text": "Total Area (mm²)"}},
        }
        if not show_legend:
            layout["showlegend"] = False
            layout["margin"] = {"r": theme.margin_right(False)}
        return go.Figure(data=traces, layout=layout)

    def run_server(self):
        """Run the Dash server (legacy entry point — use DashServerThreadArea in production)."""
        try:
            self.app.run(debug=False, port=DASH_AREA_PORT, host=DASH_HOST, threaded=True)
        except OSError as exc:
            logger.error("run_server: port %d unavailable: %s", DASH_AREA_PORT, exc)
        except Exception:  # noqa: BLE001
            logger.exception("run_server: unexpected error")

"""
Root-length dashboard (Dash): layout, callbacks and Growth-Lines hover images.

The page chrome (header, view selector, control strip, chart card, CSV export)
comes from :mod:`.dash_shell`; figures from :class:`.dash_visualizations.DashVisualizations`
over a :class:`.dash_data_cache.DataCache`; hover thumbnails from
:mod:`.dash_image_utils`. Component ids, callback Outputs and figure customdata
are a contract with the renderer and the embedding window: keep them stable.
"""

import logging
import re
from typing import Any

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, Patch, State, dcc, html
from dash.exceptions import PreventUpdate

from app.config import DASH_HOST, DASH_LENGTH_PORT

from . import dash_export, theme
from .dash_data_cache import DataCache
from .dash_image_utils import ThumbnailServer, build_available_images_map
from .dash_shell import (
    NO_OPTIONS_SUMMARY,
    DashboardShell,
    ShellSpec,
    ViewOption,
    count_label,
    create_dash_app,
    exclusions_note,
    tube_options,
    with_uirevision,
)
from .dash_visualizations import GROWTH_LINES_INTERVAL, DashVisualizations, parse_tube_selection
from .faceted_profile import MAX_TUBES, build_faceted_profile

theme.use("sprouts")

logger = logging.getLogger(__name__)

LENGTH_SHELL = ShellSpec(
    title="Root Length — trial overview",
    controls_label="Tube & date range",
    views=(
        ViewOption("Stacked Bar View", "stacked"),
        ViewOption("Growth Over Time", "time", has_controls=True),
        ViewOption("Growth Lines", "lines", has_controls=True),
        ViewOption("Faceted Depth Profile", "faceted", has_controls=True),
    ),
    plot_filename="root_length_plot",
    tube_view="lines",
)

_SHOWN = {"display": "block"}
_HIDDEN = {"display": "none"}
_IMAGES_SHOWN = {"display": "flex"}  # .viz-image-panel CSS owns size and layout
# The chart fills the chart card (a flex column of the 100vh page).
_GRAPH_STYLE = {"height": "100%", "width": "100%", "overflow": "hidden"}
# The faceted grid keeps its computed height; the chart card scrolls instead.
_FACETED_GRAPH_STYLE = {
    "height": "auto",
    "width": "100%",
    "overflow": "visible",
    "flex": "0 0 auto",
}

_DATE_TOKEN = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def _card_style(visible: bool) -> dict:
    return _SHOWN if visible else _HIDDEN


class DashApp(DashVisualizations):
    """The root-length dashboard: one Dash app over one root_lengths.csv."""

    def __init__(self, data_processor: Any, save_directory: str, image_manager=None):
        self.data_processor = data_processor
        self.save_directory = save_directory
        self.image_manager = image_manager

        # (tube, position, date) -> image path, parsed on first use (PV-17).
        self.available_images = build_available_images_map(image_manager)
        self.data_cache = DataCache(data_processor)
        self._tube_options = tube_options(self.data_cache.tubes)
        self._date_options = [
            {"label": date.strftime("%Y-%m-%d"), "value": date.isoformat()}
            for date in self.data_cache.dates
        ]

        self.shell = DashboardShell(LENGTH_SHELL)
        self.app = create_dash_app(__name__, title="SPROUTS — Root Length")
        # Hover thumbnails are served by URL from this app's own server (PV-09).
        self.thumbnail_server = ThumbnailServer(self.available_images.thumbnails)
        self.thumbnail_server.register(self.app.server)

        self._setup_layout()
        self._setup_callbacks()

    def get_encoded_image(self, tube: int, position: int, date: pd.Timestamp) -> str:
        """Hover thumbnail as a ``data:image/jpeg`` URL (``""`` if unavailable)."""
        try:
            key = (tube, position, date.strftime("%Y.%m.%d"))
        except (AttributeError, ValueError):
            return ""
        return self.available_images.thumbnails.data_url(key)

    # ---- layout ------------------------------------------------------------

    def _setup_layout(self) -> None:
        """Build the page: shell chrome around the length controls and chart."""
        self.app.layout = self.shell.page(
            controls=self._controls(),
            # Lines view: chart and hover-image panel side by side; other views
            # hide the panel. Both keep the same component ids.
            chart=html.Div(
                className="viz-lines-row",
                children=[
                    html.Div(className="viz-chart-pane", children=self.shell.chart_graph()),
                    html.Div(id="image-container", className="viz-image-panel", style=_HIDDEN),
                ],
            ),
            stores=[
                dcc.Store(id="selected-tubes-store", data=[]),
                dcc.Store(id="legend-visible-store", data=True),
                dcc.Store(id="faceted-tubes-store", data=[]),
                dcc.Store(id="faceted-dates-store", data=[]),
            ],
            notice=exclusions_note(self.data_processor),
        )

    def _controls(self) -> list:
        # Dropdown options are static: Dash 4's Dropdown drops a selected value
        # whenever its options change without it, so they are never blanked.
        return [
            dcc.Dropdown(
                id="tube-selector",
                options=self._tube_options,
                placeholder="Select Tube",
                clearable=False,
                className="viz-tube-dropdown",
                style=_HIDDEN,
            ),
            dbc.Row(
                className="gx-3",
                children=[
                    dbc.Col(self._tube_selection_card(), lg=8),
                    dbc.Col(self._viz_options_card(), lg=4),
                    dbc.Col(self._faceted_options_card(), lg=12),
                ],
            ),
        ]

    def _tube_selection_card(self) -> dbc.Card:
        """Tube pickers of the Growth Over Time view."""
        tabs = dcc.Tabs(
            id="tube-selection-tabs",
            value="tab-multiselect",
            className="viz-tabs",
            children=[
                dcc.Tab(
                    label="Multi-Select",
                    value="tab-multiselect",
                    children=html.Div(
                        className="viz-tab-body",
                        children=[
                            html.Label("Select Tubes:", className="viz-field-label"),
                            dcc.Dropdown(
                                id="tube-multiselect",
                                options=self._tube_options,
                                multi=True,
                                placeholder="Select tubes...",
                            ),
                        ],
                    ),
                ),
                dcc.Tab(
                    label="Range",
                    value="tab-range",
                    children=html.Div(
                        className="viz-tab-body viz-inline-fields",
                        children=[
                            html.Label("From:", className="viz-field-label"),
                            dcc.Input(
                                id="tube-range-from",
                                type="number",
                                placeholder="Start",
                                className="viz-number-input",
                            ),
                            html.Label("To:", className="viz-field-label"),
                            dcc.Input(
                                id="tube-range-to",
                                type="number",
                                placeholder="End",
                                className="viz-number-input",
                            ),
                            html.Button(
                                "Add Range",
                                id="add-range-btn",
                                n_clicks=0,
                                className="btn btn-primary btn-sm",
                            ),
                        ],
                    ),
                ),
                dcc.Tab(
                    label="Manual Entry",
                    value="tab-manual",
                    children=html.Div(
                        className="viz-tab-body",
                        children=[
                            html.Label(
                                "Enter tube numbers (e.g., 1,3,5,10-15), then press Enter:",
                                className="viz-field-label",
                            ),
                            # debounce: the entry is added on Enter / blur. Per keystroke,
                            # typing "12" first added tube 1 (selections accumulate).
                            dcc.Input(
                                id="tube-manual-entry",
                                type="text",
                                placeholder="1,3,5,10-15",
                                debounce=True,
                                className="viz-text-input",
                            ),
                        ],
                    ),
                ),
            ],
        )
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H4("Tube Selection", className="card-title viz-card-title"),
                    tabs,
                    html.Hr(),
                    html.Label("Selected Tubes:", className="viz-field-label"),
                    html.Div(id="selected-tubes-display", className="viz-selected-tubes"),
                    html.Div(
                        className="viz-button-row",
                        children=[
                            html.Button(
                                "Clear All",
                                id="clear-all-btn",
                                n_clicks=0,
                                className="btn btn-warning btn-sm",
                            ),
                            html.Button(
                                "Select All",
                                id="select-all-btn",
                                n_clicks=0,
                                className="btn btn-info btn-sm",
                            ),
                        ],
                    ),
                ]
            ),
            id="tube-selection-card",
            style=_HIDDEN,
        )

    @staticmethod
    def _viz_options_card() -> dbc.Card:
        """Field-average and legend options of the Growth Over Time view."""
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H4("Visualization Options", className="card-title viz-card-title"),
                    dcc.Checklist(
                        id="growth-viz-options",
                        options=[
                            {"label": "Show Field Average", "value": "show_field_avg"},
                            {"label": "Show Field Variance Shading", "value": "show_field_var"},
                        ],
                        value=["show_field_avg", "show_field_var"],
                        className="viz-checklist",
                        labelClassName="viz-check-label",
                    ),
                    html.Hr(),
                    html.Button(
                        "Toggle Legend",
                        id="toggle-legend-btn",
                        n_clicks=0,
                        className="btn btn-secondary btn-sm w-100",
                    ),
                ]
            ),
            id="viz-options-card",
            style=_HIDDEN,
        )

    def _faceted_options_card(self) -> dbc.Card:
        """Date rows and tube columns of the Faceted Depth Profile view."""
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H4("Faceted View Options", className="card-title viz-card-title"),
                    dbc.Row(
                        className="gx-3",
                        children=[
                            dbc.Col(
                                [
                                    html.Label(
                                        "Select Dates for Rows:", className="viz-field-label"
                                    ),
                                    dcc.Dropdown(
                                        id="faceted-date-selector",
                                        options=self._date_options,
                                        multi=True,
                                        placeholder="Select dates (all if none selected)...",
                                    ),
                                ],
                                md=6,
                            ),
                            dbc.Col(
                                [
                                    html.Label(
                                        f"Select Tubes for Columns (up to {MAX_TUBES}):",
                                        className="viz-field-label",
                                    ),
                                    dcc.Dropdown(
                                        id="faceted-tube-selector",
                                        options=self._tube_options,
                                        multi=True,
                                        placeholder=f"Select up to {MAX_TUBES} tubes...",
                                    ),
                                ],
                                md=6,
                            ),
                        ],
                    ),
                    html.Div(id="faceted-selection-info", className="viz-note-box"),
                    html.Label("Tube-Date Availability:", className="viz-field-label"),
                    html.Div(id="tube-date-availability", className="viz-availability"),
                ]
            ),
            id="faceted-options-card",
            style=_HIDDEN,
        )

    # ---- pure helpers --------------------------------------------------------

    def _lines_tube(self, selected_tube: Any) -> int | None:
        """The tube the Growth Lines view plots: the dropdown's, else the first."""
        tubes = [int(t) for t in self.data_cache.tubes]
        if selected_tube in tubes:
            return int(selected_tube)
        return tubes[0] if tubes else None

    def _controls_summary(self, view, tube, selected_tubes, faceted_tubes, faceted_dates) -> str:
        """One line for the collapsed control strip (PV-15)."""
        if view == "lines":
            plotted = self._lines_tube(tube)
            return f"Tube {plotted}" if plotted is not None else "No tubes"
        if view == "time":
            count = len(selected_tubes or [])
            return f"{count_label(count, 'tube')} selected" if count else "No tubes selected"
        if view == "faceted":
            dates = faceted_dates or []
            date_text = count_label(len(dates), "date") if dates else "all dates"
            return f"{len(faceted_tubes or [])}/{MAX_TUBES} tubes · {date_text}"
        return NO_OPTIONS_SUMMARY

    @staticmethod
    def _faceted_info(tubes: list, dates: list) -> html.Div:
        """Validation note under the faceted selectors (theme classes, PV-14)."""
        count = len(tubes)
        if count == 0:
            return html.Div(
                f"No tubes selected. Select 1 to {MAX_TUBES} tubes.",
                className="viz-note viz-note--muted",
            )
        if count > MAX_TUBES:
            return html.Div(
                f"Too many tubes: {count}/{MAX_TUBES} selected. "
                f"Remove {count - MAX_TUBES} to draw the chart.",
                className="viz-note viz-note--danger",
            )
        date_text = count_label(len(dates), "date") if dates else "all dates"
        return html.Div(
            [
                html.Span(f"Tubes: {count}/{MAX_TUBES} selected", className="viz-note--ok"),
                html.Span(f"Dates: {date_text}", className="viz-note--info"),
            ],
            className="viz-note viz-note--split",
        )

    def _availability_view(self, tubes: list, dates: list):
        """Which selected tubes were measured on which dates."""
        availability = self.get_tube_date_availability()
        if tubes and dates:
            header = [html.Th("Tube/Date")] + [html.Th(d.strftime("%m/%d")) for d in dates]
            rows = [html.Tr(header)]
            for tube in tubes:
                cells = [html.Td(f"T{int(tube)}", className="viz-avail-tube")]
                for date in dates:
                    has_data = availability.get((tube, date), False)
                    cells.append(
                        html.Td(
                            "✓" if has_data else "✗",
                            className="viz-avail-yes" if has_data else "viz-avail-no",
                            title="has data" if has_data else "no data",
                        )
                    )
                rows.append(html.Tr(cells))
            return [
                html.Div("Availability (✓ = has data, ✗ = no data):", className="viz-field-label"),
                html.Table(rows, className="viz-availability-table"),
            ]
        if tubes:
            lines = [
                html.Div("Dates with data for the selected tubes:", className="viz-field-label")
            ]
            for tube in tubes:
                measured = [
                    date.strftime("%Y-%m-%d")
                    for (tube_id, date), present in availability.items()
                    if tube_id == tube and present
                ]
                if measured:
                    lines.append(
                        html.Div(
                            f"Tube {int(tube)}: {', '.join(measured)}", className="viz-avail-line"
                        )
                    )
            return lines
        return html.Div("Select tubes to see availability information.", className="viz-empty-hint")

    def _thumbnail(self, tube: int, position: int, date_str: str) -> html.Div:
        """One hover image (served by URL) with its position label."""
        return html.Div(
            [
                html.Img(
                    src=self.thumbnail_server.url(tube, position, date_str),
                    alt=f"Tube {tube} L{position} {date_str}",
                    className="viz-thumb",
                ),
                html.P(f"Position L{position}", className="viz-thumb-label"),
            ],
            key=f"image-{position}-{date_str}",
            className="viz-thumb-card",
        )

    # ---- figures -----------------------------------------------------------

    def _render_view(
        self, view, selected_tube, selected_tubes, viz_options, legend_visible, tubes, dates
    ) -> tuple:
        """Outputs of update_visualization for ``view`` (figure, message, styles)."""
        keep = dash.no_update  # tube-selector options are static (see _controls)
        if view == "time":
            options = viz_options or []
            figure = self.show_growth_over_time(
                selected_tubes=selected_tubes,
                show_field_average="show_field_avg" in options,
                show_field_variance="show_field_var" in options,
                show_legend=legend_visible,
            )
            return with_uirevision(figure, "time"), "", _HIDDEN, keep, _GRAPH_STYLE, _HIDDEN
        if view == "lines":
            tube = self._lines_tube(selected_tube)
            if tube is None:
                message = "No tube data available"
                return theme.message_figure(message), message, _HIDDEN, keep, _GRAPH_STYLE, _HIDDEN
            figure = with_uirevision(self.show_growth_lines(tube), "lines", tube)
            return figure, "", _SHOWN, keep, _GRAPH_STYLE, _IMAGES_SHOWN
        if view == "faceted":
            return (*self._faceted_outputs(tubes, dates), keep, *self._faceted_styles(tubes))
        figure = with_uirevision(self.create_stacked_bar_chart(), "stacked")
        return figure, "", _HIDDEN, keep, _GRAPH_STYLE, _HIDDEN

    def _faceted_outputs(self, tubes, dates) -> tuple:
        """(figure, message, tube-selector style) of the faceted view."""
        count = len(tubes or [])
        if count == 0 or count > MAX_TUBES:
            if count > MAX_TUBES:
                text = (
                    f"Too many tubes selected ({count}/{MAX_TUBES}). "
                    f"Please remove {count - MAX_TUBES} tubes."
                )
                figure = theme.message_figure(text, color=theme.DANGER)
            else:
                figure = theme.message_figure(
                    f"Select 1 to {MAX_TUBES} tubes under “{LENGTH_SHELL.controls_label}”"
                )
            return figure, "", _HIDDEN
        selected_dates = [pd.Timestamp(d) for d in dates] if dates else None
        figure = self.create_faceted_depth_profile(tubes, selected_dates)
        key = (tuple(tubes), tuple(d.isoformat() for d in selected_dates or ()))
        return with_uirevision(figure, "faceted", *key), "", _HIDDEN

    @staticmethod
    def _faceted_styles(tubes) -> tuple:
        """(main-graph style, image-container style) of the faceted view."""
        drawable = 0 < len(tubes or []) <= MAX_TUBES
        return (_FACETED_GRAPH_STYLE if drawable else _GRAPH_STYLE), _HIDDEN

    @staticmethod
    def _legend_patch(view: str, visible: bool) -> tuple:
        """Flip the legend in place: a Patch instead of re-sending every trace (PV-07)."""
        if view != "time":
            raise PreventUpdate
        patch = Patch()
        patch["layout"]["showlegend"] = bool(visible)
        return (patch, *([dash.no_update] * 5))

    # ---- export (PV-16) ------------------------------------------------------

    def _export_table(
        self, view, tube, selected_tubes, viz_options, faceted_tubes, faceted_dates
    ) -> dash_export.ExportTable | None:
        """The rows behind ``view``, from the same DataCache aggregates as the chart."""
        cache = self.data_cache
        if view == "stacked":
            return dash_export.ExportTable(
                dash_export.tube_date_totals(cache),
                "Total root length per tube and measurement date (sum over positions)",
                {"tubes": "all", "dates": "all"},
            )
        if view == "time":
            tubes = [int(t) for t in selected_tubes or []]
            field_average = "show_field_avg" in (viz_options or [])
            return dash_export.ExportTable(
                dash_export.growth_over_time(cache, tubes, field_average=field_average),
                "Root length over time: field average of per-tube totals (dates within "
                "3 days merged; SD across tubes) and per-tube totals per measurement date",
                {
                    "tubes": ", ".join(map(str, tubes)) or "none",
                    "field average": "shown" if field_average else "hidden",
                },
            )
        if view == "lines":
            plotted = self._lines_tube(tube)
            if plotted is None:
                return None
            return dash_export.ExportTable(
                dash_export.depth_intervals(cache, plotted, interval_size=GROWTH_LINES_INTERVAL),
                f"Root length statistics per {GROWTH_LINES_INTERVAL}-position depth interval "
                "and measurement date for one tube",
                {"tube": str(plotted), "dates": "all"},
            )
        if view == "faceted":
            tubes = [int(t) for t in faceted_tubes or []]
            if not tubes or len(tubes) > MAX_TUBES:
                return None
            dates = [pd.Timestamp(d) for d in faceted_dates] if faceted_dates else None
            profile = build_faceted_profile(cache, tubes, dates)
            return dash_export.ExportTable(
                dash_export.faceted_intervals(profile, cache.value_column),
                "Mean root length per depth interval: each tube next to the field average "
                "(all tubes) of the same interval and date",
                {
                    "tubes": ", ".join(map(str, tubes)),
                    "dates": ", ".join(d.strftime("%Y-%m-%d") for d in dates) if dates else "all",
                },
            )
        return None

    # ---- callbacks -----------------------------------------------------------

    def _setup_callbacks(self) -> None:
        """Register every callback of the dashboard."""
        self.shell.register_callbacks(
            self.app,
            tubes=lambda: self.data_cache.tubes,
            summary_inputs=[
                Input("tube-selector", "value"),
                Input("selected-tubes-store", "data"),
                Input("faceted-tubes-store", "data"),
                Input("faceted-dates-store", "data"),
            ],
            summarize=self._controls_summary,
        )
        self.shell.register_export(
            self.app,
            states=[
                State("tube-selector", "value"),
                State("selected-tubes-store", "data"),
                State("growth-viz-options", "value"),
                State("faceted-tubes-store", "data"),
                State("faceted-dates-store", "data"),
            ],
            build_table=self._export_table,
            source_csv=self.data_processor.csv_path,
            metric_column=self.data_cache.value_column,
            metric_slug="root_length",
        )

        @self.app.callback(
            [
                Output("tube-selection-card", "style"),
                Output("viz-options-card", "style"),
                Output("tube-multiselect", "options"),
                Output("faceted-options-card", "style"),
                Output("faceted-tube-selector", "options"),
                Output("faceted-date-selector", "options"),
            ],
            [Input("view-selector", "value")],
        )
        def toggle_view_panels(view_type):
            """Show the control cards of the selected view.

            The options Outputs are kept for compatibility but never re-sent:
            blanking them made Dash 4's Dropdown drop the user's selection.
            """
            time_view, faceted_view = view_type == "time", view_type == "faceted"
            keep = dash.no_update
            return (
                _card_style(time_view),
                _card_style(time_view),
                keep,
                _card_style(faceted_view),
                keep,
                keep,
            )

        @self.app.callback(
            [
                Output("faceted-tubes-store", "data"),
                Output("faceted-dates-store", "data"),
                Output("faceted-selection-info", "children"),
                Output("tube-date-availability", "children"),
            ],
            [
                Input("faceted-tube-selector", "value"),
                Input("faceted-date-selector", "value"),
            ],
        )
        def manage_faceted_selection(selected_tubes, selected_dates):
            """Store the faceted selection and describe it."""
            tubes = sorted(selected_tubes or [])
            dates = sorted(pd.Timestamp(d) for d in selected_dates or [])
            return (
                tubes,
                dates,
                self._faceted_info(tubes, dates),
                self._availability_view(tubes, dates),
            )

        @self.app.callback(
            Output("selected-tubes-store", "data"),
            [
                Input("tube-multiselect", "value"),
                Input("add-range-btn", "n_clicks"),
                Input("tube-manual-entry", "value"),
                Input("clear-all-btn", "n_clicks"),
                Input("select-all-btn", "n_clicks"),
            ],
            [
                State("tube-range-from", "value"),
                State("tube-range-to", "value"),
                State("selected-tubes-store", "data"),
            ],
        )
        def manage_tube_selection(
            multiselect_values,
            range_clicks,
            manual_entry,
            clear_clicks,
            select_all_clicks,
            range_from,
            range_to,
            current_selection,
        ):
            """Merge the tube choices of the three tabs and the two buttons."""
            ctx = dash.callback_context
            if not ctx.triggered:
                return current_selection or []
            trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

            if trigger_id == "clear-all-btn":
                return []
            if trigger_id == "select-all-btn":
                return sorted(int(tube) for tube in self.data_cache.tubes)

            selected = set(current_selection or [])
            if trigger_id == "tube-multiselect" and multiselect_values:
                selected = set(multiselect_values)
            if trigger_id == "add-range-btn" and range_from is not None and range_to is not None:
                if range_from <= range_to:
                    selected.update(range(int(range_from), int(range_to) + 1))
            if trigger_id == "tube-manual-entry" and manual_entry:
                selected.update(parse_tube_selection(manual_entry))
            return sorted(selected)

        @self.app.callback(
            Output("selected-tubes-display", "children"),
            [Input("selected-tubes-store", "data")],
        )
        def display_selected_tubes(selected_tubes):
            """Selected tubes as chips (first 50)."""
            if not selected_tubes:
                return html.Div("No tubes selected", className="viz-empty-hint")
            chips = [
                html.Span(f"Tube {tube}", className="badge bg-primary viz-tube-badge")
                for tube in selected_tubes[:50]
            ]
            if len(selected_tubes) > 50:
                chips.append(
                    html.Span(f"... and {len(selected_tubes) - 50} more", className="viz-more-hint")
                )
            return html.Div(chips, className="viz-badges")

        @self.app.callback(
            Output("legend-visible-store", "data"),
            [Input("toggle-legend-btn", "n_clicks")],
            [State("legend-visible-store", "data")],
        )
        def toggle_legend(n_clicks, current_state):
            """Flip the Growth Over Time legend."""
            if n_clicks:
                return not current_state
            return current_state

        @self.app.callback(
            [
                Output("main-graph", "figure"),
                Output("click-data", "children"),
                Output("tube-selector", "style"),
                Output("tube-selector", "options"),
                Output("main-graph", "style"),
                Output("image-container", "style"),
            ],
            [
                Input("view-selector", "value"),
                Input("tube-selector", "value"),
                Input("main-graph", "clickData"),
                Input("selected-tubes-store", "data"),
                Input("growth-viz-options", "value"),
                Input("legend-visible-store", "data"),
                Input("faceted-tubes-store", "data"),
                Input("faceted-dates-store", "data"),
            ],
        )
        def update_visualization(
            view_type,
            selected_tube,
            _click_data,
            selected_tubes,
            viz_options,
            legend_visible,
            faceted_tubes,
            faceted_dates,
        ):
            ctx = dash.callback_context
            # Not ctx.triggered_id: on page load this callback is triggered by the
            # stores the other initial callbacks wrote, and it must still draw.
            changed = (
                {t["prop_id"].split(".")[0] for t in ctx.triggered} if ctx.triggered else set()
            )
            if changed == {"main-graph"}:
                # A click selects nothing; re-sending the figure only reset the zoom (PV-07).
                raise PreventUpdate
            if changed == {"legend-visible-store"}:
                return self._legend_patch(view_type, legend_visible)
            try:
                return self._render_view(
                    view_type,
                    selected_tube,
                    selected_tubes,
                    viz_options,
                    legend_visible,
                    faceted_tubes,
                    faceted_dates,
                )
            except Exception as exc:  # noqa: BLE001 -- show the failure instead of a stale chart
                logger.exception("Error in visualization callback")
                figure = theme.message_figure(
                    f"Error generating visualization: {exc}", color=theme.DANGER, size=16
                )
                message = "An error occurred while rendering this view."
                return figure, message, _HIDDEN, dash.no_update, _GRAPH_STYLE, _HIDDEN

        @self.app.callback(
            Output("image-container", "children"),
            Input("main-graph", "hoverData"),
            State("tube-selector", "value"),
            State("view-selector", "value"),
        )
        def display_hover_images(hover_data, selected_tube, view_type):
            """Thumbnails of the hovered 10-position interval (Growth Lines only)."""
            if view_type != "lines":
                raise PreventUpdate  # PV-09: no round-trip DOM churn in other views
            try:
                point = hover_data["points"][0]
                date_str = self._hover_date(point)
                end = int(point["y"])
            except (KeyError, IndexError, TypeError, ValueError):
                return []
            tube = self._lines_tube(selected_tube)
            if not date_str or tube is None:
                return []
            positions = range(end - GROWTH_LINES_INTERVAL + 1, end + 1)
            images = [
                self._thumbnail(tube, position, date_str)
                for position in positions
                if (tube, position, date_str) in self.available_images
            ]
            return images or [
                html.Div("No images available for this interval", className="viz-empty-hint")
            ]

        # Thumbnails belong to one tube and interval: clear them when the view or
        # the tube changes instead of showing the previous tube's images.
        self.app.clientside_callback(
            "function(_view, _tube) { return []; }",
            Output("image-container", "children", allow_duplicate=True),
            Input("view-selector", "value"),
            Input("tube-selector", "value"),
            prevent_initial_call=True,
        )

    @staticmethod
    def _hover_date(point: dict) -> str | None:
        """'YYYY.MM.DD' of a hovered Growth-Lines point (customdata, else its text)."""
        for source in (point.get("customdata"), point.get("text")):
            match = _DATE_TOKEN.search(str(source or ""))
            if match:
                return match.group(0)
        return None

    def run_server(self):
        """Run the Dash server (legacy entry point; the app uses DashServerThread).

        Errors are logged (with traceback) and re-raised so the caller is not
        left silently without a running server (F-008).
        """
        try:
            self.app.run(debug=False, host=DASH_HOST, port=DASH_LENGTH_PORT, threaded=True)
        except Exception:
            logger.exception("Dash length-visualization server failed to run")
            raise

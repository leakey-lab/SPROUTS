"""
Root-length dashboard: every callback, registered in one place.

:func:`register_callbacks` wires the shell callbacks (strip, summary, per-tube
selection, refit, export) and the length dashboard's own ones onto the
dashboard's Dash app. The order of registration is kept: it is the order of
``app.callback_map`` and of the callbacks Dash fires on page load.

The callbacks call the layout builders (:mod:`.dash_length_layout`) and the view
model (:mod:`.dash_length_views`) directly, so a callback costs no more calls
than when they were methods of the dashboard.
"""

import logging
from functools import partial

import dash
import pandas as pd
from dash import Input, Output, State, html
from dash.exceptions import PreventUpdate

from . import theme
from .dash_length_layout import (
    GRAPH_STYLE,
    HIDDEN,
    SHOWN,
    availability_view,
    faceted_info,
    thumbnail,
)
from .dash_length_views import (
    controls_summary,
    export_table,
    hover_date,
    legend_patch,
    lines_tube,
    render_view,
)
from .dash_shell import clientside_js
from .dash_visualizations import GROWTH_LINES_INTERVAL, auto_legend, parse_tube_selection

logger = logging.getLogger(__name__)

# (tube-selection, viz-options, multiselect options, faceted-options, faceted tube /
# date options) of a view; the options are never re-sent (see register_callbacks).
VIEW_PANELS_JS = """
function(view) {
    var keep = window.dash_clientside.no_update;
    var time = view === 'time' ? __SHOWN__ : __HIDDEN__;
    var faceted = view === 'faceted' ? __SHOWN__ : __HIDDEN__;
    return [time, time, keep, faceted, keep, keep];
}
"""


def register_callbacks(dash_app) -> None:
    """Register every callback of the length dashboard ``dash_app`` (a DashApp)."""
    dash_app.shell.register_callbacks(
        dash_app.app,
        tubes=lambda: dash_app.data_cache.tubes,
        summary_inputs=[
            Input("tube-selector", "value"),
            Input("selected-tubes-store", "data"),
            Input("faceted-tubes-store", "data"),
            Input("faceted-dates-store", "data"),
        ],
        summarize=partial(controls_summary, dash_app),
    )
    dash_app.shell.register_export(
        dash_app.app,
        states=[
            State("tube-selector", "value"),
            State("selected-tubes-store", "data"),
            State("growth-viz-options", "value"),
            State("faceted-tubes-store", "data"),
            State("faceted-dates-store", "data"),
        ],
        build_table=partial(export_table, dash_app),
        source_csv=dash_app.data_processor.csv_path,
        metric_column=dash_app.data_cache.value_column,
        metric_slug="root_length",
    )

    # The control cards of the selected view, shown in the browser in the click's
    # own frame, like the strip around them (dash_shell): the strip opens at its
    # final height before the new figure is drawn. The options Outputs are kept
    # for compatibility but never re-sent: blanking them made Dash 4's Dropdown
    # drop the user's selection.
    dash_app.app.clientside_callback(
        clientside_js(VIEW_PANELS_JS, shown=SHOWN, hidden=HIDDEN),
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

    @dash_app.app.callback(
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
            faceted_info(tubes, dates),
            availability_view(dash_app.get_tube_date_availability(), tubes, dates),
        )

    @dash_app.app.callback(
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
            return sorted(int(tube) for tube in dash_app.data_cache.tubes)

        selected = set(current_selection or [])
        if trigger_id == "tube-multiselect":
            # The dropdown replaces the selection; an emptied one clears it.
            selected = set(multiselect_values or [])
        if trigger_id == "add-range-btn" and range_from is not None and range_to is not None:
            if range_from <= range_to:
                selected.update(range(int(range_from), int(range_to) + 1))
        if trigger_id == "tube-manual-entry" and manual_entry:
            selected.update(parse_tube_selection(manual_entry))
        # Only tubes with data: "1-200" on a 160-tube trial said "200 tubes selected".
        return sorted(selected & {int(tube) for tube in dash_app.data_cache.tubes})

    @dash_app.app.callback(
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

    @dash_app.app.callback(
        Output("legend-visible-store", "data"),
        [Input("toggle-legend-btn", "n_clicks")],
        [State("legend-visible-store", "data"), State("selected-tubes-store", "data")],
    )
    def toggle_legend(n_clicks, current_state, selected_tubes):
        """Flip the Growth Over Time legend: from what is shown, automatic or chosen."""
        if not n_clicks:
            return current_state
        shown = auto_legend(selected_tubes) if current_state is None else bool(current_state)
        return not shown

    @dash_app.app.callback(
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
        changed = {t["prop_id"].split(".")[0] for t in ctx.triggered} if ctx.triggered else set()
        if changed == {"main-graph"}:
            # A click selects nothing; re-sending the figure only reset the zoom (PV-07).
            raise PreventUpdate
        if changed == {"legend-visible-store"}:
            return legend_patch(view_type, legend_visible)
        try:
            return render_view(
                dash_app,
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
            return figure, message, HIDDEN, dash.no_update, GRAPH_STYLE, HIDDEN

    @dash_app.app.callback(
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
            date_str = hover_date(point)
            end = int(point["y"])
        except (KeyError, IndexError, TypeError, ValueError):
            return []
        tube = lines_tube(dash_app.data_cache.tubes, selected_tube)
        if not date_str or tube is None:
            return []
        positions = range(end - GROWTH_LINES_INTERVAL + 1, end + 1)
        images = [
            thumbnail(dash_app.thumbnail_server, tube, position, date_str)
            for position in positions
            if (tube, position, date_str) in dash_app.available_images
        ]
        return images or [
            html.Div("No images available for this interval", className="viz-empty-hint")
        ]

    # Thumbnails belong to one tube and interval: clear them when the view or
    # the tube changes instead of showing the previous tube's images.
    dash_app.app.clientside_callback(
        "function(_view, _tube) { return []; }",
        Output("image-container", "children", allow_duplicate=True),
        Input("view-selector", "value"),
        Input("tube-selector", "value"),
        prevent_initial_call=True,
    )

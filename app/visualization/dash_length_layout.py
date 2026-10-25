"""
Root-length dashboard: the page layout (control cards, chart row, stores).

Builders only: each takes the data it shows (tube and date dropdown options, the
availability table, the thumbnail server) and returns Dash components, so the
page can be built and checked without a running dashboard. The chrome around it
(header, view selector, control strip, chart card) comes from :mod:`.dash_shell`.

Component ids are a contract with the callbacks (:mod:`.dash_length_callbacks`),
the renderer and the embedding window: keep them stable.
"""

from typing import Any

import dash_bootstrap_components as dbc
from dash import dcc, html

from .dash_shell import DashboardShell, ShellSpec, ViewOption, count_label
from .faceted_profile import MAX_TUBES

LENGTH_SHELL = ShellSpec(
    title="Root Length — trial overview",
    controls_label="Tube & date range",
    views=(
        ViewOption("Totals by Tube", "stacked"),
        ViewOption("Growth Over Time", "time", has_controls=True),
        ViewOption("Depth Profile", "lines", has_controls=True),
        ViewOption("Depth Profile Grid", "faceted", has_controls=True),
    ),
    plot_filename="root_length_plot",
    tube_view="lines",
)

SHOWN = {"display": "block"}
HIDDEN = {"display": "none"}
IMAGES_SHOWN = {"display": "flex"}  # .viz-image-panel CSS owns size and layout
# The chart fills the chart card (a flex column of the 100vh page).
GRAPH_STYLE = {"height": "100%", "width": "100%", "overflow": "hidden"}
# The faceted grid keeps its computed height; the chart card scrolls instead.
FACETED_GRAPH_STYLE = {
    "height": "auto",
    "width": "100%",
    "overflow": "visible",
    "flex": "0 0 auto",
}


def page(
    shell: DashboardShell,
    tube_options: list,
    date_options: list,
    notice: str | None = None,
) -> html.Div:
    """The page: shell chrome around the length controls and chart."""
    return shell.page(
        controls=controls(tube_options, date_options),
        # Lines view: chart and hover-image panel side by side; other views
        # hide the panel. Both keep the same component ids.
        chart=html.Div(
            className="viz-lines-row",
            children=[
                html.Div(className="viz-chart-pane", children=shell.chart_graph()),
                html.Div(id="image-container", className="viz-image-panel", style=HIDDEN),
            ],
        ),
        stores=[
            dcc.Store(id="selected-tubes-store", data=[]),
            # None: automatic (shown up to AUTO_LEGEND_MAX_TUBES tubes); the
            # toggle stores the user's choice.
            dcc.Store(id="legend-visible-store", data=None),
            dcc.Store(id="faceted-tubes-store", data=[]),
            dcc.Store(id="faceted-dates-store", data=[]),
        ],
        notice=notice,
    )


def controls(tube_options: list, date_options: list) -> list:
    """The length dashboard's controls inside the collapsible strip."""
    # Dropdown options are static: Dash 4's Dropdown drops a selected value
    # whenever its options change without it, so they are never blanked.
    return [
        dcc.Dropdown(
            id="tube-selector",
            options=tube_options,
            placeholder="Select Tube",
            clearable=False,
            className="viz-tube-dropdown",
            style=HIDDEN,
        ),
        dbc.Row(
            className="gx-3",
            children=[
                dbc.Col(tube_selection_card(tube_options), lg=8),
                dbc.Col(viz_options_card(), lg=4),
                dbc.Col(faceted_options_card(tube_options, date_options), lg=12),
            ],
        ),
    ]


def tube_selection_card(tube_options: list) -> dbc.Card:
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
                            options=tube_options,
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
        style=HIDDEN,
    )


def viz_options_card() -> dbc.Card:
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
        style=HIDDEN,
    )


def faceted_options_card(tube_options: list, date_options: list) -> dbc.Card:
    """Date rows and tube columns of the Depth Profile Grid view."""
    return dbc.Card(
        dbc.CardBody(
            [
                html.H4("Depth Profile Grid Options", className="card-title viz-card-title"),
                dbc.Row(
                    className="gx-3",
                    children=[
                        dbc.Col(
                            [
                                html.Label("Select Dates for Rows:", className="viz-field-label"),
                                dcc.Dropdown(
                                    id="faceted-date-selector",
                                    options=date_options,
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
                                    options=tube_options,
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
        style=HIDDEN,
    )


def faceted_info(tubes: list, dates: list) -> html.Div:
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


def availability_view(availability: dict, tubes: list, dates: list):
    """Which selected tubes were measured on which dates.

    ``availability`` maps ``(tube, date)`` to whether the tube has data that day
    (``DataCache.get_tube_date_availability``).
    """
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
        lines = [html.Div("Dates with data for the selected tubes:", className="viz-field-label")]
        for tube in tubes:
            measured = [
                date.strftime("%Y-%m-%d")
                for (tube_id, date), present in availability.items()
                if tube_id == tube and present
            ]
            if measured:
                lines.append(
                    html.Div(f"Tube {int(tube)}: {', '.join(measured)}", className="viz-avail-line")
                )
        return lines
    return html.Div("Select tubes to see availability information.", className="viz-empty-hint")


def thumbnail(thumbnail_server: Any, tube: int, position: int, date_str: str) -> html.Div:
    """One hover image (served by URL) with its position label."""
    return html.Div(
        [
            html.Img(
                src=thumbnail_server.url(tube, position, date_str),
                alt=f"Tube {tube} L{position} {date_str}",
                className="viz-thumb",
            ),
            html.P(f"Position L{position}", className="viz-thumb-label"),
        ],
        key=f"image-{position}-{date_str}",
        className="viz-thumb-card",
    )

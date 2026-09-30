"""
Root-length dashboard: what each view shows for a selection (the view model).

Given the dashboard and the values of the callback Inputs, these functions return
the callback outputs: the figure of a view with its message and component styles,
the one-line summary of the control strip, the legend Patch, the rows behind the
"Export CSV" of a view and the date of a hovered point. The Dash wiring lives in
:mod:`.dash_length_callbacks`, the figures in
:class:`.dash_visualizations.DashVisualizations`.

Functions that read the dashboard take it first (``dashboard``: the
:class:`.dash_app.DashApp`, or anything with the attributes of
:class:`LengthDashboard`) and read it when called, like the methods they were.
"""

import re
from typing import TYPE_CHECKING, Any

import dash
import pandas as pd
from dash import Patch
from dash.exceptions import PreventUpdate

from . import dash_export, theme
from .dash_length_layout import (
    FACETED_GRAPH_STYLE,
    GRAPH_STYLE,
    HIDDEN,
    IMAGES_SHOWN,
    LENGTH_SHELL,
    SHOWN,
)
from .dash_shell import NO_OPTIONS_SUMMARY, count_label, with_uirevision
from .dash_visualizations import GROWTH_LINES_INTERVAL
from .faceted_profile import MAX_TUBES, build_faceted_profile

if TYPE_CHECKING:
    from typing import Protocol

    from .dash_data_cache import DataCache

    class LengthDashboard(Protocol):
        """What the view model reads of the dashboard (see DashVisualizations)."""

        data_cache: DataCache

        def show_growth_over_time(self, **kwargs: Any) -> Any: ...
        def show_growth_lines(self, selected_tube: int) -> Any: ...
        def create_stacked_bar_chart(self) -> Any: ...
        def create_faceted_depth_profile(self, tubes: Any, dates: Any) -> Any: ...


DATE_TOKEN = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def lines_tube(tubes, selected_tube: Any) -> int | None:
    """The tube the Growth Lines view plots: the dropdown's, else the first.

    ``tubes`` are the dataset's tube numbers (``DataCache.tubes``).
    """
    tubes = [int(t) for t in tubes]
    if selected_tube in tubes:
        return int(selected_tube)
    return tubes[0] if tubes else None


def controls_summary(
    dashboard: "LengthDashboard", view, tube, selected_tubes, faceted_tubes, faceted_dates
) -> str:
    """One line for the collapsed control strip (PV-15)."""
    if view == "lines":
        plotted = lines_tube(dashboard.data_cache.tubes, tube)
        return f"Tube {plotted}" if plotted is not None else "No tubes"
    if view == "time":
        count = len(selected_tubes or [])
        return f"{count_label(count, 'tube')} selected" if count else "No tubes selected"
    if view == "faceted":
        dates = faceted_dates or []
        date_text = count_label(len(dates), "date") if dates else "all dates"
        return f"{len(faceted_tubes or [])}/{MAX_TUBES} tubes · {date_text}"
    return NO_OPTIONS_SUMMARY


# ---- figures -------------------------------------------------------------------


def render_view(
    dashboard: "LengthDashboard",
    view,
    selected_tube,
    selected_tubes,
    viz_options,
    legend_visible,
    tubes,
    dates,
) -> tuple:
    """Outputs of update_visualization for ``view`` (figure, message, styles)."""
    keep = dash.no_update  # tube-selector options are static (see dash_length_layout.controls)
    if view == "time":
        options = viz_options or []
        figure = dashboard.show_growth_over_time(
            selected_tubes=selected_tubes,
            show_field_average="show_field_avg" in options,
            show_field_variance="show_field_var" in options,
            show_legend=legend_visible,
        )
        return with_uirevision(figure, "time"), "", HIDDEN, keep, GRAPH_STYLE, HIDDEN
    if view == "lines":
        tube = lines_tube(dashboard.data_cache.tubes, selected_tube)
        if tube is None:
            message = "No tube data available"
            return theme.message_figure(message), message, HIDDEN, keep, GRAPH_STYLE, HIDDEN
        figure = with_uirevision(dashboard.show_growth_lines(tube), "lines", tube)
        return figure, "", SHOWN, keep, GRAPH_STYLE, IMAGES_SHOWN
    if view == "faceted":
        return (*faceted_outputs(dashboard, tubes, dates), keep, *faceted_styles(tubes))
    figure = with_uirevision(dashboard.create_stacked_bar_chart(), "stacked")
    return figure, "", HIDDEN, keep, GRAPH_STYLE, HIDDEN


def faceted_outputs(dashboard: "LengthDashboard", tubes, dates) -> tuple:
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
        return figure, "", HIDDEN
    selected_dates = [pd.Timestamp(d) for d in dates] if dates else None
    figure = dashboard.create_faceted_depth_profile(tubes, selected_dates)
    key = (tuple(tubes), tuple(d.isoformat() for d in selected_dates or ()))
    return with_uirevision(figure, "faceted", *key), "", HIDDEN


def faceted_styles(tubes) -> tuple:
    """(main-graph style, image-container style) of the faceted view."""
    drawable = 0 < len(tubes or []) <= MAX_TUBES
    return (FACETED_GRAPH_STYLE if drawable else GRAPH_STYLE), HIDDEN


def legend_patch(view: str, visible: bool) -> tuple:
    """Flip the legend in place: a Patch instead of re-sending every trace (PV-07)."""
    if view != "time":
        raise PreventUpdate
    patch = Patch()
    patch["layout"]["showlegend"] = bool(visible)
    patch["layout"]["margin"]["r"] = theme.margin_right(bool(visible))
    return (patch, *([dash.no_update] * 5))


# ---- export (PV-16) --------------------------------------------------------------


def export_table(
    dashboard: "LengthDashboard",
    view,
    tube,
    selected_tubes,
    viz_options,
    faceted_tubes,
    faceted_dates,
) -> dash_export.ExportTable | None:
    """The rows behind ``view``, from the same DataCache aggregates as the chart."""
    cache = dashboard.data_cache
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
        plotted = lines_tube(cache.tubes, tube)
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


# ---- hover -----------------------------------------------------------------------


def hover_date(point: dict) -> str | None:
    """'YYYY.MM.DD' of a hovered Growth-Lines point (customdata, else its text)."""
    for source in (point.get("customdata"), point.get("text")):
        match = DATE_TOKEN.search(str(source or ""))
        if match:
            return match.group(0)
    return None

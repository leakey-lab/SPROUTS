"""
Shared chrome for the SPROUTS Dash dashboards (root length and root area).

Both dashboards used to copy-paste the same shell: Dash construction, header band,
collapsible control strip, chart card, graph config and the shell callbacks (AR-15).
This module owns that shell once; each dashboard supplies only its views, its
controls, its figures and a one-line summary of its current selection.

Everything the page needs is served from ``app/assets`` (PV-06): Bootswatch Darkly
and the IBM Plex fonts are vendored there, so the dashboards render the same on an
offline PC and never contact a CDN or Google Fonts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from resources.resource_utils import resource_path

from . import dash_export, theme

logger = logging.getLogger(__name__)

#: ``app/assets`` -- vendored Bootstrap theme, fonts and the SPROUTS stylesheet.
ASSETS_DIR = resource_path("app", "assets")

CARET_OPEN = "▾"
CARET_CLOSED = "▸"
NO_OPTIONS_SUMMARY = "No options for this view"

#: Show the chart spinner only when a figure takes longer than this, so quick
#: updates do not flash an overlay over the chart.
LOADING_DELAY_MS = 250

#: Refit the Plotly graph when the control strip opens / closes or the view
#: changes. Targets the real Plotly div (``.js-plotly-plot``), not dcc.Graph's
#: wrapper, which ``Plotly.Plots.resize`` silently ignored (PV-05).
_REFIT_GRAPH_JS = """
function(_isOpen, _view) {
    var gd = document.querySelector('#main-graph .js-plotly-plot');
    if (window.Plotly && gd) {
        try { window.Plotly.Plots.resize(gd); } catch (e) {}
    }
    return window.dash_clientside.no_update;
}
"""


@dataclass(frozen=True)
class ViewOption:
    """One entry of the view selector."""

    label: str
    value: str
    #: The control strip holds settings for this view, so it opens on selection.
    has_controls: bool = False


@dataclass(frozen=True)
class ShellSpec:
    """What differs between the dashboards' chrome."""

    title: str
    controls_label: str
    views: tuple[ViewOption, ...]
    #: File stem for the modebar's "Download plot" (SVG) button.
    plot_filename: str
    #: The per-tube view driven by the ``tube-selector`` dropdown, if any.
    tube_view: str | None = None
    default_view: str = "stacked"

    def has_controls(self, view: str | None) -> bool:
        """True if ``view`` has settings in the control strip."""
        return any(option.value == view and option.has_controls for option in self.views)


def create_dash_app(import_name: str, title: str = "SPROUTS") -> Dash:
    """Create a Dash app that serves only local assets.

    ``compress=False``: the server is loopback-only, so compression buys nothing,
    while flask-compress re-compressed the 4.8 MB plotly bundle on every page open
    (17 ms here, up to 300 ms measured) and answered revalidations with a full 200
    instead of 304 (PV-02).
    """
    return Dash(
        import_name,
        title=title,
        assets_folder=ASSETS_DIR,
        suppress_callback_exceptions=True,
        update_title=None,
        compress=False,
    )


def graph_config(plot_filename: str) -> dict[str, Any]:
    """Plotly config shared by both dashboards.

    Only buttons that do something are shown: Plotly.js has no 'downloadCsv'
    button (the data export is the header's "Export CSV", PV-16) and box / lasso
    select had no callback behind them.
    """
    return {
        "scrollZoom": True,
        "doubleClick": "reset",
        "showTips": False,
        "displayModeBar": True,
        "displaylogo": False,
        "watermark": False,
        "responsive": True,
        "autosizable": True,
        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
        "toImageButtonOptions": {"format": "svg", "filename": plot_filename, "scale": 2},
    }


def data_notice(text: str) -> html.Div:
    """The static notice strip under the header (``viz-data-notice``)."""
    return html.Div(text, id="viz-data-notice", className="viz-data-notice", role="status")


def exclusions_note(data_processor: Any) -> str | None:
    """The processor's "N rows are not shown" sentence, or ``None`` (FR-15)."""
    exclusions = getattr(data_processor, "exclusions", None)
    message = getattr(exclusions, "message", None)
    return message() if callable(message) else None


def count_label(count: int, noun: str) -> str:
    """'1 tube', '3 tubes'."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def tube_options(tubes: Sequence[Any]) -> list[dict[str, Any]]:
    """Dropdown options for tube numbers ('Tube 12' -> 12)."""
    return [{"label": f"Tube {int(tube)}", "value": int(tube)} for tube in tubes]


def with_uirevision(figure, *key: Any):
    """Tag ``figure`` so Plotly keeps the user's zoom, pan and legend clicks
    across re-renders of the same view (PV-07). A different ``key`` (another
    view or tube) starts from the figure's own axes again."""
    figure.update_layout(uirevision=":".join(str(part) for part in key))
    return figure


class DashboardShell:
    """Builds the page chrome shared by the dashboards and wires its callbacks.

    Component ids are a public contract (callbacks, saved state): ``view-selector``,
    ``viz-controls-toggle``, ``viz-controls-collapse``, ``click-data``, ``main-graph``,
    ``viz-resize-dummy`` and ``cached-data`` keep their names.
    """

    def __init__(self, spec: ShellSpec):
        self.spec = spec

    # ---- layout ------------------------------------------------------------

    def page(
        self,
        *,
        controls: Any,
        chart: Any,
        stores: Sequence[Any] = (),
        notice: str | None = None,
    ) -> html.Div:
        """Full page: header, optional data notice, control strip, chart card, stores.

        ``notice`` is one sentence about the data (e.g. rows left out of the
        charts, FR-15); it is shown once, under the header, only when given.
        """
        return html.Div(
            className="sprouts-app viz-page",
            children=[
                self.header(),
                *([data_notice(notice)] if notice else []),
                self.control_strip(controls),
                # Hidden callback target kept for compatibility (Output of the
                # main visualization callbacks).
                html.Div(id="click-data", style={"display": "none"}),
                html.Div(className="viz-chart-card", children=chart),
                # No-op Output target of the clientside refit callback.
                dcc.Store(id="viz-resize-dummy"),
                dcc.Store(id="cached-data"),
                *stores,
            ],
        )

    def header(self) -> html.Div:
        """Eyebrow + title on the left, view selector on the right."""
        return html.Div(
            className="viz-header",
            children=[
                html.Div(
                    className="viz-header-left",
                    children=[
                        html.Div("INTERACTIVE DASHBOARD", className="viz-eyebrow"),
                        html.H1(self.spec.title, className="viz-title"),
                    ],
                ),
                html.Div(
                    className="viz-header-right",
                    children=[
                        dbc.RadioItems(
                            id="view-selector",
                            options=[{"label": v.label, "value": v.value} for v in self.spec.views],
                            value=self.spec.default_view,
                            class_name="btn-group viz-view-selector",
                            input_class_name="btn-check",
                            label_class_name="btn btn-outline-secondary",
                        ),
                        html.Button(
                            "Export CSV",
                            id="viz-export-btn",
                            n_clicks=0,
                            className="btn btn-outline-secondary viz-export-btn",
                            title="Save the data behind this chart as CSV, with its provenance",
                        ),
                        dcc.Download(id="viz-export-download"),
                    ],
                ),
            ],
        )

    def control_strip(self, controls: Any) -> html.Div:
        """Collapsible strip holding the view-specific controls."""
        return html.Div(
            className="viz-controls",
            children=[
                dbc.Button(
                    [
                        html.Span(
                            CARET_CLOSED, id="viz-controls-caret", className="viz-controls-caret"
                        ),
                        html.Span(self.spec.controls_label, className="viz-controls-label"),
                        html.Span("", id="viz-controls-summary", className="viz-controls-summary"),
                    ],
                    id="viz-controls-toggle",
                    n_clicks=0,
                    className="viz-controls-toggle",
                    color="link",
                ),
                dbc.Collapse(
                    id="viz-controls-collapse",
                    is_open=False,
                    children=html.Div(className="viz-controls-body", children=controls),
                ),
            ],
        )

    def chart_graph(self) -> dcc.Loading:
        """The ``main-graph`` inside a loading overlay that fills the chart card.

        dcc.Loading renders two wrapper divs around the graph; both carry
        ``parent_className`` so the stylesheet can stretch them (PV-05: without it
        the chart collapsed to Plotly's 450 px default). The overlay keeps the
        previous chart visible (dimmed) and only waits on the figure itself.
        """
        return dcc.Loading(
            parent_className="viz-loading",
            type="default",
            color=theme.ACCENT,
            delay_show=LOADING_DELAY_MS,
            target_components={"main-graph": "figure"},
            overlay_style={"visibility": "visible", "opacity": 0.5},
            children=dcc.Graph(
                id="main-graph",
                style={"width": "100%"},
                config=graph_config(self.spec.plot_filename),
            ),
        )

    # ---- callbacks ---------------------------------------------------------

    def register_callbacks(
        self,
        app: Dash,
        *,
        tubes: Callable[[], Sequence[Any]],
        summary_inputs: Sequence[Input] = (),
        summarize: Callable[..., str] | None = None,
    ) -> None:
        """Wire the strip, caret, summary, per-tube selection and graph refit.

        Args:
            app: the dashboard's Dash app.
            tubes: returns the dataset's tube numbers (for the per-tube view).
            summary_inputs: extra Inputs the summary depends on.
            summarize: ``summarize(view, *summary_input_values) -> str``.
        """
        spec = self.spec

        @app.callback(
            Output("viz-controls-collapse", "is_open"),
            Input("viz-controls-toggle", "n_clicks"),
            Input("view-selector", "value"),
            State("viz-controls-collapse", "is_open"),
            prevent_initial_call=True,
        )
        def sync_controls(_n_clicks, view, is_open):
            # A view change shows its controls (or hides an empty strip, PV-15);
            # the toggle button still opens / closes the strip by hand.
            if ctx.triggered_id == "view-selector":
                return spec.has_controls(view)
            return not is_open

        @app.callback(
            Output("viz-controls-caret", "children"),
            Input("viz-controls-collapse", "is_open"),
            prevent_initial_call=True,
        )
        def show_caret(is_open):
            return CARET_OPEN if is_open else CARET_CLOSED

        if summarize is not None:

            @app.callback(
                Output("viz-controls-summary", "children"),
                Input("view-selector", "value"),
                *summary_inputs,
            )
            def show_summary(view, *values):
                return summarize(view, *values)

        if spec.tube_view is not None:

            @app.callback(
                Output("tube-selector", "value"),
                Input("view-selector", "value"),
                State("tube-selector", "value"),
                prevent_initial_call=True,
            )
            def select_plotted_tube(view, current):
                # The per-tube chart opens on the first tube; show that choice in
                # the dropdown instead of its placeholder, but keep a valid pick.
                available = [int(t) for t in tubes()]
                if view != spec.tube_view or not available or current in available:
                    raise PreventUpdate
                return available[0]

        app.clientside_callback(
            _REFIT_GRAPH_JS,
            Output("viz-resize-dummy", "data"),
            Input("viz-controls-collapse", "is_open"),
            Input("view-selector", "value"),
        )

    def register_export(
        self,
        app: Dash,
        *,
        build_table: Callable[..., dash_export.ExportTable | None],
        states: Sequence[State] = (),
        source_csv: str,
        metric_column: str,
        metric_slug: str,
    ) -> None:
        """Wire "Export CSV": the current view's table plus provenance (PV-16).

        Args:
            app: the dashboard's Dash app.
            build_table: ``build_table(view, *state_values)`` -> the rows behind the
                view, or None when the view has nothing to export.
            states: the selection States ``build_table`` needs.
            source_csv: the metric CSV the dashboard was opened on.
            metric_column: e.g. "Length (mm)".
            metric_slug: file-name prefix, e.g. "root_length".
        """

        @app.callback(
            Output("viz-export-download", "data"),
            Input("viz-export-btn", "n_clicks"),
            State("view-selector", "value"),
            *states,
            prevent_initial_call=True,
        )
        def export_view(_n_clicks, view, *values):
            try:
                table = build_table(view, *values)
            except Exception:  # noqa: BLE001 -- a failed export must not break the page
                logger.exception("Could not export the %s view", view)
                raise PreventUpdate from None
            if table is None:
                raise PreventUpdate
            now = datetime.now().astimezone()
            text = dash_export.render_csv(
                table,
                source_csv=source_csv,
                metric_column=metric_column,
                view=view,
                generated_at=now,
            )
            filename = dash_export.export_filename(metric_slug, view, now)
            return dcc.send_string(text, filename, type="text/csv")

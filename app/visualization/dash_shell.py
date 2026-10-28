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

import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, Patch, State, dcc, html
from dash.exceptions import PreventUpdate

from resources.resource_utils import resource_path

from . import dash_export, theme

logger = logging.getLogger(__name__)

#: ``app/assets`` -- vendored Bootstrap theme, fonts and the SPROUTS stylesheet.
ASSETS_DIR = resource_path("app", "assets")

CARET_OPEN = "▾"
CARET_CLOSED = "▸"
#: Summary of a view without settings (its control strip is not shown at all).
NO_OPTIONS_SUMMARY = "No options for this view"
#: ``style`` of the control strip of a view without settings: no empty disclosure.
STRIP_HIDDEN = {"display": "none"}

#: Show the chart spinner only when a figure takes longer than this, so quick
#: updates do not flash an overlay over the chart.
LOADING_DELAY_MS = 250

#: The Growth Over Time legend of either dashboard: the store holds None (automatic: the
#: dashboard decides from the number of tubes) or the user's choice, which the toggle
#: button flips and which is kept across views. The ids are the length dashboard's.
LEGEND_STORE_ID = "legend-visible-store"
LEGEND_TOGGLE_ID = "toggle-legend-btn"

# The strip, its caret and its visibility change in the browser, in the click's own
# frame. As server callbacks their responses raced the new figure's: the chart was
# drawn at the old size, then again (a full Plotly redraw) once the strip had
# opened or closed.
_SYNC_CONTROLS_JS = """
function(_nClicks, view, isOpen) {
    var triggered = window.dash_clientside.callback_context.triggered || [];
    var byView = triggered.some(function (t) { return t.prop_id === 'view-selector.value'; });
    return byView ? __VIEWS_WITH_CONTROLS__.indexOf(view) >= 0 : !isOpen;
}
"""
_STRIP_VISIBILITY_JS = """
function(view) {
    return __VIEWS_WITH_CONTROLS__.indexOf(view) >= 0 ? {} : __STRIP_HIDDEN__;
}
"""
_CARET_JS = """
function(isOpen) {
    return isOpen ? __CARET_OPEN__ : __CARET_CLOSED__;
}
"""
# The per-tube view plots the dropdown's tube, else the first (plotted_tube); its
# dropdown shows that tube. In the browser: as a server callback, every view switch
# waited for its answer (almost always "no change") before asking for the figure.
_PLOTTED_TUBE_JS = """
function(view, current, options) {
    var tubes = (options || []).map(function (option) { return option.value; });
    if (view !== __TUBE_VIEW__ || !tubes.length || tubes.indexOf(current) >= 0) {
        return window.dash_clientside.no_update;
    }
    return tubes[0];
}
"""

#: Refit the Plotly graph when the control strip opens / closes or the view
#: changes. Targets the real Plotly div (``.js-plotly-plot``), not dcc.Graph's
#: wrapper, which ``Plotly.Plots.resize`` silently ignored (PV-05).
#:
#: Once a plot has autosized, ``Plotly.react`` keeps that size, so a new figure that
#: arrived after the chart card changed size (the strip opened, closed, or grew with
#: the selected-tube chips) was drawn at the old size and redrawn by the resize that
#: followed. Clearing plotly.js's ``_initialAutoSizeIsDone`` now and after every draw
#: makes each react (or resize) measure the card again: a new figure is drawn once.
#: plotly.js only reads the flag; ``test_viz_dash_plotly_bundle`` checks the bundle.
#: The resize that follows then mostly finds the chart at its size already;
#: ``app/assets/sprouts-autosize.js`` answers that relayout at once.
_REFIT_GRAPH_JS = """
function(_isOpen, _view) {
    var gd = document.querySelector('#main-graph .js-plotly-plot');
    if (window.Plotly && gd) {
        var remeasure = function () {
            if (gd._fullLayout) { gd._fullLayout._initialAutoSizeIsDone = false; }
        };
        if (!gd.__sproutsRemeasure && typeof gd.on === 'function') {
            gd.__sproutsRemeasure = true;
            gd.on('plotly_afterplot', remeasure);
        }
        remeasure();
        try { window.Plotly.Plots.resize(gd); } catch (e) {}
    }
    return window.dash_clientside.no_update;
}
"""


def js_literal(value: Any) -> str:
    """``value`` as a JavaScript literal (JSON), for constants inlined in clientside code."""
    return json.dumps(value)


def clientside_js(source: str, **constants: Any) -> str:
    """``source`` with each ``__NAME__`` placeholder replaced by ``constants[name]`` as JSON."""
    for name, value in constants.items():
        source = source.replace(f"__{name.upper()}__", js_literal(value))
    return source


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

    @property
    def views_with_controls(self) -> list[str]:
        """The views whose control strip has settings (the strip is hidden for the others)."""
        return [option.value for option in self.views if option.has_controls]


def create_dash_app(import_name: str, title: str = "SPROUTS") -> Dash:
    """Create a Dash app that serves only local assets.

    ``compress=False``: the server is loopback-only, so compression buys nothing,
    while flask-compress re-compressed the 4.8 MB plotly bundle on every page open
    (17 ms here, up to 300 ms measured) and answered revalidations with a full 200
    instead of 304 (PV-02).
    """
    app = Dash(
        import_name,
        title=title,
        assets_folder=ASSETS_DIR,
        suppress_callback_exceptions=True,
        update_title=None,
        compress=False,
    )
    # Dash keeps the newest app in a module global for dash.get_app() (Dash Pages). The
    # dashboards never use it, and it kept a released dashboard and its data alive.
    holder = sys.modules.get("dash._get_app")
    if getattr(holder, "APP", None) is app:
        holder.APP = None
    # Each new Dash app reads every installed package's entry points for plugin hooks
    # (5-20 ms) because Dash never sets its own read-once flag. The first app has loaded
    # them all, so later dashboards skip the scan.
    hooks = getattr(sys.modules.get("dash._hooks"), "HooksManager", None)
    if getattr(hooks, "_registered", None) is False:
        hooks._registered = True
    return app


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


def plotted_tube(tubes: Sequence[Any], selected_tube: Any) -> int | None:
    """The tube a per-tube view plots: the dropdown's, else the first.

    ``tubes`` are the dataset's tube numbers (``DataCache.tubes``).
    """
    tubes = [int(t) for t in tubes]
    if selected_tube in tubes:
        return int(selected_tube)
    return tubes[0] if tubes else None


def error_outputs(exc: BaseException) -> tuple[go.Figure, str]:
    """(figure, message) shown when a view could not be drawn: the failure instead of a
    stale chart."""
    figure = theme.message_figure(
        f"Error generating visualization: {exc}", color=theme.DANGER, size=16
    )
    return figure, "An error occurred while rendering this view."


def with_uirevision(figure, *key: Any):
    """Tag ``figure`` so Plotly keeps the user's zoom, pan and legend clicks
    across re-renders of the same view (PV-07). A different ``key`` (another
    view or tube) starts from the figure's own axes again."""
    figure.update_layout(uirevision=":".join(str(part) for part in key))
    return figure


def browser_figure(figure: Any) -> Any:
    """What Dash sends of ``figure`` (``figure.to_plotly_json()``), kept on the figure.

    A memoized figure shown again is then not converted again: the conversion copies
    every trace and base64-encodes every array (13 of 15 ms for a 170-tube time view).
    The kept dict stays valid because memoized figures are read-only except their
    ``uirevision`` (:func:`with_uirevision`), which it is kept with. Anything that is
    not a figure (a Patch, ``no_update``) is returned as it is.
    """
    if not isinstance(figure, go.Figure):
        return figure
    revision = figure.layout.uirevision
    kept = getattr(figure, "_sprouts_browser_json", None)
    if kept is None or kept[0] != revision:
        kept = (revision, figure.to_plotly_json())
        figure._sprouts_browser_json = kept
    return kept[1]


# ---- the Growth Over Time legend toggle ------------------------------------------


def legend_store() -> dcc.Store:
    """The legend choice: None (automatic) until the user presses the toggle."""
    return dcc.Store(id=LEGEND_STORE_ID, data=None)


def legend_shown(choice: bool | None, automatic: bool) -> bool:
    """Whether the legend is shown: the user's ``choice``, else ``automatic``."""
    return automatic if choice is None else bool(choice)


def flip_legend(n_clicks: int | None, choice: bool | None, automatic: bool) -> bool | None:
    """The choice after a press of the toggle: the opposite of what is shown (automatic
    or chosen); without a press (page load) the choice stays."""
    if not n_clicks:
        return choice
    return not legend_shown(choice, automatic)


def legend_patch(visible: bool) -> Patch:
    """Show or hide the legend of the figure on screen in place, with the right margin it
    needs: a Patch instead of re-sending every trace (PV-07)."""
    patch = Patch()
    patch["layout"]["showlegend"] = bool(visible)
    patch["layout"]["margin"]["r"] = theme.margin_right(bool(visible))
    return patch


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
        """Collapsible strip holding the view-specific controls.

        A view without settings shows no strip at all (it was a disclosure that
        opened onto nothing, next to "No options for this view").
        """
        shown = self.spec.has_controls(self.spec.default_view)
        return html.Div(
            id="viz-controls",
            className="viz-controls",
            style={} if shown else dict(STRIP_HIDDEN),
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
        summary_inputs: Sequence[Input] = (),
        summarize: Callable[..., str] | None = None,
    ) -> None:
        """Wire the strip, caret, summary, per-tube selection and graph refit.

        The per-tube selection reads the dataset's tubes from the ``tube-selector``
        options, which list them all.

        Args:
            app: the dashboard's Dash app.
            summary_inputs: extra Inputs the summary depends on.
            summarize: ``summarize(view, *summary_input_values) -> str``.
        """
        spec = self.spec
        views = spec.views_with_controls

        # A view change opens its controls, or hides the strip of a view without
        # any (PV-15, W19); the toggle button still opens / closes it by hand. All
        # three run in the browser (see _SYNC_CONTROLS_JS).
        app.clientside_callback(
            clientside_js(_SYNC_CONTROLS_JS, views_with_controls=views),
            Output("viz-controls-collapse", "is_open"),
            Input("viz-controls-toggle", "n_clicks"),
            Input("view-selector", "value"),
            State("viz-controls-collapse", "is_open"),
            prevent_initial_call=True,
        )
        app.clientside_callback(
            clientside_js(
                _STRIP_VISIBILITY_JS, views_with_controls=views, strip_hidden=STRIP_HIDDEN
            ),
            Output("viz-controls", "style"),
            Input("view-selector", "value"),
            prevent_initial_call=True,
        )
        app.clientside_callback(
            clientside_js(_CARET_JS, caret_open=CARET_OPEN, caret_closed=CARET_CLOSED),
            Output("viz-controls-caret", "children"),
            Input("viz-controls-collapse", "is_open"),
            prevent_initial_call=True,
        )

        if summarize is not None:

            @app.callback(
                Output("viz-controls-summary", "children"),
                Input("view-selector", "value"),
                *summary_inputs,
            )
            def show_summary(view, *values):
                return summarize(view, *values)

        if spec.tube_view is not None:
            # The per-tube chart opens on the first tube; show that choice in the
            # dropdown instead of its placeholder, but keep a valid pick.
            app.clientside_callback(
                clientside_js(_PLOTTED_TUBE_JS, tube_view=spec.tube_view),
                Output("tube-selector", "value"),
                Input("view-selector", "value"),
                State("tube-selector", "value"),
                State("tube-selector", "options"),
                prevent_initial_call=True,
            )

        app.clientside_callback(
            _REFIT_GRAPH_JS,
            Output("viz-resize-dummy", "data"),
            Input("viz-controls-collapse", "is_open"),
            Input("view-selector", "value"),
        )

    def register_legend_toggle(
        self,
        app: Dash,
        *,
        automatic: Callable[..., bool],
        states: Sequence[State] = (),
    ) -> None:
        """Wire the legend toggle (:data:`LEGEND_TOGGLE_ID`): each press stores the
        opposite of what is shown in :data:`LEGEND_STORE_ID` (see :func:`flip_legend`).

        Args:
            app: the dashboard's Dash app.
            automatic: ``automatic(*state_values)`` -> whether the legend is shown while
                nobody chose (e.g. up to 30 tubes).
            states: the selection States ``automatic`` needs.
        """

        @app.callback(
            Output(LEGEND_STORE_ID, "data"),
            Input(LEGEND_TOGGLE_ID, "n_clicks"),
            State(LEGEND_STORE_ID, "data"),
            *states,
            prevent_initial_call=True,
        )
        def toggle_legend(n_clicks, choice, *values):
            return flip_legend(n_clicks, choice, automatic(*values))

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

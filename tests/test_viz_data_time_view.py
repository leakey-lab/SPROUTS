"""viz-data: the Growth Over Time figures (R-VIZ X3, X4).

X4: the time views are assembled as plain dicts and validated once by ``go.Figure``.
Building one ``go.Scatter`` per tube and validating everything again in
``go.Figure(data=traces)`` took 80 ms for 160 tubes (29 ms now; area 57 -> ~20 ms), on
the server thread and holding the GIL. The browser must get the same figure: the
Dash JSON of each view is compared with the one the object-built figure produced.

X3: above :data:`AUTO_LEGEND_MAX_TUBES` tubes the length time view starts without its
legend (a 160-entry legend was ~200 ms of a ~670 ms draw, and ten colours cannot
tell 160 tubes apart; the hover names each one). "Toggle Legend" still shows it,
and an explicit choice is kept.
"""

from __future__ import annotations

import json

import plotly.graph_objects as go
import pytest
from dash._utils import to_json

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.data_processing.data_processor_area import DataProcessorArea
from app.visualization import theme
from app.visualization.dash_app_area import DashAppArea
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_visualizations import AUTO_LEGEND_MAX_TUBES, DashVisualizations
from tests.test_viz_data_cache import write_metric_csv
from tests.viz_dash_helpers import build_length_app, call_callback

DATES = ("2024.06.03", "2024.06.04", "2024.06.24", "2024.07.16")


def _rows(tubes: int):
    rows = []
    for tube in range(1, tubes + 1):
        for d, date in enumerate(DATES):
            if (tube + d) % 5 == 0:
                continue  # some tubes skip a date
            for position in (5, 15, 27):
                rows.append(
                    (tube, position, date, round(((tube * 7 + d * 3 + position) % 23) / 2, 2))
                )
    return rows


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


@pytest.fixture
def charts(tmp_path):
    csv = write_metric_csv(tmp_path / "l.csv", LENGTH_CSV_HEADERS, _rows(40))
    return _Charts(DataProcessor(csv))


@pytest.fixture
def area(tmp_path):
    csv = write_metric_csv(tmp_path / "a.csv", AREA_CSV_HEADERS, _rows(40))
    return DashAppArea(DataProcessorArea(csv), str(tmp_path))


def _json(fig) -> dict:
    """What the browser receives (Dash's serializer), parsed."""
    return json.loads(to_json(fig))


# ---- the object-built figures the dict builders must reproduce --------------------


def _reference_length(charts, tubes, show_field_average, show_field_variance, show_legend):
    label, unit = charts._metric_text()
    traces = []
    if show_field_average:
        field = charts.data_cache.get_field_growth()
        if show_field_variance and field["std"].notna().any():
            traces.append(
                go.Scatter(
                    x=field["Date"],
                    y=field["mean"] + field["std"],
                    mode="lines",
                    line=dict(color="rgba(0,0,0,0)"),
                    showlegend=False,
                    hoverinfo="skip",
                    legendgroup="field_avg",
                )
            )
            traces.append(
                go.Scatter(
                    x=field["Date"],
                    y=field["mean"] - field["std"],
                    mode="lines",
                    fill="tonexty",
                    fillcolor=theme.rgba(theme.TEXT, 0.10),
                    line=dict(color="rgba(0,0,0,0)"),
                    name="Field Variance (±1 SD)",
                    legendgroup="field_avg",
                    hovertemplate=theme.hover(
                        "Field Variance Range",
                        [("Date", "%{x|%b %d, %Y}"), ("Range", "+-1 Standard Deviation")],
                    ),
                )
            )
        traces.append(
            go.Scatter(
                x=field["Date"],
                y=field["mean"],
                mode="lines+markers",
                name="Field Average",
                line=dict(color=theme.TEXT, width=3),
                marker=dict(size=8, color=theme.TEXT),
                legendgroup="field_avg",
                hovertemplate=theme.hover(
                    "Field Average",
                    [
                        ("Date", "%{x|%b %d, %Y}"),
                        (f"Avg {label}", f"%{{y:.2f}} {unit}"),
                        ("Std Dev", f"+/-%{{customdata[0]:.2f}} {unit}"),
                        ("Tubes", "%{customdata[1]:.0f}"),
                    ],
                ),
                customdata=field[["std", "count"]].values,
            )
        )
    for idx, tube in enumerate(tubes):
        totals = charts.data_cache.get_tube_totals(tube)
        if totals.empty:
            continue
        traces.append(
            go.Scatter(
                x=totals.index,
                y=totals,
                mode="lines+markers",
                name=f"Tube {int(tube)}",
                line=dict(color=theme.color(idx), width=2),
                marker=dict(size=6),
                hovertemplate=theme.hover(
                    f"Tube {int(tube)}",
                    [("Date", "%{x|%b %d, %Y}"), (f"Total {label}", f"%{{y:.2f}} {unit}")],
                ),
            )
        )
    fig = go.Figure(data=traces)
    fig.update_layout(
        title_text="Root Growth Over Time",
        xaxis_title_text="Date",
        yaxis_title_text=f"Total {label} ({unit})",
        showlegend=show_legend,
        margin_r=180 if show_legend else 24,  # X3: no empty band for a hidden legend
        hovermode="closest",
    )
    return fig


def _reference_area(area):
    fig = go.Figure()
    for tube in area.data_cache.tubes:
        totals = area.data_cache.get_tube_totals(tube)
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


# ---- X4: same figure, built once ---------------------------------------------------


@pytest.mark.parametrize(
    "tubes, field, variance, legend",
    [
        ((1, 2, 3), True, True, True),
        ((1, 2, 3), True, False, False),
        ((), True, True, True),
        ((4, 5), False, False, True),
        (tuple(range(1, 41)), True, True, False),
        ((99,), True, True, True),  # a tube without data draws nothing for it
    ],
)
def test_the_length_time_figure_is_unchanged(charts, tubes, field, variance, legend):
    built = charts._growth_over_time_figure(tubes, field, variance, legend)

    assert _json(built) == _json(_reference_length(charts, tubes, field, variance, legend))


def test_the_area_time_figure_is_unchanged(area):
    assert _json(area._growth_over_time_figure()) == _json(_reference_area(area))


def test_the_time_figures_are_plotly_figures_the_memo_and_callers_can_use(charts, area):
    # Built from dicts and validated once by go.Figure (a misspelt property raises).
    for fig in (
        charts._growth_over_time_figure((1, 2), True, True, True),
        area.show_growth_over_time(),
    ):
        assert isinstance(fig, go.Figure)
        assert {trace.type for trace in fig.data} == {"scatter"}
        assert fig.layout.template.layout.paper_bgcolor == theme.BG_0  # the SPROUTS theme


# ---- X3: the legend of many tubes -------------------------------------------------


def test_the_legend_is_shown_for_a_few_tubes_and_hidden_for_many(charts):
    few = tuple(range(1, AUTO_LEGEND_MAX_TUBES + 1))
    many = tuple(range(1, AUTO_LEGEND_MAX_TUBES + 2))

    assert charts.show_growth_over_time(few, True, True, None).layout.showlegend is True
    assert charts.show_growth_over_time(many, True, True, None).layout.showlegend is False
    assert AUTO_LEGEND_MAX_TUBES == 30


def test_a_hidden_legend_leaves_no_empty_band_beside_the_plot(charts):
    many = tuple(range(1, 41))

    assert charts.show_growth_over_time(many, True, True, None).layout.margin.r == 24
    assert charts.show_growth_over_time(many, True, True, True).layout.margin.r == 180


@pytest.mark.parametrize("choice", [True, False])
def test_an_explicit_legend_choice_is_kept(charts, choice):
    for tubes in ((1, 2), tuple(range(1, 41))):
        assert charts.show_growth_over_time(tubes, True, True, choice).layout.showlegend is choice


def test_the_legend_starts_automatic_on_the_page(tmp_path):
    from tests.viz_dash_helpers import walk_components

    layout = build_length_app(tmp_path).app.layout
    store = next(
        c for c in walk_components(layout) if getattr(c, "id", None) == "legend-visible-store"
    )
    assert store.data is None  # automatic: shown up to AUTO_LEGEND_MAX_TUBES tubes


@pytest.mark.parametrize(
    "selected, current, expected",
    [
        (list(range(1, 41)), None, True),  # hidden automatically: the toggle shows it
        ([1, 2], None, False),  # shown automatically: the toggle hides it
        (list(range(1, 41)), True, False),
        ([1, 2], False, True),
    ],
)
def test_toggle_legend_flips_what_is_shown(tmp_path, selected, current, expected):
    dash = build_length_app(tmp_path).app
    _, out = call_callback(
        dash,
        "legend-visible-store.data",
        {"toggle-legend-btn.n_clicks": 1, "legend-visible-store.data": current,
         "selected-tubes-store.data": selected},
        changed=["toggle-legend-btn.n_clicks"],
    )  # fmt: skip
    assert out["legend-visible-store"]["data"] is expected


def test_the_page_draws_the_time_view_with_the_automatic_legend(tmp_path):
    dash = build_length_app(tmp_path).app
    _, out = call_callback(
        dash,
        "main-graph.figure",
        {"view-selector.value": "time", "selected-tubes-store.data": [1, 2],
         "growth-viz-options.value": ["show_field_avg"], "legend-visible-store.data": None},
        changed=["view-selector.value"],
    )  # fmt: skip
    assert out["main-graph"]["figure"]["layout"]["showlegend"] is True

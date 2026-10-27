"""The area dashboard's Growth Over Time legend: automatic, with a toggle (W4-SMALL S4).

The view draws one line per tube and kept a legend entry for each: 160 entries were about
200 ms of each ~660 ms draw, and the view had no way to hide them. It now does what the
length time view does since wave 2 (R-VIZ X3): the legend shows by itself up to
``AUTO_LEGEND_MAX_TUBES`` (30) tubes, "Toggle Legend" shows or hides it in place (a Patch,
not every trace again), and the choice is kept across views. The button floats over the
chart's top-left margin on that view only, so the view still has no control strip (a
strip opening or closing resizes the chart, a full redraw: measured, it took back most of
the saving). Shown, the figure is the one the view drew before.

The callbacks run through Dash's real dispatch (``tests/viz_dash_helpers``).
"""

from __future__ import annotations

import json

import pytest
from dash._utils import to_json

from app.config import AREA_CSV_HEADERS
from app.data_processing.data_processor_area import DataProcessorArea
from app.visualization import theme
from app.visualization.dash_app_area import AREA_SHELL, DashAppArea
from app.visualization.dash_shell import (
    LEGEND_STORE_ID,
    LEGEND_TOGGLE_ID,
    flip_legend,
    legend_shown,
)
from app.visualization.dash_visualizations import AUTO_LEGEND_MAX_TUBES
from tests.test_viz_data_cache import write_metric_csv
from tests.viz_dash_helpers import (
    call_callback,
    call_clientside,
    component_ids,
    walk_components,
)

FIGURE = "main-graph.figure"
DATES = ("2024.06.03", "2024.07.16")


def _app(tmp_path, tubes: int) -> DashAppArea:
    rows = [
        (tube, position, date, round(0.5 + tube * 0.1 + d, 2))
        for tube in range(1, tubes + 1)
        for d, date in enumerate(DATES)
        for position in (5, 15)
    ]
    csv = write_metric_csv(tmp_path / f"root_areas_{tubes}.csv", AREA_CSV_HEADERS, rows)
    return DashAppArea(DataProcessorArea(csv), str(tmp_path))


@pytest.fixture
def few(tmp_path):
    return _app(tmp_path, AUTO_LEGEND_MAX_TUBES)


@pytest.fixture
def many(tmp_path):
    return _app(tmp_path, AUTO_LEGEND_MAX_TUBES + 1)


def _json(figure) -> dict:
    return json.loads(to_json(figure))


def _time_view(dashboard, legend=None, changed=("view-selector.value",)) -> tuple[int, dict]:
    values = {"view-selector.value": "time", f"{LEGEND_STORE_ID}.data": legend}
    return call_callback(dashboard.app, FIGURE, values, list(changed))


def _layout(out) -> dict:
    return out["main-graph"]["figure"]["layout"]


# ---- the figure ------------------------------------------------------------------


def test_the_legend_shows_by_itself_up_to_30_tubes(few, many):
    assert few.show_growth_over_time().layout.showlegend is None  # the theme's: shown
    hidden = many.show_growth_over_time().layout
    assert hidden.showlegend is False
    assert hidden.margin.r == theme.MARGIN_RIGHT_NO_LEGEND  # no empty band beside the plot


def test_a_shown_legend_is_the_figure_the_view_drew_before(many):
    before = _json(many._growth_over_time_figure())  # the default: shown

    assert _json(many.show_growth_over_time(show_legend=True)) == before
    hidden = _json(many.show_growth_over_time(show_legend=False))
    assert hidden["data"] == before["data"]  # the same lines either way


def test_an_explicit_choice_wins_over_the_automatic_one(few, many):
    assert few.show_growth_over_time(show_legend=False).layout.showlegend is False
    assert many.show_growth_over_time(show_legend=True).layout.showlegend is None


# ---- the toggle --------------------------------------------------------------------


@pytest.mark.parametrize(
    "clicks, choice, automatic, expected",
    [
        (0, None, True, None),  # page load: nobody chose
        (1, None, False, True),  # 31+ tubes: the first press shows it
        (1, None, True, False),  # up to 30: the first press hides it
        (2, True, False, False),
        (3, False, True, True),
    ],
)
def test_a_press_stores_the_opposite_of_what_is_shown(clicks, choice, automatic, expected):
    assert flip_legend(clicks, choice, automatic) is expected
    assert legend_shown(None, automatic) is automatic


def test_the_toggle_button_flips_the_stored_choice(many):
    values = {f"{LEGEND_TOGGLE_ID}.n_clicks": 1, f"{LEGEND_STORE_ID}.data": None}
    _, out = call_callback(
        many.app, f"{LEGEND_STORE_ID}.data", values, [f"{LEGEND_TOGGLE_ID}.n_clicks"]
    )
    assert out[LEGEND_STORE_ID]["data"] is True  # hidden for 31 tubes: now shown

    values = {f"{LEGEND_TOGGLE_ID}.n_clicks": 2, f"{LEGEND_STORE_ID}.data": True}
    _, out = call_callback(
        many.app, f"{LEGEND_STORE_ID}.data", values, [f"{LEGEND_TOGGLE_ID}.n_clicks"]
    )
    assert out[LEGEND_STORE_ID]["data"] is False


@pytest.mark.parametrize("visible", [True, False])
def test_a_new_choice_patches_the_legend_in_place(many, visible):
    status, out = _time_view(many, visible, changed=[f"{LEGEND_STORE_ID}.data"])

    assert status == 200
    patch = out["main-graph"]["figure"]
    assert "data" not in json.dumps(patch).replace("__dash_patch_update", "")  # no traces
    operations = {tuple(op["location"]): op["params"]["value"] for op in patch["operations"]}
    assert operations[("layout", "showlegend")] is visible
    assert operations[("layout", "margin", "r")] == theme.margin_right(visible)


def test_a_choice_made_elsewhere_changes_nothing_on_another_view(many):
    values = {"view-selector.value": "stacked", f"{LEGEND_STORE_ID}.data": True}
    status, _ = call_callback(many.app, FIGURE, values, [f"{LEGEND_STORE_ID}.data"])

    assert status == 204


@pytest.mark.parametrize(
    "view, title",
    [
        ("stacked", "Root Area by Tube and Date"),
        ("time", "Root Area Growth Over Time"),
        ("profile", "Root Area Depth Profile - Tube 1"),
    ],
)
def test_the_page_load_call_draws_the_chart(many, view, title):
    # On page load the legend store (data None) re-sends its data as it mounts, and Dash
    # then names it as the only trigger. That is no press: the view must be drawn (the
    # Totals by Tube chart stayed empty at every open).
    values = {"view-selector.value": view, f"{LEGEND_STORE_ID}.data": None}
    status, out = call_callback(many.app, FIGURE, values, [f"{LEGEND_STORE_ID}.data"])

    assert status == 200
    figure = out["main-graph"]["figure"]
    assert "__dash_patch_update" not in figure  # the whole figure, not a legend Patch
    assert figure["data"]
    assert figure["layout"]["title"]["text"] == title
    assert out == call_callback(many.app, FIGURE, values, [])[1]  # as an untriggered draw


def test_the_choice_is_kept_when_the_view_comes_back(many):
    _, out = _time_view(many, legend=True)  # chosen earlier, then another view, then back
    assert _layout(out).get("showlegend") in (None, True)
    assert _layout(out).get("margin", {}).get("r") != theme.MARGIN_RIGHT_NO_LEGEND

    _, out = _time_view(many, legend=None)  # nobody chose: automatic
    assert _layout(out)["showlegend"] is False


# ---- the button --------------------------------------------------------------------


@pytest.mark.parametrize("view, shown", [("time", True), ("stacked", False), ("profile", False)])
def test_the_button_shows_on_the_time_view_only(many, view, shown):
    out, _ = call_clientside(
        many.app,
        f"{LEGEND_TOGGLE_ID}.style",
        {"view-selector.value": view},
        ["view-selector.value"],
    )
    style = out[LEGEND_TOGGLE_ID]["style"]
    assert (style["display"] != "none") is shown
    assert style["position"] == "absolute"  # over the chart's margin: it never resizes it


def test_the_page_starts_without_the_button_and_the_time_view_has_no_strip(many):
    layout = many.app.layout
    assert {LEGEND_TOGGLE_ID, LEGEND_STORE_ID} <= component_ids(layout)
    button = next(c for c in walk_components(layout) if getattr(c, "id", None) == LEGEND_TOGGLE_ID)
    assert button.style["display"] == "none"  # the default view is the stacked bars
    assert not AREA_SHELL.has_controls("time")
    strip = next(c for c in walk_components(layout) if getattr(c, "id", None) == "viz-controls")
    assert LEGEND_TOGGLE_ID not in component_ids(strip)

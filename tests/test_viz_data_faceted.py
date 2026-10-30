"""Faceted depth profile: pure data preparation + one-pass figure assembly (PV-08, AR-15).

Expected values are derived by hand from the fixture in test_viz_data_cache:

    date A: tube 1 bins 10 -> {2, 4}, 20 -> {}, 30 -> {10};  tube 2 bin 20 -> {6, 8}
            field (all tubes): 10 -> mean 3 (std sqrt 2), 20 -> 7 (sqrt 2), 30 -> 10 (one row)
    date B: tube 1 bins 10 -> {1}, 20 -> {3};  tube 2 bin 10 -> {5}
            field: 10 -> {1, 5} mean 3 (std sqrt 8), 20 -> {3} mean 3 (one row)

Largest bar = 10 (tube 1, date A, bin 30) -> x range +-20 (10 * 1.1 rounded up to 10s);
deepest bin = 30 -> y range [40, 0].
"""

import math

import pandas as pd
import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.visualization.dash_data_cache import DataCache
from app.visualization.faceted_profile import (
    FacetCell,
    FacetedProfile,
    build_faceted_profile,
    faceted_profile_figure,
)
from tests.test_viz_data_cache import write_metric_csv

A = pd.Timestamp("2024-07-18")
B = pd.Timestamp("2024-08-07")


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    path = write_metric_csv(tmp_path_factory.mktemp("faceted") / "l.csv", LENGTH_CSV_HEADERS)
    return DataCache(DataProcessor(path))


def test_profile_cells_hold_measured_bins_and_field_means(cache):
    profile = build_faceted_profile(cache, [1, 2])

    assert profile.dates == (A, B)
    assert profile.tubes == (1, 2)
    tube1_a, tube2_a = profile.cells[0]
    assert tube1_a.interval_ends == (10, 30)  # the empty L11-20 bin is not drawn
    assert tube1_a.interval_starts == (1, 21)
    assert tube1_a.tube_means == pytest.approx((3.0, 10.0))
    assert tube1_a.tube_counts == (2, 1)
    assert tube1_a.field_means == pytest.approx((3.0, 10.0))
    assert tube1_a.field_stds == pytest.approx((math.sqrt(2), 0.0))  # single row -> 0
    assert tube1_a.depth_fractions == pytest.approx((0.0, 1.0))  # over the L1-L30 span
    assert tube2_a.interval_ends == (20,)
    assert tube2_a.depth_fractions == (0.5,)  # a single bin sits mid-scale
    tube1_b, tube2_b = profile.cells[1]
    assert tube1_b.field_means == pytest.approx((3.0, 3.0))
    assert tube2_b.field_stds == pytest.approx((math.sqrt(8),))
    assert profile.max_value == pytest.approx(10.0)
    assert profile.max_depth == 30


def test_profile_keeps_dates_where_any_selected_tube_has_data(cache):
    profile = build_faceted_profile(cache, [2, 3], [B, A])

    assert profile.dates == (A, B)  # sorted
    assert [cell.has_data for cell in profile.cells[0]] == [True, False]  # tube 3 unmeasured
    assert build_faceted_profile(cache, [3]).dates == ()


def test_figure_draws_one_panel_per_cell_with_shared_scales(cache):
    fig = faceted_profile_figure(build_faceted_profile(cache, [1, 2]), label="Length", unit="mm")
    layout = fig.layout

    assert len(layout.shapes) == 4  # one background panel per subplot
    assert {shape.xref for shape in layout.shapes} == {
        "x domain",
        "x2 domain",
        "x3 domain",
        "x4 domain",
    }
    for key in ("xaxis", "xaxis2", "xaxis3", "xaxis4"):
        axis = layout[key]
        assert tuple(axis.range) == (-20, 20)
        assert list(axis.tickvals) == [-20, -10, 0, 10, 20]
        assert list(axis.ticktext) == ["", "10", "0", "10", ""]
        assert axis.showticklabels == (key in ("xaxis3", "xaxis4"))  # bottom row only
    for key in ("yaxis", "yaxis2", "yaxis3", "yaxis4"):
        assert tuple(layout[key].range) == (40, 0)
        assert list(layout[key].tickvals) == [0, 20, 40]
        assert list(layout[key].ticktext) == ["", "20", ""]
    assert layout.yaxis.title.text == "Vertical Depth (cm)"
    assert layout.height == 600
    assert layout.barmode == "overlay"
    titles = [a.text for a in layout.annotations]
    assert titles == ["Tube 1", "Tube 2", "2024-07-18", "2024-08-07"]


def test_figure_traces_follow_cells_in_row_major_order(cache):
    fig = faceted_profile_figure(build_faceted_profile(cache, [1, 3]), label="Length", unit="mm")

    kinds = [(t.name or t.mode, t.xaxis) for t in fig.data]
    assert kinds == [
        ("Field Avg", "x"),
        ("Tube", "x"),
        ("text", "x2"),  # tube 3 has no data on date A
        ("Field Avg", "x3"),
        ("Tube", "x3"),
        ("text", "x4"),
    ]
    legend = [(t.name, t.showlegend) for t in fig.data if t.name]
    assert legend == [("Field Avg", True), ("Tube", True), ("Field Avg", False), ("Tube", False)]
    no_data = fig.data[2]
    assert list(no_data.text) == ["No Data"] and no_data.hoverinfo == "skip"


def test_figure_hover_carries_interval_and_counts(cache):
    fig = faceted_profile_figure(build_faceted_profile(cache, [1], [A]), label="Area", unit="mm²")

    field, tube = fig.data
    assert [list(row) for row in field.customdata] == [
        [1, 10, pytest.approx(3.0), pytest.approx(math.sqrt(2))],
        [21, 30, pytest.approx(10.0), 0.0],
    ]
    assert [list(row) for row in tube.customdata] == [[1, 10, 2], [21, 30, 1]]
    assert tube.hovertemplate.startswith("<b>Tube 1 - 2024-07-18</b>")
    assert "Avg Area: %{x:.2f} mm²" in tube.hovertemplate
    assert "Count: %{customdata[2]}" in tube.hovertemplate


def _single_bar_profile(value: float) -> FacetedProfile:
    cell = FacetCell(
        tube=1,
        date=A,
        interval_ends=(10,),
        interval_starts=(1,),
        tube_means=(value,),
        tube_counts=(1,),
        field_means=(value,),
        field_stds=(0.0,),
        depth_fractions=(0.5,),
    )
    return FacetedProfile(tubes=(1,), dates=(A,), cells=((cell,),), max_value=value, max_depth=10)


@pytest.mark.parametrize(
    "value, tickvals, ticktext",
    [
        # 5 * 1.1 -> +-10: a 10-step would label only "0" (outer ticks are hidden)
        (5.0, [-10, -5, 0, 5, 10], ["", "5", "0", "5", ""]),
        # 250 * 1.1 -> +-280: a 10-step would crowd 57 ticks into each panel
        (
            250.0,
            list(range(-250, 251, 50)),
            ["", "200", "150", "100", "50", "0"] + ["50", "100", "150", "200", ""],
        ),
    ],
)
def test_x_ticks_keep_a_readable_scale(value, tickvals, ticktext):
    axis = faceted_profile_figure(_single_bar_profile(value)).layout.xaxis

    assert list(axis.tickvals) == tickvals
    assert list(axis.ticktext) == ticktext


# ---- R-VIZ X4: the grid is make_subplots' grid, without make_subplots ---------------


def _make_subplots_layout(column_titles, row_titles) -> dict:
    """The layout make_subplots builds for the grid (what the assembler used before)."""
    import json

    from plotly.subplots import make_subplots

    skeleton = make_subplots(
        rows=len(row_titles),
        cols=len(column_titles),
        shared_xaxes=False,
        shared_yaxes=True,
        vertical_spacing=0.005,
        horizontal_spacing=0.008,
        subplot_titles=[
            title if row == 0 else "" for row in range(len(row_titles)) for title in column_titles
        ],
        row_titles=list(row_titles),
    )
    layout = json.loads(skeleton.layout.to_json())
    layout.pop("template", None)
    return layout


def _assert_equal_up_to_rounding(got, expected, path="layout"):
    """The same keys, texts and integers; floats within 1e-12. Python 3.12's ``sum()``
    adds make_subplots' floats with compensation, up to 4 units in the last place away from
    the plain sums the grid keeps (``PYTHON_310_GRIDS``)."""
    if isinstance(expected, dict):
        assert isinstance(got, dict) and sorted(got) == sorted(expected), path
        for key, value in expected.items():
            _assert_equal_up_to_rounding(got[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(got, list) and len(got) == len(expected), path
        for i, (item, value) in enumerate(zip(got, expected, strict=True)):
            _assert_equal_up_to_rounding(item, value, f"{path}[{i}]")
    elif isinstance(expected, float):
        assert isinstance(got, int | float) and not isinstance(got, bool), path
        assert math.isclose(got, expected, rel_tol=0.0, abs_tol=1e-12), (path, got, expected)
    else:
        assert type(got) is type(expected) and got == expected, path


@pytest.mark.parametrize(
    "rows, cols",
    [(1, 1), (1, 6), (2, 2), (3, 5), (4, 3), (6, 6), (11, 6), (24, 6), (40, 6)],
)
def test_the_grid_layout_is_the_make_subplots_grid(rows, cols):
    # make_subplots is the oracle: a plotly upgrade that changes its grid shows up here
    # (up to rounding: the grid keeps Python 3.10's float sums, pinned below).
    import json

    from app.visualization.faceted_profile import grid_layout

    columns = [f"Tube {c + 1}" for c in range(cols)]
    dates = [f"2024-06-{r + 1:02d}" for r in range(rows)]

    first = grid_layout(columns, dates)
    again = grid_layout([f"Tube {c + 11}" for c in range(cols)], dates)

    _assert_equal_up_to_rounding(
        json.loads(json.dumps(first)), _make_subplots_layout(columns, dates)
    )
    assert [a["text"] for a in again["annotations"]][:cols] == [
        f"Tube {c + 11}" for c in range(cols)
    ]
    first["xaxis"]["range"] = [0, 1]  # every call returns a fresh layout
    assert "range" not in grid_layout(columns, dates)["xaxis"]


#: SHA-256 of ``json.dumps(grid_layout(...), sort_keys=True)`` as Python 3.10.11 gave it
#: (``sum()`` adding floats left to right). With Python 3.12's compensated ``sum()``
#: every one of these grids had other floats in the last place (panel domains, titles).
PYTHON_310_GRIDS = {
    (9, 1): "c4bbfd2b1d23b2cf91994716e7b6cebf4689798dfd0f95c1fd924ec801b419be",
    (9, 6): "4c303bb074995771e3b41582fa908e079ad47101ef442c816c7c4ff4e1b8ffa0",
    (11, 6): "6a21e7e33d403f4133052f59951dc484f48381755df50d3ebe086af72525ceda",
    (12, 3): "68904fac89a1404e50b3a035f24bfb295b0ec923aeea2e14a8bd9704d8d2cb5f",
    (17, 5): "112161441ed1470e26a2ca2cae6e503197d57e928091b42b6f08eb8b14b17690",
    (25, 6): "36824683d6f702d999e50ff408c73f5b6357ada857d15756590d82b16a1ed7ac",
    (40, 6): "afc73f0d4bec832243409e227431f08b5f8bdfd98af6a82fed90d24b04725258",
}


@pytest.mark.parametrize("rows, cols", list(PYTHON_310_GRIDS))
def test_the_grid_layout_is_the_json_python_3_10_gave(rows, cols):
    # The Depth Profile Grid's figure JSON must not change with the Python version.
    import hashlib
    import json

    from app.visualization.faceted_profile import grid_layout

    columns = [f"Tube {c + 1}" for c in range(cols)]
    dates = [f"2024-06-{r + 1:02d}" for r in range(rows)]
    text = json.dumps(grid_layout(columns, dates), sort_keys=True)

    assert hashlib.sha256(text.encode()).hexdigest() == PYTHON_310_GRIDS[rows, cols]


def test_a_nine_date_grid_places_its_panels_as_python_3_10_did():
    # Python 3.12's sum() gives 0.9466666666666667 and 0.8933333333333333 here.
    from app.visualization.faceted_profile import grid_layout

    layout = grid_layout(["Tube 1"], [f"2024-06-{r + 1:02d}" for r in range(9)])

    assert layout["annotations"][1]["y"] == 0.9466666666666668  # the first date's title
    assert layout["yaxis"]["domain"] == [0.8933333333333334, 1.0]


@pytest.mark.parametrize("rows, cols", [(202, 1), (3, 127), (0, 1), (1, 0)])
def test_a_grid_make_subplots_refuses_fails_with_its_message(rows, cols):
    # 202 or more dates with data: make_subplots' spacing check, so the dashboard shows
    # the same "Error: ..." as before instead of drawing panels with inverted domains.
    from plotly.subplots import make_subplots

    from app.visualization.faceted_profile import grid_layout

    with pytest.raises(ValueError) as expected:
        make_subplots(
            rows=rows, cols=cols, shared_yaxes=True, vertical_spacing=0.005,
            horizontal_spacing=0.008,
        )  # fmt: skip
    with pytest.raises(ValueError) as got:
        grid_layout([f"Tube {c + 1}" for c in range(cols)], [f"d{r}" for r in range(rows)])
    assert str(got.value) == str(expected.value)


def test_the_faceted_figure_is_what_validation_makes_of_its_dicts(cache, monkeypatch):
    # The figure is built with validation off (go.Figure(..., _validate=False)): the
    # browser must get what a validating go.Figure makes of the same dicts (a misspelt
    # property raises there), with the template itself, not its name: plotly.js ignores
    # a template given as a string.
    import json

    import plotly.graph_objects as go
    from dash._utils import to_json

    from app.visualization import theme

    profile = build_faceted_profile(cache, [1, 3])
    unvalidated = json.loads(to_json(faceted_profile_figure(profile)))
    init = go.Figure.__init__

    def validating(self, *args, **kwargs):
        kwargs.pop("_validate", None)
        init(self, *args, **kwargs)

    monkeypatch.setattr(go.Figure, "__init__", validating)
    validated = json.loads(to_json(faceted_profile_figure(profile)))

    assert unvalidated == validated
    assert unvalidated["layout"]["template"]["layout"]["paper_bgcolor"] == theme.BG_0


def test_the_faceted_figure_is_unchanged_by_the_direct_grid(cache, monkeypatch):
    import json

    from dash._utils import to_json

    import app.visualization.faceted_profile as fp

    profile = build_faceted_profile(cache, [1, 2])
    cached = json.loads(to_json(faceted_profile_figure(profile)))
    monkeypatch.setattr(
        fp,
        "grid_layout",
        lambda columns, rows: {**_make_subplots_layout(columns, rows), "template": "sprouts"},
    )
    direct = json.loads(to_json(faceted_profile_figure(profile)))

    assert cached == direct

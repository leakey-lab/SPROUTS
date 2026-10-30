"""Length dashboard view model and layout builders, called without a Dash server (CQ-07).

``dash_length_views`` turns the callback Inputs into outputs and ``dash_length_layout``
builds the page; both are plain functions since the split of ``dash_app.py``. The
callbacks call them directly, so a callback makes no extra call through ``DashApp``.
"""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import pytest
from dash import no_update
from dash.exceptions import PreventUpdate

from app.visualization import dash_length_layout as layout
from app.visualization import dash_length_views as views
from app.visualization import theme
from app.visualization.dash_shell import NO_OPTIONS_SUMMARY
from app.visualization.faceted_profile import MAX_TUBES
from tests.viz_dash_helpers import build_length_app, call_callback, component_ids


def _dashboard(tubes=(1, 2, 3)):
    """What the view model reads of a dashboard, with recording figure builders."""
    calls = []

    def builder(name):
        def build(*args, **kwargs):
            calls.append((name, args, kwargs))
            return go.Figure()

        return build

    return SimpleNamespace(
        data_cache=SimpleNamespace(tubes=tuple(tubes)),
        show_growth_over_time=builder("time"),
        show_growth_lines=builder("lines"),
        create_stacked_bar_chart=builder("stacked"),
        create_faceted_depth_profile=builder("faceted"),
        calls=calls,
    )


def _annotation(figure) -> str:
    (note,) = figure.layout.annotations
    return note.text


# ---- lines_tube / controls_summary --------------------------------------------------


@pytest.mark.parametrize(
    "selected, expected", [(2, 2), (2.0, 2), (None, 1), (99, 1), ("2", 1)]
)  # fmt: skip
def test_lines_tube_is_the_dropdowns_tube_else_the_first(selected, expected):
    assert views.lines_tube((1, 2, 3), selected) == expected


def test_lines_tube_without_tubes_is_none():
    assert views.lines_tube((), 1) is None


@pytest.mark.parametrize(
    "view, values, expected",
    [
        ("lines", (2, None, None, None), "Tube 2"),
        ("lines", (None, None, None, None), "Tube 1"),
        ("time", (None, [1, 2], None, None), "2 tubes selected"),
        ("time", (None, [5], None, None), "1 tube selected"),
        ("time", (None, None, None, None), "No tubes selected"),
        ("faceted", (None, None, [1, 2], None), f"2/{MAX_TUBES} tubes · all dates"),
        ("faceted", (None, None, [1], ["2024-07-18"]), f"1/{MAX_TUBES} tubes · 1 date"),
        ("stacked", (1, [1], [1], []), NO_OPTIONS_SUMMARY),
        (None, (None, None, None, None), NO_OPTIONS_SUMMARY),
    ],
)
def test_controls_summary_describes_each_view(view, values, expected):
    assert views.controls_summary(_dashboard(), view, *values) == expected


def test_controls_summary_without_tubes():
    assert views.controls_summary(_dashboard(()), "lines", 1, None, None, None) == "No tubes"


def test_summary_reads_the_tubes_when_called():
    # The shell holds partial(controls_summary, dashboard); DataCache.refresh() replaces
    # the tubes tuple, so they must not be captured when the callback is registered.
    dashboard = _dashboard((1, 2))
    summarize = functools.partial(views.controls_summary, dashboard)
    dashboard.data_cache.tubes = (7, 8)
    assert summarize("lines", None, None, None, None) == "Tube 7"


# ---- render_view / faceted ---------------------------------------------------------


def test_time_view_passes_the_options_to_the_figure_builder():
    dashboard = _dashboard()
    figure, message, tube_style, options, graph_style, images_style = views.render_view(
        dashboard, "time", None, [1, 2], ["show_field_var"], False, None, None
    )
    assert dashboard.calls == [
        ("time", (), {"selected_tubes": [1, 2], "show_field_average": False,
                      "show_field_variance": True, "show_legend": False}),
    ]  # fmt: skip
    assert figure.layout.uirevision == "time"
    assert (message, tube_style, graph_style, images_style) == (
        "",
        layout.HIDDEN,
        layout.GRAPH_STYLE,
        layout.HIDDEN,
    )
    assert options is no_update


def test_lines_view_plots_the_dropdowns_tube_and_shows_the_images():
    dashboard = _dashboard()
    figure, _, tube_style, _, graph_style, images_style = views.render_view(
        dashboard, "lines", 3, None, None, None, None, None
    )
    assert dashboard.calls == [("lines", (3,), {})]
    assert figure.layout.uirevision == "lines:3"
    assert (tube_style, graph_style, images_style) == (
        layout.SHOWN,
        layout.GRAPH_STYLE,
        layout.IMAGES_SHOWN,
    )


def test_lines_view_without_tubes_says_so():
    figure, message, *_ = views.render_view(
        _dashboard(()), "lines", 1, None, None, None, None, None
    )
    assert message == "No tube data available"
    assert _annotation(figure) == message


@pytest.mark.parametrize("view", ["stacked", "unknown", None])
def test_other_views_draw_the_stacked_bars(view):
    dashboard = _dashboard()
    figure, *_ = views.render_view(dashboard, view, None, None, None, None, None, None)
    assert dashboard.calls == [("stacked", (), {})]
    assert figure.layout.uirevision == "stacked"


def test_faceted_view_draws_the_selected_tubes_and_dates():
    dashboard = _dashboard()
    figure, message, tube_style, *_ = views.faceted_outputs(
        dashboard, [2, 1], ["2024-07-18T00:00:00"]
    )
    ((name, (tubes, dates), _),) = dashboard.calls
    assert (name, tubes, dates) == ("faceted", [2, 1], [pd.Timestamp("2024-07-18")])
    assert figure.layout.uirevision == "faceted:(2, 1):('2024-07-18T00:00:00',)"
    assert (message, tube_style) == ("", layout.HIDDEN)


def test_faceted_view_without_tubes_shows_a_hint():
    figure, message, _ = views.faceted_outputs(_dashboard(), [], [])
    assert _annotation(figure) == (
        f"Select 1 to {MAX_TUBES} tubes under “{layout.LENGTH_SHELL.controls_label}”"
    )
    assert message == ""


def test_too_many_faceted_tubes_is_an_error_without_a_chart():
    dashboard = _dashboard()
    tubes = list(range(1, MAX_TUBES + 2))
    figure, _, _ = views.faceted_outputs(dashboard, tubes, None)
    assert dashboard.calls == []
    assert f"{MAX_TUBES + 1}/{MAX_TUBES}" in _annotation(figure)
    assert figure.layout.annotations[0].font.color == theme.DANGER


@pytest.mark.parametrize(
    "tubes, graph_style",
    [(None, layout.GRAPH_STYLE), ([], layout.GRAPH_STYLE), ([1], layout.FACETED_GRAPH_STYLE),
     (list(range(1, MAX_TUBES + 2)), layout.GRAPH_STYLE)],
)  # fmt: skip
def test_only_a_drawable_faceted_grid_keeps_its_own_height(tubes, graph_style):
    assert views.faceted_styles(tubes) == (graph_style, layout.HIDDEN)


def test_legend_patch_only_touches_the_time_view():
    with pytest.raises(PreventUpdate):
        views.legend_patch("stacked", True)
    patch, *rest = views.legend_patch("time", True)
    assert patch.to_plotly_json()["operations"] == [
        {"operation": "Assign", "location": ["layout", "showlegend"], "params": {"value": True}},
        {"operation": "Assign", "location": ["layout", "margin", "r"],
         "params": {"value": theme.margin_right(True)}},
    ]  # fmt: skip
    assert rest == [no_update] * 5


# ---- export / hover -----------------------------------------------------------------


@pytest.mark.parametrize(
    "view, tube, faceted_tubes",
    [("lines", None, None), ("faceted", None, []),
     ("faceted", None, list(range(1, MAX_TUBES + 2))), ("unknown", 1, [1])],
)  # fmt: skip
def test_views_without_rows_export_nothing(view, tube, faceted_tubes):
    dashboard = _dashboard(() if view == "lines" else (1, 2, 3))
    assert views.export_table(dashboard, view, tube, [1], [], faceted_tubes, None) is None


@pytest.mark.parametrize(
    "point, expected",
    [({"customdata": "2024.07.18", "text": "2024.08.07"}, "2024.07.18"),
     ({"customdata": None, "text": "Date 2024.08.07"}, "2024.08.07"),
     ({"customdata": ["2024.07.18", 3]}, "2024.07.18"),
     ({"customdata": "no date", "text": None}, None),
     ({}, None)],
)  # fmt: skip
def test_hover_date_prefers_customdata_over_text(point, expected):
    assert views.hover_date(point) == expected


# ---- layout builders ---------------------------------------------------------------


def test_faceted_info_names_the_problem_or_the_selection():
    assert "No tubes selected" in layout.faceted_info([], []).children
    too_many = layout.faceted_info(list(range(1, MAX_TUBES + 2)), [])
    assert "viz-note--danger" in too_many.className
    ok = layout.faceted_info([1, 2], [pd.Timestamp("2024-07-18")])
    assert [span.children for span in ok.children] == [
        f"Tubes: 2/{MAX_TUBES} selected",
        "Dates: 1 date",
    ]


def test_availability_view_marks_measured_days():
    july, august = pd.Timestamp("2024-07-18"), pd.Timestamp("2024-08-07")
    availability = {(1, july): True, (1, august): False, (2, august): True}

    header, table = layout.availability_view(availability, [1, 2], [july, august])
    cells = [[cell.children for cell in row.children] for row in table.children]
    assert cells == [["Tube/Date", "07/18", "08/07"], ["T1", "✓", "✗"], ["T2", "✗", "✓"]]

    lines = layout.availability_view(availability, [1, 2], [])
    assert [line.children for line in lines[1:]] == ["Tube 1: 2024-07-18", "Tube 2: 2024-08-07"]
    assert "Select tubes" in layout.availability_view(availability, [], []).children


def test_thumbnail_links_the_served_image():
    server = SimpleNamespace(url=lambda tube, position, date: f"/t/{tube}/{position}/{date}.jpg")
    card = layout.thumbnail(server, 1, 11, "2024.07.18")
    image, label = card.children
    assert image.src == "/t/1/11/2024.07.18.jpg"
    assert label.children == "Position L11"
    assert card.key == "image-11-2024.07.18"


def test_page_holds_the_chart_row_and_the_stores():
    from app.visualization.dash_shell import DashboardShell

    options = [{"label": "Tube 1", "value": 1}]
    page = layout.page(DashboardShell(layout.LENGTH_SHELL), options, [], notice=None)
    assert {
        "image-container", "main-graph", "selected-tubes-store", "legend-visible-store",
        "faceted-tubes-store", "faceted-dates-store", "tube-selector", "tube-multiselect",
        "faceted-tube-selector", "faceted-date-selector",
    } <= component_ids(page)  # fmt: skip


# ---- DashApp keeps its names; the callbacks do not go through them -------------------


def test_dash_app_methods_return_what_the_modules_return(tmp_path):
    dashboard = build_length_app(tmp_path)
    tubes = dashboard.data_cache.tubes
    assert dashboard._lines_tube(2) == views.lines_tube(tubes, 2)
    assert dashboard._controls_summary("lines", 2, None, None, None) == "Tube 2"
    assert type(dashboard)._faceted_info is layout.faceted_info
    assert type(dashboard)._hover_date is views.hover_date
    assert dashboard._thumbnail(1, 11, "2024.07.18").children[0].src == (
        dashboard.thumbnail_server.url(1, 11, "2024.07.18")
    )
    assert dashboard._export_table("lines", 99, None, None, None, None).filters == {
        "tube": "1",
        "dates": "all",
    }


def test_callbacks_call_the_view_model_directly(tmp_path, monkeypatch):
    """A callback makes no extra call through the DashApp delegators (the latency contract)."""
    dashboard = build_length_app(tmp_path)

    def delegator(*_args, **_kwargs):
        raise AssertionError("a callback went through a DashApp delegator")

    for name in (
        "_lines_tube", "_controls_summary", "_render_view", "_faceted_outputs",
        "_faceted_styles", "_legend_patch", "_export_table", "_hover_date", "_faceted_info",
        "_availability_view", "_thumbnail",
    ):  # fmt: skip
        monkeypatch.setattr(type(dashboard), name, delegator)
    dash = dashboard.app

    for view, extra in (("stacked", {}), ("time", {"selected-tubes-store.data": [1]}),
                        ("lines", {"tube-selector.value": 2}),
                        ("faceted", {"faceted-tubes-store.data": [1, 2]})):  # fmt: skip
        status, out = call_callback(
            dash, "main-graph.figure", {"view-selector.value": view, **extra},
            ["view-selector.value"],
        )  # fmt: skip
        assert status == 200
        assert "Error generating" not in str(out["main-graph"]["figure"]), view
    status, out = call_callback(
        dash, "main-graph.figure",
        {"view-selector.value": "time", "legend-visible-store.data": False},
        ["legend-visible-store.data"],
    )  # fmt: skip
    assert status == 200 and "__dash_patch_update" in out["main-graph"]["figure"]
    _, out = call_callback(
        dash, "viz-controls-summary.children",
        {"view-selector.value": "lines", "tube-selector.value": 3}, ["view-selector.value"],
    )  # fmt: skip
    assert out["viz-controls-summary"]["children"] == "Tube 3"
    status, out = call_callback(
        dash, "viz-export-download.data",
        {"viz-export-btn.n_clicks": 1, "view-selector.value": "lines", "tube-selector.value": 1},
        ["viz-export-btn.n_clicks"],
    )  # fmt: skip
    assert status == 200 and out["viz-export-download"]["data"]["content"]
    _, out = call_callback(
        dash, "faceted-selection-info.children",
        {"faceted-tube-selector.value": [1], "faceted-date-selector.value": []},
        ["faceted-tube-selector.value"],
    )  # fmt: skip
    assert out["tube-date-availability"]["children"]
    status, out = call_callback(
        dash, "image-container.children",
        {"main-graph.hoverData": {"points": [{"y": 20, "customdata": "2024.07.18"}]},
         "tube-selector.value": 1, "view-selector.value": "lines"},
        ["main-graph.hoverData"],
    )  # fmt: skip
    assert status == 200

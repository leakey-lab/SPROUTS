"""viz-dash: regressions found in the 2026-09-30 dashboard bug hunt (PKG-VIZ, V3).

Each test names the user-visible failure it pins. Callbacks go through Dash's real
dispatch (Flask test client), figures through the dashboards' own builders.
"""

from __future__ import annotations

import os
import tracemalloc

import pytest

from tests.viz_dash_helpers import (
    AREA_COLUMN,
    build_length_app,
    call_callback,
    write_metric_csv,
)


def _displayed_categories(figure) -> list:
    """The x categories in the order Plotly draws them.

    Plotly's default ("trace") order is the order of first appearance across the
    traces; an "array" order puts ``categoryarray`` first.
    """
    seen: list = []
    for trace in figure.data:
        for x in trace.x:
            if x not in seen:
                seen.append(x)
    order = figure.layout.xaxis.categoryorder
    if order == "array":
        head = list(figure.layout.xaxis.categoryarray)
        return head + [x for x in seen if x not in head]
    assert order in (None, "trace"), f"unexpected categoryorder {order!r}"
    return seen


def test_area_profile_reads_shallow_to_deep_when_a_later_date_starts_higher(tmp_path):
    # Tube 1 was imaged from L10 on the first date and from L5 on the second. In
    # first-appearance order the axis read L10, L20, L5: the shallowest position
    # was drawn at the deep end.
    from app.data_processing.data_processor_area import DataProcessorArea
    from app.visualization.dash_app_area import DashAppArea

    rows = (
        ("T001_L010_2024.07.18", 1, 10, "2024.07.18", 1.0),
        ("T001_L020_2024.07.18", 1, 20, "2024.07.18", 2.0),
        ("T001_L005_2024.08.07", 1, 5, "2024.08.07", 3.0),
        ("T001_L010_2024.08.07", 1, 10, "2024.08.07", 4.0),
    )
    csv = write_metric_csv(os.path.join(tmp_path, "root_areas.csv"), AREA_COLUMN, rows)
    dashboard = DashAppArea(DataProcessorArea(csv), str(tmp_path))

    figure = dashboard.show_area_profile(1)

    assert _displayed_categories(figure) == ["L5", "L10", "L20"]


def test_removing_the_last_multiselect_tube_clears_the_selection(tmp_path):
    # Multi-Select replaces the selection with the dropdown's tubes; removing the
    # last one used to leave it plotted (and listed) under an empty dropdown.
    dashboard = build_length_app(tmp_path)

    _, out = call_callback(
        dashboard.app,
        "selected-tubes-store.data",
        {"tube-multiselect.value": [], "selected-tubes-store.data": [1]},
        changed=["tube-multiselect.value"],
    )

    assert out["selected-tubes-store"]["data"] == []


def test_multiselect_still_replaces_the_selection(tmp_path):
    dashboard = build_length_app(tmp_path)

    _, out = call_callback(
        dashboard.app,
        "selected-tubes-store.data",
        {"tube-multiselect.value": [2, 3], "selected-tubes-store.data": [1]},
        changed=["tube-multiselect.value"],
    )

    assert out["selected-tubes-store"]["data"] == [2, 3]


def _stacked_values(figure) -> dict:
    return {trace.name: dict(zip(trace.x, trace.y, strict=True)) for trace in figure.data}


def test_stacked_bars_leave_out_dates_a_tube_was_not_measured_on(tmp_path):
    # Tube 3 was imaged on 2024.08.07 only. Its 2024.07.18 bar was a 0.00 bar that
    # the unified hover listed as "0.00 mm", as if the tube had no roots that day.
    import math

    from app.data_processing.data_processor_area import DataProcessorArea
    from app.visualization.dash_app_area import DashAppArea

    csv = write_metric_csv(os.path.join(tmp_path, "root_areas.csv"), AREA_COLUMN)
    for dashboard in (
        build_length_app(tmp_path),
        DashAppArea(DataProcessorArea(csv), str(tmp_path)),
    ):
        values = _stacked_values(dashboard.create_stacked_bar_chart())

        assert math.isnan(values["2024-07-18"]["Tube 3"])
        assert values["2024-08-07"]["Tube 3"] == 0.75
        assert values["2024-07-18"]["Tube 1"] == 4.0  # 1.5 + 2.5


def test_stacked_bars_reach_the_browser_with_null_for_a_date_a_tube_missed(tmp_path):
    # Tube 1 was imaged on the first and third dates, not the second. The area chart sent
    # its bars as a typed array; plotly.js stacks the NaN inside one and restarted the
    # tube's later dates at 0 (Totals by Tube drew 162 of 170 real tubes too short). A
    # JSON null is left out of the stack.
    from app.data_processing.data_processor import DataProcessor
    from app.data_processing.data_processor_area import DataProcessorArea
    from app.visualization.dash_app import DashApp
    from app.visualization.dash_app_area import DashAppArea
    from tests.viz_dash_helpers import LENGTH_COLUMN, FakeImageManager

    rows = (
        ("T001_L005_2024.07.18", 1, 5, "2024.07.18", 1.5),
        ("T001_L005_2024.08.27", 1, 5, "2024.08.27", 2.5),
        ("T002_L005_2024.07.18", 2, 5, "2024.07.18", 1.0),
        ("T002_L005_2024.08.07", 2, 5, "2024.08.07", 2.0),
        ("T002_L005_2024.08.27", 2, 5, "2024.08.27", 3.0),
    )
    length_csv = write_metric_csv(os.path.join(tmp_path, "root_lengths.csv"), LENGTH_COLUMN, rows)
    area_csv = write_metric_csv(os.path.join(tmp_path, "root_areas.csv"), AREA_COLUMN, rows)
    dashboards = {
        "length": DashApp(DataProcessor(length_csv), str(tmp_path), FakeImageManager()),
        "area": DashAppArea(DataProcessorArea(area_csv), str(tmp_path)),
    }
    for name, dashboard in dashboards.items():
        _, out = call_callback(
            dashboard.app,
            "main-graph.figure",
            {"view-selector.value": "stacked"},
            changed=["view-selector.value"],
        )
        bars = {trace["name"]: trace["y"] for trace in out["main-graph"]["figure"]["data"]}

        assert bars == {
            "2024-07-18": [1.5, 1.0],
            "2024-08-07": [None, 2.0],
            "2024-08-27": [2.5, 3.0],
        }, name


def test_typed_or_ranged_tubes_without_data_are_not_selected(tmp_path):
    # "2-9" on a trial with tubes 1-3 said "8 tubes selected" (and the CSV export's
    # provenance listed 8 tubes) while the chart could only plot two of them.
    dashboard = build_length_app(tmp_path)  # tubes 1, 2 and 3

    _, typed = call_callback(
        dashboard.app,
        "selected-tubes-store.data",
        {"tube-manual-entry.value": "2-9", "selected-tubes-store.data": []},
        changed=["tube-manual-entry.value"],
    )
    _, ranged = call_callback(
        dashboard.app,
        "selected-tubes-store.data",
        {
            "add-range-btn.n_clicks": 1,
            "tube-range-from.value": 1,
            "tube-range-to.value": 50,
            "selected-tubes-store.data": [],
        },
        changed=["add-range-btn.n_clicks"],
    )

    assert typed["selected-tubes-store"]["data"] == [2, 3]
    assert ranged["selected-tubes-store"]["data"] == [1, 2, 3]


@pytest.mark.parametrize(
    "values, trigger, expected",
    [
        ({"tube-manual-entry.value": "2-2000000"}, "tube-manual-entry.value", [2, 3]),
        (
            {"add-range-btn.n_clicks": 1, "tube-range-from.value": 1,
             "tube-range-to.value": 2_000_000},
            "add-range-btn.n_clicks",
            [1, 2, 3],
        ),
    ],
    ids=["typed", "ranged"],
)  # fmt: skip
def test_a_wide_tube_span_is_matched_against_the_trial_not_expanded(
    tmp_path, values, trigger, expected
):
    # A typo with extra digits ("1-100000000") listed every number of the span before
    # the trial's tubes were kept: gigabytes and seconds on the Dash server thread, which
    # holds the GIL, so the whole app froze. (Two million here: a regression fails this
    # test instead of exhausting the machine.)
    dashboard = build_length_app(tmp_path)  # tubes 1, 2 and 3

    # A trace that already runs (PYTHONTRACEMALLOC, a profiler) stays on; the peak is
    # counted above what is held when the measurement starts.
    tracing = tracemalloc.is_tracing()
    if tracing:
        tracemalloc.reset_peak()
    else:
        tracemalloc.start()
    held = tracemalloc.get_traced_memory()[0]
    try:
        _, out = call_callback(
            dashboard.app,
            "selected-tubes-store.data",
            {**values, "selected-tubes-store.data": []},
            changed=[trigger],
        )
        peak = tracemalloc.get_traced_memory()[1] - held
    finally:
        if not tracing:
            tracemalloc.stop()

    assert out["selected-tubes-store"]["data"] == expected
    assert peak < 16 * 2**20, f"{peak / 2**20:.0f} MiB at peak for a 2-million span"


@pytest.mark.parametrize(
    "low, high, expected",
    [(2.1, 2.9, [2]), (2.9, 2.1, []), (-5, 2, [1, 2]), (3, 3, [3]), (4, 9, [])],
)
def test_add_range_truncates_its_bounds_and_needs_from_up_to_to(tmp_path, low, high, expected):
    dashboard = build_length_app(tmp_path)  # tubes 1, 2 and 3

    _, out = call_callback(
        dashboard.app,
        "selected-tubes-store.data",
        {
            "add-range-btn.n_clicks": 1,
            "tube-range-from.value": low,
            "tube-range-to.value": high,
            "selected-tubes-store.data": [],
        },
        changed=["add-range-btn.n_clicks"],
    )

    assert out["selected-tubes-store"]["data"] == expected


def test_parse_tube_selection_keeps_only_the_given_tubes():
    from app.visualization.dash_visualizations import parse_tube_selection

    assert parse_tube_selection("3,1-2000000", within={2, 7, 500}) == [2, 7, 500]
    assert parse_tube_selection("7, 9, 400-600, x", within={2, 7, 500}) == [7, 500]
    assert parse_tube_selection("5-3", within={4}) == []
    # Without ``within``: every number the text names, as before.
    assert parse_tube_selection("1,3,5,10-12") == [1, 3, 5, 10, 11, 12]
    assert parse_tube_selection("1,foo,3") == [1, 3]  # F-037: a malformed part is skipped
    assert parse_tube_selection("  ") == []

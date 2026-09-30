"""viz-dash: regressions found in the 2026-09-30 dashboard bug hunt (PKG-VIZ, V3).

Each test names the user-visible failure it pins. Callbacks go through Dash's real
dispatch (Flask test client), figures through the dashboards' own builders.
"""

from __future__ import annotations

import os

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

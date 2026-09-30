"""viz-dash: the dashboards load plotly.js's "basic" bundle (PKG-VIZ, V2).

dcc.Graph uses ``window.Plotly`` when the page already defines it. The dashboards'
assets define it with the basic bundle (scatter, bar and pie traces; 1.1 MB) instead
of the full 4.8 MB bundle that dcc.Graph fetched after the page had rendered and
that took ~0.3 s to evaluate on every dashboard open. These tests pin the bundle to
the plotly.js version plotly.py builds figures for and every dashboard figure to the
trace types the bundle has (a new trace type must switch back to the full bundle).
"""

from __future__ import annotations

import os
import re

import pandas as pd
import pytest
from plotly.offline import get_plotlyjs_version

from app.visualization.dash_shell import ASSETS_DIR
from tests.viz_dash_helpers import build_area_app, build_length_app

BUNDLE = os.path.join(ASSETS_DIR, "plotly-basic.min.js")
#: Trace types in plotly.js's basic bundle.
BASIC_TRACES = {"scatter", "bar", "pie"}


def test_the_bundle_is_the_plotly_js_version_plotly_py_targets():
    with open(BUNDLE, encoding="utf-8") as fh:
        head = fh.read(400)
    match = re.search(r"plotly\.js \(basic - minified\) v(\d+\.\d+\.\d+)", head)
    assert match, "not plotly.js's basic dist bundle"
    assert match.group(1) == get_plotlyjs_version(), (
        "plotly.py was upgraded: vendor the matching plotly.js-basic-dist-min "
        "(see app/assets/THIRD_PARTY.md)"
    )


@pytest.mark.parametrize("builder", [build_length_app, build_area_app], ids=["length", "area"])
def test_each_dashboard_loads_the_bundle_locally_before_the_renderer(tmp_path, builder):
    server = builder(tmp_path).app.server.test_client()
    html = server.get("/").data.decode("utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    bundle = [i for i, src in enumerate(scripts) if "plotly-basic.min.js" in src]
    renderer = [i for i, src in enumerate(scripts) if "dash_renderer" in src]

    assert bundle and renderer and bundle[0] < renderer[0]
    served = server.get(scripts[bundle[0]])
    assert served.status_code == 200
    assert re.match(rb"/\*\*\s*\* plotly\.js \(basic - minified\)", served.data)


def _length_figures(tmp_path):
    dashboard = build_length_app(tmp_path)
    tubes = [int(t) for t in dashboard.data_cache.tubes]
    dates = [pd.Timestamp(d) for d in dashboard.data_cache.dates]
    return [
        dashboard.create_stacked_bar_chart(),
        dashboard.show_growth_over_time(tubes, True, True, True),
        dashboard.show_growth_over_time([], True, False, False),
        dashboard.show_growth_lines(tubes[0]),
        dashboard.create_faceted_depth_profile(tubes[:2], dates),
        dashboard.create_faceted_depth_profile([], None),  # message figure
    ]


def _area_figures(tmp_path):
    dashboard = build_area_app(tmp_path)
    return [
        dashboard.create_stacked_bar_chart(),
        dashboard.show_growth_over_time(),
        dashboard.show_area_profile(int(dashboard.data_cache.tubes[0])),
    ]


@pytest.mark.parametrize("figures", [_length_figures, _area_figures], ids=["length", "area"])
def test_every_dashboard_figure_uses_only_basic_trace_types(tmp_path, figures):
    for figure in figures(tmp_path):
        assert {trace.type for trace in figure.data} <= BASIC_TRACES


def test_the_bundle_still_has_the_autosize_flag_the_refit_clears():
    # dash_shell._REFIT_GRAPH_JS clears plotly.js's _fullLayout._initialAutoSizeIsDone so
    # the figure after a strip or view change is drawn once, at its real size (R-VIZ X2).
    # A plotly.js that renamed it would silently bring the second redraw back.
    with open(BUNDLE, encoding="utf-8") as fh:
        source = fh.read()
    assert "_initialAutoSizeIsDone" in source

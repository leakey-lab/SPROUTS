"""The dashboards keep the user's zoom through Dash 4.4.1's figure sync (plotly/dash#4093).

Dash 4.4.1's dcc.Graph copies the layout keys a relayout touched (a zoom's axes) back into
its figure prop and draws that figure again; plotly.js then takes the zoom as the figure's
own and resets it on the next figure with the same uirevision (Select All, Clear All).
``app/assets/sprouts-uirevision.js`` answers that redraw without drawing. The script runs
here in Qt's JavaScript engine against a stand-in for plotly.js. The real flows (QtWebEngine:
zoom, Toggle Legend, a view option, Select All, Clear All) were checked against Dash 4.1.0
when Dash 4.4.1 came in: every axis range the same (CHANGELOG, 2026-10-27).
"""

from __future__ import annotations

import os
import re

import pytest

from app.visualization.dash_shell import ASSETS_DIR
from tests.viz_dash_helpers import build_area_app, build_length_app

GUARD = os.path.join(ASSETS_DIR, "sprouts-uirevision.js")

# A plotly.js stand-in: react records what reaches it; a chart div with plotly.js's events.
_FAKE_PLOTLY = """
var window = this;
window.drawn = [];
window.Plotly = {
    react: function (gd, figure) {
        var layout = arguments.length > 2 ? arguments[2] : figure.layout;  // both forms
        window.drawn.push(JSON.stringify(layout));
        if (typeof gd === 'object') {
            gd.layout = JSON.parse(JSON.stringify(layout));  // plotly.js keeps its input
        }
        return Promise.resolve(gd);
    }
};
function chart() {
    var listeners = {};
    return {
        on: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
        removeListener: function (name, fn) {
            listeners[name] = (listeners[name] || []).filter(function (f) { return f !== fn; });
        },
        emit: function (name, data) { (listeners[name] || []).forEach(function (f) { f(data); }); },
        count: function (name) { return (listeners[name] || []).length; }
    };
}
var DATA = [{type: 'scatter', x: [1, 2], y: [3, 4]}];
var CONFIG = {responsive: true, doubleClickDelay: 300};
function figure(layout, data, config) {
    // as dcc.Graph builds it on every draw: a new config object, the figure's data
    return {data: data || DATA, layout: layout, frames: undefined,
            config: JSON.parse(JSON.stringify(config || CONFIG))};
}
function base() {
    return {uirevision: 'time', showlegend: true, xaxis: {title: {text: 'Date'}},
            yaxis: {title: {text: 'Length'}}, autosize: true, height: undefined};
}
// A mouse zoom: plotly.js writes the ranges into its layout, then emits the relayout.
function zoom(gd, x0, x1) {
    gd.layout.xaxis.range = [x0, x1];
    gd.layout.xaxis.autorange = false;
    gd.layout.yaxis.range = [0, 9];
    gd.layout.yaxis.autorange = false;
    gd.emit('plotly_relayout', {'xaxis.range[0]': x0, 'xaxis.range[1]': x1,
                                'yaxis.range[0]': 0, 'yaxis.range[1]': 9});
}
// What dcc.Graph 4.4.1 redraws after a relayout: its figure with the touched keys synced.
function synced(gd, keys) {
    var layout = base();
    keys.forEach(function (key) { layout[key] = JSON.parse(JSON.stringify(gd.layout[key])); });
    return figure(layout);
}
"""


@pytest.fixture
def engine(qapp):
    from PyQt6.QtQml import QJSEngine

    js = QJSEngine()
    _run(js, _FAKE_PLOTLY)
    with open(GUARD, encoding="utf-8") as fh:
        _run(js, fh.read())
    _run(js, "window.gd = chart(); Plotly.react(window.gd, figure(base()));")
    return js


def _run(js, source: str):
    result = js.evaluate(source)
    assert not result.isError(), result.toString()
    return result


def _draws(js, call: str) -> bool:
    """Run ``call`` (a react); True if it reached plotly.js."""
    before = _run(js, "window.drawn.length").toInt()
    _run(js, f"window.answer = {call};")
    return _run(js, "window.drawn.length").toInt() > before


def test_the_redraw_of_a_synced_zoom_is_answered_without_drawing(engine, qapp):
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    assert not _draws(engine, "Plotly.react(window.gd, synced(window.gd, ['xaxis', 'yaxis']))")
    _run(engine, "window.answer.then(function (gd) { window.resolved = gd === window.gd; });")
    for _ in range(20):  # Promise reactions run from posted events
        if _run(engine, "window.resolved === true").toBool():
            break
        qapp.processEvents()
        qapp.sendPostedEvents()
    assert _run(engine, "window.resolved === true").toBool()


def test_a_new_figure_from_a_callback_is_drawn(engine):
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    assert _draws(engine, "Plotly.react(window.gd, figure(base(), [{type: 'scatter'}]))")


def test_a_patch_after_a_zoom_is_drawn(engine):
    # Toggle Legend: the synced figure with showlegend patched (same data).
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    call = "(function () { var f = synced(window.gd, ['xaxis', 'yaxis']);" \
           " f.layout.showlegend = false; return Plotly.react(window.gd, f); })()"  # fmt: skip
    assert _draws(engine, call)


def test_two_zooms_before_the_redraws_are_both_answered(engine):
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');"
                 " window.first = synced(window.gd, ['xaxis', 'yaxis']);"
                 " zoom(window.gd, '2024-07-26', '2024-07-28');")  # fmt: skip
    assert not _draws(engine, "Plotly.react(window.gd, window.first)")
    assert not _draws(engine, "Plotly.react(window.gd, synced(window.gd, ['xaxis', 'yaxis']))")


@pytest.mark.parametrize(
    "call",
    [
        # the same figure again, no relayout since: not an echo
        "Plotly.react(window.gd, figure(base()))",
        # another config
        "Plotly.react(window.gd, figure(base(), undefined, {responsive: false}))",
        # four-argument form
        "Plotly.react(window.gd, DATA, base(), CONFIG)",
        # a chart addressed by id
        "Plotly.react('main-graph', figure(base()))",
    ],
    ids=["no-relayout", "config", "four-arguments", "id-string"],
)
def test_any_other_react_is_drawn(qapp, call):
    from PyQt6.QtQml import QJSEngine

    js = QJSEngine()
    _run(js, _FAKE_PLOTLY)
    with open(GUARD, encoding="utf-8") as fh:
        _run(js, fh.read())
    _run(js, "window.gd = chart(); Plotly.react(window.gd, figure(base()));")
    if "config" in call:
        _run(js, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    assert _draws(js, call)


def test_a_key_no_relayout_touched_is_drawn(engine):
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    call = "(function () { var f = synced(window.gd, ['xaxis', 'yaxis']);" \
           " f.layout.title = {text: 'new'}; return Plotly.react(window.gd, f); })()"  # fmt: skip
    assert _draws(engine, call)


def test_the_relayout_listener_is_kept_once(engine):
    for _ in range(3):
        _run(engine, "Plotly.react(window.gd, figure(base(), [{type: 'bar'}]));")
    assert _run(engine, "window.gd.count('plotly_relayout')").toInt() == 1


def test_without_plotly_the_script_does_nothing(qapp):
    from PyQt6.QtQml import QJSEngine

    js = QJSEngine()
    _run(js, "var window = this;")
    with open(GUARD, encoding="utf-8") as fh:
        _run(js, fh.read())
    assert _run(js, "window.Plotly === undefined").toBool()


def test_a_second_load_wraps_react_once(engine):
    with open(GUARD, encoding="utf-8") as fh:
        _run(engine, fh.read())
    _run(engine, "zoom(window.gd, '2024-07-24', '2024-07-31');")
    assert not _draws(engine, "Plotly.react(window.gd, synced(window.gd, ['xaxis', 'yaxis']))")
    assert _run(engine, "window.Plotly.react.sproutsEchoGuard === true").toBool()


@pytest.mark.parametrize("builder", [build_length_app, build_area_app], ids=["length", "area"])
def test_each_dashboard_loads_the_guard_after_plotly_and_before_the_renderer(tmp_path, builder):
    html = builder(tmp_path).app.server.test_client().get("/").data.decode("utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    order = {
        name: next(i for i, src in enumerate(scripts) if name in src)
        for name in ("plotly-basic.min.js", "sprouts-uirevision.js", "dash_renderer")
    }
    assert order["plotly-basic.min.js"] < order["sprouts-uirevision.js"] < order["dash_renderer"]


def test_the_dash_graph_still_syncs_and_redraws_the_way_the_guard_expects():
    # The guard answers what dcc.Graph 4.4.1 draws after a relayout (dash#3882). A Dash that
    # stopped syncing, or synced other keys, would need the guard re-checked (or dropped).
    import dash

    graph = os.path.join(os.path.dirname(dash.__file__), "dcc", "async-graph.js")
    with open(graph, encoding="utf-8") as fh:
        source = fh.read()
    assert re.search(r'\.split\("\."\)\[0\]\.split\("\["\)\[0\]', source), "relayout key sync"
    assert re.search(r'\["autosize","width","height"\]', source), "resize keys left out"
    assert "Plotly.react(" in source

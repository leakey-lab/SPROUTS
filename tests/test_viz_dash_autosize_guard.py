"""The dashboards skip plotly.js's autosize relayout when the chart already has its size.

``app/assets/sprouts-autosize.js`` replaces the relayout that ``Plotly.Plots.resize`` makes
(through plotly.js's API registry) with a no-op when the size plotly.js would measure is
the size the chart was drawn at: after a view switch that relayout drew nothing but took a
60-170 ms frame on the 170-tube and grid views. The script runs here in Qt's JavaScript
engine against a stand-in for plotly.js; ``test_viz_dash_plotly_bundle`` checks that the
vendored bundle still works the way the guard expects.
"""

from __future__ import annotations

import os
import re

import pytest

from app.visualization.dash_shell import ASSETS_DIR
from tests.viz_dash_helpers import build_area_app, build_length_app

GUARD = os.path.join(ASSETS_DIR, "sprouts-autosize.js")

# A plotly.js stand-in: relayout records what reaches it, register keeps the modules.
_FAKE_PLOTLY = """
var window = this;
window.forwarded = [];
window.registered = [];
window.Plotly = {
    relayout: function (gd, update) {
        window.forwarded.push(JSON.stringify(update));
        return Promise.resolve('drawn');
    },
    register: function (module) { window.registered.push(module); }
};
window.style = {width: '1390px', height: '430.109px', maxWidth: 'none', maxHeight: 'none'};
window.getComputedStyle = function () { return window.style; };
function plot(full, layout, extra) {
    var gd = {
        _fullLayout: full, layout: layout || {}, _context: {},
        classList: {contains: function (name) { return name === 'js-plotly-plot'; }}
    };
    for (var key in extra || {}) { gd[key] = extra[key]; }
    return gd;
}
function full(width, height, extra) {
    var layout = {width: width, height: height, shapes: []};
    for (var key in extra || {}) { layout[key] = extra[key]; }
    return layout;
}
"""


@pytest.fixture
def engine(qapp):
    from PyQt6.QtQml import QJSEngine

    js = QJSEngine()
    _run(js, _FAKE_PLOTLY)
    with open(GUARD, encoding="utf-8") as fh:
        _run(js, fh.read())
    return js


def _run(js, source: str):
    result = js.evaluate(source)
    assert not result.isError(), result.toString()
    return result


def _relayout(js, gd: str, update: str = "{autosize: true}") -> bool:
    """Call the registered relayout with ``gd`` (JS); True if it reached plotly.js."""
    before = _run(js, "window.forwarded.length").toInt()
    _run(js, f"window.answer = window.registered[0].fn({gd}, {update});")
    return _run(js, "window.forwarded.length").toInt() > before


def test_the_guard_replaces_the_registry_relayout_only(engine):
    assert _run(engine, "window.registered.length").toInt() == 1
    assert _run(engine, "window.registered[0].moduleType").toString() == "apiMethod"
    assert _run(engine, "window.registered[0].name").toString() == "relayout"
    assert _run(engine, "typeof window.Plotly.relayout === 'function'").toBool()


def test_a_chart_drawn_at_its_size_is_not_drawn_again(engine, qapp):
    _run(engine, "window.gd = plot(full(1390, 430.109), {}, {autoplay: true});")
    assert not _relayout(engine, "window.gd")
    assert _run(engine, "window.gd.autoplay").toBool() is False  # as relayout leaves it
    _run(engine, "window.answer.then(function (gd) { window.resolved = gd === window.gd; });")
    for _ in range(20):  # Promise reactions run from posted events
        if _run(engine, "window.resolved === true").toBool():
            break
        qapp.processEvents()
        qapp.sendPostedEvents()
    assert _run(engine, "window.resolved === true").toBool()


@pytest.mark.parametrize(
    "width, height, reaches",
    [("1390px", "430.109px", False), ("1391px", "431px", False),  # plotly.js allows 1 px
     ("1392px", "430px", True), ("1390px", "300px", True), ("auto", "auto", False)],
)  # fmt: skip
def test_only_a_change_of_size_reaches_plotly(engine, width, height, reaches):
    # 'auto' (no pixel size): plotly.js keeps the drawn size, so nothing would change.
    _run(engine, f"window.style.width = '{width}'; window.style.height = '{height}';")
    assert _relayout(engine, "plot(full(1390, 430.109))") is reaches


def test_a_fixed_dimension_is_not_compared(engine):
    # The Depth Profile Grid fixes its height: plotly.js autosizes the width only.
    _run(engine, "window.style.height = '2000px';")
    assert not _relayout(engine, "plot(full(1390, 900), {height: 900})")
    _run(engine, "window.style.width = '1000px';")
    assert _relayout(engine, "plot(full(1390, 900), {height: 900})")


@pytest.mark.parametrize(
    "update",
    ["{autosize: true, width: 500}", "{autosize: false}", "{'xaxis.range': [0, 1]}", "{}",
     "null", "'autosize'"],
)  # fmt: skip
def test_any_other_relayout_runs_as_before(engine, update):
    assert _relayout(engine, "plot(full(1390, 430.109))", update)


@pytest.mark.parametrize(
    "gd",
    [
        "plot(undefined)",  # not drawn yet
        "plot(full(1390, 430.109), {}, {classList: {contains: function () { return false; }}})",
        "plot(full(1390, 430.109), {}, {_transitioningWithDuration: true})",
        "plot(full(1390, 430.109), {}, {_transitioning: true})",
        "plot(full(1390, 430.109), {}, {_context: {fillFrame: true}})",
        "plot(full(1390, 430.109), {}, {_context: {frameMargins: 0.1}})",
        "plot(full(1390, 430.109, {shapes: [{showlegend: true}]}))",
        "plot(full(1390, 430.109), {autosize: false})",
        "'main-graph'",
    ],
    ids=["undrawn", "not-a-plot", "transition", "transition-start", "fill-frame",
         "frame-margins", "legend-shape", "autosize-off", "id-string"],
)  # fmt: skip
def test_cases_plotly_measures_differently_run_as_before(engine, gd):
    assert _relayout(engine, gd)


def test_without_plotly_the_script_does_nothing(qapp):
    from PyQt6.QtQml import QJSEngine

    js = QJSEngine()
    _run(js, "var window = this;")
    with open(GUARD, encoding="utf-8") as fh:
        _run(js, fh.read())
    assert _run(js, "window.Plotly === undefined").toBool()


@pytest.mark.parametrize("builder", [build_length_app, build_area_app], ids=["length", "area"])
def test_each_dashboard_loads_the_guard_after_plotly_and_before_the_renderer(tmp_path, builder):
    html = builder(tmp_path).app.server.test_client().get("/").data.decode("utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    order = {
        name: next(i for i, src in enumerate(scripts) if name in src)
        for name in ("plotly-basic.min.js", "sprouts-autosize.js", "dash_renderer")
    }
    assert order["plotly-basic.min.js"] < order["sprouts-autosize.js"] < order["dash_renderer"]

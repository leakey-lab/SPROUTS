/* SPROUTS: skip plotly.js's autosize relayout when the chart already has its size.
 *
 * Plotly.Plots.resize (called by dcc.Graph when its box changes size and by the
 * dashboards' refit callback, dash_shell._REFIT_GRAPH_JS) waits 100 ms and then calls
 * relayout({autosize: true}). The refit makes every new figure measure its box when
 * it is drawn, so after a view switch that relayout mostly finds the size unchanged:
 * it draws nothing, but recomputes the defaults of every trace and emits its events
 * (60-90 ms on the 170-tube views, about 230 ms over ten view switches).
 *
 * The guard answers that relayout at once when the size plotly.js would measure (as
 * its plotAutoSize does) is the size the chart was drawn at. Every other relayout, and
 * one during a transition, runs as before. Loaded after plotly-basic.min.js (assets
 * load in name order); tests/test_viz_dash_plotly_bundle.py checks what it relies on.
 */
(function () {
    'use strict';
    var Plotly = window.Plotly;
    if (!Plotly || typeof Plotly.register !== 'function' || !Plotly.relayout) {
        return;
    }
    var relayout = Plotly.relayout;
    var MIN_SIZE = 10;  // plotly.js's smallest layout width and height

    function px(value) {
        return typeof value === 'string' && value.slice(-2) === 'px' && parseFloat(value);
    }

    // True when relayout({autosize: true}) would find the chart at its size already.
    function drawnAtBoxSize(gd) {
        var full = gd._fullLayout;
        var layout = gd.layout;
        var context = gd._context || {};
        if (!full || !layout || !gd.classList || !gd.classList.contains('js-plotly-plot')) {
            return false;
        }
        if (layout.autosize === false) {
            return false;  // the relayout turns autosize on, which later draws keep
        }
        if (context.fillFrame || context.frameMargins > 0) {
            return false;
        }
        if (gd._transitioning || gd._transitioningWithDuration) {
            return false;
        }
        if ((full.shapes || []).some(function (shape) { return shape.showlegend; })) {
            return false;  // relayout redraws these even at the same size
        }
        var style = window.getComputedStyle(gd);
        var width = px(style.width) || px(style.maxWidth) || full.width;
        var height = px(style.height) || px(style.maxHeight) || full.height;
        if (width < MIN_SIZE) { width = MIN_SIZE; }
        if (height < MIN_SIZE) { height = MIN_SIZE; }
        var widthChanged = !layout.width && Math.abs(full.width - width) > 1;
        var heightChanged = !layout.height && Math.abs(full.height - height) > 1;
        return !widthChanged && !heightChanged;
    }

    function guardedRelayout(gd, update) {
        var autosizeOnly = arguments.length === 2 && update !== null &&
            typeof update === 'object' && update.autosize === true &&
            Object.keys(update).length === 1;
        if (autosizeOnly && gd && typeof gd === 'object' && drawnAtBoxSize(gd)) {
            gd.autoplay = false;  // what relayout's undo queue leaves (Plots.resize set it)
            return Promise.resolve(gd);
        }
        return relayout.apply(this, arguments);
    }

    // Plots.resize calls relayout through plotly.js's API registry (Registry.call), which
    // this replaces; Plotly.relayout itself is left as it is.
    Plotly.register({moduleType: 'apiMethod', name: 'relayout', fn: guardedRelayout});
})();

/* SPROUTS: keep the user's zoom through Dash 4.4.1's figure sync (plotly/dash#4093).
 *
 * Since Dash 4.4.1 (#3882) dcc.Graph copies every layout key a relayout event touched (a
 * zoom's xaxis and yaxis) from the chart back into its figure prop, then draws that figure
 * again with Plotly.react. The redraw changes nothing on screen, but plotly.js now takes the
 * zoomed ranges as the figure's own and forgets that the user set them, so the next figure
 * a callback returns with the same uirevision (Select All, Clear All) resets the axes. Up to
 * Dash 4.4.0 that redraw did not happen, and the zoom stayed.
 *
 * The guard answers that redraw at once: a react with the same data, frames and config as
 * the last one drawn, whose layout differs from it only in keys a relayout event touched,
 * each as the chart showed it after that event. Every other react runs as before; the
 * figure prop keeps the synced ranges (a later Patch applies to them, as #3882 wants).
 * Loaded after plotly-basic.min.js (assets load in name order);
 * tests/test_viz_dash_uirevision.py checks it.
 */
(function () {
    'use strict';
    var Plotly = window.Plotly;
    if (!Plotly || typeof Plotly.react !== 'function' || Plotly.react.sproutsEchoGuard) {
        return;
    }
    var react = Plotly.react;
    // Relayouts from the resize machinery, not from the user; dcc.Graph syncs none of them.
    var RESIZE_KEYS = ['autosize', 'width', 'height'];
    var MAX_EVENTS = 8;  // relayouts remembered until the next figure is drawn

    function text(value) {
        return value === undefined ? undefined : JSON.stringify(value);
    }

    function snapshot(layout) {
        var keys = {};
        Object.keys(layout || {}).forEach(function (key) { keys[key] = text(layout[key]); });
        return keys;
    }

    function topKey(eventKey) {
        return eventKey.split('.')[0].split('[')[0];  // 'xaxis.range[0]' -> 'xaxis'
    }

    // Remember what the chart showed after each relayout, for the keys the event touched.
    function watch(gd) {
        if (typeof gd.on !== 'function' || typeof gd.removeListener !== 'function') {
            return;
        }
        if (!gd.sproutsOnRelayout) {
            gd.sproutsOnRelayout = function (eventData) {
                var shown = {};
                Object.keys(eventData || {}).forEach(function (eventKey) {
                    var key = topKey(eventKey);
                    if (RESIZE_KEYS.indexOf(key) < 0 && gd.layout) {
                        shown[key] = text(gd.layout[key]);
                    }
                });
                gd.sproutsRelayouts.push(shown);
                if (gd.sproutsRelayouts.length > MAX_EVENTS) {
                    gd.sproutsRelayouts.shift();
                }
            };
        }
        // Once per emitter: Plotly.purge drops the listeners, removeListener the old copy.
        gd.removeListener('plotly_relayout', gd.sproutsOnRelayout);
        gd.on('plotly_relayout', gd.sproutsOnRelayout);
    }

    function isEcho(gd, figure) {
        var last = gd.sproutsLastReact;
        var events = gd.sproutsRelayouts || [];
        if (!last || !events.length || figure.data !== last.data || figure.frames !== last.frames) {
            return false;
        }
        if (text(figure.config) !== last.config) {
            return false;
        }
        var layout = snapshot(figure.layout);
        var keys = Object.keys(layout).concat(Object.keys(last.layout));
        var changed = false;
        for (var i = 0; i < keys.length; i++) {
            var key = keys[i];
            if (layout[key] === last.layout[key]) {
                continue;
            }
            var seen = events.some(function (shown) {
                return Object.prototype.hasOwnProperty.call(shown, key) && shown[key] === layout[key];
            });
            if (!seen) {
                return false;
            }
            changed = true;
        }
        return changed;
    }

    function guardedReact(gd, figure) {
        var oneFigure = arguments.length === 2 && gd !== null && typeof gd === 'object' &&
            figure !== null && typeof figure === 'object' && !Array.isArray(figure);
        if (!oneFigure) {
            return react.apply(this, arguments);
        }
        if (isEcho(gd, figure)) {
            return Promise.resolve(gd);
        }
        var drawn = {
            data: figure.data,
            frames: figure.frames,
            config: text(figure.config),
            layout: snapshot(figure.layout)
        };
        var result = react.apply(this, arguments);
        gd.sproutsLastReact = drawn;
        gd.sproutsRelayouts = [];
        watch(gd);
        return result;
    }

    guardedReact.sproutsEchoGuard = true;
    Plotly.react = guardedReact;
})();

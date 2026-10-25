"""Length dashboard: the served page and the callback wiring are frozen (CQ-07 split).

``dash_app.py`` was split into ``dash_length_layout`` (page), ``dash_length_views``
(view model) and ``dash_length_callbacks`` (wiring) with no behaviour change. These
goldens hold what the browser receives: the layout, every callback's Outputs / Inputs /
States in registration order (the order Dash fires them on page load) and the
clientside JavaScript. The values were taken from the unsplit module and are unchanged
by the split. A deliberate change to the page updates them (the failure message shows
the new value); a refactor must leave them as they are.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.viz_dash_helpers import build_length_app

GOLDEN_LAYOUT_SHA256 = "c0fb067a8c23e60e0d6f825154585e99cdc8e5b2f39babb2db6187cfb022f51c"
GOLDEN_CLIENTSIDE_JS_SHA256 = "9b5e33951972d377b9efc7595e5f78097044174135651414335f57422efc3338"
# (output, inputs, states, prevent_initial_call, runs in the browser), in registration order.
GOLDEN_CALLBACKS = (
    ("viz-controls-collapse.is_open", ("viz-controls-toggle.n_clicks", "view-selector.value"),
     ("viz-controls-collapse.is_open",), True, True),
    ("viz-controls.style", ("view-selector.value",), (), True, True),
    ("viz-controls-caret.children", ("viz-controls-collapse.is_open",), (), True, True),
    ("viz-controls-summary.children",
     ("view-selector.value", "tube-selector.value", "selected-tubes-store.data",
      "faceted-tubes-store.data", "faceted-dates-store.data"), (), False, False),
    ("tube-selector.value", ("view-selector.value",), ("tube-selector.value",), True, False),
    ("viz-resize-dummy.data", ("viz-controls-collapse.is_open", "view-selector.value"), (),
     False, True),
    ("viz-export-download.data", ("viz-export-btn.n_clicks",),
     ("view-selector.value", "tube-selector.value", "selected-tubes-store.data",
      "growth-viz-options.value", "faceted-tubes-store.data", "faceted-dates-store.data"),
     True, False),
    ("..tube-selection-card.style...viz-options-card.style...tube-multiselect.options..."
     "faceted-options-card.style...faceted-tube-selector.options...faceted-date-selector.options..",
     ("view-selector.value",), (), False, True),
    ("..faceted-tubes-store.data...faceted-dates-store.data...faceted-selection-info.children..."
     "tube-date-availability.children..",
     ("faceted-tube-selector.value", "faceted-date-selector.value"), (), False, False),
    ("selected-tubes-store.data",
     ("tube-multiselect.value", "add-range-btn.n_clicks", "tube-manual-entry.value",
      "clear-all-btn.n_clicks", "select-all-btn.n_clicks"),
     ("tube-range-from.value", "tube-range-to.value", "selected-tubes-store.data"), False, False),
    ("selected-tubes-display.children", ("selected-tubes-store.data",), (), False, False),
    ("legend-visible-store.data", ("toggle-legend-btn.n_clicks",),
     ("legend-visible-store.data", "selected-tubes-store.data"), False, False),
    ("..main-graph.figure...click-data.children...tube-selector.style...tube-selector.options..."
     "main-graph.style...image-container.style..",
     ("view-selector.value", "tube-selector.value", "main-graph.clickData",
      "selected-tubes-store.data", "growth-viz-options.value", "legend-visible-store.data",
      "faceted-tubes-store.data", "faceted-dates-store.data"), (), False, False),
    ("image-container.children", ("main-graph.hoverData",),
     ("tube-selector.value", "view-selector.value"), False, False),
    ("image-container.children@67caad7e09fef719a94e6e0284ade55e3356ab950ad1ee37d8918f6d8ce0357a",
     ("view-selector.value", "tube-selector.value"), (), True, True),
)  # fmt: skip


@pytest.fixture(scope="module")
def length_app(tmp_path_factory):
    return build_length_app(tmp_path_factory.mktemp("length_golden"))


def _served(dash, path: str):
    resp = dash.server.test_client().get(path)
    assert resp.status_code == 200
    return json.loads(resp.data)


def _sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _props(items) -> tuple[str, ...]:
    return tuple(f"{item['id']}.{item['property']}" for item in items)


def test_served_layout_is_unchanged(length_app):
    sha = _sha256(_served(length_app.app, "/_dash-layout"))
    assert sha == GOLDEN_LAYOUT_SHA256, f"the served layout changed (new sha256 {sha})"


def test_callback_wiring_is_unchanged(length_app):
    wiring = tuple(
        (
            callback["output"],
            _props(callback["inputs"]),
            _props(callback["state"]),
            callback["prevent_initial_call"],
            callback["clientside_function"] is not None,
        )
        for callback in _served(length_app.app, "/_dash-dependencies")
    )
    assert wiring == GOLDEN_CALLBACKS


def test_clientside_javascript_is_unchanged(length_app):
    sha = _sha256(list(length_app.app._inline_scripts))
    assert sha == GOLDEN_CLIENTSIDE_JS_SHA256, f"clientside JavaScript changed (new sha256 {sha})"


def test_every_callback_is_served_once(length_app):
    outputs = [callback["output"] for callback in _served(length_app.app, "/_dash-dependencies")]
    assert len(outputs) == len(set(outputs)) == len(length_app.app.callback_map)

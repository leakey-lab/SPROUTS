"""Shared helpers for the viz-dash tests (``tests/test_viz_dash_*.py``).

Builds the length / area dashboards from small synthetic CSVs written under a
pytest ``tmp_path`` and drives Dash callbacks through Flask's test client, i.e.
through Dash's real dispatch (``/_dash-update-component``), so ``no_update``,
``Patch`` and ``allow_duplicate`` behave exactly as in the app. Clientside
(JavaScript) callbacks run in Qt's JavaScript engine (:func:`call_clientside`):
no browser is needed.
"""

from __future__ import annotations

import json
import os
from typing import Any

#: (image stem, tube, position, date, value) rows shared by both metrics.
ROWS: tuple[tuple[str, int, int, str, float], ...] = (
    ("T001_L005_2024.07.18", 1, 5, "2024.07.18", 1.5),
    ("T001_L015_2024.07.18", 1, 15, "2024.07.18", 2.5),
    ("T001_L005_2024.08.07", 1, 5, "2024.08.07", 4.0),
    ("T002_L005_2024.07.18", 2, 5, "2024.07.18", 3.0),
    ("T002_L012_2024.08.07", 2, 12, "2024.08.07", 5.25),
    ("T003_L020_2024.08.07", 3, 20, "2024.08.07", 0.75),
)

LENGTH_COLUMN = "Length (mm)"
AREA_COLUMN = "Area (mm²)"


def write_metric_csv(path: str, column: str, rows=ROWS) -> str:
    """Write a root_lengths / root_areas style CSV and return its path."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"Image,Tube,Position,Date,Time,{column},Error\n")
        for image, tube, position, date, value in rows:
            fh.write(f"{image},{tube},{position},{date},120000,{value},\n")
    return path


class FakeImageManager:
    """Minimal stand-in for ImageManager: ``images`` maps name -> path."""

    def __init__(self, images: dict[str, str] | None = None):
        self.images = images or {}


def build_length_app(tmp_path, image_manager=None):
    """Return a DashApp over a synthetic root_lengths.csv in ``tmp_path``."""
    from app.data_processing.data_processor import DataProcessor
    from app.visualization.dash_app import DashApp

    csv = write_metric_csv(os.path.join(tmp_path, "root_lengths.csv"), LENGTH_COLUMN)
    return DashApp(DataProcessor(csv), str(tmp_path), image_manager or FakeImageManager())


def build_area_app(tmp_path):
    """Return a DashAppArea over a synthetic root_areas.csv in ``tmp_path``."""
    from app.data_processing.data_processor_area import DataProcessorArea
    from app.visualization.dash_app_area import DashAppArea

    csv = write_metric_csv(os.path.join(tmp_path, "root_areas.csv"), AREA_COLUMN)
    return DashAppArea(DataProcessorArea(csv), str(tmp_path))


def _split_output_key(key: str):
    """Mirror Dash's callback-id format: '..a.x...b.y..' (multi) or 'a.x' (single)."""
    if key.startswith(".."):
        return [_split_output_key(part) for part in key[2:-2].split("...")]
    component_id, prop = key.rsplit(".", 1)
    return {"id": component_id, "property": prop}


def find_callback_key(dash, output: str) -> str:
    """Return the callback_map key whose outputs include ``output`` ('id.prop').

    The callback that owns the plain output wins; ``allow_duplicate`` outputs
    (keys with an ``@hash`` suffix) match only when nothing else does. Exactly one
    callback must match.
    """

    def matching(strip_suffix: bool) -> list[str]:
        found = []
        for key in dash.callback_map:
            parts = _split_output_key(key)
            parts = parts if isinstance(parts, list) else [parts]
            props = (p["property"].split("@")[0] if strip_suffix else p["property"] for p in parts)
            if output in {f"{p['id']}.{prop}" for p, prop in zip(parts, props, strict=True)}:
                found.append(key)
        return found

    matches = matching(strip_suffix=False) or matching(strip_suffix=True)
    assert len(matches) == 1, f"{output!r} is written by {len(matches)} callbacks: {matches}"
    return matches[0]


def call_callback(
    dash,
    output: str,
    values: dict[str, Any] | None = None,
    changed: list[str] | tuple[str, ...] = (),
):
    """POST one callback through Dash's dispatch.

    Args:
        dash: the ``dash.Dash`` instance.
        output: any 'id.prop' written by the callback (selects the callback).
        values: 'id.prop' -> value for its Inputs/States (missing ones are None).
        changed: the 'id.prop' Inputs that triggered this call.

    Returns:
        (status_code, {id: {prop: value}}) -- the dict is empty for 204 responses.
    """
    values = values or {}
    key = find_callback_key(dash, output)
    spec = dash.callback_map[key]

    def fill(items):
        return [
            {
                "id": item["id"],
                "property": item["property"],
                "value": values.get(f"{item['id']}.{item['property']}"),
            }
            for item in items
        ]

    payload = {
        "output": key,
        "outputs": _split_output_key(key),
        "inputs": fill(spec["inputs"]),
        "state": fill(spec["state"]),
        "changedPropIds": list(changed),
    }
    client = dash.server.test_client()
    resp = client.post(
        "/_dash-update-component",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code == 204:
        return 204, {}
    assert resp.status_code == 200, resp.data[:500]
    body = json.loads(resp.data)
    return 200, {
        cid: {p.split("@")[0]: v for p, v in props.items()}
        for cid, props in body["response"].items()
    }


def walk_components(node):
    """Yield every Dash component in a layout tree (depth first)."""
    if node is None or isinstance(node, (str, int, float)):
        return
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from walk_components(child)
        return
    yield node
    yield from walk_components(getattr(node, "children", None))


def component_ids(layout) -> set[str]:
    """All string component ids in a layout tree."""
    return {c.id for c in walk_components(layout) if isinstance(getattr(c, "id", None), str)}


# ---- clientside (JavaScript) callbacks --------------------------------------------

#: What :func:`call_clientside` returns for ``window.dash_clientside.no_update``.
NO_UPDATE = "<no_update>"
_QAPP = None

_JS_PRELUDE = """
var window = this;
window.dash_clientside = {no_update: {description: 'no_update'}};
window.setTimeout = function (f) { f(); return 0; };
window.document = {querySelector: function () { return null; }};
var document = window.document;
"""


def clientside_function(dash, output: str) -> dict:
    """The ``_callback_list`` entry of the clientside callback writing ``output``."""
    key = find_callback_key(dash, output)
    entry = next(cb for cb in dash._callback_list if cb["output"] == key)
    assert entry.get("clientside_function"), f"{output!r} is written by a server callback"
    return entry


def call_clientside(
    dash,
    output: str,
    values: dict[str, Any] | None = None,
    changed: list[str] | tuple[str, ...] = (),
    *,
    setup: str = "",
):
    """Run the clientside callback writing ``output`` as the Dash renderer would.

    The page's inline clientside scripts are evaluated in a ``QJSEngine`` with a
    minimal ``window`` (``dash_clientside.no_update``, ``callback_context.triggered``,
    an immediate ``setTimeout`` and a ``document`` without elements); ``setup`` is
    JavaScript run before the call (e.g. a fake Plotly graph). A returned Promise
    is awaited.

    Returns:
        (outputs {id: {prop: value}}, engine): a ``no_update`` value is
        :data:`NO_UPDATE`; the engine allows inspecting what the callback changed.
    """
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
    from PyQt6.QtWidgets import QApplication

    global _QAPP
    # Kept referenced: a QApplication nobody holds is deleted at once, and Qt with it.
    _QAPP = QApplication.instance() or QApplication(["viz-dash-tests"])
    values = values or {}
    entry = clientside_function(dash, output)
    spec = dash.callback_map[entry["output"]]
    args = [
        values.get(f"{item['id']}.{item['property']}") for item in spec["inputs"] + spec["state"]
    ]
    engine = QJSEngine()

    def run(source: str):
        result = engine.evaluate(source)
        assert not result.isError(), result.toString()
        return result

    run(_JS_PRELUDE)
    run(setup)
    for script in dash._inline_scripts:
        run(script)
    triggered = [{"prop_id": prop, "value": values.get(prop)} for prop in changed]
    function = entry["clientside_function"]
    run(
        "window.__result = undefined; window.dash_clientside.callback_context = "
        f"{{triggered: {json.dumps(triggered)}}};"
        f"var __ret = window.dash_clientside[{json.dumps(function['namespace'])}]"
        f"[{json.dumps(function['function_name'])}].apply(null, {json.dumps(args)});"
        "delete window.dash_clientside.callback_context;"
        "if (__ret && typeof __ret.then === 'function') {"
        "  __ret.then(function (v) { window.__result = {value: v}; });"
        "} else { window.__result = {value: __ret}; }"
    )
    for _ in range(20):  # Promise reactions run from posted events
        if run("window.__result !== undefined").toBool():
            break
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents()
    encoded = run(
        "JSON.stringify(window.__result.value, function (k, v) {"
        f" return v === window.dash_clientside.no_update ? {json.dumps(NO_UPDATE)} : v; }})"
    ).toString()
    result = json.loads(encoded) if encoded != "undefined" else None
    parts = _split_output_key(entry["output"])
    if not isinstance(parts, list):
        return {parts["id"]: {parts["property"]: result}}, engine
    outputs: dict[str, dict[str, Any]] = {}
    for part, value in zip(parts, result, strict=True):
        outputs.setdefault(part["id"], {})[part["property"].split("@")[0]] = value
    return outputs, engine

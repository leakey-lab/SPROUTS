"""Shared helpers for the viz-dash tests (``tests/test_viz_dash_*.py``).

Builds the length / area dashboards from small synthetic CSVs written under a
pytest ``tmp_path`` and drives Dash callbacks through Flask's test client, i.e.
through Dash's real dispatch (``/_dash-update-component``), so ``no_update``,
``Patch`` and ``allow_duplicate`` behave exactly as in the app.
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

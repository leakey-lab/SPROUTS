"""PV-16: 'Export CSV' writes the data behind the current view, with provenance.

The expected numbers below are derived by hand from ``tests.viz_dash_helpers.ROWS``:

    tube 1: 2024-07-18 L5=1.5 L15=2.5 | 2024-08-07 L5=4.0
    tube 2: 2024-07-18 L5=3.0         | 2024-08-07 L12=5.25
    tube 3:                           | 2024-08-07 L20=0.75
"""

from __future__ import annotations

import hashlib
import io
import math
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

import app
from app import provenance
from app.visualization import dash_export
from tests.viz_dash_helpers import build_area_app, build_length_app, call_callback


def _read(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), comment="#")


def _header(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        if line.startswith("# ") and ": " in line:
            key, value = line[2:].split(": ", 1)
            fields[key] = value
    return fields


def _sha256(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.fixture
def length_app(tmp_path):
    return build_length_app(tmp_path)


@pytest.fixture
def area_app(tmp_path):
    return build_area_app(tmp_path)


def _export(dash, values) -> dict:
    status, out = call_callback(
        dash, "viz-export-download.data", {"viz-export-btn.n_clicks": 1, **values},
        changed=["viz-export-btn.n_clicks"],
    )  # fmt: skip
    assert status == 200
    return out["viz-export-download"]["data"]


# ---- through the dashboard ------------------------------------------------------


def test_stacked_export_holds_tube_date_totals_and_provenance(length_app, tmp_path):
    download = _export(length_app.app, {"view-selector.value": "stacked"})
    assert download["filename"].startswith("root_length_stacked_")
    assert download["filename"].endswith(".csv")

    table = _read(download["content"])
    assert list(table.columns) == ["Tube", "Date", "Total Length (mm)"]
    rows = {(r.Tube, r.Date): r[2] for r in table.itertuples(index=False)}
    assert rows == {
        (1, "2024-07-18"): pytest.approx(4.0),
        (1, "2024-08-07"): pytest.approx(4.0),
        (2, "2024-07-18"): pytest.approx(3.0),
        (2, "2024-08-07"): pytest.approx(5.25),
        (3, "2024-08-07"): pytest.approx(0.75),
    }

    header = _header(download["content"])
    assert header["source_sha256"] == _sha256(tmp_path / "root_lengths.csv")
    assert header["source_csv"] == "root_lengths.csv"
    assert header["view"] == "stacked"
    assert header["calibration"].startswith("field of view 18.00 x 13.00 mm")
    assert "no record next to root_lengths.csv" in header["calibration"]
    assert app.__version__ in header["software"]
    generated = datetime.fromisoformat(header["generated_at"].replace("Z", "+00:00"))
    assert generated.tzinfo is not None


def test_the_export_takes_the_calibration_from_the_csv_record(length_app, tmp_path):
    provenance.write_sidecar(
        tmp_path / "root_lengths.csv",
        {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}},
    )
    line = _header(_export(length_app.app, {"view-selector.value": "stacked"})["content"])[
        "calibration"
    ]
    assert "18.00 x 13.50 mm, as recorded with root_lengths.csv" in line
    assert "; this app now uses 18.00 x 13.00 mm" in line
    assert line.count("(mm per pixel = field of view / image resolution)") == 1


def test_a_record_equal_to_this_apps_calibration_adds_no_note(length_app, tmp_path):
    provenance.write_sidecar(
        tmp_path / "root_lengths.csv",
        {"calibration": provenance.calibration_info()},
    )
    line = _header(_export(length_app.app, {"view-selector.value": "stacked"})["content"])[
        "calibration"
    ]
    assert line == (
        "field of view 18.00 x 13.00 mm, as recorded with root_lengths.csv "
        "(mm per pixel = field of view / image resolution)"
    )


def test_time_export_has_the_field_average_and_the_selected_tubes(length_app):
    download = _export(
        length_app.app,
        {"view-selector.value": "time", "selected-tubes-store.data": [2],
         "growth-viz-options.value": ["show_field_avg", "show_field_var"]},
    )  # fmt: skip
    table = _read(download["content"])
    assert list(table.columns) == ["Series", "Date", "Total Length (mm)", "SD (mm)", "Tubes"]
    field = table[table.Series == "Field average"].set_index("Date")
    assert field.loc["2024-07-18", "Total Length (mm)"] == pytest.approx(3.5)
    assert field.loc["2024-07-18", "SD (mm)"] == pytest.approx(math.sqrt(0.5))
    assert field.loc["2024-08-07", "Total Length (mm)"] == pytest.approx(10.0 / 3)
    assert field.loc["2024-08-07", "Tubes"] == 3
    tube = table[table.Series == "Tube 2"].set_index("Date")["Total Length (mm)"]
    assert tube.to_dict() == {"2024-07-18": pytest.approx(3.0), "2024-08-07": pytest.approx(5.25)}
    assert _header(download["content"])["filters"] == "tubes=2; field average=shown"


def test_time_export_without_field_average_lists_only_tubes(length_app):
    download = _export(
        length_app.app,
        {"view-selector.value": "time", "selected-tubes-store.data": [1],
         "growth-viz-options.value": []},
    )  # fmt: skip
    assert set(_read(download["content"]).Series) == {"Tube 1"}


def test_lines_export_lists_depth_intervals_of_the_plotted_tube(length_app):
    download = _export(length_app.app, {"view-selector.value": "lines", "tube-selector.value": 1})
    table = _read(download["content"])
    assert list(table.columns) == [
        "Tube", "Date", "Interval start", "Interval end", "Mean Length (mm)",
        "Min Length (mm)", "Max Length (mm)", "SD (mm)", "Count",
    ]  # fmt: skip
    got = {(r.Date, r[3]): (r[4], r.Count) for r in table.itertuples(index=False)}
    assert got == {
        ("2024-07-18", 10): (pytest.approx(1.5), 1),
        ("2024-07-18", 20): (pytest.approx(2.5), 1),
        ("2024-08-07", 10): (pytest.approx(4.0), 1),
    }
    assert set(table.Tube) == {1}


def test_faceted_export_pairs_tube_and_field_means(length_app):
    download = _export(
        length_app.app,
        {"view-selector.value": "faceted", "faceted-tubes-store.data": [2],
         "faceted-dates-store.data": []},
    )  # fmt: skip
    table = _read(download["content"])
    assert list(table.columns) == [
        "Tube", "Date", "Interval start", "Interval end", "Mean Length (mm)", "Count",
        "Field Mean Length (mm)", "Field SD (mm)",
    ]  # fmt: skip
    got = {(r.Date, r[3]): (r[4], r[6]) for r in table.itertuples(index=False)}
    # Tube 2 has L5 on 07-18 (interval L1-10) and L12 on 08-07 (L11-20). Field
    # means pool every tube in the same interval and date:
    #   07-18 L1-10  = mean(tube 1 L5 1.5, tube 2 L5 3.0)     = 2.25
    #   08-07 L11-20 = mean(tube 2 L12 5.25, tube 3 L20 0.75) = 3.0
    assert got == {
        ("2024-07-18", 10): (pytest.approx(3.0), pytest.approx(2.25)),
        ("2024-08-07", 20): (pytest.approx(5.25), pytest.approx(3.0)),
    }


def test_faceted_export_without_tubes_is_not_offered(length_app):
    status, _ = call_callback(
        length_app.app,
        "viz-export-download.data",
        {"viz-export-btn.n_clicks": 1, "view-selector.value": "faceted",
         "faceted-tubes-store.data": []},
        changed=["viz-export-btn.n_clicks"],
    )  # fmt: skip
    assert status == 204


def test_area_profile_export_lists_position_means(area_app, tmp_path):
    download = _export(area_app.app, {"view-selector.value": "profile", "tube-selector.value": 2})
    assert download["filename"].startswith("root_area_profile_")
    table = _read(download["content"])
    assert list(table.columns) == ["Tube", "Date", "Position", "Mean Area (mm²)"]
    got = {(r.Date, r.Position): r[3] for r in table.itertuples(index=False)}
    assert got == {("2024-07-18", 5): pytest.approx(3.0), ("2024-08-07", 12): pytest.approx(5.25)}
    assert _header(download["content"])["source_sha256"] == _sha256(tmp_path / "root_areas.csv")


def test_area_stacked_export_uses_the_area_column(area_app):
    table = _read(_export(area_app.app, {"view-selector.value": "stacked"})["content"])
    assert list(table.columns) == ["Tube", "Date", "Total Area (mm²)"]
    assert table["Total Area (mm²)"].sum() == pytest.approx(17.0)


# ---- the Qt-free writer ------------------------------------------------------------


def test_render_csv_round_trips_the_table(tmp_path):
    source = tmp_path / "root_lengths.csv"
    source.write_text("Image,Tube\n", encoding="utf-8")
    frame = pd.DataFrame({"Tube": [1, 2], "Date": ["2024-07-18", "2024-08-07"], "v": [0.1, 2.5]})
    table = dash_export.ExportTable(frame, description="demo rows", filters={"tubes": "all"})
    when = datetime(2026, 9, 28, 17, 45, 12, tzinfo=timezone.utc)
    text = dash_export.render_csv(
        table,
        source_csv=str(source),
        metric_column="Length (mm)",
        view="stacked",
        generated_at=when,
    )
    pd.testing.assert_frame_equal(_read(text), frame)
    header = _header(text)
    assert header["generated_at"] == "2026-09-28T17:45:12Z"
    assert header["description"] == "demo rows"
    assert header["metric_column"] == "Length (mm)"
    assert header["filters"] == "tubes=all"


def test_missing_source_is_recorded_not_fatal(tmp_path):
    text = dash_export.render_csv(
        dash_export.ExportTable(pd.DataFrame({"a": [1]}), description="d"),
        source_csv=os.path.join(tmp_path, "gone.csv"),
        metric_column="Area (mm²)",
        view="time",
    )
    assert _header(text)["source_sha256"] == "unavailable"


def test_export_filename_is_descriptive_and_sortable():
    when = datetime(2026, 9, 28, 17, 45, 12)
    assert dash_export.export_filename("root_area", "profile", when) == (
        "root_area_profile_20260928_174512.csv"
    )


def test_the_export_compares_with_the_projects_calibration(tmp_path):
    """O3: the export compares the CSV's recorded field of view with its project's."""
    root = tmp_path / "project"
    (root / "skeletons").mkdir(parents=True)
    (root / "sprouts_project.json").write_text(
        '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}}',
        encoding="utf-8",
    )
    source = root / "skeletons" / "root_lengths.csv"
    source.write_text("Image,Tube\n", encoding="utf-8")
    provenance.write_sidecar(source, {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}})
    table = dash_export.ExportTable(pd.DataFrame({"a": [1]}), description="d")
    lines = dash_export.provenance_lines(
        table, source_csv=str(source), metric_column="Length (mm)", view="stacked"
    )
    (line,) = [line for line in lines if line.startswith("# calibration: ")]
    assert "18.00 x 13.50 mm, as recorded with root_lengths.csv" in line
    assert "this app now uses" not in line
    # Recorded at 13.0 in a 13.5 project: the note names the project's field of view.
    provenance.write_sidecar(source, {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.0}})
    lines = dash_export.provenance_lines(
        table, source_csv=str(source), metric_column="Length (mm)", view="stacked"
    )
    (line,) = [line for line in lines if line.startswith("# calibration: ")]
    assert line.endswith("; this project uses 18.00 x 13.50 mm")


def test_the_export_takes_the_project_root_when_the_caller_knows_it(tmp_path):
    """A CSV outside the project's folders (or deeper than the walk) is compared with the
    project's field of view when the caller passes the project root (review I3)."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "sprouts_project.json").write_text(
        '{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}}',
        encoding="utf-8",
    )
    source = tmp_path / "copied" / "root_lengths.csv"
    source.parent.mkdir()
    source.write_text("Image,Tube\n", encoding="utf-8")
    provenance.write_sidecar(source, {"calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.0}})
    table = dash_export.ExportTable(pd.DataFrame({"a": [1]}), description="d")
    text = dash_export.render_csv(
        table,
        source_csv=str(source),
        metric_column="Length (mm)",
        view="stacked",
        project_root=str(root),
    )
    assert _header(text)["calibration"].endswith("; this project uses 18.00 x 13.50 mm")
    # Without it, the CSV's own folders hold no project file: the app's default, the same.
    assert "uses" not in dash_export.calibration_line(str(source))

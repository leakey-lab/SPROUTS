"""
"Export CSV" for the SPROUTS dashboards (PV-16, FAIR R1.2).

The export holds the table behind the current view: the same DataCache
aggregates the chart is drawn from (nothing is recomputed another way), in tidy
long form, after ``# key: value`` provenance lines -- source file and SHA-256,
UTC timestamp, software version and commit, calibration, view and filters. Read
it back with ``pandas.read_csv(path, comment="#")``.

This is a separate file: the metric CSVs (LENGTH_CSV_HEADERS / AREA_CSV_HEADERS)
are untouched. Qt-free and Dash-free.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.provenance import (
    app_info,
    calibration_info,
    csv_calibration,
    file_sha256,
    git_commit,
    same_calibration,
)

DATE_FORMAT = "%Y-%m-%d"
FIELD_AVERAGE_SERIES = "Field average"


@dataclass(frozen=True)
class ExportTable:
    """The rows behind one view plus how to describe them in the header."""

    frame: pd.DataFrame
    #: One line saying what a row is.
    description: str
    #: Selection that produced the rows, e.g. {"tubes": "1, 3", "dates": "all"}.
    filters: Mapping[str, str] = field(default_factory=dict)


# ---- tables (one per kind of view) -------------------------------------------


def _unit(value_column: str) -> str:
    """'Length (mm)' -> 'mm'."""
    match = re.search(r"\(([^)]*)\)\s*$", value_column)
    return match.group(1) if match else ""


def _day(date: Any) -> str:
    return pd.Timestamp(date).strftime(DATE_FORMAT)


def tube_date_totals(cache) -> pd.DataFrame:
    """Total metric per tube and date (the stacked bars; ``cache.tube_date_groups``)."""
    total = f"Total {cache.value_column}"
    frame = cache.tube_date_groups.rename(total).reset_index()
    frame["Tube"] = frame["Tube"].astype("int64")
    frame["Date"] = frame["Date"].dt.strftime(DATE_FORMAT)
    return frame[["Tube", "Date", total]]


def growth_over_time(cache, tubes: Sequence[Any], *, field_average: bool) -> pd.DataFrame:
    """Field-average series (mean, SD and tube count per merged date) and per-tube totals."""
    total, sd = f"Total {cache.value_column}", f"SD ({_unit(cache.value_column)})"
    columns = ["Series", "Date", total, sd, "Tubes"]
    parts = []
    if field_average:
        stats = cache.get_field_growth()
        parts.append(
            pd.DataFrame(
                {
                    "Series": FIELD_AVERAGE_SERIES,
                    "Date": stats["Date"].dt.strftime(DATE_FORMAT),
                    total: stats["mean"],
                    sd: stats["std"],
                    "Tubes": stats["count"].astype("Int64"),
                }
            )
        )
    for tube in tubes:
        totals = cache.get_tube_totals(tube)
        if totals.empty:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "Series": f"Tube {int(tube)}",
                    "Date": totals.index.strftime(DATE_FORMAT),
                    total: totals.to_numpy(),
                    sd: float("nan"),
                    "Tubes": pd.array([pd.NA] * len(totals), dtype="Int64"),
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=columns)
    return pd.concat(parts, ignore_index=True)[columns]


def depth_intervals(
    cache, tube: Any, dates: Iterable[Any] | None = None, interval_size: int = 10
) -> pd.DataFrame:
    """Per-interval statistics of one tube for every date (the Depth Profile view)."""
    value, unit = cache.value_column, _unit(cache.value_column)
    rows = []
    for date in cache.dates if dates is None else dates:
        table = cache.get_interval_data(tube, date, interval_size)
        for end in sorted(table):
            stats = table[end]
            if stats["count"] > 0:
                rows.append(
                    (int(tube), _day(date), stats["interval_start"], end, stats["avg"],
                     stats["min"], stats["max"], stats["std"], stats["count"])
                )  # fmt: skip
    columns = [
        "Tube", "Date", "Interval start", "Interval end", f"Mean {value}", f"Min {value}",
        f"Max {value}", f"SD ({unit})", "Count",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)


def faceted_intervals(profile, value_column: str) -> pd.DataFrame:
    """Tube vs field-average interval means of a faceted profile (as drawn).

    ``profile`` is a :class:`~app.visualization.faceted_profile.FacetedProfile`.
    """
    unit = _unit(value_column)
    rows = []
    for row in profile.cells:
        for cell in row:
            for start, end, mean, count, field_mean, field_sd in zip(
                cell.interval_starts, cell.interval_ends, cell.tube_means,
                cell.tube_counts, cell.field_means, cell.field_stds, strict=True,
            ):  # fmt: skip
                rows.append((int(cell.tube), _day(cell.date), start, end, mean, count,
                             field_mean, field_sd))  # fmt: skip
    columns = [
        "Tube", "Date", "Interval start", "Interval end", f"Mean {value_column}", "Count",
        f"Field Mean {value_column}", f"Field SD ({unit})",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)


def position_means(cache, tube: Any) -> pd.DataFrame:
    """Mean metric per measured position and date for one tube (the area profile)."""
    rows = []
    for date in cache.dates:
        table = cache.get_interval_data(tube, date, 1)  # one "interval" per position
        for position in sorted(table):
            stats = table[position]
            if stats["count"] > 0:
                rows.append((int(tube), _day(date), position, stats["avg"]))
    return pd.DataFrame(rows, columns=["Tube", "Date", "Position", f"Mean {cache.value_column}"])


# ---- writing ------------------------------------------------------------------


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


_FORMULA = "(mm per pixel = field of view / image resolution)"


def _fov_mm(calibration: Mapping[str, float]) -> str:
    return f"{calibration['fov_width_mm']:.2f} x {calibration['fov_height_mm']:.2f} mm"


def calibration_line(source_csv: str) -> str:
    """The ``calibration`` header line: the field of view ``source_csv`` was measured with
    (its provenance sidecar), and this app's when that differs; this app's alone, saying so,
    when the CSV has no record (M2)."""
    name = os.path.basename(source_csv)
    ours = calibration_info()
    recorded = csv_calibration(source_csv)
    if recorded is None:
        return f"field of view {_fov_mm(ours)}, this app's: no record next to {name} {_FORMULA}"
    line = f"field of view {_fov_mm(recorded)}, as recorded with {name} {_FORMULA}"
    if not same_calibration(recorded, ours):
        line += f"; this app now uses {_fov_mm(ours)}"
    return line


def provenance_lines(
    table: ExportTable,
    *,
    source_csv: str,
    metric_column: str,
    view: str,
    generated_at: datetime | None = None,
) -> list[str]:
    """``# key: value`` header lines describing where the rows come from."""
    when = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    info = app_info()
    commit = git_commit()
    fields = [
        ("export", f"{info['full_name']} dashboard data"),
        ("description", table.description),
        ("view", view),
        ("filters", "; ".join(f"{k}={v}" for k, v in table.filters.items()) or "none"),
        ("metric_column", metric_column),
        ("source_csv", os.path.basename(source_csv)),
        ("source_sha256", file_sha256(source_csv) or "unavailable"),
        ("generated_at", when.strftime("%Y-%m-%dT%H:%M:%SZ")),
        (
            "software",
            f"{info['name']} {info['version']} (git {commit[:12] if commit else 'unknown'})",
        ),
        ("calibration", calibration_line(source_csv)),
        ("read_with", 'pandas.read_csv(path, comment="#")'),
    ]
    return [f"# {key}: {_one_line(value)}" for key, value in fields]


def render_csv(
    table: ExportTable,
    *,
    source_csv: str,
    metric_column: str,
    view: str,
    generated_at: datetime | None = None,
) -> str:
    """The export file's text: provenance lines, then the table as CSV."""
    header = provenance_lines(
        table,
        source_csv=source_csv,
        metric_column=metric_column,
        view=view,
        generated_at=generated_at,
    )
    return "\n".join(header) + "\n" + table.frame.to_csv(index=False, lineterminator="\n")


def export_filename(metric_slug: str, view: str, when: datetime) -> str:
    """``root_length_stacked_20260928_174512.csv`` (local time, sorts by date)."""
    return f"{metric_slug}_{view}_{when:%Y%m%d_%H%M%S}.csv"

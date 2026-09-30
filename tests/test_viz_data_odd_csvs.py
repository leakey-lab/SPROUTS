"""viz-data: a metric CSV the dashboards cannot chart says why (PKG-VIZ, V3).

Before, every such file opened a dashboard that only said "No data found in CSV
file": also a 47k-row CSV whose measurements had all failed, or a hand-edited
file without its Date column. ``MetricDataProcessor.empty_reason()`` now names
the cause, and the dashboard window shows it.
"""

from __future__ import annotations

import csv

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.data_processing.data_processor_area import DataProcessorArea


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return str(path)


def test_a_csv_with_rows_has_no_empty_reason(tmp_path):
    csv_path = _write(
        tmp_path / "l.csv",
        LENGTH_CSV_HEADERS,
        [["CAM_T001_L010_2024.07.18_120000_001", 1, 10, "2024.07.18", "12:00:00", 1.5, ""]],
    )
    assert DataProcessor(csv_path).empty_reason() is None


def test_only_a_header_is_reported_as_no_rows(tmp_path):
    processor = DataProcessor(_write(tmp_path / "l.csv", LENGTH_CSV_HEADERS, []))
    assert processor.df.empty
    assert processor.empty_reason() == "The CSV has no measurement rows."


def test_a_csv_whose_measurements_all_failed_says_so(tmp_path):
    failed = [
        [f"CAM_T001_L{i:03d}", "", "", "", "", 0, "cannot identify image file"] for i in range(3)
    ]
    processor = DataProcessor(_write(tmp_path / "l.csv", LENGTH_CSV_HEADERS, failed))

    reason = processor.empty_reason()

    assert processor.df.empty
    assert "3 failed measurements" in reason
    assert "Error column" in reason


def test_a_csv_without_the_metric_column_names_it(tmp_path):
    # e.g. a root_areas.csv copied over root_lengths.csv
    header = ["Image", "Tube", "Position", "Date", "Time", "Area (mm²)", "Error"]
    processor = DataProcessor(
        _write(tmp_path / "l.csv", header, [["x", 1, 1, "2024.07.18", "12:00:00", 1.0, ""]])
    )
    assert processor.df.empty
    assert processor.empty_reason() == "The CSV has no 'Length (mm)' column."


def test_a_csv_without_a_key_column_names_it(tmp_path):
    header = ["Image", "Tube", "Position", "Time", "Area (mm²)", "Error"]
    processor = DataProcessorArea(
        _write(tmp_path / "a.csv", header, [["x", 1, 1, "12:00:00", 1.0, ""]])
    )
    assert processor.df.empty
    assert processor.empty_reason() == "The CSV has no 'Date' column."

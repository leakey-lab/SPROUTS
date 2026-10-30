"""Regression test for the CSV 'Error' column (config item 4).

Pins that write_metric_csv persists the 'Error' text already attached by the
per-image workers on failure, and that DataProcessor tolerates the extra
trailing 'Error' column (it selects by value_column name, so it is harmless).
"""

import csv

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.inference.metrics import run_metric_pool, write_metric_csv


def test_error_column_persisted_and_dataprocessor_tolerates_it(tmp_path):
    # A worker that raises for exactly one image ("b"), succeeds for the rest.
    def worker(name, path):
        if name == "b":
            raise RuntimeError("boom")
        info_pos = int(name)  # "a"->fails below; we use numeric names for success rows
        return {
            "Image": name,
            "Tube": 1,
            "Position": info_pos,
            "Date": "2024.01.01",
            "Time": "00:00:01",
            "Length (mm)": round(float(info_pos), 2),
        }

    # Failure handler mirrors length_row's failure dict (sets 'Error').
    def safe_worker(name, path):
        try:
            return worker(name, path)
        except Exception as e:  # noqa: BLE001
            return {
                "Image": name,
                "Tube": None,
                "Position": None,
                "Date": None,
                "Time": None,
                "Length (mm)": 0,
                "Error": str(e),
            }

    images = {"10": "p10", "b": "pb", "20": "p20"}
    results = run_metric_pool(images, safe_worker)

    csv_path = tmp_path / "root_lengths.csv"
    write_metric_csv(results, str(csv_path), LENGTH_CSV_HEADERS)

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = {r["Image"]: r for r in csv.DictReader(f)}

    # (a) failed row has a non-empty Error cell and Length (mm) == 0
    assert "Error" in rows["b"]
    assert rows["b"]["Error"] != ""
    assert float(rows["b"]["Length (mm)"]) == 0

    # (b) successful rows have an empty Error cell
    assert rows["10"]["Error"] == ""
    assert rows["20"]["Error"] == ""

    # DataProcessor tolerates the extra trailing 'Error' column: it selects by
    # value_column name, so the trailing column is harmless. The failed row is
    # dropped (NaN Tube/Position/Date), leaving the two successful tubes' rows.
    dp = DataProcessor(str(csv_path))
    assert list(dp.get_unique_tubes()) == [1]
    assert dp.get_unique_positions() == [10, 20]


def _fake_png(tmp_path):
    p = tmp_path / "deep" / "dir" / "x_T1_L1_fake.png"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"not a png")
    return p


def test_an_unreadable_file_names_only_the_file(tmp_path):
    from app.inference import metrics

    p = _fake_png(tmp_path)
    assert metrics.length_row("x", str(p))["Error"] == "Could not read image: x_T1_L1_fake.png"
    assert metrics.area_row("x", str(p))["Error"] == "Could not read mask: x_T1_L1_fake.png"


def test_an_os_error_loses_its_folder(tmp_path, monkeypatch):
    from app.inference import metrics

    p = _fake_png(tmp_path)

    def boom(path, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(p))

    monkeypatch.setattr(metrics, "preprocess_image", boom)
    error = metrics.length_row("x", str(p))["Error"]
    assert "x_T1_L1_fake.png" in error
    assert str(tmp_path) not in error
    assert repr(str(tmp_path))[1:-1] not in error

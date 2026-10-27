"""The dashboards' chart frame (app/data_processing/data_processor.py).

The four chart columns are typed in a frame of their own, so the Image, Time and Error
text is neither copied while the CSV is prepared nor kept with the dashboard.
"""

import csv
import gc
import weakref

import pandas as pd

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor, RowExclusions


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LENGTH_CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in LENGTH_CSV_HEADERS})
    return str(path)


def _row(image, tube, position, date="2024.07.18", length=1.5, error=""):
    return {
        "Image": image,
        "Tube": tube,
        "Position": position,
        "Date": date,
        "Time": "12:00:00",
        "Length (mm)": length,
        "Error": error,
    }


GOOD = [
    _row("CAM_T001_L010_2024.07.18_120000_001", 1, 10),
    _row("CAM_T001_L011_2024.07.18_120002_001", 1, 11),
]
FAILED = _row("CAM_T001_L012_2024.07.18_120004", "", "", date="", length=0, error="boom")
BAD_NAME = _row("IMG_0001", "", "", date="")
LATER = _row("CAM_T002_L003_2024.07.19_120000_001", "2.0", "3.0", "2024.07.19", 4)


def test_kept_rows_keep_their_csv_row_numbers_and_types(tmp_path):
    dp = DataProcessor(_write(tmp_path / "l.csv", [FAILED, *GOOD, BAD_NAME, LATER]))

    assert dp.df.index.tolist() == [1, 2, 4]
    assert [str(dtype) for dtype in dp.df.dtypes] == [
        "datetime64[ns]",
        "int64",
        "int64",
        "float64",
    ]
    assert dp.df["Tube"].tolist() == [1, 1, 2]  # "2.0" read as a number, then an integer
    assert dp.df["Position"].tolist() == [10, 11, 3]
    assert dp.df["Length (mm)"].tolist() == [1.5, 1.5, 4.0]
    assert dp.df["Date"].tolist() == [
        pd.Timestamp("2024-07-18"),
        pd.Timestamp("2024-07-18"),
        pd.Timestamp("2024-07-19"),
    ]
    assert dp.exclusions == RowExclusions(failed=1, bad_name=1)


def test_a_csv_without_left_out_rows_keeps_its_default_row_numbers(tmp_path):
    dp = DataProcessor(_write(tmp_path / "l.csv", [*GOOD, LATER]))

    assert isinstance(dp.df.index, pd.RangeIndex)
    assert dp.df.index.tolist() == [0, 1, 2]
    assert dp.exclusions == RowExclusions()


def test_the_frame_read_from_the_csv_is_not_kept(tmp_path, monkeypatch):
    # The Image, Time and Error text goes with the frame read from the CSV.
    read = []
    original = DataProcessor._read_csv

    def keep_a_weak_reference(self):
        frame = original(self)
        read.append(weakref.ref(frame))
        return frame

    monkeypatch.setattr(DataProcessor, "_read_csv", keep_a_weak_reference)
    dp = DataProcessor(_write(tmp_path / "l.csv", [FAILED, *GOOD]))
    gc.collect()

    assert len(dp.df) == 2
    assert read and read[0]() is None

"""MetricDataProcessor reports the rows it leaves out of the dashboards (FR-15).

Rows without a readable Tube/Position/Date/value used to vanish silently. The
processor now counts them by cause and logs one warning: failed measurements
(non-empty ``Error`` cell, written by the Measure workers), image names that
``app.naming`` cannot read, and other rows (missing or non-numeric fields).
"""

import csv
import logging

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
# What the Measure workers write for an image they could not process.
FAILED = _row("CAM_T001_L012_2024.07.18_120004", "", "", date="", length=0, error="boom")
# What they write for a name the parser could not read (no Error text).
BAD_NAME = _row("IMG_0001", "", "", date="")


def test_clean_csv_excludes_nothing(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app.data_processing.data_processor"):
        dp = DataProcessor(_write(tmp_path / "l.csv", GOOD))

    assert dp.dropped_rows == 0
    assert dp.exclusions == RowExclusions()
    assert dp.exclusions.message() is None
    assert not caplog.records


def test_excluded_rows_are_counted_by_cause_and_logged(tmp_path, caplog):
    rows = GOOD + [FAILED, BAD_NAME, dict(BAD_NAME, Image="IMG_0002")]
    with caplog.at_level(logging.WARNING, logger="app.data_processing.data_processor"):
        dp = DataProcessor(_write(tmp_path / "l.csv", rows))

    assert len(dp.df) == 2
    assert dp.dropped_rows == 3
    assert dp.exclusions == RowExclusions(failed=1, bad_name=2)
    assert dp.exclusions.message() == (
        "3 rows are not shown: 1 failed measurement (see the CSV's Error column) and "
        "2 image names not in the <field>_T<tube>_L<position>_YYYY.MM.DD_HHMMSS_... format."
    )
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert dp.exclusions.message() in caplog.records[0].getMessage()


def test_readable_name_with_a_non_numeric_value_is_unreadable(tmp_path):
    odd = _row("CAM_T001_L013_2024.07.18_120006_001", 1, 13, length="n/a")
    dp = DataProcessor(_write(tmp_path / "l.csv", GOOD + [odd]))

    assert dp.exclusions == RowExclusions(unreadable=1)
    assert dp.exclusions.message() == (
        "1 row is not shown: 1 row with a missing or non-numeric tube, position, date or value."
    )


def test_csv_without_error_column_still_classifies_names(tmp_path):
    path = tmp_path / "legacy.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LENGTH_CSV_HEADERS[:6])
        writer.writerow(["CAM_T001_L010_2024.07.18_120000_001", 1, 10, "2024.07.18", "", 1.0])
        writer.writerow(["IMG_0001", "", "", "", "", 0])

    dp = DataProcessor(str(path))

    assert dp.exclusions == RowExclusions(bad_name=1)


def test_message_lists_every_cause_in_order():
    assert RowExclusions(failed=2, bad_name=1, unreadable=1).message() == (
        "4 rows are not shown: 2 failed measurements (see the CSV's Error column), "
        "1 image name not in the <field>_T<tube>_L<position>_YYYY.MM.DD_HHMMSS_... format "
        "and 1 row with a missing or non-numeric tube, position, date or value."
    )


def test_length_processor_never_reads_an_area_column(tmp_path, caplog):
    from app.config import AREA_CSV_HEADERS

    path = tmp_path / "root_areas.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(AREA_CSV_HEADERS)
        writer.writerow(["CAM_T001_L010_2024.07.18_120000_001", 1, 10, "2024.07.18", "", 3.25, ""])

    with caplog.at_level(logging.ERROR, logger="app.data_processing.data_processor"):
        dp = DataProcessor(str(path))

    assert dp.df.empty  # before: the area values were silently charted as length
    assert "missing metric column" in caplog.text


def test_area_processor_maps_a_mangled_legacy_area_header(tmp_path):
    from app.data_processing.data_processor_area import DataProcessorArea

    path = tmp_path / "root_areas.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Image", "Tube", "Position", "Date", "Time", "Area (mm^2)", "Error"])
        writer.writerow(["CAM_T001_L010_2024.07.18_120000_001", 1, 10, "2024.07.18", "", 3.25, ""])

    dp = DataProcessorArea(str(path))

    assert dp.df["Area (mm²)"].tolist() == [3.25]


_HEADER = b"Image,Tube,Position,Date,Time,Length (mm),Error\n"
_MEASURED = "Feldé_T001_L005_2024.07.18_120000_001,1,5,2024.07.18,120000,1.5,\n"
_BAD_NAME_ROW = b"IMG_0002,,5,2024.07.18,120000,2.0,\n"


def test_a_legacy_csv_that_is_not_utf8_is_read_as_latin1(tmp_path):
    path = tmp_path / "root_lengths.csv"
    path.write_bytes(_HEADER + _MEASURED.encode("cp1252") + _BAD_NAME_ROW)

    dp = DataProcessor(str(path))

    assert dp.df["Length (mm)"].tolist() == [1.5]
    assert dp.exclusions == RowExclusions(bad_name=1)


def test_a_damaged_utf8_csv_keeps_its_header_and_names(tmp_path):
    # A NUL byte before a byte UTF-8 cannot decode: "utf-8-sig" refuses the file, the exact
    # "utf-8" attempt reads it (pandas' C parser stops each field at the NUL) and strips the
    # BOM. Read as latin-1, the header would start with "ï»¿" and the Image column be lost.
    path = tmp_path / "root_lengths.csv"
    damaged = b"IMG_0001,1,5,2024.07.18,120000,,x\x00\xff\n"
    path.write_bytes(b"\xef\xbb\xbf" + _HEADER + _MEASURED.encode() + damaged + _BAD_NAME_ROW)

    dp = DataProcessor(str(path))

    assert dp.df["Length (mm)"].tolist() == [1.5]
    assert dp.exclusions == RowExclusions(failed=1, bad_name=1)


def test_the_frame_keeps_only_the_columns_the_charts_read(tmp_path):
    # Image, Time and Error are read once, to count the left-out rows; kept in the frame
    # they were 80 % of its memory for as long as the dashboard stayed open.
    dp = DataProcessor(_write(tmp_path / "l.csv", GOOD + [FAILED, BAD_NAME]))

    assert list(dp.df.columns) == ["Date", "Tube", "Position", "Length (mm)"]
    assert dp.df["Position"].tolist() == [10, 11]
    assert dp.exclusions == RowExclusions(failed=1, bad_name=1)

"""MeasurementIndex: read-only per-image length/area lookup from the Measure CSVs.

The index never computes a metric: it only reads ``root_lengths.csv`` /
``root_areas.csv`` where the Measure handlers write them (KNOWN_ISSUES #3).
"""

from __future__ import annotations

import os
import time

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.inference.metrics import write_metric_csv

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
OTHER = "24EFCAM1_T001_L045_2024.07.18_111612_001_SMM"


def _row(stem: str, column: str, value, error: str = "") -> dict:
    row = {"Image": stem, "Tube": 1, "Position": 44, "Date": "2024.07.18", "Time": "11:15:30"}
    row[column] = value
    if error:
        row["Error"] = error
    return row


def _write_lengths(root, rows) -> str:
    folder = root / "skeletons"
    folder.mkdir(exist_ok=True)
    path = str(folder / "root_lengths.csv")
    write_metric_csv(rows, path, LENGTH_CSV_HEADERS)
    return path


def _write_areas(root, rows) -> str:
    folder = root / "mask"
    folder.mkdir(exist_ok=True)
    path = str(folder / "root_areas.csv")
    write_metric_csv(rows, path, AREA_CSV_HEADERS)
    return path


def _bump_mtime(path: str, seconds: float = 5.0) -> None:
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + int(seconds * 1e9)))


def test_values_are_read_verbatim_from_both_csvs(tmp_path):
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 18.77)])
    _write_areas(tmp_path, [_row(STEM, AREA_CSV_HEADERS[5], 16.41)])

    m = MeasurementIndex(str(tmp_path)).lookup(STEM)

    assert m == Measurement(18.77, 16.41, None)
    length, area, error, length_source, area_source = m  # tuple-compatible
    assert (length, area, error) == (18.77, 16.41, None)
    assert (length_source, area_source) == (None, None)  # no Source column


def test_lookup_accepts_an_image_path_or_a_stem_as_it_is(tmp_path):
    # A bare name is a stem, never stripped again: the stem of "X.png.jpg" is "X.png", whose
    # row is not image X's (final review D1).
    rows = [_row(STEM, LENGTH_CSV_HEADERS[5], 3.5), _row(STEM + ".png", LENGTH_CSV_HEADERS[5], 1.5)]
    _write_lengths(tmp_path, rows)
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup(os.path.join(str(tmp_path), STEM + ".jpg")).length_mm == 3.5
    assert index.lookup(STEM).length_mm == 3.5
    assert index.lookup(STEM + ".png").length_mm == 1.5
    assert index.lookup(os.path.join(str(tmp_path), STEM + ".png.jpg")).length_mm == 1.5
    assert index.lookup(OTHER + ".png").length_mm is None  # no row of that stem


def test_missing_csvs_give_all_none(tmp_path):
    assert MeasurementIndex(str(tmp_path)).lookup(STEM) == Measurement(None, None, None)


def test_unknown_stem_gives_none(tmp_path):
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 1.0)])
    assert MeasurementIndex(str(tmp_path)).lookup(OTHER) == Measurement(None, None, None)


def test_error_row_gives_none_value_and_message_never_the_zero(tmp_path):
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 0, error="cannot read image")])
    _write_areas(tmp_path, [_row(STEM, AREA_CSV_HEADERS[5], 2.25)])

    m = MeasurementIndex(str(tmp_path)).lookup(STEM)

    assert m.length_mm is None
    assert m.area_mm2 == 2.25
    assert "cannot read image" in m.error
    assert "length" in m.error.lower()


def test_rewritten_csv_is_reparsed(tmp_path):
    path = _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 1.0)])
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup(STEM).length_mm == 1.0

    write_metric_csv([_row(STEM, LENGTH_CSV_HEADERS[5], 2.5)], path, LENGTH_CSV_HEADERS)
    _bump_mtime(path)

    assert index.lookup(STEM).length_mm == 2.5


def test_unchanged_csv_is_parsed_once(tmp_path, monkeypatch):
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 1.0)])
    index = MeasurementIndex(str(tmp_path))
    calls = []
    from app.data_processing import measurement_index as mod

    real = mod._read_metric_csv
    monkeypatch.setattr(mod, "_read_metric_csv", lambda *a: calls.append(a) or real(*a))
    for _ in range(5):
        index.lookup(STEM)
    assert len(calls) == 1


def test_csv_created_after_first_lookup_is_found(tmp_path):
    index = MeasurementIndex(str(tmp_path), relocate_after_s=0.0)
    assert index.lookup(STEM).length_mm is None
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 7.0)])
    assert index.lookup(STEM).length_mm == 7.0


def test_invalidate_forces_relocation(tmp_path):
    index = MeasurementIndex(str(tmp_path), relocate_after_s=3600.0)
    assert index.lookup(STEM).area_mm2 is None
    _write_areas(tmp_path, [_row(STEM, AREA_CSV_HEADERS[5], 4.0)])
    index.invalidate()
    assert index.lookup(STEM).area_mm2 == 4.0


def test_peek_never_parses_and_reports_needs_load(tmp_path):
    _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 5.0)])
    index = MeasurementIndex(str(tmp_path))
    assert index.needs_load() is True
    assert index.lookup(STEM, load=False) == Measurement(None, None, None)
    index.prewarm()
    assert index.needs_load() is False
    assert index.lookup(STEM, load=False).length_mm == 5.0


def test_malformed_csv_is_ignored_not_raised(tmp_path):
    folder = tmp_path / "skeletons"
    folder.mkdir()
    (folder / "root_lengths.csv").write_bytes(b"\xff\xfe not,a,metric\x00csv\n1,2")
    assert MeasurementIndex(str(tmp_path)).lookup(STEM) == Measurement(None, None, None)


def test_outdated_when_skeleton_is_newer_than_length_csv(tmp_path):
    path = _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 1.0)])
    skel = tmp_path / "skeletons" / f"{STEM}_fake.png"
    skel.write_bytes(b"x")
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup(STEM).length_mm == 1.0  # the bar looks up first, then asks
    st = os.stat(path)
    os.utime(str(skel), ns=(st.st_atime_ns, st.st_mtime_ns - 10**9))
    assert index.is_outdated(STEM) is False
    os.utime(str(skel), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert index.is_outdated(STEM) is True


def test_sources_name_the_csv_files(tmp_path):
    path = _write_lengths(tmp_path, [_row(STEM, LENGTH_CSV_HEADERS[5], 1.0)])
    index = MeasurementIndex(str(tmp_path))
    index.lookup(STEM)
    assert os.path.normcase(index.sources()["length"]) == os.path.normcase(os.path.normpath(path))
    assert index.sources()["area"] is None


def test_warm_lookup_is_fast(tmp_path):
    rows = [_row(f"IMG_{i:06d}", LENGTH_CSV_HEADERS[5], i / 100) for i in range(46_000)]
    _write_lengths(tmp_path, rows)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    t0 = time.perf_counter()
    for i in range(200):
        index.lookup(f"IMG_{i:06d}")
    per = (time.perf_counter() - t0) / 200
    assert per < 2e-3  # measured ~0.02 ms; generous for CI

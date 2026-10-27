"""MeasurementIndex.reading: the calls of one metrics-bar refresh share their CSV stats.

A refresh asks ``lookup(load=False)``, ``needs_load``, ``outdated`` and ``measured_from``,
and each looked for the CSVs again: both CSVs and their inputs records were stat'ed once per
call, 16 stats for every Library click. Inside :meth:`MeasurementIndex.reading` each is
stat'ed once. The answers are the same; a CSV rewritten after a block is seen by the next
one; a load inside a block is seen by the calls after it; another thread's calls are not
grouped with this one's; and the record keys are :func:`input_key`'s.
"""

from __future__ import annotations

import hashlib
import os
import threading

import pytest

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS
from app.data_processing.measurement_index import Measurement, MeasurementIndex
from app.inference.measure_inputs import input_key, inputs_record_path, write_inputs_record
from tests.test_shell_info_measurement_index import _bump_mtime, _row, _write_areas, _write_lengths

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
GT_STEM = "24EFCAM1_T001_L045_2024.07.18_111612_001_SMM"


def _fingerprint(path: str) -> tuple[int, int, str]:
    st = os.stat(path)
    with open(path, "rb") as fh:
        return st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest()


def _project(tmp_path) -> dict[str, str]:
    """Both CSVs with a ``Source`` column and an inputs record, a prediction per row and a
    GT skeleton measured as ``gt``: the files a refresh stats."""
    root = str(tmp_path)
    for folder in ("skeletons", "mask", os.path.join("gt", "skeletons")):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    files = {
        "skeleton": os.path.join(root, "skeletons", f"{STEM}_fake.png"),
        "mask": os.path.join(root, "mask", f"{STEM}.png"),
        "gt": os.path.join(root, "gt", "skeletons", f"{GT_STEM}.png"),
        "gt_prediction": os.path.join(root, "skeletons", f"{GT_STEM}_fake.png"),
    }
    for path in files.values():
        with open(path, "wb") as fh:
            fh.write(os.path.basename(path).encode())
    lengths = [
        {**_row(STEM, LENGTH_CSV_HEADERS[5], 18.77), "Source": "prediction"},
        {**_row(GT_STEM, LENGTH_CSV_HEADERS[5], 4.5), "Source": "gt"},
    ]
    files["lengths"] = _write_lengths(tmp_path, lengths)
    files["areas"] = _write_areas(
        tmp_path, [{**_row(STEM, AREA_CSV_HEADERS[5], 16.41), "Source": "prediction"}]
    )
    for csv_path, inputs in (
        (files["lengths"], (files["skeleton"], files["gt"])),
        (files["areas"], (files["mask"],)),
    ):
        write_inputs_record(csv_path, {input_key(p, root): _fingerprint(p) for p in inputs})
    return files


def _refresh(index: MeasurementIndex, stem: str) -> tuple:
    """What MetricsController.refresh asks of the index."""
    return (
        index.lookup(stem, load=False),
        index.needs_load(),
        index.outdated(stem),
        index.sources(),
        index.by_content(),
        index.gt_skip_reason(stem),
        index.measured_from(stem),
        index.calibrations(),
    )


@pytest.fixture
def stats(monkeypatch):
    """The paths ``os.stat`` was asked for (normalised), in order."""
    seen: list[str] = []
    real = os.stat

    def counting(path, *args, **kwargs):
        seen.append(os.path.normcase(os.path.normpath(os.fspath(path))))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", counting)
    return seen


def _count(seen: list[str], path: str) -> int:
    return seen.count(os.path.normcase(os.path.normpath(path)))


@pytest.mark.parametrize("stem", [STEM, GT_STEM])
def test_a_refresh_reads_the_same_inside_a_reading_block(tmp_path, stem):
    _project(tmp_path)
    plain = MeasurementIndex(str(tmp_path))
    plain.prewarm()
    grouped = MeasurementIndex(str(tmp_path))
    grouped.prewarm()

    expected = _refresh(plain, stem)
    with grouped.reading():
        answer = _refresh(grouped, stem)

    assert answer == expected
    assert answer[0].length_mm is not None  # a measured image, with its sources
    assert answer[6]["length"] is not None


def test_inside_a_block_each_csv_and_record_is_stated_once(tmp_path, stats):
    files = _project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()

    stats.clear()
    _refresh(index, STEM)
    unscoped = {name: _count(stats, files[name]) for name in ("lengths", "areas")}
    stats.clear()
    with index.reading():
        _refresh(index, STEM)
    scoped = {name: _count(stats, files[name]) for name in ("lengths", "areas")}
    records = {name: _count(stats, inputs_record_path(files[name])) for name in scoped}

    assert unscoped == {"lengths": 4, "areas": 4}  # one per call that looks the CSV up
    assert scoped == {"lengths": 1, "areas": 1}
    assert records == {"lengths": 1, "areas": 1}


def test_a_csv_rewritten_after_a_block_is_seen_by_the_next_one(tmp_path):
    files = _project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    with index.reading():
        assert index.lookup(STEM, load=False).length_mm == 18.77

    _write_lengths(tmp_path, [{**_row(STEM, LENGTH_CSV_HEADERS[5], 2.5), "Source": "prediction"}])
    _bump_mtime(files["lengths"])
    with index.reading():
        assert index.needs_load() is True  # the new CSV is seen, not the one held before
        assert index.lookup(STEM, load=False).length_mm is None  # not parsed yet
    index.prewarm()
    with index.reading():
        assert index.lookup(STEM, load=False).length_mm == 2.5


def test_a_load_inside_a_block_is_seen_by_the_calls_after_it(tmp_path):
    files = _project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    with index.reading():
        assert index.lookup(STEM, load=False).length_mm == 18.77
        _write_lengths(
            tmp_path, [{**_row(STEM, LENGTH_CSV_HEADERS[5], 7.0), "Source": "prediction"}]
        )
        _bump_mtime(files["lengths"])
        worker = threading.Thread(target=index.prewarm)  # the pool's parse
        worker.start()
        worker.join()
        assert index.needs_load() is False
        assert index.lookup(STEM, load=False).length_mm == 7.0


def test_another_threads_calls_are_not_grouped_with_this_ones(tmp_path):
    files = _project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    seen = []
    with index.reading():
        assert index.lookup(STEM, load=False).length_mm == 18.77
        os.remove(files["areas"])  # gone: a thread that looks again finds no area value

        def other():
            seen.append(index.lookup(STEM, load=False))

        worker = threading.Thread(target=other)
        worker.start()
        worker.join()

    assert seen == [Measurement(18.77, None, None, "prediction", None)]  # it looked itself
    assert index.lookup(STEM, load=False).area_mm2 is None


def test_the_files_measured_from_are_named_by_their_record_keys(tmp_path):
    files = _project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    root = str(tmp_path)

    with index.reading():
        for stem, metric, path in (
            (STEM, "length", files["skeleton"]),
            (STEM, "area", files["mask"]),
            (GT_STEM, "length", files["gt"]),
        ):
            assert index.measured_from(stem)[metric][0] == input_key(path, root)
        assert index.outdated(STEM) == {"length": False, "area": False}
        assert index.outdated(GT_STEM) == {"length": False, "area": False}

    with open(files["gt"], "ab") as fh:  # a GT skeleton changed since it was measured
        fh.write(b"edited")
    _bump_mtime(files["gt"])
    with index.reading():
        assert index.outdated(GT_STEM) == {"length": True, "area": False}
        assert index.outdated(STEM) == {"length": False, "area": False}

"""A measurement index is freed by refcount alone: it is in no reference cycle.

The window drops its index when another project opens. ``app.gc_policy`` freezes the heap
after each warm-up (and after a job end that imported modules), and a frozen cycle is never
collected: an index in one would keep the previous project's parsed rows, inputs records
and GT listings for the rest of the session (about 35 MiB for two 46k-row CSVs).
"""

from __future__ import annotations

import gc
import weakref

import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.measurement_index import MeasurementIndex
from app.inference.metrics import write_metric_csv

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
#: A legacy folder that only the ``test_latest`` walk finds.
WALKED = ("results", "run1", "test_latest", "images")


@pytest.fixture
def refcount_only():
    """The cyclic garbage collector off: only refcounting frees an object."""
    gc.collect()
    gc.disable()
    yield
    gc.enable()


def _write_lengths(folder) -> None:
    folder.mkdir(parents=True)
    row = {"Image": STEM, "Tube": 1, "Position": 44, "Date": "2024.07.18", "Time": "11:15:30"}
    row[LENGTH_CSV_HEADERS[5]] = 9.25
    write_metric_csv([row], str(folder / "root_lengths.csv"), LENGTH_CSV_HEADERS)


def test_a_new_index_is_freed_by_refcount(tmp_path, refcount_only):
    index = MeasurementIndex(str(tmp_path))
    ref = weakref.ref(index)
    del index
    assert ref() is None


def test_an_index_that_walked_and_parsed_is_freed_by_refcount(tmp_path, refcount_only):
    _write_lengths(tmp_path.joinpath(*WALKED))
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()  # the worker's search: walks, keeps what the walk found, parses
    assert index.lookup(STEM, load=False).length_mm == 9.25
    assert index.find_lengths_csv() is not None  # the dashboards' check, from the kept walk
    index.invalidate()  # a Measure job end
    ref = weakref.ref(index)
    del index
    assert ref() is None

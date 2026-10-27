"""The Measure status line runs one count at a time (S06-05).

Each Measure-stage entry, each CSV parse that lands on Measure and each job end there asks
:meth:`StageStatus.check` for a count of the inputs. A count holds the GIL (it lists 46k
files), so two at once only took longer and stalled the GUI more, and the older one's
result was dropped anyway. A check asked for while a count runs is now kept (the newest,
with the CSVs found when it was asked for) and counted when that count ends; the text
shown is still the newest count's.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.gui import stage_status
from app.gui.stage_status import StageStatus
from app.gui.stage_summary import Freshness
from app.project_layout import ProjectLayout
from tests.qt_teardown import flush_deleted


class _Pool:
    """QThreadPool stand-in: jobs wait until a test runs them (on this thread)."""

    def __init__(self):
        self.jobs = []

    def globalInstance(self):  # noqa: N802 - Qt's name
        return self

    def start(self, job):
        self.jobs.append(job)

    def run_next(self):
        self.jobs.pop(0)()


@pytest.fixture
def pool(monkeypatch):
    fake = _Pool()
    monkeypatch.setattr(stage_status, "QThreadPool", fake)
    return fake


@pytest.fixture
def counted(monkeypatch):
    """The CSV each count read, in order; a count's result names its length CSV."""
    calls = []

    def freshness(folder, suffix, csv_path, **_kw):
        calls.append(csv_path)
        return Freshness(inputs=1, measured=csv_path is not None, changed=len(calls))

    monkeypatch.setattr(stage_status, "input_freshness", freshness)
    monkeypatch.setattr(stage_status, "load_inputs_record", lambda _csv: None)
    monkeypatch.setattr(stage_status, "read_sidecar", lambda _csv: None)
    return calls


@pytest.fixture
def status(app):
    made = StageStatus()
    yield made
    made.deleteLater()
    del made
    flush_deleted()


class _Index:
    def __init__(self, length=None):
        self.length = length
        self.located = 0

    def needs_load(self):
        self.located += 1
        return False

    def sources(self):
        return {"length": self.length, "area": None}


def _changes(status):
    seen = []
    status.changed.connect(lambda: seen.append(status._freshness["length"].changed))
    return seen


def test_checks_asked_for_during_a_count_run_once_after_it(tmp_path, pool, counted, status):
    layout, index = ProjectLayout(str(tmp_path)), _Index()
    seen = _changes(status)
    for _ in range(3):
        status.check(index, layout)
    assert len(pool.jobs) == 1  # one count at a time
    assert index.located == 3  # each check still looks for the CSVs when asked
    pool.run_next()  # its result is dropped: a newer check was asked for
    assert len(pool.jobs) == 1 and seen == []
    pool.run_next()
    assert pool.jobs == [] and len(seen) == 1
    status.check(index, layout)  # nothing runs: starts at once
    assert len(pool.jobs) == 1


def test_a_kept_check_counts_the_csvs_found_when_it_was_asked_for(tmp_path, pool, counted, status):
    layout = ProjectLayout(str(tmp_path))
    index = _Index(length=str(tmp_path / "a" / "root_lengths.csv"))
    status.check(index, layout)
    index.length = str(tmp_path / "b" / "root_lengths.csv")
    status.check(index, layout)  # kept with b
    index.length = str(tmp_path / "c" / "root_lengths.csv")
    pool.run_next()
    pool.run_next()
    assert [csv for csv in counted if csv is not None] == [
        str(tmp_path / "a" / "root_lengths.csv"),
        str(tmp_path / "b" / "root_lengths.csv"),
    ]


@pytest.mark.parametrize("ending", [ValueError("x"), SystemExit(3), KeyboardInterrupt()])
def test_a_failed_count_keeps_the_text_and_never_blocks_the_next(
    tmp_path, pool, counted, status, monkeypatch, ending, caplog
):
    layout, index = ProjectLayout(str(tmp_path)), _Index()
    seen = _changes(status)
    status.check(index, layout)
    pool.run_next()
    assert len(seen) == 1
    before = status._freshness

    def broken(*_a, **_k):
        raise ending

    monkeypatch.setattr(stage_status, "input_freshness", broken)
    status.check(index, layout)
    pool.run_next()  # reports (None): nothing escapes the pool job
    assert status._freshness is before and len(seen) == 1
    assert "Could not count the Measure inputs" in caplog.text
    status.check(index, layout)
    assert len(pool.jobs) == 1  # the next count starts at once


def test_reset_during_a_count_voids_it_and_the_next_starts_at_once(tmp_path, pool, counted, status):
    layout, index = ProjectLayout(str(tmp_path)), _Index()
    seen = _changes(status)
    status.check(index, layout)
    status.check(index, layout)  # kept
    status.reset()  # another project: both are void
    status.check(index, layout)
    assert len(pool.jobs) == 2  # the voided count still ends; the new one runs now
    pool.run_next()  # the voided count: ignored, starts nothing
    assert len(pool.jobs) == 1 and seen == []
    pool.run_next()
    assert pool.jobs == [] and len(seen) == 1


def test_the_measure_text_is_the_newest_counts(tmp_path, pool, counted, status):
    layout = ProjectLayout(str(tmp_path))
    index = _Index(length=str(tmp_path / "skeletons" / "root_lengths.csv"))
    manager = SimpleNamespace(images={"a": "a.jpg"}, masks={"a"}, skeleton_stems={"a"})
    status.check(index, layout)
    status.check(index, layout)
    pool.run_next()
    pool.run_next()
    # length then area per count: the second count's length is the third call
    assert status._freshness["length"].changed == 3
    assert status.text("Measure", manager).startswith("1 skeleton: 3 changed since measured")

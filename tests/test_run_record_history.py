"""A no-op generation run keeps ``_sprouts_run.json``; every other run is a history line (R6, O16)."""

from __future__ import annotations

import json
import logging
import os

import pytest

from app import provenance
from app.inference import mask_inference, run_record
from app.inference.batch_engine import BatchReport
from tests.test_generation_inference import (  # noqa: F401 - cpu_runtime is a fixture
    _images,
    _StubMaskModel,
    cpu_runtime,
)

pytestmark = pytest.mark.usefixtures("cpu_runtime")


def _run(src, out, **kwargs):
    return mask_inference.generate_masks(
        src, out, model=_StubMaskModel().eval(), batch_size=2, **kwargs
    )


def _manifest_bytes(out):
    with open(provenance.run_manifest_path(out), "rb") as fh:
        return fh.read()


def _history_lines(out):
    with open(provenance.run_history_path(out), encoding="utf-8") as fh:
        return fh.read().splitlines()


def test_a_keep_existing_run_with_nothing_missing_leaves_the_record_byte_identical(tmp_path):
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "b.jpg"])
    out = str(tmp_path / "imgs" / "mask")
    _run(src, out)
    before = _manifest_bytes(out)

    report = _run(src, out, skip_stems=frozenset({"a", "b"}))

    assert report.written == []
    assert _manifest_bytes(out) == before
    assert len(_history_lines(out)) == 1


def test_a_cancel_before_the_first_batch_leaves_the_record_alone(tmp_path):
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "b.jpg"])
    out = str(tmp_path / "imgs" / "mask")
    _run(src, out)
    before = _manifest_bytes(out)

    report = _run(src, out, should_stop=lambda: True)

    assert report.written == []
    assert _manifest_bytes(out) == before
    assert len(_history_lines(out)) == 1


def test_a_partial_run_appends_a_line_and_replaces_the_latest(tmp_path):
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "b.jpg", "c.jpg"])
    out = str(tmp_path / "imgs" / "mask")
    _run(src, out)
    os.remove(os.path.join(out, "c.png"))

    _run(src, out, skip_stems=frozenset({"a", "b"}))

    with open(provenance.run_manifest_path(out), encoding="utf-8") as fh:
        assert json.load(fh)["outputs"]["n_written"] == 1
    lines = _history_lines(out)
    assert len(lines) == 2
    assert json.loads(lines[-1])["written"] == ["c.jpg"]


def test_an_all_failed_run_still_writes(tmp_path):
    src = _images(str(tmp_path / "imgs"), ["x.jpg", "y.jpg"], corrupt={"x.jpg", "y.jpg"})
    out = str(tmp_path / "imgs" / "mask")

    _run(src, out)

    with open(provenance.run_manifest_path(out), encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["outputs"]["n_failed"] == 2
    assert record["outputs"]["status"] == "completed"
    lines = _history_lines(out)
    assert len(lines) == 1
    assert json.loads(lines[0])["n_failed"] == 2


def test_more_than_the_limit_written_names_are_a_count_and_a_digest():
    record = provenance.build_record("mask_generation")
    names = [f"{i}.jpg" for i in range(run_record.HISTORY_NAMES_LIMIT + 1)]

    over = run_record.history_entry(record, BatchReport(written=names))
    assert "written" not in over
    assert over["written_count"] == 1001
    assert over["written_sha256"] == provenance.names_digest(names)

    at_limit = run_record.history_entry(record, BatchReport(written=names[:1000]))
    assert len(at_limit["written"]) == 1000
    assert "written_count" not in at_limit


def test_a_history_failure_never_fails_the_run(tmp_path, monkeypatch, caplog):
    def broken(output_dir, entry):
        raise OSError("disk says no")

    monkeypatch.setattr(provenance, "append_run_history", broken)
    out = str(tmp_path / "mask")
    os.makedirs(out)

    with caplog.at_level(logging.WARNING, logger=run_record.logger.name):
        path = run_record.write_run_manifest(
            "mask_generation",
            input_dir=str(tmp_path),
            output_dir=out,
            input_names=["a.jpg"],
            report=BatchReport(written=["a.jpg"]),
            models={},
            parameters={},
        )

    assert path == provenance.run_manifest_path(out)
    assert os.path.exists(path)
    assert "disk says no" in caplog.text

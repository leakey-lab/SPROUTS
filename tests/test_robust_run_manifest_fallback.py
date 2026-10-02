"""R5: a run whose provenance record cannot be written never leaves an older one behind.

``run_record.write_run_manifest`` logs a provenance failure and lets the run succeed
(the outputs are on disk). The previous ``_sprouts_run.json`` then stayed in the
folder, so it described an older run than the outputs next to it. Now the run writes a
minimal honest record instead: the same top-level keys (readers keep working), the
run's outcome in ``outputs`` (status, counts, failed inputs), the sections that could
not be described left empty, and ``provenance_error`` saying why. If even that cannot
be written, the outdated record is removed. CPU stub models, no checkpoints.
"""

from __future__ import annotations

import errno
import logging
import os

import pytest

from app import provenance
from app.inference import mask_inference, run_record
from app.inference import skeleton_inference as si
from app.inference.batch_engine import BatchReport
from tests.test_generation_failed_run_manifest import (
    ERROR_TEXT,
    KINDS,
    STALE,
    _folder_with_stale_manifest,
    _manifest,
    _model,
    _pngs,
    _run,
    cpu_runtime,  # noqa: F401 - fixture
)
from tests.test_generation_inference import _images

# The keys every record has (docs/DATA_DICTIONARY.md, "Provenance records").
RECORD_KEYS = {
    "schema",
    "kind",
    "created_utc",
    "app",
    "git_commit",
    "calibration",
    "runtime",
    "models",
    "inputs",
    "parameters",
    "outputs",
}


def _broken_build_record(*args, **kwargs):
    raise KeyError("provenance bug (simulated)")


def _run_kind(kind):
    return mask_inference.RUN_KIND if kind == "mask" else si.RUN_KIND


@pytest.mark.parametrize("kind", KINDS)
def test_a_record_that_cannot_be_built_is_replaced_by_a_minimal_one(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    monkeypatch,
    caplog,
    kind,
):
    monkeypatch.setattr(provenance, "build_record", _broken_build_record)
    src = _images(str(tmp_path / "imgs"), ["a.jpg", "bad.jpg", "c.jpg"], corrupt={"bad.jpg"})
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")

    with caplog.at_level(logging.WARNING):
        report = _run(kind, src, out, _model(kind))

    assert report.status == "completed" and len(_pngs(out)) == 2  # the run still succeeds
    record = _manifest(out)  # no longer the older run's record
    assert RECORD_KEYS <= set(record)
    assert record["schema"] == provenance.SCHEMA and record["kind"] == _run_kind(kind)
    assert record["created_utc"].endswith("Z")
    assert record["outputs"] == report.summary()  # the outcome is fully known
    assert record["outputs"]["failed"] == [{"input": "bad.jpg", "reason": report.failed[0][1]}]
    assert "provenance bug (simulated)" in record["provenance_error"]
    assert record["models"] == {} and record["git_commit"] is None  # not described: empty
    assert "provenance bug (simulated)" in caplog.text


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_run_whose_record_cannot_be_built_still_says_it_failed(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    monkeypatch,
    kind,
):
    monkeypatch.setattr(provenance, "build_record", _broken_build_record)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(5)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")
    model = _model(kind, fail_on=2)

    with pytest.raises(RuntimeError) as caught:
        _run(kind, src, out, model)

    assert caught.value is model.error  # the run's own error, unchanged
    outputs = _manifest(out)["outputs"]
    assert outputs["status"] == "failed" and outputs["error"] == f"RuntimeError: {ERROR_TEXT}"
    assert outputs["n_written"] == len(_pngs(out)) == 2


@pytest.mark.parametrize("kind", KINDS)
def test_when_no_record_can_be_written_the_outdated_one_is_removed(
    tmp_path,
    cpu_runtime,  # noqa: F811 - fixture
    monkeypatch,
    caplog,
    kind,
):
    attempts = []

    def disk_full(path, record):
        attempts.append(record)
        raise OSError(errno.ENOSPC, "No space left on device (simulated)", path)

    monkeypatch.setattr(provenance, "_write_json", disk_full)
    src = _images(str(tmp_path / "imgs"), [f"i{k}.jpg" for k in range(3)])
    out = _folder_with_stale_manifest(tmp_path / "imgs" / "out")

    with caplog.at_level(logging.WARNING):
        report = _run(kind, src, out, _model(kind))

    assert report.status == "completed" and len(_pngs(out)) == 3
    assert len(attempts) == 2 and "provenance_error" in attempts[1]  # full, then minimal
    assert not os.path.exists(provenance.run_manifest_path(out))  # never the older run's
    assert "No space left on device" in caplog.text


def test_an_outdated_record_that_cannot_be_removed_is_logged(tmp_path, monkeypatch, caplog):
    out = _folder_with_stale_manifest(tmp_path / "out")

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Access is denied (simulated)")

    monkeypatch.setattr(provenance, "_write_json", denied)
    monkeypatch.setattr(run_record.os, "remove", denied)
    with caplog.at_level(logging.WARNING):
        path = run_record.write_run_manifest(
            "mask_generation",
            input_dir=str(tmp_path),
            output_dir=out,
            input_names=["a.jpg"],
            report=BatchReport(total=1, written=["a.jpg"]),
            models={},
            parameters={},
        )

    assert path is None  # never raises: the outputs exist
    with open(provenance.run_manifest_path(out), "rb") as fh:
        assert fh.read() == STALE
    assert "Could not remove the outdated run manifest" in caplog.text


def test_a_record_that_can_be_built_is_written_in_full(tmp_path):
    out = str(tmp_path / "out")
    os.makedirs(out)
    report = BatchReport(total=1, written=["a.jpg"])

    path = run_record.write_run_manifest(
        "mask_generation",
        input_dir=str(tmp_path),
        output_dir=out,
        input_names=["a.jpg"],
        report=report,
        models={},
        parameters={"batch_size": 1},
    )

    record = _manifest(out)
    assert path == provenance.run_manifest_path(out)
    assert "provenance_error" not in record
    assert record["parameters"] == {"batch_size": 1} and record["outputs"] == report.summary()
    assert record["inputs"]["n_images"] == 1


def test_the_minimal_record_has_every_record_key():
    outputs = BatchReport(total=2, written=["a.jpg"], failed=[("b.jpg", "OSError: x")]).summary()
    record = provenance.minimal_record("skeleton_generation", outputs=outputs, reason="boom")

    assert set(record) == RECORD_KEYS | {"provenance_error"}
    assert record["outputs"] == outputs and record["provenance_error"] == "boom"
    assert record["app"] == provenance.app_info()
    assert record["calibration"] == provenance.calibration_info()

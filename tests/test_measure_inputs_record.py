"""Measure records the files it measured; "changed since measured" compares content (R5).

A Measure run writes ``<csv>.inputs.json`` beside its CSV: each input's size, time and
SHA-256, and the SHA-256 of the CSV it describes. The Measure status line, the metrics bar
and :meth:`MeasureRun.unchanged` compare the inputs on disk with that record: size and
time first, the content only when they differ. A copied or restamped folder is current,
a repainted mask with an older time is changed, and deleted and added inputs are named.
A CSV without a (matching) record keeps the file-time rule, and the metrics bar says so.
"""

from __future__ import annotations

import hashlib
import json
import os

import cv2
import numpy as np
import pytest

from app.data_processing.measurement_index import MeasurementIndex
from app.gui.stage_summary import Freshness, input_freshness, measure_text
from app.inference import measure_inputs
from app.inference.measure_inputs import inputs_record_path, load_inputs_record
from app.inference.measure_run import AREA, LENGTH, MeasureRun
from app.project_layout import ProjectLayout


def _sha256(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _line_image(i: int) -> np.ndarray:
    img = np.zeros((64, 80), np.uint8)
    cv2.line(img, (0, i), (79, 40 + i), 255, 2)
    return img


def _measured(tmp_path, n=3):
    """A project of ``n`` images with a mask each, measured for area: (layout, run, stems)."""
    root = tmp_path
    (root / "mask").mkdir(parents=True, exist_ok=True)
    stems = []
    for i in range(n):
        name = f"x_T{i + 1}_L2_2024.01.02_01020{i}_001"
        cv2.imwrite(str(root / f"{name}.jpg"), _line_image(i))
        cv2.imwrite(str(root / "mask" / f"{name}.png"), _line_image(i))
        stems.append(name)
    layout = ProjectLayout(str(root))
    run = MeasureRun(
        AREA, layout.scan_masks(extensions=(".png",)), layout.mask_dir, project_root=str(root)
    )
    run.compute()
    run.write()
    return layout, run, stems


def _freshness(layout, csv_path):
    return input_freshness(
        layout.mask_dir,
        ".png",
        csv_path,
        record=load_inputs_record(csv_path),
        project_root=layout.root,
    )


def _restamp(path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _csv_mtime_ns(csv_path) -> int:
    return os.stat(csv_path).st_mtime_ns


def _repaint(path) -> None:
    img = np.zeros((64, 80), np.uint8)
    cv2.circle(img, (40, 32), 20, 255, -1)
    cv2.imwrite(str(path), img)


def test_measure_writes_an_inputs_record_beside_the_csv(tmp_path):
    layout, run, stems = _measured(tmp_path)
    record_path = os.path.join(layout.mask_dir, "root_areas.csv.inputs.json")
    assert inputs_record_path(run.csv_path) == record_path
    assert os.path.isfile(record_path)
    with open(record_path, encoding="utf-8") as fh:
        record = json.load(fh)
    assert set(record) == {"csv_sha256", "inputs"}
    assert record["csv_sha256"] == _sha256(run.csv_path)
    assert set(record["inputs"]) == {f"mask/{stem}.png" for stem in stems}
    for stem in stems:
        path = os.path.join(layout.mask_dir, f"{stem}.png")
        st = os.stat(path)
        assert record["inputs"][f"mask/{stem}.png"] == [st.st_size, st.st_mtime_ns, _sha256(path)]


@pytest.mark.parametrize("spec", [LENGTH, AREA], ids=["length", "area"])
def test_rows_from_bytes_equal_rows_from_the_path(tmp_path, spec):
    name = "x_T1_L2_2024.01.02_010200_001"
    path = tmp_path / f"{name}.png"
    cv2.imwrite(str(path), _line_image(3))
    with open(path, "rb") as fh:
        data = fh.read()
    assert spec.row(name, str(path), data=data) == spec.row(name, str(path))
    missing = str(tmp_path / "gone.png")
    from_bytes = spec.row(name, missing, data=b"")
    assert "Error" in from_bytes
    assert from_bytes == spec.row(name, missing)


def test_restamped_inputs_with_equal_content_are_current(tmp_path):
    layout, run, stems = _measured(tmp_path)
    later = _csv_mtime_ns(run.csv_path) + 100 * 10**9
    for stem in stems:
        _restamp(os.path.join(layout.mask_dir, f"{stem}.png"), later)
    assert _freshness(layout, run.csv_path) == Freshness(
        3, True, 0, new=0, removed=0, by_content=True
    )


def test_regenerated_identical_files_are_current(tmp_path):
    layout, run, stems = _measured(tmp_path)
    for stem in stems:
        path = os.path.join(layout.mask_dir, f"{stem}.png")
        with open(path, "rb") as fh:
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(data)
    fresh = _freshness(layout, run.csv_path)
    assert fresh.by_content is True
    assert fresh.changed == 0


def test_a_repainted_mask_with_an_older_time_is_changed(tmp_path):
    layout, run, stems = _measured(tmp_path)
    path = os.path.join(layout.mask_dir, f"{stems[1]}.png")
    _repaint(path)
    _restamp(path, _csv_mtime_ns(run.csv_path) - 3600 * 10**9)
    assert _freshness(layout, run.csv_path).changed == 1

    index = MeasurementIndex(layout.root)
    index.prewarm()
    assert index.by_content() == {"length": False, "area": True}
    assert index.outdated(stems[1])["area"] is True
    assert index.outdated(stems[0])["area"] is False
    assert index.outdated(stems[2])["area"] is False


def test_a_deleted_input_is_reported_removed(tmp_path):
    layout, run, stems = _measured(tmp_path)
    os.remove(os.path.join(layout.mask_dir, f"{stems[0]}.png"))
    fresh = _freshness(layout, run.csv_path)
    assert (fresh.inputs, fresh.removed, fresh.changed, fresh.new) == (2, 1, 0, 0)

    index = MeasurementIndex(layout.root)
    index.prewarm()
    assert index.outdated(stems[0])["area"] is True
    assert index.outdated(stems[1])["area"] is False


def test_a_new_input_is_reported_new(tmp_path):
    layout, run, _stems = _measured(tmp_path)
    cv2.imwrite(os.path.join(layout.mask_dir, "new.png"), _line_image(7))
    fresh = _freshness(layout, run.csv_path)
    assert (fresh.inputs, fresh.new, fresh.changed, fresh.removed) == (4, 1, 0, 0)


def test_a_record_for_another_csv_is_ignored(tmp_path):
    layout, run, _stems = _measured(tmp_path)
    with open(run.csv_path, "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert load_inputs_record(run.csv_path) is None
    assert _freshness(layout, run.csv_path).by_content is False


def test_a_csv_without_a_record_keeps_the_file_time_rule(tmp_path):
    layout, run, stems = _measured(tmp_path)
    os.remove(inputs_record_path(run.csv_path))
    _restamp(
        os.path.join(layout.mask_dir, f"{stems[2]}.png"),
        _csv_mtime_ns(run.csv_path) + 100 * 10**9,
    )
    assert _freshness(layout, run.csv_path) == Freshness(3, True, 1)

    index = MeasurementIndex(layout.root)
    index.prewarm()
    assert index.by_content()["area"] is False
    assert index.outdated(stems[2])["area"] is True
    assert index.outdated(stems[0])["area"] is False


def test_hashes_are_cached_for_the_session(tmp_path, monkeypatch):
    layout, run, stems = _measured(tmp_path)
    later = _csv_mtime_ns(run.csv_path) + 100 * 10**9
    for stem in stems:
        _restamp(os.path.join(layout.mask_dir, f"{stem}.png"), later)
    record = load_inputs_record(run.csv_path)
    calls = []
    real = measure_inputs._hash_file

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(measure_inputs, "_hash_file", counting)
    for _ in range(2):
        fresh = input_freshness(
            layout.mask_dir, ".png", run.csv_path, record=record, project_root=layout.root
        )
        assert fresh.changed == 0
    assert len(calls) == 3


def _counting_reads(monkeypatch) -> list[str]:
    """The records :func:`load_inputs_record` parses (not served from its shared parse)."""
    reads: list[str] = []
    real = measure_inputs._read_inputs_record
    monkeypatch.setattr(
        measure_inputs, "_read_inputs_record", lambda path: reads.append(path) or real(path)
    )
    return reads


def test_a_held_record_is_parsed_once_while_its_files_keep_their_size_and_time(
    tmp_path, monkeypatch
):
    """The metrics bar's index holds the record of each loaded CSV; the Measure status count
    and MeasureRun.unchanged() then reuse that parse (7.6 MB of JSON at 46k images)."""
    _layout, run, _stems = _measured(tmp_path)
    held = load_inputs_record(run.csv_path)
    reads = _counting_reads(monkeypatch)
    assert load_inputs_record(run.csv_path) is held
    assert load_inputs_record(os.path.join(os.path.dirname(run.csv_path), ".", "root_areas.csv"))
    assert reads == []
    with pytest.raises(TypeError):
        held.inputs["mask/new.png"] = (1, 2, "0" * 64)  # shared: read-only
    os.remove(inputs_record_path(run.csv_path))
    assert load_inputs_record(run.csv_path) is None  # gone: never served from the parse


def test_a_record_no_one_holds_is_parsed_again(tmp_path, monkeypatch):
    _layout, run, _stems = _measured(tmp_path)
    assert load_inputs_record(run.csv_path) is not None  # not held: freed at once
    reads = _counting_reads(monkeypatch)
    assert load_inputs_record(run.csv_path) is not None
    assert reads == [run.csv_path]


def test_a_record_rewritten_with_the_same_size_and_time_is_read_again(tmp_path):
    """A coarse file clock (FAT, HFS+) can keep a rewritten record's stamp: the writer drops
    the shared parse of the CSV it writes."""
    _layout, run, stems = _measured(tmp_path)
    held = load_inputs_record(run.csv_path)
    record_path = inputs_record_path(run.csv_path)
    before = os.stat(record_path)
    key = f"mask/{stems[0]}.png"
    size, mtime_ns, digest = held.inputs[key]
    other = "0" * 64 if digest != "0" * 64 else "1" * 64
    measure_inputs.write_inputs_record(run.csv_path, {**held.inputs, key: (size, mtime_ns, other)})
    os.utime(record_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(record_path).st_size == before.st_size
    assert load_inputs_record(run.csv_path).inputs[key] == (size, mtime_ns, other)


def test_a_changed_csv_is_never_paired_with_a_held_record(tmp_path):
    _layout, run, _stems = _measured(tmp_path)
    held = load_inputs_record(run.csv_path)
    with open(run.csv_path, "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert held is not None and load_inputs_record(run.csv_path) is None


def test_a_record_for_another_csv_is_parsed_once_while_its_files_keep_their_size_and_time(
    tmp_path, monkeypatch
):
    _layout, run, _stems = _measured(tmp_path)
    with open(run.csv_path, "a", encoding="utf-8") as fh:
        fh.write("\n")
    reads = _counting_reads(monkeypatch)
    assert load_inputs_record(run.csv_path) is None
    assert load_inputs_record(run.csv_path) is None
    assert reads == [run.csv_path]


def _fails_once(real, path):
    """``real`` (``open`` or a hash function) that raises a sharing violation on its first
    call for ``path``: a transient read failure (antivirus, a file not downloaded yet)."""
    target = os.path.normcase(os.path.abspath(path))
    left = [1]

    def flaky(name, *args, **kwargs):
        if left[0] and os.path.normcase(os.path.abspath(name)) == target:
            left[0] = 0
            raise PermissionError(13, "sharing violation (simulated)", name)
        return real(name, *args, **kwargs)

    return flaky


def test_a_record_that_could_not_be_read_is_read_again(tmp_path, monkeypatch):
    """A failed read is not kept: with the record taken for missing, the status count would
    fall back to file times and every Measure click would measure everything again."""
    _layout, run, _stems = _measured(tmp_path)
    flaky = _fails_once(open, inputs_record_path(run.csv_path))
    monkeypatch.setattr(measure_inputs, "open", flaky, raising=False)
    assert load_inputs_record(run.csv_path) is None
    again = load_inputs_record(run.csv_path)
    assert again is not None and again.inputs
    assert run.unchanged() is True


def test_a_csv_that_could_not_be_hashed_is_hashed_again(tmp_path, monkeypatch):
    _layout, run, _stems = _measured(tmp_path)
    monkeypatch.setattr(measure_inputs, "_sha256_cache", {})  # the CSV's hash is not known
    flaky = _fails_once(measure_inputs._hash_file, run.csv_path)
    monkeypatch.setattr(measure_inputs, "_hash_file", flaky)
    assert load_inputs_record(run.csv_path) is None
    assert load_inputs_record(run.csv_path) is not None


def test_measure_text_names_changed_new_and_removed():
    text = measure_text(0, 0, area=Freshness(46175, True, 36, removed=3, by_content=True))
    assert text.endswith("46,175 masks: 36 changed, 3 removed since measured")
    both = Freshness(12, True, 1, new=2, removed=3, by_content=True)
    assert measure_text(0, 0, area=both).endswith(
        "12 masks: 1 changed, 2 new, 3 removed since measured"
    )
    current = Freshness(12, True, 0, by_content=True)
    assert measure_text(0, 0, area=current).endswith("12 masks: measured, current")


def test_the_metrics_bar_says_when_the_comparison_is_by_file_time(app):
    from app.gui.metrics_bar import MetricsBar

    bar = MetricsBar()
    bar.set_metrics(length=1.0, stale=True, by_time=True, source="x.csv")
    assert "Compared by file time" in bar._length_cell.toolTip()
    bar.set_metrics(length=1.0, stale=True, by_time=False, source="x.csv")
    assert "Compared by file time" not in bar._length_cell.toolTip()
    bar.set_metrics(length=1.0, stale=False, by_time=True, source="x.csv")
    assert "Compared by file time" not in bar._length_cell.toolTip()
    bar.set_metrics(length=1.0, area=2.0, stale=True, by_time=True, area_by_time=False)
    assert "Compared by file time" in bar._length_cell.toolTip()
    assert "Compared by file time" not in bar._area_cell.toolTip()


def test_unchanged_is_true_only_when_every_input_matches(tmp_path):
    layout, run, stems = _measured(tmp_path / "repaint")
    assert run.unchanged() is True
    _repaint(os.path.join(layout.mask_dir, f"{stems[0]}.png"))
    assert run.unchanged() is False

    layout, run, _stems = _measured(tmp_path / "added")
    cv2.imwrite(os.path.join(layout.mask_dir, "new.png"), _line_image(7))
    again = MeasureRun(
        AREA, layout.scan_masks(extensions=(".png",)), layout.mask_dir, project_root=layout.root
    )
    assert again.unchanged() is False

    layout, run, _stems = _measured(tmp_path / "unrecorded")
    os.remove(inputs_record_path(run.csv_path))
    assert run.unchanged() is False

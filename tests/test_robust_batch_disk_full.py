"""R7: a full disk ends a generation run at the next batch boundary.

A failed save is recorded per image and the run goes on (PI-03): right for a mask
another program holds open, wrong for a full disk. Once the disk is full every later
save fails too, but the run went on decoding and running the model over the rest of
the folder (for 46k images, minutes of GPU time) only to report thousands of failures
at the end. A save that fails with ENOSPC now stops the run at the next batch
boundary with that error: the outputs already on disk are kept and listed, the run
manifest says "failed" (R7), and the handler shows the message at once.
"""

from __future__ import annotations

import errno
import os

import pytest

from app.inference.batch_engine import BatchReport, run_batches
from tests.test_generation_batch_engine import NAMES, _make_images, _spec, _step


def _disk_fills_after(n_saves, out_dir, *, err=errno.ENOSPC):
    """A save that writes the first ``n_saves`` files, then fails like a full disk."""
    from PIL import Image

    saved = []

    def save(arr, path):
        if len(saved) >= n_saves:
            raise OSError(err, os.strerror(err), path)
        saved.append(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        Image.fromarray(arr, mode="L").save(os.path.join(out_dir, stem + ".png"))

    return save


def test_a_full_disk_stops_the_run_at_the_next_batch(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)  # 7 images
    out = str(tmp_path / "out")
    steps = []

    def step(x):
        steps.append(len(x))
        return _step(x)

    report = BatchReport()
    with pytest.raises(OSError) as caught:
        run_batches(
            paths,
            _spec(out, step=step, save=_disk_fills_after(2, out)),
            batch_size=2,
            io_threads=1,
            report=report,
        )

    assert caught.value.errno == errno.ENOSPC
    assert report.written == sorted(os.listdir(out)) == NAMES[:2]  # what reached the disk
    assert report.failed and all(
        "No space" in reason or "28" in reason for _, reason in report.failed
    )
    assert len(steps) < 4  # the model did not run over the whole folder


def test_other_save_errors_still_fail_one_image_each(tmp_path):
    paths = _make_images(str(tmp_path / "in"), NAMES)
    out = str(tmp_path / "out")

    report = run_batches(
        paths,
        _spec(out, save=_disk_fills_after(2, out, err=errno.EACCES)),
        batch_size=2,
        io_threads=1,
    )

    assert report.status == "completed"
    assert len(report.written) == 2 and len(report.failed) == len(NAMES) - 2

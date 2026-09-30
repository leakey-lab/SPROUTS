"""ReadAhead and read_image: the Correct page reads its next images off the GUI thread
(KNOWN_ISSUES #6, background prefetch of the neighbour images). Qt-free."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Executor, Future
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.gui.correct.read_ahead import AheadImage, ReadAhead, file_stamp, read_image

ROOT = Path(__file__).resolve().parents[1]


class InlineExecutor(Executor):
    """Runs each job at once on the calling thread (a finished Future comes back)."""

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, fn, /, *args, **kwargs):
        self.submitted += 1
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - the future carries it
            future.set_exception(exc)
        return future


class Payload:
    """A layer payload that reports its size."""

    def __init__(self, nbytes: int) -> None:
        self.nbytes = nbytes


def photo(tmp_path, name: str = "a.png", w: int = 40, h: int = 30, value: int = 90) -> str:
    path = str(tmp_path / name)
    cv2.imwrite(path, np.full((h, w, 3), value, np.uint8))
    return path


def bump(path: str) -> None:
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


def entry(path: str, nbytes: int = 0) -> AheadImage:
    return AheadImage(path, file_stamp(path), np.zeros((1, 1, 3), np.uint8), {"x": Payload(nbytes)})


# ------------------------------------------------------------------ read_image
def test_read_image_decodes_the_photo_read_only_and_hands_its_size_to_each_reader(tmp_path):
    path = photo(tmp_path, w=40, h=30)
    sizes = []

    def reader(size):
        sizes.append(size)
        return Payload(7)

    result = read_image(path, {"mask": reader, "skeleton": reader})

    assert result is not None and result.image_path == path
    assert result.photo.shape == (30, 40, 3) and not result.photo.flags.writeable
    assert result.size == (40, 30) and sizes == [(40, 30), (40, 30)]
    assert set(result.layers) == {"mask", "skeleton"}
    assert result.nbytes == 30 * 40 * 3 + 14
    assert result.stamp == file_stamp(path) and result.is_current()


def test_the_photo_is_decoded_as_the_editor_decodes_it(tmp_path):
    from app import enhancement

    path = photo(tmp_path)

    result = read_image(path, {})

    assert np.array_equal(result.photo, enhancement.SourceImageCache().get(path))


def test_a_reader_that_raises_is_left_out_and_the_others_are_kept(tmp_path, caplog):
    path = photo(tmp_path)

    def broken(_size):
        raise RuntimeError("boom")

    result = read_image(path, {"mask": broken, "skeleton": lambda _size: "payload"})

    assert dict(result.layers) == {"skeleton": "payload"}
    assert "Could not read the mask" in caplog.text


@pytest.mark.parametrize("content", [None, b"", b"not an image"])
def test_a_missing_empty_or_undecodable_photo_reads_nothing(tmp_path, content):
    path = str(tmp_path / "x.png")
    if content is not None:
        Path(path).write_bytes(content)

    assert read_image(path, {"mask": lambda _size: 1}) is None


def test_a_photo_rewritten_after_its_read_is_not_current(tmp_path):
    path = photo(tmp_path)
    result = read_image(path, {})

    bump(path)

    assert not result.is_current()


# ------------------------------------------------------------------- ReadAhead
def test_a_read_is_handed_over_once(tmp_path):
    path = photo(tmp_path)
    ahead = ReadAhead(executor=InlineExecutor())

    assert ahead.request(path, lambda: read_image(path, {}))
    first = ahead.take(path)

    assert first is not None and first.image_path == path
    assert ahead.take(path) is None


def test_an_image_already_read_or_being_read_is_not_queued_again(tmp_path):
    path = photo(tmp_path)
    executor = InlineExecutor()
    ahead = ReadAhead(executor=executor)

    assert ahead.request(path, lambda: read_image(path, {}))
    assert not ahead.request(path, lambda: read_image(path, {}))
    assert executor.submitted == 1


def test_a_stale_read_is_read_again_and_never_handed_over(tmp_path):
    path = photo(tmp_path)
    executor = InlineExecutor()
    ahead = ReadAhead(executor=executor)
    ahead.request(path, lambda: read_image(path, {}))

    bump(path)

    assert ahead.take(path) is None  # the photo changed since the read
    ahead.request(path, lambda: read_image(path, {}))
    bump(path)
    assert ahead.request(path, lambda: read_image(path, {}))  # stale: queued again
    assert executor.submitted == 3
    assert ahead.take(path) is not None


def test_take_never_waits_for_a_running_job(tmp_path):
    path = photo(tmp_path)
    release = threading.Event()
    ahead = ReadAhead()
    try:

        def slow():
            release.wait(5)
            return read_image(path, {})

        ahead.request(path, slow)
        t0 = time.perf_counter()
        assert ahead.take(path) is None
        assert time.perf_counter() - t0 < 0.1
        release.set()
        deadline = time.perf_counter() + 5
        result = None
        while result is None and time.perf_counter() < deadline:
            result = ahead.take(path)
            time.sleep(0.005)
        assert result is not None and result.image_path == path
    finally:
        release.set()
        ahead.close()


def test_the_worker_is_not_the_calling_thread(tmp_path):
    path = photo(tmp_path)
    seen = []
    ahead = ReadAhead()
    try:
        ahead.request(path, lambda: seen.append(threading.current_thread()) or read_image(path, {}))
        deadline = time.perf_counter() + 5
        while ahead.take(path) is None and time.perf_counter() < deadline:
            time.sleep(0.005)
    finally:
        ahead.close()

    assert seen and seen[0] is not threading.current_thread()


def test_a_failed_job_hands_over_nothing(tmp_path, caplog):
    path = photo(tmp_path)
    ahead = ReadAhead(executor=InlineExecutor())

    def broken():
        raise OSError("disk gone")

    ahead.request(path, broken)

    assert ahead.take(path) is None
    assert "read-ahead job failed" in caplog.text


def test_it_keeps_at_most_max_images_dropping_the_oldest(tmp_path):
    paths = [photo(tmp_path, f"{name}.png") for name in "abcd"]
    ahead = ReadAhead(max_images=3, executor=InlineExecutor())

    for path in paths:
        ahead.request(path, lambda path=path: entry(path))

    assert ahead.take(paths[0]) is None
    assert all(ahead.take(path) is not None for path in paths[1:])


def test_it_keeps_at_most_max_bytes_but_always_the_newest(tmp_path):
    paths = [photo(tmp_path, f"{name}.png") for name in "abc"]
    ahead = ReadAhead(max_images=3, max_bytes=100, executor=InlineExecutor())

    ahead.request(paths[0], lambda: entry(paths[0], 60))
    ahead.request(paths[1], lambda: entry(paths[1], 60))  # 120 > 100: the oldest goes
    assert ahead.pending() == [os.path.normcase(os.path.abspath(paths[1]))]
    ahead.request(paths[2], lambda: entry(paths[2], 500))  # alone over budget: still kept

    assert ahead.take(paths[2]) is not None


def test_keep_only_invalidate_and_clear_forget_entries(tmp_path):
    a, b, c = (photo(tmp_path, f"{name}.png") for name in "abc")
    ahead = ReadAhead(executor=InlineExecutor())
    for path in (a, b, c):
        ahead.request(path, lambda path=path: entry(path))

    ahead.keep_only([b, c])
    assert ahead.take(a) is None
    ahead.invalidate(b)
    assert ahead.take(b) is None
    ahead.clear()
    assert ahead.take(c) is None and ahead.pending() == []


def test_keep_only_cancels_a_queued_job(tmp_path):
    a, b = photo(tmp_path, "a.png"), photo(tmp_path, "b.png")
    release = threading.Event()
    ahead = ReadAhead()
    try:
        ahead.request(a, lambda: release.wait(5) and entry(a))  # occupies the one worker
        ran = []
        ahead.request(b, lambda: ran.append(b) or entry(b))  # queued behind it
        ahead.keep_only([a])
        release.set()
        deadline = time.perf_counter() + 5
        while ahead.take(a) is None and time.perf_counter() < deadline:
            time.sleep(0.005)
    finally:
        release.set()
        ahead.close()

    assert ran == []


def test_a_closed_read_ahead_takes_no_more_jobs(tmp_path):
    path = photo(tmp_path)
    ahead = ReadAhead()
    ahead.close()

    assert not ahead.request(path, lambda: read_image(path, {}))
    assert ahead.take(path) is None


def test_paths_are_matched_like_the_file_system_matches_them(tmp_path):
    path = photo(tmp_path)
    ahead = ReadAhead(executor=InlineExecutor())
    ahead.request(path, lambda: read_image(path, {}))

    other_spelling = os.path.join(os.path.dirname(path), ".", os.path.basename(path))
    if os.name == "nt":
        other_spelling = other_spelling.upper()

    assert ahead.take(other_spelling) is not None


def test_the_module_loads_no_pyqt():
    probe = (
        "import importlib, sys; importlib.import_module('app.gui.correct.read_ahead'); "
        "print(sorted(m for m in sys.modules if m == 'PyQt6' or m.startswith('PyQt6.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_a_read_taken_for_an_open_that_did_not_happen_can_be_put_back(tmp_path):
    path = photo(tmp_path)
    ahead = ReadAhead(executor=InlineExecutor())
    ahead.request(path, lambda: read_image(path, {}))
    taken = ahead.take(path)

    ahead.put(taken)

    assert ahead.take(path) is taken
    ahead.close()
    ahead.put(taken)  # closed: nothing is kept
    assert ahead.pending() == []

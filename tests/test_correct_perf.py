"""Latency of the Correct page (plan P7b Step 4, spec section 14).

Structural checks, always on: one photo decode per image opened; a layer switch opens no
document and reads no file; stroke segments paint the canvas in place; a skeleton eraser drag
repaints at most once per 16 ms frame; the undo timeline is bounded.

Wall-clock smoke, always on, with about 10x headroom over the budgets: open < 150 ms,
layer switch < 20 ms, stroke segment < 1 ms, polyline commit < 60 ms, save both < 100 ms.

Strict budgets, only with ``SPROUTS_PERF_TESTS=1`` (medians, this machine, offscreen raster):
open <= 10 ms (640, both layers) and <= 90 ms (2048), layer switch <= 5 ms, stroke segment
<= 0.05 ms, polyline commit <= 6 ms (640), save both <= 25 ms (640), and the first open in a
fresh process after ``prewarm_skeletonize()`` (as ``MainWindow`` does once it shows) <= 100 ms.
The inputs are the synthetic golden project (a model mask and a 341x256 generated skeleton);
the timed opens get its photo as the real data comes, a quality-40 JPEG, at 640x480 and at
2048x1536 (with the mask resized to match).
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import app.enhancement as enhancement  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.masks import MaskStore  # noqa: E402
from tests.correct_goldens_support import SYNTH_STEM, make_synth_project  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    Mouse,
    click,
    drag,
    editor_factory,
    install_guards,
    qapp,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRICT = os.environ.get("SPROUTS_PERF_TESTS") == "1"
strict = pytest.mark.skipif(not STRICT, reason="wall-clock budget: set SPROUTS_PERF_TESTS=1")
CASE = "synth_model_mask_and_skeleton"
PAGE = (1400, 900)


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


def synth(tmp_path, name: str = "a") -> tuple[str, str]:
    """A fresh copy of the synthetic 640 project: (folder, image)."""
    return make_synth_project(str(tmp_path / name), CASE)


def like_the_data(tmp_path, name: str, size: tuple[int, int] = (640, 480)) -> tuple[str, str]:
    """The synthetic project as the real data comes: a JPEG photo (quality 40, the data's)
    of ``size`` with the model mask at that size and the 341x256 generated skeleton."""
    folder, png = synth(tmp_path, name)
    photo = cv2.resize(cv2.imread(png), size, interpolation=cv2.INTER_CUBIC)
    image = os.path.splitext(png)[0] + ".jpg"
    cv2.imwrite(image, photo, [cv2.IMWRITE_JPEG_QUALITY, 40])
    os.remove(png)
    mask_path = os.path.join(folder, "mask", f"{SYNTH_STEM}.png")
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    cv2.imwrite(mask_path, cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST))
    return folder, image


def median_ms(sample, count: int, warmup: int = 2) -> float:
    """Median wall time of ``sample()`` in ms (``sample`` may return its own ms)."""
    times = []
    for index in range(warmup + count):
        t0 = time.perf_counter()
        result = sample()
        elapsed = result if isinstance(result, float) else (time.perf_counter() - t0) * 1000
        if index >= warmup:
            times.append(elapsed)
    return statistics.median(times)


class Counter:
    """Wraps a callable and counts its calls."""

    def __init__(self, target) -> None:
        self.target, self.calls = target, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.target(*args, **kwargs)


# ============================================================================ structural
def test_an_image_is_decoded_once_per_open_and_not_again_on_re_entry(
    make_editor, tmp_path, monkeypatch
):
    folder, image = synth(tmp_path)
    project = os.path.normcase(os.path.abspath(folder))
    reads: list[str] = []
    real_fromfile = np.fromfile

    def fromfile(source, *args, **kwargs):
        if isinstance(source, (str, os.PathLike)):  # a path (a library may pass a file object)
            path = os.path.normcase(os.path.abspath(os.fspath(source)))
            if path.startswith(project):
                reads.append(path)
        return real_fromfile(source, *args, **kwargs)

    monkeypatch.setattr(np, "fromfile", fromfile)
    editor = make_editor(image, folder, size=PAGE)
    photo = os.path.normcase(os.path.abspath(image))
    assert reads.count(photo) == 1  # one decode feeds the photo, Enhance and the pyramid
    assert len(reads) == 3  # the photo, the model mask, the generated skeleton

    editor.apply_normalization()
    editor.load_image(image, folder)  # re-entry of the same, unchanged image
    editor.set_active_layer(SKELETON)
    assert len(reads) == 3


def test_a_layer_switch_opens_no_document_and_reads_no_file(make_editor, tmp_path, monkeypatch):
    folder, image = synth(tmp_path)
    editor = make_editor(image, folder, size=PAGE)
    spies = {
        "fromfile": Counter(np.fromfile),
        "imdecode": Counter(cv2.imdecode),
        "decode": Counter(enhancement.imread_unicode),
        "mask read": Counter(MaskStore.read),
    }
    monkeypatch.setattr(np, "fromfile", spies["fromfile"])
    monkeypatch.setattr(cv2, "imdecode", spies["imdecode"])
    monkeypatch.setattr(enhancement, "imread_unicode", spies["decode"])
    monkeypatch.setattr(MaskStore, "read", spies["mask read"])
    for layer_id, layer in editor.layers.items():
        for name in ("open", "revert", "close"):
            spies[f"{layer_id}.{name}"] = spy = Counter(getattr(layer, name))
            monkeypatch.setattr(layer, name, spy)

    for index in range(20):
        editor.set_active_layer(SKELETON if index % 2 == 0 else MASK)
        QApplication.processEvents()
    editor.swap_layer()
    editor.request_tool(MASK, "fill")

    assert {name: spy.calls for name, spy in spies.items() if spy.calls} == {}


def test_stroke_segments_paint_the_canvas_in_place(make_editor, tmp_path):
    folder, image = synth(tmp_path)
    editor = make_editor(image, folder, size=PAGE)
    canvas, router, event = editor.layers[MASK].canvas, editor.router, Mouse()
    serial = canvas.image.cacheKey() >> 32

    router.canvas_press(QPoint(10, 10), event)
    for i in range(100):
        router.canvas_move(QPoint(10 + i * 5, 10 + i * 3), event, True)
        assert canvas.image.cacheKey() >> 32 == serial, i  # never a deep copy of the canvas
    router.canvas_release(QPoint(505, 307), event)

    assert canvas.image.cacheKey() >> 32 == serial


def test_a_skeleton_eraser_drag_repaints_at_most_once_per_frame(make_editor, tmp_path, monkeypatch):
    folder, image = synth(tmp_path)
    editor = make_editor(image, folder, size=PAGE)
    skeleton = editor.layers[SKELETON]
    editor.request_tool(SKELETON, "eraser")
    spy = Counter(skeleton.item.set_mask)
    monkeypatch.setattr(skeleton.item, "set_mask", spy)
    router, event = editor.router, Mouse()

    router.canvas_press(QPoint(100, 100), event)
    assert spy.calls == 1  # the press shows what it erased at once
    for i in range(100):  # a burst of moves between two frames
        router.canvas_move(QPoint(100 + i * 4, 100 + (i % 7)), event, True)
    assert spy.calls == 1
    QTest.qWait(3 * skeleton.RENDER_INTERVAL_MS)
    assert spy.calls == 2  # one coalesced frame for the whole burst
    router.canvas_release(QPoint(500, 100), event)
    assert spy.calls == 3  # the release thins and shows the result


def test_the_undo_timeline_is_bounded(make_editor, tmp_path):
    folder, image = synth(tmp_path)
    editor = make_editor(image, folder, size=PAGE)
    journal = editor.session.journal
    editor.layers[MASK].set_size(2)

    for i in range(journal.maxlen + 4):
        click(editor, (20 + (i % 60) * 10, 20 + (i // 60) * 10))

    assert journal.maxlen == 300  # each layer's newest 100 actions
    assert len(journal) == 100  # the mask's entries, capped at its own history's depth
    assert len(editor.layers[MASK].document.history.undo_stack) == 100  # the mask's own cap
    editor.undo()
    assert editor.session.journal.redo_entries == (MASK,)


# ====================================================================== wall-clock timings
def opens(make_editor, projects) -> float:
    """Median ms of ``load_image`` alternating between two images (each a real open)."""
    folder, image = projects[0]
    editor = make_editor(image, folder, size=PAGE)
    state = {"i": 0}

    def open_next() -> None:
        state["i"] += 1
        folder, image = projects[state["i"] % 2]
        editor.load_image(image, folder)

    return median_ms(open_next, 10)


def switches(editor) -> float:
    state = {"i": 0}

    def switch() -> None:
        state["i"] += 1
        editor.set_active_layer(SKELETON if state["i"] % 2 else MASK)

    return median_ms(switch, 20)


def stroke_segments(editor) -> float:
    editor.set_active_layer(MASK)
    router, event = editor.router, Mouse()
    router.canvas_press(QPoint(20, 20), event)
    times = []
    for i in range(200):
        point = QPoint(20 + (i * 3) % 600, 20 + (i * 7) % 440)
        t0 = time.perf_counter()
        router.canvas_move(point, event, True)
        times.append((time.perf_counter() - t0) * 1000)
    router.canvas_release(QPoint(20, 20), event)
    return statistics.median(times)


def polyline_commits(editor) -> float:
    router, event = editor.router, Mouse()

    def commit() -> float:
        editor.request_tool(SKELETON, "polyline")
        for point in ((100, 100), (220, 180), (380, 150)):
            router.canvas_press(QPoint(*point), event)
            router.canvas_release(QPoint(*point), event)
        t0 = time.perf_counter()
        editor.controls.finish_button.click()
        elapsed = (time.perf_counter() - t0) * 1000
        assert editor.layers[SKELETON].draft_state().points == 0
        editor.undo()
        return elapsed

    return median_ms(commit, 8)


def saves_of_both(editor) -> float:
    state = {"i": 0}

    def save() -> float:
        state["i"] += 1
        dy = (state["i"] * 13) % 300  # a new edit each time: the same one is saved already
        editor.set_active_layer(MASK)
        drag(editor, (30, 30 + dy), (200, 90 + dy))
        editor.request_tool(SKELETON, "polyline")
        click(editor, (100, 60 + dy))
        click(editor, (300, 80 + dy))
        editor.controls.finish_button.click()
        assert editor.session.dirty_layers() == [MASK, SKELETON]
        t0 = time.perf_counter()
        assert editor.save() is True
        return (time.perf_counter() - t0) * 1000

    return median_ms(save, 5, warmup=1)


@pytest.fixture
def page(make_editor, tmp_path):
    folder, image = synth(tmp_path)
    return make_editor(image, folder, size=PAGE)


def test_open_smoke(make_editor, tmp_path):
    assert opens(make_editor, [like_the_data(tmp_path, "a"), like_the_data(tmp_path, "b")]) < 150


def test_layer_switch_smoke(page):
    assert switches(page) < 20


def test_stroke_segment_smoke(page):
    assert stroke_segments(page) < 1


def test_polyline_commit_smoke(page):
    assert polyline_commits(page) < 60


def test_save_both_smoke(page):
    assert saves_of_both(page) < 100


@strict
def test_open_640_within_10_ms(make_editor, tmp_path):
    assert opens(make_editor, [like_the_data(tmp_path, "a"), like_the_data(tmp_path, "b")]) <= 10


@strict
def test_open_2048_within_90_ms(make_editor, tmp_path):
    big = [like_the_data(tmp_path, name, (2048, 1536)) for name in ("a", "b")]
    assert opens(make_editor, big) <= 90


@strict
def test_layer_switch_within_5_ms(page):
    assert switches(page) <= 5


@strict
def test_stroke_segment_within_0_05_ms(page):
    assert stroke_segments(page) <= 0.05


@strict
def test_polyline_commit_within_6_ms(page):
    assert polyline_commits(page) <= 6


@strict
def test_save_both_within_25_ms(page):
    assert saves_of_both(page) <= 25


FIRST_OPEN = """
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT"] = "0"
os.environ["SPROUTS_CONFIG_DIR"] = sys.argv[3]
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from app.gui import skeleton_graph_model
from app.gui.correct.editor import CorrectEditor
editor = CorrectEditor()
editor.resize(1400, 900)
editor.show()
app.processEvents()
skeleton_graph_model.prewarm_skeletonize()  # MainWindow runs it off the GUI thread at show
prewarmed = "skimage.morphology" in sys.modules
t0 = time.perf_counter()
editor.load_image(sys.argv[1], sys.argv[2])
elapsed = (time.perf_counter() - t0) * 1000
assert editor.layers["skeleton"].document.model.mask.any()
print(f"{elapsed:.3f} {prewarmed}", flush=True)
# Close the page before the QApplication goes: a script that leaves the page to the garbage
# collector after the application object is gone can crash at exit (as any Qt script can).
from PyQt6.QtCore import QEvent
editor.close()
editor.deleteLater()
QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
"""


@strict
def test_the_first_open_in_a_fresh_process_after_the_prewarm_within_100_ms(tmp_path):
    folder, image = like_the_data(tmp_path, "a")
    times = []
    for _ in range(3):
        run = subprocess.run(
            [sys.executable, "-c", FIRST_OPEN, image, folder, str(tmp_path / "cfg")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode == 0, run.stderr
        elapsed, prewarmed = run.stdout.split()
        assert prewarmed == "True"  # scikit-image was loaded before the open
        times.append(float(elapsed))

    assert statistics.median(times) <= 100, times

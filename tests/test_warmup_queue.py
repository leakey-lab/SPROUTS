"""The warm-up queue and the hover intent (W3-WARM W2-W4, :mod:`app.gui.warmup`).

One warm-up at a time, each on a fresh thread of its own (or in the pool when named
``pooled``), each at most once, never started inside the call that asked for it, never
while ``blocked()`` says why not (except the start-up one), and dropped when the window
closes before it started. With ``freeze``, full collections are held while a job imports
and the heap is frozen afterwards on the GUI thread, but only when the job itself imported
its package. Jobs are stubs, except in the one subprocess test that imports torch and
torchvision the way the app does (a reused pool thread deadlocked there).
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt, QThreadPool  # noqa: E402
from PyQt6.QtGui import QEnterEvent, QFocusEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app import gc_policy  # noqa: E402
from app.gui import warmup  # noqa: E402
from app.gui.warmup import HoverIntent, WarmupQueue  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test"])


@pytest.fixture
def pool():
    own = QThreadPool()
    own.setMaxThreadCount(4)
    yield own
    assert own.waitForDone(10_000)
    QApplication.processEvents()


@pytest.fixture
def made(app):
    """Objects to delete after the test (queues, widgets); a queue's thread is joined first."""
    objects: list = []
    yield objects
    for obj in objects:
        if isinstance(obj, WarmupQueue):
            assert obj.wait(10)
    QApplication.processEvents()
    delete_widgets(objects, close=False)


@pytest.fixture(autouse=True)
def unfrozen():
    thresholds = gc.get_threshold()
    frozen = gc.get_freeze_count()
    yield
    while gc_policy.full_collections_held():
        gc_policy.release_full_collections()
    gc.set_threshold(*thresholds)
    count = gc.get_freeze_count()
    if count > frozen or (frozen and not count):
        gc.unfreeze()
        gc.collect()
        if frozen:
            gc.freeze()


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


class Job:
    """A stub warm-up: records its thread and time, optionally waits for ``release``."""

    def __init__(self, name: str, *, result: str | None = "", hold: bool = False) -> None:
        self.name = name
        self.result = result
        self.runs: list[tuple[int, float, float]] = []  # (thread, start, end)
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.started = threading.Event()

    def __call__(self):
        start = time.perf_counter()
        self.started.set()
        self.release.wait(10)
        self.runs.append((threading.get_ident(), start, time.perf_counter()))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_queue(made, pool, jobs, **kwargs) -> WarmupQueue:
    queue = WarmupQueue(None, jobs=jobs, pool=pool, retry_ms=kwargs.pop("retry_ms", 20), **kwargs)
    finished: list[tuple[str, str]] = []
    queue.finished.connect(lambda key, error: finished.append((key, error)))
    queue.finished_log = finished
    made.append(queue)
    return queue


# ------------------------------------------------------------------ the queue
def test_a_request_runs_its_job_once_off_the_gui_thread_after_the_event_loop_turns(made, pool):
    job = Job("a")
    queue = make_queue(made, pool, {"a": job})

    assert queue.request("a") is True
    assert not job.started.wait(0.2)  # nothing inside the call that asked
    assert queue.state("a") == "queued"

    assert wait(lambda: queue.finished_log == [("a", "")])
    assert len(job.runs) == 1 and job.runs[0][0] != threading.main_thread().ident
    assert queue.state("a") == "done" and queue.result("a") == ""
    assert queue.request("a") is False  # at most once per session
    QTest.qWait(100)
    assert len(job.runs) == 1


def test_unknown_or_repeated_requests_are_ignored(made, pool):
    job = Job("a", hold=True)
    queue = make_queue(made, pool, {"a": job})
    assert queue.request("nope") is False and queue.request(None) is False
    assert queue.request("a") is True
    assert queue.request("a") is False  # queued
    assert wait(lambda: queue.running() == "a")
    assert queue.request("a") is False  # running
    job.release.set()
    assert wait(lambda: queue.state("a") == "done")


def test_warmups_run_one_at_a_time_in_the_order_asked(made, pool):
    first, second = Job("first", hold=True), Job("second")
    queue = make_queue(made, pool, {"first": first, "second": second})
    queue.request("first")
    queue.request("second")
    assert wait(lambda: first.started.is_set())
    QTest.qWait(150)
    assert not second.started.is_set()  # never two heavy imports at once
    assert queue.pending() == ("second",)
    first.release.set()
    assert wait(lambda: [key for key, _ in queue.finished_log] == ["first", "second"])
    assert second.runs[0][1] >= first.runs[0][2]


def test_a_blocked_warmup_waits_and_asks_again(made, pool):
    job = Job("a")
    reasons = ["a folder is loading"]
    asked: list[float] = []

    def blocked():
        asked.append(time.perf_counter())
        return reasons[0]

    queue = make_queue(made, pool, {"a": job}, blocked=blocked, retry_ms=20)
    queue.request("a")
    QTest.qWait(200)
    assert job.runs == [] and len(asked) >= 3  # asked again every retry_ms
    reasons[0] = None
    assert wait(lambda: len(job.runs) == 1)


def test_the_start_up_warmup_ignores_the_gate(made, pool):
    job = Job("startup")
    queue = make_queue(
        made, pool, {"startup": job}, blocked=lambda: "always busy", ungated=("startup",)
    )
    queue.request("startup")
    assert wait(lambda: len(job.runs) == 1)


def test_shutdown_drops_the_queued_warmups_and_lets_the_running_one_finish(made, pool):
    running, queued = Job("running", hold=True), Job("queued")
    queue = make_queue(made, pool, {"running": running, "queued": queued})
    queue.request("running")
    queue.request("queued")
    assert wait(running.started.is_set)
    queue.shutdown()
    assert queue.pending() == ()
    assert queue.request("queued") is False  # closed
    running.release.set()
    assert wait(lambda: queue.finished_log == [("running", "")])
    QTest.qWait(150)
    assert queued.runs == []


def test_a_warmup_blocked_at_close_never_starts(made, pool):
    job = Job("a")
    queue = make_queue(made, pool, {"a": job}, blocked=lambda: "the project is settling")
    queue.request("a")
    QTest.qWait(60)
    queue.shutdown()
    QTest.qWait(150)
    assert job.runs == [] and queue.state("a") == "idle"


def test_a_failing_job_reports_its_error_and_the_queue_goes_on(made, pool, caplog):
    broken = Job("broken", result=RuntimeError("DLL load failed while importing _C"))
    failed = Job("failed", result="dash is not installed")
    after = Job("after")
    queue = make_queue(made, pool, {"broken": broken, "failed": failed, "after": after})
    for key in ("broken", "failed", "after"):
        queue.request(key)
    assert wait(lambda: len(queue.finished_log) == 3)
    assert queue.finished_log == [
        ("broken", "DLL load failed while importing _C"),
        ("failed", "dash is not installed"),
        ("after", ""),
    ]
    assert "Warm-up broken failed" in caplog.text


# ------------------------------------------------------------------ threads
def test_each_warmup_runs_on_a_thread_of_its_own(made, pool):
    """Never a reused QThreadPool thread: torch's C++ code there deadlocked the next job."""
    names: list[tuple[str, int]] = []

    def job(key):
        def run():
            names.append((threading.current_thread().name, threading.get_ident()))
            return ""

        return run

    queue = make_queue(made, pool, {"a": job("a"), "b": job("b")})
    queue.request("a")
    queue.request("b")
    assert wait(lambda: len(queue.finished_log) == 2)
    assert [name for name, _ident in names] == ["warm-up a", "warm-up b"]
    assert pool.activeThreadCount() == 0 and QThreadPool.globalInstance().activeThreadCount() == 0


def test_pooled_warmups_run_in_the_pool(made, pool):
    seen = []

    def run():
        seen.append((threading.current_thread().name, pool.activeThreadCount()))
        return ""

    queue = make_queue(made, pool, {"startup": run}, pooled=("startup",))
    queue.request("startup")
    assert wait(lambda: queue.finished_log == [("startup", "")])
    assert seen[0][0] != "warm-up startup" and seen[0][1] == 1


REPO = Path(__file__).resolve().parents[1]


def test_torch_then_its_models_warm_up_without_a_deadlock():
    """The app's two Generate warm-ups, in order, in a fresh process (torch imported there
    first): on one reused QThreadPool thread the second hung for good (4 of 4 runs)."""
    code = textwrap.dedent(
        """
        import sys, time
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication(["test"])
        from app.gui import warmup
        queue = warmup.WarmupQueue(
            jobs={warmup.TORCH: warmup.warm_inference, warmup.MODELS: warmup.warm_models}
        )
        done = []
        queue.finished.connect(lambda key, error: done.append((key, error)))
        queue.request(warmup.TORCH)
        queue.request(warmup.MODELS)
        end = time.time() + 150
        while time.time() < end and len(done) < 2:
            app.processEvents()
            time.sleep(0.01)
        print(done == [("torch", ""), ("models", "")], "torchvision" in sys.modules, flush=True)
        queue.wait(10)
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["True", "True"]


# ------------------------------------------------------------------ garbage collection
FAKE_PACKAGE = "_w3warm_fake_package"


@pytest.fixture
def fake_package(monkeypatch):
    """A warm-up key whose job 'imports' a package (puts it in sys.modules)."""
    monkeypatch.setitem(warmup.MARKERS, "pkg", FAKE_PACKAGE)
    seen: dict = {}

    def job():
        seen["threshold"] = gc.get_threshold()
        seen["held"] = gc_policy.full_collections_held()
        sys.modules[FAKE_PACKAGE] = types.ModuleType(FAKE_PACKAGE)
        return ""

    yield job, seen
    sys.modules.pop(FAKE_PACKAGE, None)


def test_a_fresh_import_is_frozen_after_a_hold_on_the_full_collections(made, pool, fake_package):
    job, seen = fake_package
    before = gc.get_threshold()
    frozen = gc.get_freeze_count()
    queue = make_queue(made, pool, {"pkg": job}, freeze=True)
    queue.request("pkg")
    assert wait(lambda: queue.finished_log == [("pkg", "")])
    assert seen["held"] and seen["threshold"][2] > 1_000_000  # no full pass mid-import
    assert gc.get_threshold() == before  # released afterwards
    assert gc.get_freeze_count() > frozen


def test_nothing_is_frozen_when_the_package_was_imported_before(
    made, pool, fake_package, monkeypatch
):
    job, seen = fake_package
    sys.modules[FAKE_PACKAGE] = types.ModuleType(FAKE_PACKAGE)  # a job got there first
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda *a, **k: calls.append((a, k)))
    queue = make_queue(made, pool, {"pkg": job}, freeze=True)
    queue.request("pkg")
    assert wait(lambda: queue.finished_log == [("pkg", "")])
    assert seen["held"] and calls == []
    assert not gc_policy.full_collections_held()


def test_without_freeze_nothing_is_held_or_frozen(made, pool, fake_package, monkeypatch):
    job, seen = fake_package
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda *a, **k: calls.append((a, k)))
    queue = make_queue(made, pool, {"pkg": job}, freeze=False)
    queue.request("pkg")
    assert wait(lambda: queue.finished_log == [("pkg", "")])
    assert seen["held"] is False and calls == []


def test_the_start_up_freeze_is_a_young_one_too(made, pool, fake_package, monkeypatch):
    """A full collection at the start-up freeze found nothing to free and cost 20-30 ms."""
    job, _seen = fake_package
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda reason, **k: calls.append((reason, k)))
    queue = make_queue(made, pool, {"pkg": job}, freeze=True, ungated=("pkg",))
    queue.request("pkg")
    assert wait(lambda: calls)
    assert calls == [("the pkg warm-up", {})]


def test_the_freeze_runs_on_the_gui_thread(made, pool, fake_package, monkeypatch):
    job, _seen = fake_package
    threads = []
    monkeypatch.setattr(gc_policy, "freeze", lambda *a, **k: threads.append(threading.get_ident()))
    queue = make_queue(made, pool, {"pkg": job}, freeze=True)
    queue.request("pkg")
    assert wait(lambda: threads)
    assert threads == [threading.main_thread().ident]


def test_a_package_a_job_imported_is_frozen_once_the_job_ends(made, pool, monkeypatch):
    """A Generate run that starts before its warm-up imports torch itself."""
    monkeypatch.setattr(warmup, "MARKERS", {"pkg": FAKE_PACKAGE})
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda reason, **k: calls.append(reason))
    queue = make_queue(made, pool, {}, freeze=True)
    assert queue.freeze_new_imports("a run's imports") is False  # nothing heavy imported
    try:
        sys.modules[FAKE_PACKAGE] = types.ModuleType(FAKE_PACKAGE)  # the job's import
        assert queue.freeze_new_imports("a run's imports") is True
        assert queue.freeze_new_imports("a run's imports") is False  # nothing new since
    finally:
        sys.modules.pop(FAKE_PACKAGE, None)
    assert calls == ["a run's imports"]


def test_a_warmup_freeze_counts_for_the_job_end(made, pool, fake_package, monkeypatch):
    job, _seen = fake_package
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda reason, **k: calls.append(reason))
    queue = make_queue(made, pool, {"pkg": job}, freeze=True)
    queue.request("pkg")
    assert wait(lambda: calls == ["the pkg warm-up"])
    assert queue.freeze_new_imports("a run's imports") is False  # the warm-up froze it


def test_no_job_end_freeze_without_freezing_or_during_a_warmup(made, pool, monkeypatch):
    calls = []
    monkeypatch.setattr(gc_policy, "freeze", lambda reason, **k: calls.append(reason))
    off = make_queue(made, pool, {}, freeze=False)
    assert off.freeze_new_imports("a run's imports") is False
    busy = Job("busy", hold=True)
    queue = make_queue(made, pool, {"busy": busy}, freeze=True)
    queue.request("busy")
    assert wait(lambda: queue.running() == "busy")
    assert queue.freeze_new_imports("a run's imports") is False  # its own freeze follows
    busy.release.set()
    assert wait(lambda: queue.state("busy") == "done")
    assert calls == []  # a stub package-less job: nothing fresh, nothing frozen


# ------------------------------------------------------------------ hover intent
@pytest.fixture
def buttons(made):
    host = QWidget()
    host.resize(300, 100)
    watched, other = QPushButton("Generate", host), QPushButton("Other", host)
    other.move(150, 0)
    made.append(host)
    host.show()
    return watched, other


def enter(widget) -> None:
    point = QPointF(5, 5)
    QApplication.sendEvent(widget, QEnterEvent(point, point, widget.mapToGlobal(point)))


def leave(widget) -> None:
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))


def test_resting_on_a_watched_button_asks_for_its_warmup(made, buttons):
    watched, other = buttons
    asked: list[str] = []
    intent = HoverIntent(None, asked.append, dwell_ms=60)
    made.append(intent)
    intent.watch(watched, "torch")
    intent.watch(None, "ignored")  # a missing button is skipped

    enter(watched)
    QTest.qWait(20)
    assert asked == []  # passing over is not intent
    assert wait(lambda: asked == ["torch"], 2.0)

    enter(other)  # not watched
    QTest.qWait(120)
    assert asked == ["torch"]


def test_leaving_before_the_dwell_asks_for_nothing(made, buttons):
    watched, _other = buttons
    asked: list[str] = []
    intent = HoverIntent(None, asked.append, dwell_ms=80)
    made.append(intent)
    intent.watch(watched, "torch")
    enter(watched)
    QTest.qWait(20)
    leave(watched)
    QTest.qWait(150)
    assert asked == []


@pytest.mark.parametrize(
    "reason, expected",
    [
        (Qt.FocusReason.TabFocusReason, ["dashboards"]),
        (Qt.FocusReason.ShortcutFocusReason, ["dashboards"]),
        (Qt.FocusReason.MouseFocusReason, []),
    ],
)
def test_keyboard_focus_asks_at_once(made, buttons, reason, expected):
    watched, _other = buttons
    asked: list[str] = []
    intent = HoverIntent(None, asked.append, dwell_ms=5000)
    made.append(intent)
    intent.watch(watched, "dashboards")
    QApplication.sendEvent(watched, QFocusEvent(QEvent.Type.FocusIn, reason))
    assert asked == expected

"""The thread keeper of ``app.workers``: no running QThread is ever destroyed.

A ``QThread`` destroyed while it runs ends the whole process ("QThread: Destroyed while
thread ... is still running", exit 0xC0000409, no traceback). A real session ended so
(app.log.1:7831, 'DashServerThreadArea'): both dashboard threads were parented to the
dashboard, so deleting a dashboard whose server still ran destroyed it. Now every thread is
held from ``start()`` until Qt's own ``finished`` (``keep``, which ``GuardedThread.start``
calls), never has a parent, is deleted after it finished (``release_qthread``), and the
quit asks the kept threads to stop and waits (``stop_all`` on ``aboutToQuit``). The aborts
are checked in child processes; the rules of the code are checked on its source.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import textwrap
import threading
import weakref
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, pyqtSignal

from app import workers
from app.workers import (
    GuardedThread,
    base_finished,
    keep,
    kept_threads,
    release_qthread,
    stop_all,
)
from tests.leak_census import assert_freed, no_gc_fixture  # noqa: F401
from tests.qt_wait import wait_until

ROOT = Path(__file__).resolve().parents[1]


def _run_python(code: str, timeout: float = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ended(thread: QThread) -> bool:
    """``thread`` finished and the keeper let it go (Qt's own finished was delivered)."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return thread not in kept_threads()


class _Gate(GuardedThread):
    """Runs until ``gate`` is set (or it is cancelled)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def work(self) -> None:
        while not self.gate.wait(0.005):
            self.check_cancelled()


# ------------------------------------------------------------------ held while running
def test_a_started_guarded_thread_is_kept_until_qt_says_it_finished(app, no_gc):
    thread = _Gate()
    thread.start()
    try:
        assert thread in kept_threads()
        ref = weakref.ref(thread)
        del thread
        assert ref() is not None  # running: the keeper holds it
    finally:
        ref().gate.set()
    assert ref().wait(5000)
    assert_freed(ref, "the finished thread", pump=True)  # no reference cycle holds it


def test_an_owner_keeps_its_thread_after_it_finished(app):
    """The keeper drops only its own reference: handlers read ``report`` after a run and ask
    ``isRunning()`` later, so it never deletes a thread nobody released."""
    thread = _Gate()
    thread.start()
    thread.gate.set()
    assert thread.wait(5000)
    assert wait_until(lambda: _ended(thread))
    assert not sip.isdeleted(thread) and not thread.isRunning()


def test_keep_takes_a_thread_off_its_parent(app):
    parent = QObject()
    thread = QThread(parent)
    keep(thread)
    assert thread.parent() is None and thread in kept_threads()
    parent.deleteLater()  # a parent deleted while its thread runs destroyed that thread
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not sip.isdeleted(thread)
    release_qthread(thread)  # never started: deleted at once
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(thread) and thread not in kept_threads()


def test_a_guarded_thread_started_with_a_parent_runs_without_it(app):
    parent = QObject()
    thread = _Gate()
    thread.setParent(parent)
    thread.start()
    try:
        assert thread.parent() is None
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert thread.isRunning()
    finally:
        thread.gate.set()
        assert thread.wait(5000)
    assert wait_until(lambda: _ended(thread))


def test_a_kept_thread_its_owner_deleted_is_forgotten(app):
    thread = QThread()
    keep(thread)
    thread.start()
    thread.quit()
    assert thread.wait(2000)
    thread.deleteLater()  # deleted before Qt's own finished reached the keeper
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(thread)
    assert thread not in kept_threads()
    assert stop_all(0) is True


# ----------------------------------------------------------------------------- release
def test_release_deletes_a_running_thread_once_it_finished(app):
    thread = _Gate()
    thread.start()
    destroyed = []
    thread.destroyed.connect(lambda *_: destroyed.append(True))
    release_qthread(thread)
    assert thread.isRunning() and not destroyed  # it did not wait
    thread.gate.set()
    assert wait_until(lambda: _ended(thread) and destroyed == [True])


class _Result(GuardedThread):
    """Emits its result as the last statement of ``work()``, as the Measure thread does."""

    done = pyqtSignal()

    def work(self) -> None:
        self.done.emit()


def test_release_in_a_result_slot_never_waits_for_the_thread(app):
    """A kept thread is deleted when Qt's own finished arrives: the slot no longer blocks the
    GUI thread until the thread has ended (a wait() there took about 0.6 ms each time)."""
    for _ in range(30):
        thread = _Result()
        waited, destroyed = [], []
        thread.wait = lambda *args, waited=waited: waited.append(args) or True
        thread.destroyed.connect(lambda *_, destroyed=destroyed: destroyed.append(True))
        thread.done.connect(lambda thread=thread: release_qthread(thread))
        thread.start()
        assert wait_until(lambda destroyed=destroyed: destroyed)
        assert waited == []
        del thread


def test_release_of_a_running_plain_qthread_never_blocks_and_outlives_its_references():
    # In a child process: destroying a running QThread aborts the whole process. A thread
    # that was not started through GuardedThread is kept from the release on.
    result = _run_python(
        """
        import sys, time
        from PyQt6.QtCore import QCoreApplication, QThread, QTimer, pyqtSignal
        from app.workers import release_qthread

        app = QCoreApplication(sys.argv)

        class Sleeper(QThread):
            finished = pyqtSignal(str)  # shadows QThread.finished, like the app's workers

            def run(self):
                time.sleep(0.3)
                self.finished.emit("done")

        destroyed = []
        thread = Sleeper()
        thread.destroyed.connect(lambda *_: destroyed.append(1))
        thread.destroyed.connect(app.quit)  # quits once deleted (after the append above)
        thread.start()
        t0 = time.perf_counter()
        release_qthread(thread)
        blocked = time.perf_counter() - t0
        del thread  # the keeper must hold the running thread itself
        QTimer.singleShot(1500, app.quit)  # the deadline: a leaked thread ends with destroyed=0
        app.exec()
        print(f"blocked={blocked:.3f} destroyed={len(destroyed)}")
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    blocked, destroyed = result.stdout.split()
    assert float(blocked.split("=")[1]) < 0.05
    assert destroyed == "destroyed=1"


def test_release_deletes_a_finished_thread_at_once(app):
    thread = QThread()
    thread.start()
    thread.quit()
    assert thread.wait(2000)
    destroyed = []
    thread.destroyed.connect(lambda *_: destroyed.append(True))
    release_qthread(thread)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed == [True]
    release_qthread(thread)  # twice, or a deleted thread: nothing happens
    release_qthread(None)


# ---------------------------------------------------------------------------- stop_all
class _Server(QThread):
    """A serve loop that ends on ``stop()``, like the dashboards' server thread."""

    def __init__(self) -> None:
        super().__init__()
        self.stopped = threading.Event()

    def run(self) -> None:
        self.stopped.wait(10)

    def stop(self) -> None:
        self.stopped.set()


class _Stubborn(QThread):
    """Ignores every stop request until ``gate`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def run(self) -> None:
        self.gate.wait(10)


def test_stop_all_stops_every_kept_thread_and_waits(app):
    server, job, loop = _Server(), _Gate(), QThread()  # loop: a plain event-loop thread
    cancelled = []
    job.cancelled.connect(lambda: cancelled.append(True))
    for thread in (server, loop):
        keep(thread)
        thread.start()
    job.start()
    assert all(t.isRunning() for t in (server, job, loop))
    assert stop_all(5000) is True
    assert not any(t.isRunning() for t in (server, job, loop))
    for thread in (server, job, loop):
        assert wait_until(lambda thread=thread: _ended(thread))
    assert cancelled == [True]  # a GuardedThread is asked through cancel()


def test_stop_all_names_a_thread_that_does_not_stop(app, caplog):
    thread = _Stubborn()
    thread.setObjectName("StubbornThread")
    keep(thread)
    thread.start()
    try:
        with caplog.at_level(logging.WARNING, logger=workers.LOGGER_NAME):
            assert stop_all(50) is False
        assert "StubbornThread" in caplog.text
    finally:
        thread.gate.set()
        assert thread.wait(5000)
    assert wait_until(lambda: _ended(thread))


def test_the_quit_stops_the_kept_threads():
    result = _run_python(
        """
        import sys, threading
        from PyQt6.QtCore import QCoreApplication, QTimer
        from app.workers import GuardedThread

        app = QCoreApplication(sys.argv)

        class Loop(GuardedThread):
            def work(self):
                while not self.is_cancelled():
                    threading.Event().wait(0.01)

        thread = Loop()
        thread.start()
        QTimer.singleShot(100, app.quit)
        app.exec()  # aboutToQuit: stop_all
        print("running after the quit:", thread.isRunning())
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "running after the quit: False"


# ------------------------------------------------------------- the aborts, in a process
def test_a_wrapper_dropped_right_after_start_never_aborts():
    # Without the keeper, 3 of 3 runs of this ended the process (exit 0xC0000409).
    result = _run_python(
        """
        import sys, threading, time
        from PyQt6.QtCore import QCoreApplication, QEvent
        from app.workers import GuardedThread, kept_threads

        app = QCoreApplication(sys.argv)
        ran = []

        class Quick(GuardedThread):
            def work(self):
                ran.append(1)

        for _ in range(300):
            Quick().start()  # the only reference goes at once
        end = time.monotonic() + 20
        while (len(ran) < 300 or kept_threads()) and time.monotonic() < end:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            time.sleep(0.002)
        print("ran", len(ran), "kept", len(kept_threads()))
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ran 300 kept 0"


def test_release_in_a_custom_finished_slot_never_aborts():
    # RB-08: a custom finished signal is the last statement of run(); dropping the only
    # reference in its slot raced Qt's end of the thread (about 1 abort in 8,000 runs).
    result = _run_python(
        """
        import sys
        from PyQt6.QtCore import QCoreApplication, QThread, QTimer, pyqtSignal
        from app.workers import release_qthread

        app = QCoreApplication(sys.argv)
        N = 1500

        class Quick(QThread):
            finished = pyqtSignal(str)

            def run(self):
                self.finished.emit("x")

        class Owner:
            thread = None
            done = 0

            def launch(self):
                if self.done >= N:
                    print("survived", self.done)
                    app.quit()
                    return
                self.thread = Quick()
                self.thread.finished.connect(self.on_finished)
                self.thread.start()

            def on_finished(self, _value):
                release_qthread(self.thread)
                self.thread = None
                self.done += 1
                QTimer.singleShot(0, self.launch)

        owner = Owner()
        QTimer.singleShot(0, owner.launch)
        app.exec()
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "survived 1500"


#: A dashboard (fake web view, stub WSGI app, the real server thread) that reached its page
#: load, then deleted while its server thread runs: what any path that skips cleanup_server
#: did (DashboardController._discard deletes a dashboard in a finally even when
#: cleanup_server raised). Each variant ended the process before the keeper.
_DASHBOARD = """
import os, sys, time
from PyQt6 import QtWebEngineWidgets  # noqa: F401
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget
from app.visualization._viz_server import _DashServerThreadBase
from app.visualization.viz_window import DashVizWindow
from app.workers import kept_threads

variant = sys.argv[1]


class Profile(QObject):
    downloadRequested = pyqtSignal(object)


class Page(QObject):
    renderProcessTerminated = pyqtSignal(object, int)

    def __init__(self, profile):
        super().__init__()
        self._profile, self.broken = profile, False

    def profile(self):
        if self.broken:
            raise RuntimeError("wrapped C/C++ object of type QWebEngineProfile has been deleted")
        return self._profile

    def setBackgroundColor(self, _color):
        pass


class View(QWidget):
    loadFinished = pyqtSignal(bool)

    def __init__(self, profile):
        super().__init__()
        self._page, self.loads = Page(profile), []

    def page(self):
        return self._page

    def load(self, url):
        self.loads.append(url.toString())

    def setUrl(self, url):
        pass


class Dash:
    class app:  # noqa: N801
        @staticmethod
        def server(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/html")])
            return [b"<html></html>"]


class AreaServer(_DashServerThreadBase):
    def __init__(self, dash_app, port=0):
        super().__init__(dash_app, port, object_name="DashServerThreadArea")


class Dashboard(DashVizWindow):
    server_thread_class = AreaServer

    def _dashboard_builder(self):
        return lambda: (object(), Dash())

    def _create_web_view(self):
        return View(PROFILE)


def pump_until(predicate, timeout=10.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


app = QApplication([])
PROFILE = Profile()
csv = os.path.join(sys.argv[2], "root_areas.csv")
with open(csv, "w", encoding="utf-8") as fh:
    fh.write("Image,Area\\n")
host = QWidget()
dashboard = Dashboard(csv, host)
assert pump_until(lambda: dashboard.web_view.loads), "the page never loaded"
server = dashboard.server_thread
assert server.isRunning() and server.parent() is None
if variant == "cleanup_raise":
    dashboard.web_view.page().broken = True
    try:
        dashboard.cleanup_server()
    except RuntimeError:
        pass
    finally:
        dashboard.deleteLater()
    print("server stopped by the cleanup:", server.wait(3000))
elif variant == "delete":
    dashboard.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
del dashboard
QTimer.singleShot(200, app.quit)
app.exec()  # aboutToQuit: the kept threads are stopped
if variant == "window_exit":
    host.deleteLater()  # main._release_window deletes the window after the event loop
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
from PyQt6 import sip
print("server running at the end:", not sip.isdeleted(server) and server.isRunning())
"""


@pytest.mark.parametrize("variant", ["delete", "cleanup_raise", "window_exit"])
def test_a_dashboard_deleted_while_its_server_runs_never_aborts(tmp_path, variant):
    script = tmp_path / "dashboard_abort.py"
    script.write_text(_DASHBOARD, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, str(script), variant, str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (result.returncode, result.stderr[-2000:])
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "server running at the end: False"
    if variant == "cleanup_raise":  # cleanup_server stops the threads even when it raises
        assert "server stopped by the cleanup: True" in lines


# ------------------------------------------------------------------ the rules, on source
def _classes() -> dict[str, tuple[str, list[str], ast.ClassDef]]:
    """Every class of app/: name -> (file, base names, node)."""
    found = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [
                    b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases
                ]
                found[node.name] = (str(path.relative_to(ROOT)).replace("\\", "/"), bases, node)
    return found


def _chain(name: str, classes: dict) -> list[str]:
    """``name`` and its bases inside app/, up to the first one defined outside it."""
    chain, todo = [], [name]
    while todo:
        current = todo.pop()
        if current in chain:
            continue
        chain.append(current)
        if current in classes:
            todo.extend(classes[current][1])
    return chain


def test_every_qthread_subclass_is_a_guarded_thread_without_its_own_run():
    classes = _classes()
    threads = {n for n in classes if n != "GuardedThread" and "QThread" in _chain(n, classes)}
    assert {"ImageLoaderThread", "_DashServerThreadBase", "DashServerThreadArea"} <= threads
    unguarded = sorted(
        f"{classes[n][0]}:{n}" for n in threads if "GuardedThread" not in _chain(n, classes)
    )
    assert unguarded == []  # GuardedThread is the QThread base of every thread of app/
    own_run = sorted(
        f"{classes[n][0]}:{n}"
        for n in threads
        if any(isinstance(s, ast.FunctionDef) and s.name == "run" for s in classes[n][2].body)
    )
    assert own_run == []  # GuardedThread.run is the error boundary


def _init_params(name: str, classes: dict) -> list[str] | None:
    """The ``__init__`` parameters (without self) of ``name`` or of its first base in app/
    that defines one; None when only Qt's constructor applies (``QThread(parent)``)."""
    for current in _chain(name, classes):
        node = classes.get(current, (None, None, None))[2]
        for stmt in node.body if node is not None else ():
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "__init__":
                return [a.arg for a in stmt.args.args[1:]]
    return None


def test_no_qthread_is_given_a_parent():
    classes = _classes()
    threads = {n for n in classes if "QThread" in _chain(n, classes)} | {"QThread"}
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in threads:  # a constructor call: no parent argument
                params = _init_params(name, classes) if name != "QThread" else None
                index = params.index("parent") if params and "parent" in params else None
                if params is None:
                    index = 0  # QThread(parent)
                given = len(node.args) > index if index is not None else False
                if given or any(k.arg == "parent" for k in node.keywords):
                    offenders.append(f"{rel}:{node.lineno} {name}(..., parent)")
            elif name == "setParent" and isinstance(func, ast.Attribute):
                target = ast.unparse(func.value).lower()
                unparents = len(node.args) == 1 and ast.unparse(node.args[0]) == "None"
                if ("thread" in target or "worker" in target) and not unparents:
                    offenders.append(f"{rel}:{node.lineno} {ast.unparse(func)}(...)")
    assert offenders == []


def test_the_rules_see_the_patterns_they_forbid():
    """The checks above find a parented thread (the two dashboard sites before the fix)."""
    source = textwrap.dedent(
        """
        worker = DashInitWorker(self._dashboard_builder(), self)
        thread = self.server_thread_class(dash_app, self.port)
        thread.setParent(self)
        thread.setParent(None)
        """
    )
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)]
    set_parents = [
        ast.unparse(c.args[0]) for c in calls if getattr(c.func, "attr", "") == "setParent"
    ]
    assert set_parents == ["self", "None"]
    params = _init_params("DashInitWorker", _classes())
    assert params == ["build", "parent"]  # so DashInitWorker(build, self) is caught


class _Shadowing(GuardedThread):
    """Shadows ``finished`` with a result signal, as the app's workers do."""

    finished = pyqtSignal(str)

    def work(self) -> None:
        self.finished.emit("result")


def test_base_finished_is_qts_own_signal(app):
    thread = _Shadowing()
    got = []
    base_finished(thread).connect(lambda: got.append("qt"))
    thread.finished.connect(got.append)
    thread.start()
    assert thread.wait(5000)
    assert wait_until(lambda: got == ["result", "qt"] and _ended(thread))

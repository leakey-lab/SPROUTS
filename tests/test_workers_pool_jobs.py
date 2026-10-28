"""Pool jobs go through ``app.workers.start_pool_job``, and torch refuses to run in them.

PyQt gives every job on a reused ``QThreadPool`` thread a fresh Python thread state; once
torch's C++ code had run on a pool thread, the next job there that entered it waited
forever for the interpreter lock (dc11edf, 4 of 4 runs, never on a fresh thread). The rule
lived in comments only. Now every pool job is started by ``start_pool_job``, which marks it
(``in_pool_job``), and ``refuse_pool_thread`` turns a torch warm-up started there into an
error instead of a hang. The app's pool jobs never import torch at all.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from app.gui import warmup
from app.gui.warmup import WarmupQueue
from app.workers import in_pool_job, refuse_pool_thread, start_pool_job
from tests.qt_teardown import delete_widgets
from tests.qt_wait import wait_until

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pool(app):
    own = QThreadPool()
    own.setMaxThreadCount(1)  # one thread, reused by every job: the deadlock's setting
    yield own
    assert own.waitForDone(10_000)
    QApplication.processEvents()


def test_a_pool_job_is_marked_for_its_whole_run_and_only_there(pool):
    seen = []

    def job():
        seen.append(in_pool_job())
        try:
            refuse_pool_thread("torch")
        except RuntimeError as exc:
            seen.append(str(exc))

    start_pool_job(job, pool)
    start_pool_job(job, pool)  # the same pool thread again
    assert pool.waitForDone(5000)
    assert seen[0::2] == [True, True]
    assert all("must not run on a QThreadPool thread" in text for text in seen[1::2])

    fresh = []
    thread = threading.Thread(target=lambda: fresh.append(in_pool_job()))
    thread.start()
    thread.join(5)
    assert fresh == [False] and in_pool_job() is False  # a thread of its own; the GUI thread
    refuse_pool_thread("torch")  # nothing to refuse here


def test_a_failing_pool_job_does_not_leave_the_thread_marked(pool):
    outcome = []

    def failing():
        try:
            raise ValueError("job error")  # a job's own boundary catches it
        finally:
            outcome.append(in_pool_job())

    start_pool_job(lambda: _swallow(failing), pool)
    assert pool.waitForDone(5000)
    start_pool_job(lambda: outcome.append(in_pool_job()), pool)
    assert pool.waitForDone(5000)
    assert outcome == [True, True]
    assert in_pool_job() is False


def _swallow(job) -> None:
    try:
        job()
    except ValueError:
        pass


def test_a_torch_warmup_started_in_the_pool_fails_at_once(app, pool, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # any import of torch fails loudly
    queue = WarmupQueue(  # a misconfigured window: the torch warm-up in the pool
        None, jobs={warmup.TORCH: warmup.warm_inference}, pooled=(warmup.TORCH,), pool=pool
    )
    finished = []
    queue.finished.connect(lambda key, error: finished.append((key, error)))
    made = [queue]
    try:
        queue.request(warmup.TORCH)
        assert wait_until(lambda: finished)
        key, error = finished[0]
        assert key == warmup.TORCH
        assert "must not run on a QThreadPool thread" in error
    finally:
        delete_widgets(made, close=False)


def _pool_starts(path: Path) -> list[int]:
    """Lines of ``path`` that start a job on a ``QThreadPool`` themselves."""
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        func = node.func if isinstance(node, ast.Call) else None
        if isinstance(func, ast.Attribute) and func.attr in ("start", "tryStart"):
            receiver = ast.unparse(func.value)
            if "QThreadPool" in receiver or "pool" in receiver.lower():
                lines.append(node.lineno)
    return lines


#: Pool starts not on start_pool_job yet (their modules are outside this lane); the list only
#: shrinks.
KNOWN_DIRECT_STARTS = {"app/gui/stage_status.py", "app/gui/window/metrics.py"}


def test_every_pool_job_is_started_by_start_pool_job():
    home = ROOT / "app" / "workers.py"
    direct = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in sorted((ROOT / "app").rglob("*.py"))
        if path != home and _pool_starts(path)
    }
    assert direct == KNOWN_DIRECT_STARTS
    assert _pool_starts(home)  # the helper itself (the check finds a start)


def test_the_apps_pool_jobs_never_import_torch():
    """The device check and the scikit-image warm-up, as pool jobs in a fresh process."""
    code = textwrap.dedent(
        """
        import sys
        from PyQt6.QtCore import QCoreApplication, QThreadPool
        app = QCoreApplication(sys.argv)
        from app.gui import device_check, skeleton_graph_model
        from app.workers import start_pool_job

        results = []
        start_pool_job(lambda: results.append(device_check.resolve_device_logged()))
        start_pool_job(lambda: results.append(skeleton_graph_model.prewarm_skeletonize()))
        assert QThreadPool.globalInstance().waitForDone(120_000)
        print(len(results), "torch" in sys.modules)
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "2 False"

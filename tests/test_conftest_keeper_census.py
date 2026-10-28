"""The suite's keeper census (``tests/conftest.py``): a test that leaves a kept thread running
gets a warning naming it, once, and never a failure.

Run in a child pytest with the conftest loaded as a plugin, over a small test file of three
tests: the first starts a thread and leaves it running, the second runs while it still
runs, the third lets it end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD_TESTS = textwrap.dedent(
    """
    import threading

    from PyQt6.QtCore import QCoreApplication

    from app.workers import GuardedThread, kept_threads

    release = threading.Event()


    class Held(GuardedThread):
        def work(self):
            release.wait(30)


    def test_leaves_a_job_running(qapp):
        thread = Held()
        thread.setObjectName("held-job")
        thread.start()


    def test_runs_while_it_still_runs(qapp):
        assert any(t.objectName() == "held-job" and t.isRunning() for t in kept_threads())


    def test_lets_it_end(qapp):
        release.set()
        for thread in kept_threads():
            assert thread.wait(10_000)
        QCoreApplication.processEvents()
    """
)


def test_a_kept_thread_left_running_is_named_once_and_fails_nothing(tmp_path):
    (tmp_path / "test_child.py").write_text(CHILD_TESTS, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider"]
        + ["-o", "addopts=", "--rootdir", str(tmp_path), str(tmp_path / "test_child.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = result.stdout
    assert result.returncode == 0, out[-3000:] + result.stderr[-2000:]
    assert "3 passed" in out, out[-3000:]
    assert out.count("KeptThreadRunning: still running after") == 1, out[-3000:]
    assert "test_child.py::test_leaves_a_job_running: held-job" in out, out[-3000:]

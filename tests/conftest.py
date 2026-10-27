"""Suite-wide set-up: offscreen Qt, one QtWebEngine rule, and a clean slate after every test.

- Qt runs offscreen unless ``QT_QPA_PLATFORM`` says otherwise.
- torch's idle OpenMP workers sleep at once (``KMP_BLOCKTIME=0``, as ``main.py`` sets it):
  the torch tests ran far slower on a loaded machine without it.
- The user's config folder is never read or written: ``SPROUTS_CONFIG_DIR`` names a temp
  folder of the session (removed at its end; module fixtures and subprocesses use it too),
  and every test has its own folder in it (made when the app first writes there), so
  Enhance defaults a test saves never reach a later test. QSettings are not redirected: a
  store given no settings object uses the user's (``QSettings("LeakeyLab", "SPROUTS")``).
- One QApplication, as in a full run: ``AA_ShareOpenGLContexts`` is set before it exists,
  the canvases default to raster viewports (``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=0``), and
  the session fixtures ``app`` and ``qapp`` make it on first use and keep it until exit.
- ``QtWebEngineWidgets`` is imported before any test can create the QApplication: Qt loads it
  only while no QCoreApplication exists, and ``app.gui.visualization_manager`` imports it.
  Without this, ``tests/test_import_smoke.py`` failed whenever an earlier file created the
  application first (``pytest tests/test_correct_controls.py tests/test_import_smoke.py``).
- After every test (once a QApplication exists) the posted deletions and the queued calls are
  delivered and the garbage is collected (``qt_teardown.flush_deleted``). A fixture cannot
  free the widget it yielded itself (pytest keeps the value until after the teardown), and a
  dead wrapper left for the next test can make sip hand out a wrapper of the wrong type
  there (see ``qt_teardown``).
- ``gc.freeze()`` after the collection and after each test file: the objects alive then
  (the test modules and everything they imported, torch, dash and pandas among them, about
  530,000 after the collection) are taken out of the collector's sight, so a full
  collection looks at the newer objects only (1-10 ms instead of 200-250 ms), which makes a
  collection after every test affordable. A frozen object is never collected; at the end of
  a file its fixtures and widgets are gone (the flush runs first), and what stays is
  process-lifetime state: modules, caches, the QApplication and its children.
- Also after a slow flush (a test imported a package while a QApplication exists: the next
  collections would each look at it), but then only if no Qt object that can have a parent
  (``sip.wrapper``: widgets and other QObjects, graphics and tree items) is in sight but the
  application: a frozen one, deleted later, would never be freed.
- Shared fixtures a test or module asks for (none is autouse): the guards (``no_prompts``,
  ``no_file_dialogs``, ``no_temp_sweep``, ``gl_off``, ``slot_errors``, ``window_guards``;
  ``tests/guards.py``), the Correct page's (``correct_guards``, ``make_editor``,
  ``project``, ``answers``; ``tests/correct_support.py``) and ``cpu_runtime``. They import
  Qt widgets, app modules and torch when used, so Qt-free runs do not pay for them.
"""

from __future__ import annotations

import gc
import itertools
import os
import shutil
import tempfile
import time

import pytest

# Rich assert messages in the shared helpers too (registered before any import of them).
pytest.register_assert_rewrite(
    "tests.correct_support", "tests.generation_support", "tests.guards", "tests.window_support"
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KMP_BLOCKTIME", "0")  # as main.py (P3-01): idle OpenMP workers sleep
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
# The suite logs into its own folder, never into the user's application log (X5).
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="sprouts-test-logs-")
os.environ.setdefault("SPROUTS_LOG_DIR", _TEST_LOG_DIR)
# Nor the user's config folder: set, not defaulted, so a portable install's stays untouched.
_TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="sprouts-test-config-")
os.environ["SPROUTS_CONFIG_DIR"] = _TEST_CONFIG_DIR

try:  # before any QApplication exists (see the module docstring)
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
except ImportError:  # no QtWebEngine: the dashboard tests report it themselves
    pass

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QCoreApplication, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tests.qt_teardown import flush_deleted  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

#: A flush slower than this collected a big unfrozen heap.
SLOW_FLUSH_S = 0.02

_slow_freeze_allowed = [True]  # False once refused in a file, until the file ends
_test_numbers = itertools.count()
_SESSION_APP: list = []  # the QApplication of the app / qapp fixtures, kept until exit


def _no_qt_object_in_sight() -> bool:
    """True when no ``sip.wrapper`` but the application is among the unfrozen objects."""
    application = QCoreApplication.instance()
    return not any(
        issubclass(type(obj), sip.wrapper) and obj is not application for obj in gc.get_objects()
    )


def pytest_sessionfinish(session, exitstatus):
    """Remove the session's log and config folders."""
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)
    shutil.rmtree(_TEST_CONFIG_DIR, ignore_errors=True)


def pytest_collection_finish(session):
    """Freeze what the collection imported (see the module docstring)."""
    gc.collect()
    gc.freeze()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Flush after the test, once pytest has dropped its fixture values; freeze what is left
    after a file's last test, or after a slow flush when that is safe."""
    result = yield
    last_of_file = nextitem is None or nextitem.path != item.path
    started = time.perf_counter()
    if QCoreApplication.instance() is not None:
        flush_deleted()
    elif last_of_file:
        gc.collect()
    if last_of_file:
        gc.freeze()
        _slow_freeze_allowed[0] = True
    elif time.perf_counter() - started > SLOW_FLUSH_S and _slow_freeze_allowed[0]:
        # A widget that lives on (a module fixture's) keeps the heap unfrozen until the
        # file ends: one look per file.
        _slow_freeze_allowed[0] = _no_qt_object_in_sight()
        if _slow_freeze_allowed[0]:
            gc.freeze()
    return result


@pytest.fixture(autouse=True)
def _own_config_dir():
    """Every test has its own config folder (see the module docstring). Its own patch, not
    the ``monkeypatch`` fixture: a test's fixtures keep their set-up and teardown order."""
    with pytest.MonkeyPatch.context() as patch:
        folder = os.path.join(_TEST_CONFIG_DIR, "t", str(next(_test_numbers)))
        patch.setenv("SPROUTS_CONFIG_DIR", folder)
        yield


# ================================================================== the one QApplication
def _session_app() -> QApplication:
    if not _SESSION_APP:
        _SESSION_APP.append(QApplication.instance() or QApplication([]))
    return _SESSION_APP[0]


@pytest.fixture(scope="session")
def app() -> QApplication:
    """The one QApplication, made on first use and kept until exit."""
    return _session_app()


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """The same QApplication as ``app``. It does not ask for ``app``: a module that has its
    own ``app`` fixture of a narrower scope would make that a scope error."""
    return _session_app()


# ========================================================================== the guards
@pytest.fixture
def no_prompts(monkeypatch):
    """Every message box fails the test (``guards.refuse_prompts``)."""
    from tests import guards

    guards.refuse_prompts(monkeypatch)


@pytest.fixture
def no_file_dialogs(monkeypatch):
    """Every file dialog fails the test (``guards.refuse_file_dialogs``)."""
    from tests import guards

    guards.refuse_file_dialogs(monkeypatch)


@pytest.fixture
def no_temp_sweep(monkeypatch):
    """A loaded project's delayed stale-temp sweep does nothing (``guards.skip_temp_sweep``)."""
    from tests import guards

    guards.skip_temp_sweep(monkeypatch)


@pytest.fixture
def gl_off(monkeypatch):
    """The canvases draw without OpenGL even when the environment asks for it."""
    from tests import guards

    guards.gl_off(monkeypatch)


@pytest.fixture
def slot_errors(request, monkeypatch):
    """An exception raised in a Qt slot fails the test (``guards.record_slot_errors``). A
    module's ``SLOT_ERRORS_FROM`` (paths or file names) limits that to the exceptions whose
    traceback passes through those files. The value is the list of recorded exceptions."""
    from tests import guards

    sources = getattr(request.module, "SLOT_ERRORS_FROM", None)
    yield from guards.record_slot_errors(monkeypatch, sources)


@pytest.fixture
def window_guards(no_prompts, no_file_dialogs, no_temp_sweep, gl_off):
    """A MainWindow test's guards: no prompt, no file dialog, no delayed temp sweep, no
    OpenGL."""


# ===================================================================== the Correct page
@pytest.fixture
def correct_guards(monkeypatch, tmp_path):
    """``correct_support.install_guards``: no prompt, no file dialog, no OpenGL, and an
    exception of the editor in a Qt slot fails the test."""
    from tests.correct_support import install_guards

    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    """``correct_support.editor_factory``: ``make(image=None, base=None, *, size, show)``."""
    from tests.correct_support import editor_factory

    yield from editor_factory()


@pytest.fixture
def project(tmp_path):
    """``correct_support.new_project``: photos a (mask and skeleton) and b (neither)."""
    from tests.correct_support import new_project

    return new_project(tmp_path)


@pytest.fixture
def answers(monkeypatch):
    """``correct_support.answer_prompts``: the boxes shown, answered from a queue."""
    from tests.correct_support import answer_prompts

    return answer_prompts(monkeypatch)


# ======================================================================== the CPU runtime
@pytest.fixture
def cpu_runtime(monkeypatch):
    """The inference runtime with CUDA unavailable; the runtime cache is cleared before and
    after the test."""
    import torch

    from app.inference import runtime

    runtime.get_runtime.cache_clear()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    yield runtime.get_runtime()
    runtime.get_runtime.cache_clear()

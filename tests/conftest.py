"""Suite-wide set-up: offscreen Qt, one QtWebEngine rule, and a clean slate after every test.

- Qt runs offscreen unless ``QT_QPA_PLATFORM`` says otherwise.
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
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The suite logs into its own folder, never into the user's application log (X5).
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="sprouts-test-logs-")
os.environ.setdefault("SPROUTS_LOG_DIR", _TEST_LOG_DIR)

try:  # before any QApplication exists (see the module docstring)
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
except ImportError:  # no QtWebEngine: the dashboard tests report it themselves
    pass

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QCoreApplication  # noqa: E402

from tests.qt_teardown import flush_deleted  # noqa: E402

#: A flush slower than this collected a big unfrozen heap.
SLOW_FLUSH_S = 0.02

_slow_freeze_allowed = [True]  # False once refused in a file, until the file ends


def _no_qt_object_in_sight() -> bool:
    """True when no ``sip.wrapper`` but the application is among the unfrozen objects."""
    application = QCoreApplication.instance()
    return not any(
        issubclass(type(obj), sip.wrapper) and obj is not application for obj in gc.get_objects()
    )


def pytest_sessionfinish(session, exitstatus):
    """Remove the session's log folder."""
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


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

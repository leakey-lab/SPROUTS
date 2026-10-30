"""Waiting in the GUI tests: run the event loop until a condition holds, or let the posted
events run.

``wait_until`` is the ``wait`` / ``_wait`` that many test modules defined for themselves and
``settle`` their ``settle``. A module imports them under its old names
(``from tests.qt_wait import wait_until as wait``), so its call sites stay as they are.
Importing this module creates no QApplication.
"""

from __future__ import annotations

import time

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


def wait_until(predicate, timeout: float = 5.0) -> bool:
    """Run the event loop until ``predicate()`` holds (or ``timeout`` seconds pass)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def settle() -> None:
    """Let posted events and zero timers run."""
    for _ in range(3):
        QApplication.processEvents()

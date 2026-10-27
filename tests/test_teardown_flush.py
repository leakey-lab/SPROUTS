"""The suite's Qt teardown (``tests/qt_teardown.py``, ``tests/conftest.py``).

A widget whose lambda captures it (``lambda: self.save()``, as the editors' shortcuts do)
outlived ``deleteLater()`` and one ``sendPostedEvents(None, DeferredDelete)`` as a dead
Python wrapper: PyQt frees the lambda's signal proxy through a queued deletion of its own.
A dead wrapper keeps the wrappers of its C++-made children (scroll bars, viewports) in sip's
address map, and sip handed one of those out for a new object at the same address: an
access violation in a later test. The helpers free such a widget at once, and conftest.py
flushes again after every test, once pytest has dropped the fixture values.
"""

from __future__ import annotations

import importlib
import weakref

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QPushButton, QScrollArea, QWidget

from tests import conftest
from tests.qt_teardown import delete_widgets, flush_deleted


class _Page(QWidget):
    """A page with a lambda that captures it and C++-made children (a scroll area's bars)."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0
        self.saved = 0
        self.button = QPushButton("Save", self)
        self.button.clicked.connect(lambda: self.save())
        self.area = QScrollArea(self)
        self.area.verticalScrollBar()  # a wrapper for a child Qt made

    def save(self) -> None:
        self.saved += 1

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.closed += 1
        super().closeEvent(event)


def test_delete_widgets_frees_a_widget_its_lambda_captures(app):
    made = [_Page(), _Page()]
    refs = [weakref.ref(page) for page in made]

    delete_widgets(made)

    assert made == []  # emptied: the list holds no wrapper through the collection
    assert [ref() for ref in refs] == [None, None]


def test_delete_widgets_can_skip_the_close_and_skips_deleted_widgets(app):
    gone, kept_open = _Page(), _Page()
    sip.delete(gone)
    prepared, closes = [], []
    kept_open.destroyed.connect(lambda: closes.append(kept_open.closed))

    delete_widgets([gone, kept_open], close=False, prepare=prepared.append)

    assert prepared == [kept_open]  # the deleted one is skipped, not an error
    assert closes == [0]  # deleted without a close event (an editor's close can prompt)
    assert sip.isdeleted(kept_open)


def test_flush_deleted_frees_what_a_test_deleted_itself(app):
    page = _Page()
    ref = weakref.ref(page)
    page.deleteLater()
    del page

    flush_deleted()

    assert ref() is None


_LEFT_BY_THE_FIRST_TEST: list[weakref.ref] = []


@pytest.fixture
def page(app):
    """A page whose teardown only calls deleteLater(), as many fixtures did: pytest still
    holds the value then, so only the flush after the test can free it."""
    made = _Page()
    yield made
    made.deleteLater()


def test_a_fixture_value_is_freed_after_its_test_1_of_2(page):
    _LEFT_BY_THE_FIRST_TEST.append(weakref.ref(page))
    page.button.click()
    assert page.saved == 1


def test_a_fixture_value_is_freed_after_its_test_2_of_2(app):
    if not _LEFT_BY_THE_FIRST_TEST:
        pytest.skip("runs after test_a_fixture_value_is_freed_after_its_test_1_of_2")
    assert _LEFT_BY_THE_FIRST_TEST[0]() is None  # conftest.py flushed after that test


def test_webengine_widgets_load_after_the_application_exists(app):
    # conftest.py imported it before any test made the application (Qt refuses it after).
    assert importlib.import_module("PyQt6.QtWebEngineWidgets")


def test_the_heap_is_not_frozen_while_a_widget_lives(app):
    widget = QWidget()
    try:
        assert not conftest._no_qt_object_in_sight()
    finally:
        delete_widgets([widget])

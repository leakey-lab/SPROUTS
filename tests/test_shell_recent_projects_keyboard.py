"""Recent projects: removing one keeps the keyboard's place; the list stays bounded (R-SHELL-B).

Removing a recent project with the keyboard (Tab to its ×, Space) rebuilt the rows and left
no widget focused: the next Tab started over at Browse, so clearing a few stale entries
meant tabbing back each time. Focus now moves to the × of the row that took the removed
row's place (the last row's when the removed one was last, Browse when none is left).

The store capped the list only when writing: a stored list of 500 entries (hand-edited, or
written by another build) built 500 rows at start-up (~0.5 s) and probed 500 folders, and a
path stored twice showed twice. Reading now keeps the newest ``max_entries`` distinct paths.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.recent_projects import RecentProjectsStore  # noqa: E402
from app.gui.welcome_screen import WelcomeWidget  # noqa: E402


class _Settings:
    def __init__(self, entries=()):
        self.data = {"recent_projects": json.dumps(list(entries))}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.data[key] = value


def _entries(n: int, prefix: str = "p") -> list[dict]:
    return [
        {"name": f"{prefix}{i}", "path": f"D:/x/{prefix}{i}", "count": 5, "ts": "2026-09-20T10:00"}
        for i in range(n)
    ]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def welcome(app):
    made = []

    def make(entries):
        widget = WelcomeWidget(
            lambda: None,
            lambda path: None,
            recent_store=RecentProjectsStore(_Settings(entries)),
            probe_async=False,
            on_about=lambda: None,
        )
        made.append(widget)
        widget.resize(1280, 800)
        widget.show()
        widget.activateWindow()
        QApplication.processEvents()
        return widget

    yield make
    for widget in made:
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _remove_with_keyboard(widget: WelcomeWidget, index: int) -> None:
    button = widget.recent_rows()[index].remove_button
    button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert QApplication.focusWidget() is button
    QTest.keyClick(button, Qt.Key.Key_Space)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_focus_moves_to_the_row_that_took_the_removed_rows_place(welcome):
    widget = welcome(_entries(3))
    _remove_with_keyboard(widget, 1)
    rows = widget.recent_rows()
    assert [r.entry.name for r in rows] == ["p0", "p2"]
    assert QApplication.focusWidget() is rows[1].remove_button


def test_removing_the_last_row_focuses_the_new_last_row(welcome):
    widget = welcome(_entries(3))
    _remove_with_keyboard(widget, 2)
    rows = widget.recent_rows()
    assert QApplication.focusWidget() is rows[-1].remove_button


def test_removing_the_only_row_focuses_browse(welcome):
    widget = welcome(_entries(1))
    _remove_with_keyboard(widget, 0)
    assert widget.recent_rows() == []
    assert QApplication.focusWidget() is widget.browse_button


def test_a_mouse_removal_does_not_steal_focus(welcome):
    widget = welcome(_entries(3))
    widget.browse_button.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    widget.recent_rows()[1].remove_button.click()  # a click: the × never had focus
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert QApplication.focusWidget() is widget.browse_button


def test_reading_keeps_the_newest_max_entries():
    store = RecentProjectsStore(_Settings(_entries(500)))
    entries = store.entries()
    assert [e.name for e in entries] == [f"p{i}" for i in range(store.max_entries)]


def test_reading_shows_a_path_stored_twice_once():
    stored = _entries(3)
    stored.insert(2, dict(stored[0], path="D:/x/p0/", name="again"))  # same folder, other spelling
    names = [e.name for e in RecentProjectsStore(_Settings(stored)).entries()]
    assert names == ["p0", "p1", "p2"]


def test_a_huge_stored_list_builds_at_most_max_entries_rows(welcome):
    widget = welcome(_entries(500))
    assert len(widget.recent_rows()) == RecentProjectsStore(_Settings()).max_entries

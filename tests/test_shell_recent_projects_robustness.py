"""A malformed recent-projects list never stops SPROUTS from starting.

Bugs (found 2026-09-30): the Welcome screen is built with the window, and it raised on
two values the stored list can hold -

* a timestamp with a UTC offset (``"2026-09-30T10:00:00+00:00"``): subtracting it from
  the naive ``datetime.now()`` raised ``TypeError``;
* a count of ``NaN`` / ``Infinity`` (valid in Python's JSON): ``int()`` raised
  ``ValueError`` / ``OverflowError`` in ``RecentProjectsStore.entries``;

so ``MainWindow()`` failed and the app showed "could not start" until the settings were
edited by hand (the same class of input as R4's nested JSON). The rows also said
"1 images" and "46178 images"; they now count like the library ("1 image", "46,178").
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from PyQt6.QtCore import QSettings

from app.gui.recent_projects import SETTINGS_KEY, RecentProjectsStore
from app.gui.welcome_screen import WelcomeWidget
from tests.qt_teardown import delete_widgets
from tests.window_support import build_window


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt API name
        self[key] = value


def _store(raw: str) -> RecentProjectsStore:
    return RecentProjectsStore(Settings({SETTINGS_KEY: raw}))


def _welcome(store: RecentProjectsStore) -> WelcomeWidget:
    return WelcomeWidget(
        on_get_started=lambda: None,
        recent_store=store,
        probe_async=False,
        path_exists=lambda _path: True,
    )


def _info(widget: WelcomeWidget) -> list[str]:
    return [row._info_label.text() for row in widget.recent_rows()]


def test_a_timestamp_with_an_offset_shows_a_relative_date(app):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    raw = json.dumps([{"path": "C:/data", "name": "data", "count": 3, "ts": stamp}])
    widget = _welcome(_store(raw))
    assert _info(widget) == ["3 images  ·  3h ago"]
    widget.deleteLater()


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "junk"])
def test_extreme_or_unreadable_timestamps_are_left_out(app, ts):
    raw = json.dumps([{"path": "C:/data", "count": 2, "ts": ts}])
    widget = _welcome(_store(raw))
    assert len(widget.recent_rows()) == 1
    assert _info(widget)[0].startswith("2 images")
    widget.deleteLater()


@pytest.mark.parametrize("count", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_count_is_dropped_not_raised(app, count):
    raw = f'[{{"path": "C:/data", "count": {count}}}]'
    entries = _store(raw).entries()
    assert [(e.path, e.count) for e in entries] == [("C:/data", None)]
    widget = _welcome(_store(raw))
    assert _info(widget) == [""]
    widget.deleteLater()


def test_rows_count_images_like_the_library(app):
    raw = json.dumps(
        [
            {"path": "C:/one", "count": 1, "ts": ""},
            {"path": "C:/many", "count": 46178, "ts": ""},
            {"path": "C:/half", "count": 2.0, "ts": ""},
        ]
    )
    widget = _welcome(_store(raw))
    assert _info(widget) == ["1 image", "46,178 images", "2 images"]
    widget.deleteLater()


def test_the_window_starts_with_such_a_list(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue(
        SETTINGS_KEY,
        '[{"path": "C:/a", "count": NaN, "ts": "2026-09-30T10:00:00+00:00"},'
        ' {"path": "C:/b", "count": 5, "ts": "2026-09-30T10:00:00+02:00"}]',
    )
    window = build_window(settings=settings, size=None, show=False)
    try:
        assert len(window.welcome.recent_rows()) == 2
        assert not any(math.isnan(e.count) for e in window.recent_projects.entries() if e.count)
    finally:
        delete_widgets([window])

"""RecentProjectsStore: the one MRU implementation shared by MainWindow and Welcome."""

from __future__ import annotations

import json
import os
from datetime import datetime

from app.gui.recent_projects import RecentProject, RecentProjectsStore


class _FakeSettings:
    """QSettings stand-in (``value`` / ``setValue``)."""

    def __init__(self, raw=None):
        self.data = {} if raw is None else {"recent_projects": raw}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt API name
        self.data[key] = value


def test_empty_store_has_no_entries():
    assert RecentProjectsStore(_FakeSettings()).entries() == []


def test_add_writes_the_legacy_json_format(tmp_path):
    settings = _FakeSettings()
    store = RecentProjectsStore(settings)
    store.add(str(tmp_path), 12, now=datetime(2026, 9, 28, 10, 0, 0))

    raw = json.loads(settings.data["recent_projects"])
    assert raw == [
        {
            "name": os.path.basename(str(tmp_path)),
            "path": str(tmp_path),
            "count": 12,
            "ts": "2026-09-28T10:00:00",
        }
    ]


def test_add_dedupes_moves_to_front_and_caps_at_8(tmp_path):
    store = RecentProjectsStore(_FakeSettings())
    paths = [str(tmp_path / f"p{i}") for i in range(10)]
    for p in paths:
        store.add(p, 1)
    store.add(paths[3], 5)
    entries = store.entries()
    assert len(entries) == 8
    assert entries[0].path == paths[3] and entries[0].count == 5
    assert [e.path for e in entries].count(paths[3]) == 1


def test_reads_existing_settings_and_skips_garbage():
    raw = json.dumps([{"path": "D:/a", "name": "a", "count": 3, "ts": "x"}, {"nope": 1}, "str"])
    entries = RecentProjectsStore(_FakeSettings(raw)).entries()
    assert entries == [RecentProject(path="D:/a", name="a", count=3, ts="x")]
    assert RecentProjectsStore(_FakeSettings("{not json")).entries() == []


def test_remove_and_clear(tmp_path):
    store = RecentProjectsStore(_FakeSettings())
    store.add(str(tmp_path / "a"), 1)
    store.add(str(tmp_path / "b"), 1)
    store.remove(str(tmp_path / "a"))
    assert [e.name for e in store.entries()] == ["b"]
    store.clear()
    assert store.entries() == []


def test_exists_reports_missing_folders(tmp_path):
    assert RecentProject(str(tmp_path), "x").exists() is True
    assert RecentProject(str(tmp_path / "gone"), "x").exists() is False


def test_default_store_uses_the_app_qsettings_scope():
    from app import APP_NAME, ORGANIZATION_NAME

    settings = RecentProjectsStore().settings
    assert (settings.organizationName(), settings.applicationName()) == (
        ORGANIZATION_NAME,
        APP_NAME,
    )

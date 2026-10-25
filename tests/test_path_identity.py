"""One path identity for every "is this the same file" question (O21, WX-T4).

APFS (the macOS default) is case-insensitive and Unicode-normalising, ext4 is neither, NTFS
folds case only. The platform is an argument or the ``io_utils._current_platform`` accessor,
never the global ``sys.platform`` (patching that breaks lazy imports such as numpy's).
"""

from __future__ import annotations

import os

import pytest

from app import io_utils
from app.gui.recent_projects import RecentProjectsStore
from app.io_utils import path_key, same_file


class _Settings:
    def __init__(self):
        self.values: dict = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt API name
        self.values[key] = value


def test_darwin_keys_ignore_case_and_unicode_form():
    assert path_key("/Data/T1.png", "darwin") == path_key("/data/t1.png", "darwin")
    assert path_key("/x/Ae\u0301.png", "darwin") == path_key("/x/a\u00e9.png", "darwin")


def test_linux_keys_keep_case():
    assert path_key("/Data/T1.png", "linux") != path_key("/data/t1.png", "linux")


def test_linux_keys_keep_unicode_form():
    assert path_key("/x/e\u0301.png", "linux") != path_key("/x/\u00e9.png", "linux")


@pytest.mark.skipif(os.name != "nt", reason="abspath of a drive path is OS-specific")
def test_windows_keys_use_normcase():
    assert path_key("C:/A/B.png", "win32") == path_key("c:\\a\\b.png", "win32")


def test_a_path_like_and_a_relative_path_give_the_same_key(tmp_path, monkeypatch):
    assert path_key(tmp_path / "a.png", "linux") == path_key(str(tmp_path / "a.png"), "linux")
    monkeypatch.chdir(tmp_path)
    assert path_key("sub/../a.png", "linux") == path_key(tmp_path / "a.png", "linux")


def test_same_file_follows_the_current_platform(monkeypatch):
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "darwin")
    assert same_file("/Data/T1.png", "/data/t1.png")
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    assert not same_file("/Data/T1.png", "/data/t1.png")
    assert same_file("/Data/T1.png", "/Data/../Data/T1.png")


def test_the_default_platform_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "darwin")
    assert path_key("/A/B") == path_key("/a/b", "darwin")
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    assert path_key("/A/B") == path_key("/A/B", "linux")


def test_recents_dedupe_case_variants_on_darwin(monkeypatch):
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "darwin")
    store = RecentProjectsStore(_Settings())
    store.add("/Proj/A", 3)
    store.add("/proj/a", 4)
    entries = store.entries()
    assert [e.path for e in entries] == ["/proj/a"]
    assert entries[0].count == 4


def test_recents_keep_case_variants_apart_on_linux(monkeypatch):
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    store = RecentProjectsStore(_Settings())
    store.add("/Proj/A", 3)
    store.add("/proj/a", 4)
    assert [e.path for e in store.entries()] == ["/proj/a", "/Proj/A"]


def test_the_display_read_ahead_key_is_the_path_key(monkeypatch):
    from app.gui import display_read_ahead

    monkeypatch.setattr(io_utils, "_current_platform", lambda: "darwin")
    assert display_read_ahead._key("/Data/T1.png") == display_read_ahead._key("/data/t1.png")

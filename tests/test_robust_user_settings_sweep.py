"""R4: the per-user settings folder is swept for the temp files of killed writes.

The Enhance defaults are saved atomically to ``user_config_dir()/normalization_defaults.json``
(``app.io_utils``: a hidden ``.<name>.<8 hex>.tmp`` swapped in). A process killed between
the two left that temp file there for good: the project sweep (R8) only covers a
project's output folders. The defaults store now sweeps its own folder too, with the
same age rule (older than an hour), once per process, on a daemon thread: loading the
defaults (the editors do it while the window is built) never waits for it.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from app import enhancement
from app.enhancement import DEFAULTS_FILE_NAME, EnhanceDefaultsStore

HOUR = 3600.0


def _file(folder, name, *, age_s=0.0, text="{}"):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


@pytest.fixture(autouse=True)
def _fresh_sweep_state(monkeypatch):
    # Each test sees a process in which no settings folder was swept yet.
    monkeypatch.setattr(enhancement, "_swept_settings_dirs", set())


def _store(folder):
    factory = _file(
        folder.parent / "factory", DEFAULTS_FILE_NAME, text=json.dumps({"clahe_tile": 9})
    )
    return EnhanceDefaultsStore(user_path=str(folder / DEFAULTS_FILE_NAME), factory_path=factory)


def test_loading_the_defaults_sweeps_stale_temp_files_from_the_settings_folder(tmp_path):
    folder = tmp_path / "SPROUTS"
    stale = _file(folder, f".{DEFAULTS_FILE_NAME}.1a2b3c4d.tmp", age_s=2 * HOUR)
    fresh = _file(folder, f".{DEFAULTS_FILE_NAME}.0badf00d.tmp", age_s=60)  # a save in progress
    other = _file(folder, "notes.tmp", age_s=2 * HOUR)  # not an atomic-write temp name
    store = _store(folder)

    assert store.load().tile == 9  # seeded from the factory file, as before
    store.sweep_thread.join(5)

    assert not os.path.exists(stale)
    assert os.path.exists(fresh) and os.path.exists(other)
    assert os.path.exists(store.user_path)


def test_the_sweep_runs_on_a_daemon_thread_and_load_never_waits_for_it(tmp_path, monkeypatch):
    folder = tmp_path / "SPROUTS"
    stale = _file(folder, f".{DEFAULTS_FILE_NAME}.1a2b3c4d.tmp", age_s=2 * HOUR)
    release, calls = threading.Event(), []
    real_sweep = enhancement.sweep_stale_temp_files

    def held_sweep(swept_folder, **kwargs):
        calls.append((swept_folder, threading.current_thread()))
        release.wait(10)
        return real_sweep(swept_folder, **kwargs)

    monkeypatch.setattr(enhancement, "sweep_stale_temp_files", held_sweep)
    store = _store(folder)
    started = time.monotonic()
    try:
        store.load()
        returned_after = time.monotonic() - started
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert os.path.exists(stale)  # still held: load() did not wait
    finally:
        release.set()
    store.sweep_thread.join(5)

    assert returned_after < 2.0
    ((swept_folder, thread),) = calls
    assert os.path.normcase(swept_folder) == os.path.normcase(str(folder))
    assert thread is not threading.main_thread() and thread.daemon
    assert not os.path.exists(stale)


def test_the_settings_folder_is_swept_once_per_process(tmp_path, monkeypatch):
    folder = tmp_path / "SPROUTS"
    calls = []
    monkeypatch.setattr(
        enhancement, "sweep_stale_temp_files", lambda f, **kw: calls.append(f) or []
    )

    first = _store(folder)
    first.load()
    first.sweep_thread.join(5)
    second = _store(folder)  # e.g. the Trace GT editor's controls after Correct's
    second.load()
    first.load()

    assert len(calls) == 1
    assert second.sweep_thread is None


def test_a_missing_settings_folder_is_not_an_error(tmp_path):
    store = EnhanceDefaultsStore(
        user_path=str(tmp_path / "missing" / DEFAULTS_FILE_NAME),
        factory_path=str(tmp_path / "no_factory.json"),
    )
    assert store.load() == enhancement.DEFAULT_SETTINGS
    store.sweep_thread.join(5)
    assert not (tmp_path / "missing").exists()  # sweeping creates nothing

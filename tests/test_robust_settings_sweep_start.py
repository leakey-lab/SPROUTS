"""R4 follow-up: loading the Enhance defaults never raises, even when no thread can start.

``EnhanceDefaultsStore.load()`` promises never to raise (the editors call it while the
window is built). Its once-per-process settings-folder sweep (R4) starts a daemon
thread first; ``Thread.start()`` raises ``RuntimeError("can't start new thread")`` when
the process is out of threads or memory, which escaped ``load()``. The sweep is
housekeeping: without a thread it is skipped (logged), and a later load tries again.
"""

from __future__ import annotations

import logging
import threading

import pytest

from app import enhancement
from app.enhancement import DEFAULTS_FILE_NAME, EnhanceDefaultsStore


@pytest.fixture(autouse=True)
def _fresh_sweep_state(monkeypatch):
    monkeypatch.setattr(enhancement, "_swept_settings_dirs", set())


def _store(tmp_path):
    return EnhanceDefaultsStore(
        user_path=str(tmp_path / "SPROUTS" / DEFAULTS_FILE_NAME),
        factory_path=str(tmp_path / "no_factory.json"),
    )


def test_load_never_raises_when_the_sweep_thread_cannot_start(tmp_path, monkeypatch, caplog):
    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    store = _store(tmp_path)

    with caplog.at_level(logging.WARNING):
        settings = store.load()

    assert settings == enhancement.DEFAULT_SETTINGS
    assert store.sweep_thread is None
    assert "can't start new thread" in caplog.text


def test_a_later_load_sweeps_once_a_thread_can_start_again(tmp_path, monkeypatch):
    real_start = threading.Thread.start

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    _store(tmp_path).load()
    monkeypatch.setattr(threading.Thread, "start", real_start)

    store = _store(tmp_path)
    store.load()

    assert store.sweep_thread is not None  # the folder was not marked as swept
    store.sweep_thread.join(5)

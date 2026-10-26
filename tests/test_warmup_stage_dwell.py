"""Stage-entry warm-ups wait for the user to stay in the stage (V9, W5-D, :mod:`app.gui.warmup`).

Entering Generate used to start the torch import at once, so a user passing through on the
way to Correct got one 52-69 ms GUI stall while zooming there (the import holds the
interpreter lock; 13-21 ms without warm-ups). :class:`~app.gui.warmup.StageIntent` asks for a
stage's warm-ups (:data:`~app.gui.warmup.STAGE_WARMUPS`) only once the user has stayed in it
for :data:`~app.gui.warmup.STAGE_DWELL_MS` (about a second); leaving sooner asks for nothing.
Resting on a run button still asks after :data:`~app.gui.warmup.HOVER_DWELL_MS`
(tests/test_warmup_queue.py), and the queue's rules (one at a time, the gates) are unchanged.
The request is a stub here; the main window's own tests cover the wiring.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui import warmup  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

POLICY = {"Generate": ("torch", "models"), "Measure": ("dashboards",)}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test"])


@pytest.fixture
def made(app):
    """Intents to delete after the test."""
    objects: list = []
    yield objects
    QApplication.processEvents()
    delete_widgets(objects, close=False)


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


class Asked:
    """The request callable: which warm-ups were asked for, and when (perf_counter)."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(self, keys) -> None:
        self.calls.append((keys, time.perf_counter()))

    def keys(self) -> list[tuple[str, ...]]:
        return [keys for keys, _t in self.calls]


def make_intent(made, dwell_ms: int):
    asked = Asked()
    intent = warmup.StageIntent(None, asked, warmups=POLICY, dwell_ms=dwell_ms)
    made.append(intent)
    return intent, asked


# ------------------------------------------------------------------ the policy
def test_stage_entries_wait_about_a_second_and_run_buttons_keep_200_ms():
    assert warmup.STAGE_DWELL_MS == 1000
    assert warmup.HOVER_DWELL_MS == 200
    assert warmup.STAGE_WARMUPS == {
        "Generate": (warmup.TORCH, warmup.MODELS),
        "Measure": (warmup.DASHBOARDS,),
    }


# ------------------------------------------------------------------ the dwell
def test_staying_in_a_stage_asks_for_its_warmups_once_the_dwell_is_over(made):
    intent, asked = make_intent(made, dwell_ms=300)
    start = time.perf_counter()
    intent.stage_changed("Generate")
    assert intent.waiting() == "Generate"
    QTest.qWait(40)
    assert asked.calls == []  # entering is not enough
    assert wait(lambda: asked.calls)
    assert asked.keys() == [("torch", "models")]
    assert asked.calls[0][1] - start >= 0.28  # a coarse timer fires within 5 % of 300 ms
    assert intent.waiting() is None
    QTest.qWait(400)
    assert len(asked.calls) == 1  # once: the dwell does not repeat


def test_leaving_the_stage_before_the_dwell_asks_for_nothing(made):
    intent, asked = make_intent(made, dwell_ms=400)
    intent.stage_changed("Generate")
    QTest.qWait(30)
    intent.stage_changed("Correct")  # passing through Generate on the way to Correct
    assert intent.waiting() is None
    QTest.qWait(600)
    assert asked.calls == []


def test_another_stage_with_warmups_starts_its_own_dwell(made):
    intent, asked = make_intent(made, dwell_ms=400)
    intent.stage_changed("Generate")
    QTest.qWait(30)
    start = time.perf_counter()
    intent.stage_changed("Measure")
    assert intent.waiting() == "Measure"
    assert wait(lambda: asked.calls)
    QTest.qWait(500)
    assert asked.keys() == [("dashboards",)]  # never Generate's
    assert asked.calls[0][1] - start >= 0.38


def test_coming_back_waits_a_whole_dwell_again(made):
    intent, asked = make_intent(made, dwell_ms=200)
    intent.stage_changed("Generate")
    QTest.qWait(120)
    intent.stage_changed("Library")
    intent.stage_changed("Generate")
    start = time.perf_counter()
    assert wait(lambda: asked.calls)
    assert asked.calls[0][1] - start >= 0.18  # the time before the detour does not count
    assert asked.keys() == [("torch", "models")]


def test_the_same_stage_reported_again_keeps_its_dwell(made):
    intent, asked = make_intent(made, dwell_ms=300)
    intent.stage_changed("Generate")
    QTest.qWait(200)
    intent.stage_changed("Generate")  # no change of stage: its dwell runs on (due at 0.3 s)
    QTest.qWait(150)
    intent.stage_changed("Library")  # would drop a dwell restarted at 0.2 s (due at 0.5 s)
    QTest.qWait(400)
    assert asked.keys() == [("torch", "models")]


@pytest.mark.parametrize("stage", ["Library", "Correct", "Visualize", None])
def test_a_stage_without_warmups_starts_no_dwell(made, stage):
    intent, asked = make_intent(made, dwell_ms=50)
    intent.stage_changed(stage)
    assert intent.waiting() is None
    QTest.qWait(150)
    assert asked.calls == []


def test_the_app_policy_and_dwell_are_the_defaults(made, monkeypatch):
    """Both are read when the intent is built (the window tests shorten the dwell so)."""
    monkeypatch.setattr(warmup, "STAGE_DWELL_MS", 60)
    asked = Asked()
    intent = warmup.StageIntent(None, asked)
    made.append(intent)
    start = time.perf_counter()
    intent.stage_changed("Measure")
    assert wait(lambda: asked.calls, 2.0)
    assert asked.calls[0][1] - start < 0.8  # 60 ms, not the 1 s default
    intent.stage_changed("Generate")
    assert wait(lambda: len(asked.calls) == 2, 2.0)
    assert asked.keys() == [warmup.STAGE_WARMUPS["Measure"], warmup.STAGE_WARMUPS["Generate"]]

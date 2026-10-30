"""
Tests for the right-panel index enum (``app/gui/panels.py``) and the UI feedback
protocol with its Qt-free doubles (``app/gui/feedback.py``).

``Panel`` pins the right_panel page-index contract (0 display, 1 Correct,
2 viz placeholder, 3 GT tracing) and maps the ``switch_right_panel`` string keys;
the keys and the index of the two editors the Correct stage replaced are rejected
(a clean break, no aliases). The feedback doubles are what handler tests use
instead of reaching into ``main_window`` widgets.
"""

from __future__ import annotations

import pytest

from app.feedback import (
    FeedbackCall,
    NullFeedback,
    RecordingFeedback,
    UiFeedback,
    progress_percent,
)
from app.gui.panels import Panel

# --- Panel ---------------------------------------------------------------------------


def test_panel_values_pin_the_right_panel_contract():
    assert [int(p) for p in Panel] == [0, 1, 2]
    assert Panel.DISPLAY == 0
    assert Panel.CORRECT == 1
    assert Panel.VISUALIZATION == 2
    assert not hasattr(Panel, "GT_TRACING")  # Trace GT is a layer of Correct (WG-T11)


@pytest.mark.parametrize(
    ("key", "panel"),
    [
        ("display", Panel.DISPLAY),
        ("correct", Panel.CORRECT),
        ("visualization", Panel.VISUALIZATION),
    ],
)
def test_panel_key_round_trips_switch_keys(key, panel):
    assert panel.key == key
    assert Panel.coerce(key) is panel
    assert Panel.coerce(key.upper()) is panel


def test_panel_coerce_accepts_panels_and_indices():
    assert Panel.coerce(Panel.VISUALIZATION) is Panel.VISUALIZATION
    assert Panel.coerce(1) is Panel.CORRECT


@pytest.mark.parametrize(
    "bad",
    # the removed editors' keys and indices: the Correct stage replaced them (no aliases)
    ["mask_tracing", "skeleton_correction", "gt_tracing", 3, 4, "nope", "", 7, -1, None, 1.5],
)
def test_panel_coerce_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        Panel.coerce(bad)


# --- progress arithmetic -------------------------------------------------------------


@pytest.mark.parametrize(
    ("done", "total", "expected"),
    [
        (0, None, 0),
        (42, None, 42),
        (150, None, 100),
        (-5, None, 0),
        (1, 3, 33),
        (2, 3, 66),
        (3, 3, 100),
        (5, 3, 100),
        (0, 0, 100),
        (7, 1000, 0),
    ],
)
def test_progress_percent(done, total, expected):
    assert progress_percent(done, total) == expected


# --- doubles -------------------------------------------------------------------------


def test_doubles_satisfy_the_protocol():
    assert isinstance(NullFeedback(), UiFeedback)
    assert isinstance(RecordingFeedback(), UiFeedback)


def test_null_feedback_accepts_every_call():
    fb = NullFeedback()
    fb.task_started("Generating masks", total=10, cancelable=True, on_cancel=lambda: None)
    fb.task_progress(3)
    fb.task_progress(4, 10)
    fb.task_finished("done")
    fb.task_failed("boom")
    fb.status("hello", timeout_ms=10)
    fb.notify("saved", kind="info")


def test_recording_feedback_records_calls_in_order():
    fb = RecordingFeedback()
    fb.task_started("Generating masks", total=4)
    fb.task_progress(1)
    fb.task_finished("Masks ready")
    assert fb.names() == ["task_started", "task_progress", "task_finished"]
    assert fb.calls[0] == FeedbackCall(
        "task_started",
        ("Generating masks",),
        {"total": 4, "cancelable": False, "on_cancel": None},
    )


def test_recording_feedback_tracks_task_state():
    fb = RecordingFeedback()
    assert fb.busy is False
    fb.task_started("Measuring", total=4)
    assert (fb.busy, fb.label, fb.percent) == (True, "Measuring", 0)
    fb.task_progress(1)
    assert fb.percent == 25  # counts against the total given to task_started
    fb.task_progress(50, 100)  # an explicit total overrides it
    assert fb.percent == 50
    fb.task_failed("root_lengths.csv is open in another program")
    assert fb.busy is False
    assert fb.last_message == "root_lengths.csv is open in another program"


def test_recording_feedback_percent_mode_without_total():
    fb = RecordingFeedback()
    fb.task_started("Loading")
    fb.task_progress(37)
    assert fb.percent == 37
    fb.task_finished()
    assert (fb.busy, fb.percent) == (False, 100)


def test_recording_feedback_cancel_invokes_latest_on_cancel():
    fb = RecordingFeedback()
    hits = []
    fb.task_started("Generating skeletons", cancelable=True, on_cancel=lambda: hits.append(1))
    fb.cancel()
    assert hits == [1]


def test_recording_feedback_cancel_without_handler_is_a_no_op():
    fb = RecordingFeedback()
    fb.task_started("Loading")
    fb.cancel()  # must not raise

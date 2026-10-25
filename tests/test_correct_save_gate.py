"""SaveGate: a layer save that would replace a file the editor did not show asks first (M1-A).

The session asks the gate before each layer save. Driven through fake layers
(``tests/correct_fakes.py``) and scripted prompts, like ``test_correct_session.py``; no Qt.
"""

from __future__ import annotations

from app.gui.correct.layer import MASK, SKELETON, SaveOutcome
from app.gui.correct.save_gate import (
    CANCEL,
    REPLACE,
    SaveGate,
    SaveReport,
    unloaded_label_of,
)
from app.gui.correct.session import CorrectSession
from tests.correct_fakes import FakeLayer, FakePrompts

IMAGE = "/data/images/a.jpg"
PARENT = "PARENT"
KEPT = "Mask not saved: mask/a.png was kept"
PGDN = "mask/a.png could not be shown: save with Ctrl+S to replace it"


class _Legacy:
    """A layer written before ``DiskGuardedLayer``: all of ``inner`` but ``unloaded_label``."""

    def __init__(self, inner: FakeLayer) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        if name == "unloaded_label":
            raise AttributeError(name)
        return getattr(self._inner, name)


def _rig(mask, *, unsaved=(), replace=()):
    """A session over ``mask`` and a fake skeleton, opened on IMAGE, logs cleared."""
    prompts = FakePrompts(unsaved=unsaved, replace=replace)
    events: list[tuple] = []
    session = CorrectSession(
        [mask, FakeLayer(SKELETON)],
        prompts=prompts,
        parent=lambda: PARENT,
        on_dirty_changed=lambda _dirty: None,
        on_notice=lambda message, kind: events.append(("notice", message, kind)),
        on_history_changed=lambda: None,
        on_undone=lambda _i, _h: None,
        on_saved=lambda outcome: events.append(("saved", outcome)),
    )
    session.open(IMAGE, (640, 480), "/data/images")
    mask.calls.clear()
    return session, prompts, events


def _unloaded_mask() -> FakeLayer:
    mask = FakeLayer(MASK)
    mask.dirty = True
    mask.unloaded = "mask/a.png"
    return mask


# -------------------------------------------------------------------------- session
def test_a_save_over_an_unloaded_file_asks_and_keep_it_declines():
    mask = _unloaded_mask()
    session, prompts, events = _rig(mask, replace=[CANCEL])
    mask.dirty = True  # open made it clean

    report = session.save(MASK)

    assert "save" not in mask.names()
    declined = SaveOutcome(MASK, ok=False, declined=True, message=KEPT, kind="info")
    assert report.outcomes == (declined,)
    assert prompts.asked == [("replace", PARENT, "mask", "mask/a.png")]
    assert report.declined == (MASK,) and report.failed == () and not report.ok
    assert ("saved", declined) in events and ("notice", KEPT, "info") in events
    assert mask.is_dirty()  # the edits stay


def test_replace_saves_the_layer():
    mask = _unloaded_mask()
    session, prompts, _ = _rig(mask, replace=[REPLACE])
    mask.dirty = True

    report = session.save(MASK)

    assert "save" in mask.names() and report.ok
    assert report.declined == () and report.failed == ()
    assert [a[0] for a in prompts.asked] == ["replace"]


def test_pgdn_never_asks_and_stays():
    mask = _unloaded_mask()
    session, prompts, events = _rig(mask)
    mask.dirty = True

    report = session.save_dirty()

    assert prompts.asked == [] and "save" not in mask.names()
    assert report.outcomes == (
        SaveOutcome(MASK, ok=False, declined=True, message=PGDN, kind="warn"),
    )
    assert not report.ok and report.declined == (MASK,) and report.failed == ()
    assert ("notice", PGDN, "warn") in events


def test_a_declined_save_in_the_leave_prompt_stays_without_the_failed_save_question():
    mask = _unloaded_mask()
    session, prompts, _ = _rig(mask, unsaved=["save"], replace=["cancel"])
    mask.dirty = True

    assert session.release() is False

    assert [a[0] for a in prompts.asked] == ["unsaved", "replace"]
    assert "save" not in mask.names() and "revert" not in mask.names()
    assert mask.is_dirty()


def test_a_declined_layer_stays_even_when_the_other_failed_one_is_discarded():
    mask = _unloaded_mask()
    skeleton = FakeLayer(SKELETON)
    skeleton.save_outcomes = [SaveOutcome(SKELETON, ok=False, message="disk full", kind="danger")]
    prompts = FakePrompts(unsaved=["save"], after_failed=["discard"], replace=["cancel"])
    session = CorrectSession(
        [mask, skeleton],
        prompts=prompts,
        parent=lambda: PARENT,
        on_dirty_changed=lambda _dirty: None,
        on_notice=lambda _m, _k: None,
        on_history_changed=lambda: None,
        on_undone=lambda _i, _h: None,
        on_saved=lambda _outcome: None,
    )
    session.open(IMAGE, (640, 480), "/data/images")
    mask.dirty = skeleton.dirty = True

    assert session.release() is False

    assert [(a[0], a[2]) for a in prompts.asked] == [
        ("unsaved", "mask and skeleton"),
        ("replace", "mask"),
        ("after_failed", "skeleton"),
    ]
    assert "revert" in skeleton.names() and "revert" not in mask.names()


def test_a_replaced_save_in_the_leave_prompt_moves_on():
    mask = _unloaded_mask()
    session, prompts, _ = _rig(mask, unsaved=["save"], replace=[REPLACE])
    mask.dirty = True

    assert session.release() is True
    assert "save" in mask.names()


def test_a_layer_without_the_protocol_is_saved_as_today():
    inner = _unloaded_mask()  # would name a file, but the layer does not say so
    session, prompts, _ = _rig(_Legacy(inner))
    inner.dirty = True

    assert session.save(MASK).ok and session.save_dirty().outcomes == ()

    assert prompts.asked == [] and inner.names().count("save") == 1


def test_a_layer_with_nothing_unloaded_is_saved_without_a_question():
    mask = FakeLayer(MASK)
    session, prompts, _ = _rig(mask)
    mask.dirty = True

    assert session.save_dirty().ok
    assert prompts.asked == [] and "save" in mask.names()


# ----------------------------------------------------------------------------- parts
def test_unloaded_label_of_reads_the_method_else_nothing():
    mask = FakeLayer(MASK)
    mask.unloaded = "mask/a.png"

    assert unloaded_label_of(mask) == "mask/a.png"
    assert unloaded_label_of(_Legacy(mask)) == ""
    assert unloaded_label_of(object()) == ""


def test_the_gate_asks_the_prompts_with_the_parent_of_the_moment():
    parents = iter(["first", "second"])
    prompts = FakePrompts(replace=[REPLACE, CANCEL])
    gate = SaveGate(prompts, lambda: next(parents))
    mask = _unloaded_mask()

    assert gate.before_save(MASK, mask, ask=True) is None
    assert gate.before_save(MASK, mask, ask=True).declined

    assert [a[1] for a in prompts.asked] == ["first", "second"]


def test_the_report_keeps_declined_apart_from_failed():
    report = SaveReport(
        (
            SaveOutcome(MASK, ok=False, declined=True, kind="info"),
            SaveOutcome(SKELETON, ok=False, kind="danger"),
        )
    )

    assert report.declined == (MASK,) and report.failed == (SKELETON,) and not report.ok
    assert SaveReport().ok and SaveReport().declined == ()

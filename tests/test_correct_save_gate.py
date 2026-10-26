"""SaveGate: a layer save that would replace a file the editor did not show asks first (M1-A),
and so does one over a file that changed on disk since it was opened or last saved (E2, O19).

The session asks the gate before each layer save. Driven through fake layers
(``tests/correct_fakes.py``) and scripted prompts, like ``test_correct_session.py``; no Qt.
"""

from __future__ import annotations

from app.gui.correct.layer import MASK, SKELETON, SaveOutcome
from app.gui.correct.save_gate import (
    CANCEL,
    DISCARD,
    OVERWRITE,
    RELOAD,
    REPLACE,
    SaveGate,
    SaveReport,
    matches_disk_of,
    unloaded_label_of,
    write_stamps_of,
)
from app.gui.correct.session import CorrectSession
from app.gui.key_text import platform_keys
from tests.correct_fakes import FakeHost, FakeLayer, FakePrompts

IMAGE = "/data/images/a.jpg"
BASE = "/data/images"
PARENT = "PARENT"
KEPT = "Mask not saved: mask/a.png was kept"
PGDN = platform_keys("mask/a.png could not be shown: save with Ctrl+S to replace it")
# The mask file at open, and rewritten by another program afterwards.
AT_OPEN = (("/data/images/mask/a.png", 1, 10),)
REWRITTEN = (("/data/images/mask/a.png", 2, 10),)
RELOADED = "Mask reloaded: the file changed on disk"
CHANGED_PGDN = platform_keys(
    "mask/a.png changed on disk after you opened it: press Ctrl+S to choose"
)
CHANGED_KEPT = "Mask not saved: mask/a.png changed on disk"


class _Legacy:
    """A layer written before ``DiskGuardedLayer``: all of ``inner`` but ``unloaded_label``."""

    def __init__(self, inner: FakeLayer) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        if name == "unloaded_label":
            raise AttributeError(name)
        return getattr(self._inner, name)


def _rig(mask, *, unsaved=(), replace=(), changed=(), skeleton=None):
    """A session over ``mask`` and a fake skeleton, opened on IMAGE, logs cleared."""
    prompts = FakePrompts(unsaved=unsaved, replace=replace, changed=changed)
    events: list[tuple] = []
    session = CorrectSession(
        [mask, skeleton or FakeLayer(SKELETON)],
        prompts=prompts,
        parent=lambda: PARENT,
        on_dirty_changed=lambda _dirty: None,
        on_notice=lambda message, kind: events.append(("notice", message, kind)),
        on_history_changed=lambda: None,
        on_undone=lambda _i, _h: None,
        on_saved=lambda outcome: events.append(("saved", outcome)),
    )
    session.open(IMAGE, (640, 480), BASE)
    mask.calls.clear()
    return session, prompts, events


def _rewritten(**rig) -> tuple[FakeLayer, CorrectSession, FakePrompts, list[tuple]]:
    """A mask opened on AT_OPEN with one edit in the timeline, then rewritten on disk."""
    mask = FakeLayer(MASK)
    mask.stamps = AT_OPEN
    session, prompts, events = _rig(mask, **rig)
    mask.host = FakeHost(session)
    mask.act()
    mask.stamps = REWRITTEN
    return mask, session, prompts, events


def _gate(prompts: FakePrompts, reopened: list[str] | None = None) -> SaveGate:
    """A gate on its own: reopens are recorded in ``reopened``; the root is BASE."""
    record = reopened if reopened is not None else []
    return SaveGate(prompts, lambda: PARENT, reopen=record.append, root=lambda: BASE)


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
    gate = SaveGate(prompts, lambda: next(parents), reopen=lambda _id: None, root=lambda: BASE)
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


# ------------------------------------------------------------- files changed on disk (E2)
def test_a_save_over_a_rewritten_file_asks_and_overwrite_saves():
    mask, session, prompts, _ = _rewritten(changed=[OVERWRITE])

    report = session.save(MASK)

    assert prompts.asked == [("changed", PARENT, "mask", "mask/a.png")]
    assert "save" in mask.names() and report.ok
    assert session.save(MASK).ok and len(prompts.asked) == 1  # stamps taken after the save


def test_reload_reopens_the_layer_and_declines():
    mask, session, prompts, events = _rewritten(changed=[RELOAD])

    report = session.save(MASK)

    assert "open" in mask.names() and "save" not in mask.names()
    declined = SaveOutcome(MASK, ok=False, declined=True, message=RELOADED, kind="info")
    assert report.outcomes == (declined,) and report.failed == ()
    assert ("notice", RELOADED, "info") in events
    assert MASK not in session.journal.undo_entries and not session.can_undo()
    assert not mask.is_dirty()
    mask.dirty = True
    assert session.save(MASK).ok and len(prompts.asked) == 1  # the reload took the stamps


def test_cancel_keeps_the_edits_and_the_file():
    mask, session, _, events = _rewritten(changed=[CANCEL])

    report = session.save(MASK)

    declined = SaveOutcome(MASK, ok=False, declined=True, message=CHANGED_KEPT, kind="info")
    assert report.outcomes == (declined,)
    assert "save" not in mask.names() and "open" not in mask.names()
    assert mask.is_dirty() and session.journal.undo_entries == (MASK,)
    assert ("notice", CHANGED_KEPT, "info") in events


def test_pgdn_over_a_rewritten_file_never_asks_and_stays():
    mask, session, prompts, events = _rewritten()

    report = session.save_dirty()

    assert prompts.asked == [] and "save" not in mask.names()
    warn = SaveOutcome(MASK, ok=False, declined=True, message=CHANGED_PGDN, kind="warn")
    assert report.outcomes == (warn,) and not report.ok and report.failed == ()
    assert ("notice", CHANGED_PGDN, "warn") in events


def test_a_clean_layer_over_a_rewritten_file_is_neither_saved_nor_read_by_ctrl_s():
    mask = FakeLayer(MASK)
    mask.stamps = AT_OPEN
    session, prompts, events = _rig(mask)
    mask.stamps = REWRITTEN

    report = session.save(MASK)  # Ctrl+S with nothing unsaved: O2, nothing is written

    assert prompts.asked == [] and "save" not in mask.names() and "open" not in mask.names()
    assert report.outcomes == ()
    assert ("notice", "Nothing unsaved", "info") in events
    assert session.files_changed()  # opening the image again reads the file


def test_the_gate_reloads_a_clean_layer_over_a_rewritten_file():
    prompts, reopened = FakePrompts(), []
    gate = _gate(prompts, reopened)
    mask = FakeLayer(MASK)
    mask.stamps = AT_OPEN
    gate.take(MASK, mask)
    mask.stamps = REWRITTEN

    outcome = gate.before_save(MASK, mask, ask=True)

    assert outcome == SaveOutcome(MASK, ok=True, message=RELOADED, kind="info")
    assert prompts.asked == [] and reopened == [MASK]


def test_matching_files_only_refresh_the_stamps():
    prompts, reopened = FakePrompts(), []
    gate = _gate(prompts, reopened)
    mask = FakeLayer(MASK)
    mask.stamps, mask.dirty = AT_OPEN, True
    gate.take(MASK, mask)
    mask.stamps, mask.disk_same = REWRITTEN, True  # the same pixels, written again

    assert gate.before_save(MASK, mask, ask=True) is None

    mask.disk_same = False  # the stamps were taken again: the files are not even read
    assert gate.before_save(MASK, mask, ask=True) is None
    assert prompts.asked == [] and reopened == []


def test_matching_files_save_without_a_question():
    mask, session, prompts, _ = _rewritten()
    mask.disk_same = True

    assert session.save_dirty().ok
    mask.dirty = True  # edited again
    assert session.save(MASK).ok

    assert prompts.asked == [] and mask.names().count("save") == 2


def test_discard_reopens_a_changed_layer_and_reverts_an_unchanged_one():
    skeleton = FakeLayer(SKELETON)
    mask, session, prompts, events = _rewritten(unsaved=[DISCARD], skeleton=skeleton)
    skeleton.calls.clear()
    skeleton.dirty = True

    assert session.release() is True

    assert "open" in mask.names() and "revert" not in mask.names()
    assert "revert" in skeleton.names() and "open" not in skeleton.names()
    assert ("notice", RELOADED, "info") in events
    assert MASK not in session.journal.undo_entries
    assert not session.is_dirty()
    mask.dirty = True
    assert session.save(MASK).ok and [a[0] for a in prompts.asked] == ["unsaved"]


def test_discard_reverts_a_changed_layer_whose_files_still_match():
    mask, session, _, events = _rewritten(unsaved=[DISCARD])
    mask.disk_same = True

    assert session.release() is True

    assert "revert" in mask.names() and "open" not in mask.names()
    assert ("notice", RELOADED, "info") not in events


def test_reloaded_documents_take_the_stamps_again():
    mask, session, prompts, _ = _rewritten()

    session.reload_documents()  # the page read the files again (same image, files changed)
    mask.dirty = True

    assert session.save(MASK).ok and prompts.asked == []


def test_forget_drops_the_stamps_taken():
    prompts = FakePrompts()
    gate = _gate(prompts)
    mask = FakeLayer(MASK)
    mask.stamps, mask.dirty = AT_OPEN, True
    gate.take(MASK, mask)

    gate.forget()  # the session closed
    mask.stamps = REWRITTEN

    assert gate.before_save(MASK, mask, ask=True) is None and prompts.asked == []


def test_the_label_is_the_first_changed_file_relative_to_the_root():
    prompts = FakePrompts()
    gate = _gate(prompts)
    skeleton = FakeLayer(SKELETON)
    skeleton.dirty = True
    skeleton.stamps = (
        ("/data/images/gt/skeletons/a.png", 1, 5),
        ("/data/images/skeletons/a_fake.png", 1, 5),
    )
    gate.take(SKELETON, skeleton)
    skeleton.stamps = (skeleton.stamps[0], ("/data/images/skeletons/a_fake.png", 2, 5))

    gate.before_save(SKELETON, skeleton, ask=True)

    assert prompts.asked == [("changed", PARENT, "skeleton", "skeletons/a_fake.png")]


class _StampsOnly:
    """A layer written before the stamp contract: ``file_stamps`` and nothing else of it."""

    def __init__(self, stamps: tuple) -> None:
        self.stamps = stamps

    def file_stamps(self) -> tuple:
        return self.stamps


def test_write_stamps_of_and_matches_disk_of_read_the_methods_else_fall_back():
    mask = FakeLayer(MASK)
    mask.stamps, mask.disk_same = AT_OPEN, True

    assert write_stamps_of(mask) == AT_OPEN and matches_disk_of(mask) is True
    assert write_stamps_of(_StampsOnly(REWRITTEN)) == REWRITTEN  # its file_stamps()
    assert matches_disk_of(_StampsOnly(REWRITTEN)) is False and matches_disk_of(object()) is False

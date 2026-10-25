"""CorrectSession: the Qt-free document lifecycle of the Correct workspace (C15).

Driven through fake layers (``tests/correct_fakes.py``) and scripted prompts; no Qt.
"""

from __future__ import annotations

import pytest

from app.gui.correct.layer import MASK, SKELETON, Notice, SaveOutcome
from app.gui.correct.session import CANCEL, DISCARD, KEEP, SAVE, CorrectSession, SaveReport
from tests.correct_fakes import FakeHost, FakeLayer, FakePrompts

IMAGE = "/data/images/a.jpg"
SIZE = (640, 480)
BASE = "/data/images"


class _Rig:
    """A session over a fake mask and a fake skeleton, recording every callback in order."""

    def __init__(self, *, unsaved=(), after_failed=(), skeleton_history: int = 25) -> None:
        self.host = FakeHost()
        self.mask = FakeLayer(MASK, host=self.host)
        self.skeleton = FakeLayer(SKELETON, host=self.host, history_limit=skeleton_history)
        self.prompts = FakePrompts(unsaved, after_failed)
        self.events: list[tuple] = []
        self.history_probe = None  # called on every history change when set
        self.session = CorrectSession(
            [self.mask, self.skeleton],
            prompts=self.prompts,
            parent=lambda: "PARENT",
            on_dirty_changed=lambda dirty: self.events.append(("dirty", dirty)),
            on_notice=lambda message, kind: self.events.append(("notice", message, kind)),
            on_history_changed=self._history_changed,
            on_undone=lambda layer_id, how: self.events.append(("undone", layer_id, how)),
            on_saved=lambda outcome: self.events.append(("saved", outcome)),
        )
        self.host.session = self.session

    def _history_changed(self) -> None:
        self.events.append(("history",))
        if self.history_probe is not None:
            self.history_probe()

    def opened(self) -> _Rig:
        self.session.open(IMAGE, SIZE, BASE)
        self.clear_log()
        return self

    def clear_log(self) -> None:
        self.events.clear()
        self.mask.calls.clear()
        self.skeleton.calls.clear()

    def of(self, kind: str) -> list[tuple]:
        return [event[1:] for event in self.events if event[0] == kind]

    def notices(self) -> list[tuple[str, str]]:
        return [(message, kind) for message, kind in self.of("notice")]

    def saved(self) -> list[SaveOutcome]:
        return [outcome for (outcome,) in self.of("saved")]


def _failure(layer_id: str, message: str) -> SaveOutcome:
    return SaveOutcome(layer_id, ok=False, message=message, kind="danger")


# ---------------------------------------------------------------- open and close
def test_layers_are_kept_by_id_mask_first_whatever_the_given_order():
    mask, skeleton = FakeLayer(MASK), FakeLayer(SKELETON)
    session = CorrectSession(
        [skeleton, mask],
        prompts=FakePrompts(),
        parent=lambda: None,
        on_dirty_changed=lambda _dirty: None,
        on_notice=lambda _m, _k: None,
        on_history_changed=lambda: None,
        on_undone=lambda _i, _h: None,
        on_saved=lambda _o: None,
    )

    assert list(session.layers) == [MASK, SKELETON]
    assert session.layers[MASK] is mask and session.layers[SKELETON] is skeleton
    assert session.image_path == ""


def test_open_opens_every_layer_with_the_same_arguments_and_forwards_their_notices():
    rig = _Rig()
    rig.mask.open_notices = [Notice("Saved mask size does not match the image", "warn")]
    rig.skeleton.open_notices = [Notice("Opened the generated skeleton")]

    rig.session.open(IMAGE, SIZE, BASE)

    assert rig.mask.calls == [("open", IMAGE, SIZE, BASE)]
    assert rig.skeleton.calls == [("open", IMAGE, SIZE, BASE)]
    assert rig.notices() == [
        ("Saved mask size does not match the image", "warn"),
        ("Opened the generated skeleton", "info"),
    ]
    assert rig.session.image_path == IMAGE


def test_opening_an_image_clears_the_undo_timeline():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.act()
    assert rig.session.journal.undo_entries == (MASK, SKELETON)

    rig.session.open("/data/images/b.jpg", SIZE, BASE)

    assert rig.session.journal.undo_entries == ()
    assert not rig.session.can_undo()


def test_files_changed_compares_every_layers_stamps_with_those_at_open():
    rig = _Rig()
    rig.mask.stamps = (("mask/a.png", None),)
    rig.skeleton.stamps = (("corrections/a_skeleton.png", None), ("skeletons/a_fake.png", 5, 9))
    assert not rig.session.files_changed()  # nothing open

    rig.session.open(IMAGE, SIZE, BASE)
    assert not rig.session.files_changed()

    # our own first save creates the correction
    rig.skeleton.stamps = (("corrections/a_skeleton.png", 7, 3), ("skeletons/a_fake.png", 5, 9))
    assert rig.session.files_changed()

    rig.session.reload_documents()
    assert not rig.session.files_changed()


def test_image_changed_follows_the_image_file_stamp(tmp_path):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x" * 10)
    rig = _Rig()
    assert not rig.session.image_changed()  # nothing open

    rig.session.open(str(image), SIZE, str(tmp_path))
    assert not rig.session.image_changed()

    image.write_bytes(b"y" * 20)
    assert rig.session.image_changed()
    rig.session.reload_documents()  # reloads the documents, not the photo
    assert rig.session.image_changed()

    image.unlink()
    assert rig.session.image_changed()


def test_reload_documents_reopens_every_layer_and_clears_the_timeline():
    rig = _Rig().opened()
    rig.mask.act()
    rig.clear_log()

    rig.session.reload_documents()

    assert rig.mask.calls == [("open", IMAGE, SIZE, BASE)]
    assert rig.skeleton.calls == [("open", IMAGE, SIZE, BASE)]
    assert rig.session.journal.undo_entries == ()
    assert rig.session.image_path == IMAGE


def test_close_closes_the_layers_and_forgets_the_image_and_the_timeline():
    rig = _Rig().opened()
    rig.mask.act()
    rig.clear_log()

    rig.session.close()

    assert rig.mask.names() == ["close"] and rig.skeleton.names() == ["close"]
    assert rig.session.image_path == ""
    assert rig.session.journal.undo_entries == ()
    assert not rig.session.files_changed()


# ------------------------------------------------------------------ save targets
def test_save_writes_the_dirty_layers_mask_first():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True

    report = rig.session.save(SKELETON)

    assert [outcome.layer_id for outcome in report.outcomes] == [MASK, SKELETON]
    assert [outcome.layer_id for outcome in rig.saved()] == [MASK, SKELETON]
    assert report.ok and report.failed == ()


def test_save_leaves_a_clean_inactive_layer_alone():
    rig = _Rig().opened()
    rig.mask.dirty = True

    report = rig.session.save(SKELETON)

    assert [outcome.layer_id for outcome in report.outcomes] == [MASK]
    assert "save" not in rig.skeleton.names()


def test_save_with_nothing_dirty_writes_nothing_and_notifies():  # O2
    rig = _Rig().opened()

    report = rig.session.save(SKELETON)

    assert report.outcomes == () and report == SaveReport()
    assert "save" not in rig.mask.names() and "save" not in rig.skeleton.names()
    assert rig.saved() == []
    assert rig.notices()[-1] == ("Nothing unsaved", "info")


def test_save_with_nothing_open_saves_nothing():
    rig = _Rig()

    assert rig.session.save(MASK) == SaveReport()
    assert rig.mask.calls == [] and rig.events == []


def test_a_failed_layer_does_not_stop_the_next_one():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True
    rig.mask.save_outcomes = [_failure(MASK, "Could not save mask to m.png: disk full")]

    report = rig.session.save(MASK)

    assert [outcome.layer_id for outcome in report.outcomes] == [MASK, SKELETON]
    assert not report.ok
    assert report.failed == (MASK,)
    assert rig.session.dirty_layers() == [MASK]


# ------------------------------------------------------------------------ toasts
def test_one_summary_toast_names_every_written_layer():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True

    rig.session.save(MASK)

    assert rig.notices() == [("Saved mask and skeleton · a.jpg", "success")]


def test_a_single_written_layer_is_named_alone():
    rig = _Rig().opened()
    rig.skeleton.dirty = True

    rig.session.save(SKELETON)

    assert rig.notices() == [("Saved skeleton · a.jpg", "success")]


def test_an_emptied_mask_only_shows_its_own_message():
    rig = _Rig().opened()
    rig.mask.dirty = True
    message = "Empty mask: removed saved mask for a.jpg"
    rig.mask.save_outcomes = [
        SaveOutcome(MASK, ok=True, cleared=True, message=message, kind="info")
    ]

    rig.session.save(MASK)

    assert rig.notices() == [(message, "info")]


def test_each_failure_is_a_danger_toast_with_its_message():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True
    rig.mask.save_outcomes = [_failure(MASK, "Could not save mask to m.png: disk full")]

    rig.session.save(MASK)

    assert rig.notices() == [
        ("Saved skeleton · a.jpg", "success"),
        ("Could not save mask to m.png: disk full", "danger"),
    ]


def test_a_failure_is_a_danger_toast_even_without_a_message_or_kind():
    rig = _Rig().opened()
    rig.skeleton.dirty = True
    rig.skeleton.save_outcomes = [SaveOutcome(SKELETON, ok=False)]

    rig.session.save(SKELETON)

    assert rig.notices() == [("Could not save the skeleton for a.jpg", "danger")]


def test_nothing_written_and_nothing_to_say_shows_no_toast():
    rig = _Rig().opened()
    rig.mask.dirty = True
    rig.mask.save_outcomes = [SaveOutcome(MASK, ok=True)]  # an empty mask without a file

    rig.session.save(MASK)

    assert rig.notices() == []


# ---------------------------------------------------------------- PgDn: save_dirty
def test_save_dirty_saves_only_the_dirty_layers():
    rig = _Rig().opened()
    rig.skeleton.dirty = True

    report = rig.session.save_dirty()

    assert [outcome.layer_id for outcome in report.outcomes] == [SKELETON]
    assert "save" not in rig.mask.names()
    assert [outcome.layer_id for outcome in rig.saved()] == [SKELETON]


def test_save_dirty_with_nothing_dirty_does_nothing():
    rig = _Rig().opened()

    report = rig.session.save_dirty()

    assert report == SaveReport() and report.ok
    assert rig.saved() == [] and rig.notices() == []
    assert "save" not in rig.mask.names() + rig.skeleton.names()


# ------------------------------------------------------------------ save reports
def test_every_save_attempt_is_reported_once_mask_first_failures_included():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True
    failed = _failure(MASK, "Could not remove mask: busy")
    written = SaveOutcome(SKELETON, ok=True, path="skeleton_corrections/a_skeleton.png")
    rig.mask.save_outcomes = [failed]
    rig.skeleton.save_outcomes = [written]

    rig.session.save(MASK)

    assert rig.saved() == [failed, written]


def test_save_dirty_reports_every_attempt():
    rig = _Rig().opened()
    rig.mask.dirty = rig.skeleton.dirty = True

    rig.session.save_dirty()

    assert [outcome.layer_id for outcome in rig.saved()] == [MASK, SKELETON]


def test_a_retry_after_a_failure_is_reported_again():
    rig = _Rig().opened()
    rig.mask.dirty = True
    rig.mask.save_outcomes = [_failure(MASK, "Could not save mask to m.png: disk full")]

    rig.session.save(MASK)
    rig.session.save(MASK)

    assert [outcome.ok for outcome in rig.saved()] == [False, True]


def test_the_prompts_save_reports_every_dirty_layer():
    rig = _Rig(unsaved=[SAVE]).opened()
    rig.mask.dirty = rig.skeleton.dirty = True

    assert rig.session.release()

    assert [outcome.layer_id for outcome in rig.saved()] == [MASK, SKELETON]
    assert rig.notices() == [("Saved mask and skeleton · a.jpg", "success")]


# ------------------------------------------------------------------ gestures first
def _finished_before(layer: FakeLayer, name: str) -> bool:
    names = layer.names()
    return "finish_gesture" in names and names.index("finish_gesture") < names.index(name)


def test_save_commits_a_gesture_first_and_saves_what_it_committed():
    rig = _Rig().opened()
    rig.mask.start_gesture(dirty_on_finish=True)  # a stroke in progress, not yet dirty

    report = rig.session.save(SKELETON)

    assert [outcome.layer_id for outcome in report.outcomes] == [MASK]
    assert _finished_before(rig.mask, "save")
    assert rig.session.journal.undo_entries == (MASK,)  # the committed stroke is undoable


def test_save_dirty_commits_a_gesture_first():
    rig = _Rig().opened()
    rig.skeleton.start_gesture(dirty_on_finish=True)

    report = rig.session.save_dirty()

    assert [outcome.layer_id for outcome in report.outcomes] == [SKELETON]
    assert _finished_before(rig.skeleton, "save")


def test_release_commits_a_gesture_first_and_asks_about_it():
    rig = _Rig(unsaved=[CANCEL]).opened()
    rig.mask.start_gesture(dirty_on_finish=True)

    assert not rig.session.release()

    assert [(prompt, noun) for prompt, _p, noun, _n in rig.prompts.asked] == [("unsaved", "mask")]
    assert not rig.mask.gesture_active()


@pytest.mark.parametrize(
    ("action", "layer_call"),
    [
        (lambda s: s.open(IMAGE, SIZE, BASE), "open"),
        (lambda s: s.close(), "close"),
        (lambda s: s.reload_documents(), "open"),
    ],
    ids=["open", "close", "reload_documents"],
)
def test_lifecycle_calls_commit_a_gesture_before_touching_the_documents(action, layer_call):
    rig = _Rig().opened()
    rig.skeleton.start_gesture(dirty_on_finish=True)

    action(rig.session)

    assert _finished_before(rig.skeleton, layer_call)
    assert "finish_gesture" not in rig.mask.names()  # no gesture there: left alone


def test_finish_gestures_touches_only_layers_with_a_gesture():
    rig = _Rig().opened()
    rig.mask.start_gesture()

    rig.session.finish_gestures()

    assert rig.mask.names() == ["finish_gesture"]
    assert rig.skeleton.names() == []
    assert not rig.mask.gesture_active()


# ------------------------------------------------------------------------ release
def test_release_with_nothing_dirty_moves_on_without_asking():
    rig = _Rig().opened()

    assert rig.session.release()
    assert rig.prompts.asked == []


def test_release_cancel_stays_and_keeps_the_edits():
    rig = _Rig(unsaved=[CANCEL]).opened()
    rig.mask.act()

    assert not rig.session.release()

    assert rig.session.dirty_layers() == [MASK]
    assert "save" not in rig.mask.names() and "revert" not in rig.mask.names()
    assert rig.session.journal.undo_entries == (MASK,)


def test_release_names_the_dirty_layers_and_the_image():
    rig = _Rig(unsaved=[CANCEL, CANCEL, CANCEL]).opened()
    rig.mask.dirty = True
    rig.session.release()
    rig.mask.dirty, rig.skeleton.dirty = False, True
    rig.session.release()
    rig.mask.dirty = True
    rig.session.release()

    assert rig.prompts.asked == [
        ("unsaved", "PARENT", "mask", "a.jpg"),
        ("unsaved", "PARENT", "skeleton", "a.jpg"),
        ("unsaved", "PARENT", "mask and skeleton", "a.jpg"),
    ]


def test_release_discard_reverts_the_dirty_layers_and_forgets_their_history():
    rig = _Rig(unsaved=[DISCARD]).opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.skeleton.dirty = False  # saved since: only the mask is dirty
    rig.mask.revert_notices = [Notice("Mask reverted")]

    assert rig.session.release()

    assert "revert" in rig.mask.names() and "revert" not in rig.skeleton.names()
    assert rig.session.journal.undo_entries == (SKELETON,)
    assert rig.notices() == [("Mask reverted", "info")]
    assert not rig.session.is_dirty()


def test_release_save_saves_and_moves_on():
    rig = _Rig(unsaved=[SAVE]).opened()
    rig.skeleton.dirty = True

    assert rig.session.release()

    assert "save" in rig.skeleton.names() and "save" not in rig.mask.names()
    assert not rig.session.is_dirty()


def test_a_failed_save_asks_again_about_the_failed_layers_only():
    rig = _Rig(unsaved=[SAVE], after_failed=[KEEP]).opened()
    rig.mask.dirty = rig.skeleton.dirty = True
    rig.skeleton.save_outcomes = [_failure(SKELETON, "Could not save a_skeleton.png: denied")]

    assert not rig.session.release()  # Keep editing: the caller stays

    assert rig.prompts.asked[-1] == ("after_failed", "PARENT", "skeleton", "a.jpg")
    assert rig.session.dirty_layers() == [SKELETON]
    assert "revert" not in rig.skeleton.names()


def test_discard_after_a_failed_save_reverts_the_failed_layers():
    rig = _Rig(unsaved=[SAVE], after_failed=[DISCARD]).opened()
    rig.mask.dirty = rig.skeleton.dirty = True
    rig.mask.save_outcomes = [_failure(MASK, "Could not save mask to m.png: disk full")]

    assert rig.session.release()

    assert "revert" in rig.mask.names()
    assert "revert" not in rig.skeleton.names()  # it was saved
    assert not rig.session.is_dirty()


def test_release_can_be_limited_to_some_layers():
    rig = _Rig(unsaved=[DISCARD]).opened()
    rig.mask.dirty = rig.skeleton.dirty = True

    assert rig.session.release([SKELETON])

    assert [noun for _k, _p, noun, _n in rig.prompts.asked] == ["skeleton"]
    assert "revert" in rig.skeleton.names() and "revert" not in rig.mask.names()
    assert rig.session.dirty_layers() == [MASK]


def test_reset_releases_then_closes():
    rig = _Rig(unsaved=[DISCARD]).opened()
    rig.mask.act()

    assert rig.session.reset()

    assert rig.mask.names()[-2:] == ["revert", "close"]
    assert rig.session.image_path == ""


def test_a_cancelled_reset_keeps_everything():
    rig = _Rig(unsaved=[CANCEL]).opened()
    rig.mask.act()

    assert not rig.session.reset()

    assert "close" not in rig.mask.names()
    assert rig.session.image_path == IMAGE
    assert rig.session.dirty_layers() == [MASK]


# ---------------------------------------------------------------- undo and redo
def test_undo_steps_the_newest_action_of_either_layer():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.clear_log()

    assert rig.session.undo(MASK) == SKELETON  # "what I just did", not the active layer's

    assert rig.skeleton.names() == ["undo"]
    assert "undo" not in rig.mask.names()
    assert rig.of("undone") == [(SKELETON, "undo")]


def test_undo_and_redo_are_refused_while_a_gesture_is_in_progress():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.session.undo(MASK)  # the skeleton action can be redone
    rig.skeleton.draft_points = 1
    rig.skeleton.start_gesture()
    rig.clear_log()

    assert rig.session.undo(SKELETON) is None
    assert rig.session.redo() is None

    assert rig.mask.calls == [] and rig.skeleton.calls == []
    assert rig.session.journal.undo_entries == (MASK,)
    assert rig.session.journal.redo_entries == (SKELETON,)
    assert rig.skeleton.draft_points == 1


def test_a_polyline_draft_loses_its_last_point_before_the_timeline_steps():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.draft_points = 2
    rig.clear_log()

    assert rig.session.undo(SKELETON) == SKELETON

    assert rig.skeleton.draft_points == 1
    assert "undo" not in rig.mask.names()
    assert rig.session.journal.undo_entries == (MASK,)
    assert rig.of("undone") == []  # not a journal step


def test_undo_skips_actions_a_layer_already_evicted():
    rig = _Rig(skeleton_history=1).opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.skeleton.act()  # the bounded history keeps one

    assert rig.session.undo(MASK) == SKELETON
    assert rig.session.undo(MASK) == MASK  # the evicted skeleton entry is dropped

    assert rig.of("undone") == [(SKELETON, "undo"), (MASK, "undo")]
    assert rig.session.journal.undo_entries == ()
    assert rig.session.undo(MASK) is None


def test_redo_mirrors_undo():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.session.undo(MASK)
    rig.session.undo(MASK)
    rig.events.clear()

    assert rig.session.redo() == MASK
    assert rig.session.redo() == SKELETON
    assert rig.session.redo() is None

    assert rig.of("undone") == [(MASK, "redo"), (SKELETON, "redo")]


def test_can_undo_and_can_redo_follow_the_timeline():
    rig = _Rig().opened()
    assert (rig.session.can_undo(), rig.session.can_redo()) == (False, False)

    rig.mask.act()
    assert (rig.session.can_undo(), rig.session.can_redo()) == (True, False)

    rig.session.undo(MASK)
    assert (rig.session.can_undo(), rig.session.can_redo()) == (False, True)


def test_history_callbacks_never_see_a_half_moved_entry():
    rig = _Rig().opened()
    rig.mask.act()
    seen = []
    rig.history_probe = lambda: seen.append((rig.session.can_undo(), rig.session.can_redo()))

    rig.session.undo(MASK)

    assert seen and set(seen) == {(False, True)}


def test_a_new_action_ends_every_redo_branch():
    rig = _Rig().opened()
    rig.skeleton.act()
    rig.session.undo(SKELETON)
    assert rig.skeleton.can_redo()
    rig.clear_log()

    rig.mask.act()

    assert rig.skeleton.names() == ["drop_redo"]
    assert "drop_redo" not in rig.mask.names()
    assert not rig.skeleton.can_redo()
    assert rig.session.journal.redo_entries == ()
    assert rig.session.journal.undo_entries == (MASK,)
    assert ("history",) in rig.events


def test_forget_drops_one_layers_entries():
    rig = _Rig().opened()
    rig.mask.act()
    rig.skeleton.act()
    rig.mask.act()
    rig.events.clear()

    rig.session.forget(MASK)  # e.g. the mask history was cleared

    assert rig.session.journal.undo_entries == (SKELETON,)
    assert ("history",) in rig.events


# ------------------------------------------------------------------------- dirty
def test_dirty_changed_fires_once_per_aggregate_transition():
    rig = _Rig().opened()

    rig.mask.act()  # clean -> dirty
    rig.skeleton.act()  # still dirty
    rig.session.save(MASK)  # both saved: dirty -> clean
    rig.session.layer_changed(MASK)  # no change

    assert rig.of("dirty") == [(True,), (False,)]


def test_is_dirty_and_dirty_layers_aggregate_the_layers():
    rig = _Rig().opened()
    assert not rig.session.is_dirty() and rig.session.dirty_layers() == []

    rig.skeleton.dirty = True
    assert rig.session.is_dirty() and rig.session.dirty_layers() == [SKELETON]

    rig.mask.dirty = True
    assert rig.session.dirty_layers() == [MASK, SKELETON]


def test_a_save_report_is_ok_only_when_every_outcome_is():
    ok = SaveOutcome(MASK, ok=True, path="m.png")
    bad = SaveOutcome(SKELETON, ok=False, message="no")

    assert SaveReport().ok and SaveReport().failed == ()
    assert SaveReport((ok,)).ok
    assert not SaveReport((ok, bad)).ok
    assert SaveReport((ok, bad)).failed == (SKELETON,)

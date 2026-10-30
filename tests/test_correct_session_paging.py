"""CorrectSession on the paging path: payloads read ahead reach the layers that read them,
and a step that changes nothing does not refresh the page (Qt-free, fake layers)."""

from __future__ import annotations

from app.gui.correct.layer import MASK, SKELETON, ReadAheadLayer, SaveOutcome, missing_members
from app.gui.correct.session import CorrectSession
from tests.correct_fakes import FakeHost, FakeLayer, FakePrompts

IMAGE = "/data/images/a.jpg"
SIZE = (640, 480)
BASE = "/data/images"


class AheadLayer(FakeLayer):
    """A fake layer with the optional read-ahead hook; ``open`` records its payload."""

    def read_ahead(self, image_path, images_base_folder):
        return lambda size: ("payload", image_path, size)

    def open(self, image_path, size, images_base_folder, ahead=None):
        notices = super().open(image_path, size, images_base_folder)
        if ahead is not None:
            self.calls[-1] = (*self.calls[-1], ahead)
        return notices


class Rig:
    def __init__(self, *, mask=None, unsaved=()) -> None:
        self.host = FakeHost()
        self.mask = mask or FakeLayer(MASK, host=self.host)
        self.mask.host = self.host
        self.skeleton = FakeLayer(SKELETON, host=self.host)
        self.prompts = FakePrompts(unsaved)
        self.refreshes = 0
        self.session = CorrectSession(
            [self.mask, self.skeleton],
            prompts=self.prompts,
            parent=lambda: None,
            on_dirty_changed=lambda _dirty: None,
            on_notice=lambda _message, _kind: None,
            on_history_changed=self._refreshed,
            on_undone=lambda _layer_id, _how: None,
            on_saved=lambda _outcome: None,
        )
        self.host.session = self.session

    def _refreshed(self) -> None:
        self.refreshes += 1


# ------------------------------------------------------------------ read-ahead
def test_the_fake_read_ahead_layer_has_the_optional_hook():
    assert missing_members(AheadLayer(MASK), ReadAheadLayer) == []
    assert missing_members(FakeLayer(MASK), ReadAheadLayer) == ["read_ahead"]


def test_a_payload_read_ahead_goes_to_its_layer_the_others_open_as_usual():
    rig = Rig(mask=AheadLayer(MASK))

    rig.session.open(IMAGE, SIZE, BASE, ahead={MASK: "mask payload"})

    assert rig.mask.calls == [("open", IMAGE, SIZE, BASE, "mask payload")]
    assert rig.skeleton.calls == [("open", IMAGE, SIZE, BASE)]


def test_a_layer_without_a_payload_opens_as_usual():
    rig = Rig(mask=AheadLayer(MASK))

    rig.session.open(IMAGE, SIZE, BASE, ahead={})

    assert rig.mask.calls == [("open", IMAGE, SIZE, BASE)]


def test_reloading_the_documents_never_uses_an_old_payload():
    rig = Rig(mask=AheadLayer(MASK))
    rig.session.open(IMAGE, SIZE, BASE, ahead={MASK: "mask payload"})
    rig.mask.calls.clear()

    rig.session.reload_documents()

    assert rig.mask.calls == [("open", IMAGE, SIZE, BASE)]


# ----------------------------------------------------------- refreshes on paging
def opened_rig(**kwargs) -> Rig:
    rig = Rig(**kwargs)
    rig.session.open(IMAGE, SIZE, BASE)
    rig.refreshes = 0
    return rig


def test_paging_with_nothing_unsaved_does_not_refresh_the_page():
    rig = opened_rig()

    report = rig.session.save_dirty()

    assert report.outcomes == () and rig.refreshes == 0


def test_releasing_a_clean_image_does_not_refresh_the_page():
    rig = opened_rig()

    assert rig.session.release() is True
    assert rig.refreshes == 0 and rig.prompts.asked == []


def test_a_cancelled_prompt_does_not_refresh_the_page():
    rig = opened_rig(unsaved=["cancel"])
    rig.mask.act()
    rig.refreshes = 0

    assert rig.session.release() is False
    assert rig.refreshes == 0


def test_every_step_that_changes_something_still_refreshes_the_page_once():
    rig = opened_rig(unsaved=["discard"])
    steps = {
        "edit": rig.mask.act,
        "undo": lambda: rig.session.undo(MASK),
        "redo": rig.session.redo,
        "save": lambda: rig.session.save(MASK),
        "save_dirty": lambda: (rig.skeleton.act(), rig.session.save_dirty()),
        "forget": lambda: rig.session.forget(MASK),
        "discard": lambda: (rig.mask.act(), rig.session.release()),
        "reload": rig.session.reload_documents,
        "open": lambda: rig.session.open(IMAGE, SIZE, BASE),
        "close": rig.session.close,
    }
    counts = {}
    for name, step in steps.items():
        rig.refreshes = 0
        step()
        counts[name] = rig.refreshes

    assert all(count >= 1 for count in counts.values()), counts
    assert counts["open"] == counts["close"] == counts["save"] == 1


def test_a_gesture_finished_by_paging_is_saved_and_refreshes_the_page():
    rig = opened_rig()
    rig.mask.start_gesture(dirty_on_finish=True)

    report = rig.session.save_dirty()

    assert [outcome.layer_id for outcome in report.outcomes] == [MASK]
    assert rig.refreshes >= 1


# ------------------------------------------------------------ our own saves
def test_a_layers_own_save_is_no_change_on_disk():
    rig = opened_rig()
    rig.mask.act()
    rig.mask.stamps = (("mask.png", 1, 10),)  # the save writes the file

    rig.session.save(MASK)

    assert not rig.session.files_changed()
    rig.mask.stamps = (("mask.png", 2, 10),)  # someone else writes it afterwards
    assert rig.session.files_changed()


def test_a_failed_save_keeps_the_stamps_of_the_open():
    rig = opened_rig()
    rig.mask.act()
    rig.mask.save_outcomes = [SaveOutcome(MASK, ok=False, message="disk full", kind="danger")]
    rig.mask.stamps = (("mask.png", 1, 10),)

    rig.session.save(MASK)

    assert rig.session.files_changed()

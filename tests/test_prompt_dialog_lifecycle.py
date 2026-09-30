"""RB-16: a dialog made for one prompt is freed once answered.

The non-modal Retry prompt of Measure (``_offer_retry``) left one QMessageBox child on
its long-lived parent (the main window) per prompt until the window closed. The modal
"Masks already exist" prompt of Generate (``ask_overwrite_policy``) went only with its
last Python reference (PyQt's ``exec()`` hands the box to Python); both are now deleted
once answered. (The task progress hide timer: ``tests/test_shell_chrome_task_progress.py``;
the F1 sheet and About dialog: ``tests/test_integration_main_window_wiring.py``.)
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app.gui.feedback import RecordingFeedback  # noqa: E402
from app.handlers import mask_generation_handler as mgh  # noqa: E402
from app.handlers.job_registry import JobRegistry  # noqa: E402
from app.handlers.skeleton_handler import SkeletonHandler  # noqa: E402
from app.inference.mask_origin import ExistingMasks, OverwritePolicy  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

_Button = QMessageBox.StandardButton
_Role = QMessageBox.ButtonRole


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    """A long-lived parent, as the main window is for every prompt."""
    widget = QWidget()
    yield widget
    widget.deleteLater()
    del widget
    flush_deleted()


def _flush_deferred_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _boxes(parent: QWidget) -> list[QMessageBox]:
    return parent.findChildren(QMessageBox)


# --------------------------------------------------------------------------- Generate prompt
def _answer_when_shown(role: QMessageBox.ButtonRole, answered: list) -> None:
    """Click the button with ``role`` on the modal box once ``exec()`` has shown it.

    The box goes into ``answered``: while any reference to it lives, only an explicit
    delete frees it (PyQt's ``exec()`` hands the box to Python's garbage collection).
    """

    def click() -> None:
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(10, click)
            return
        answered.append(box)
        [button] = [b for b in box.buttons() if box.buttonRole(b) == role]
        button.click()

    QTimer.singleShot(0, click)


@pytest.mark.parametrize(
    ("role", "policy"),
    [
        (_Role.AcceptRole, OverwritePolicy.KEEP_EXISTING),  # "Keep existing masks"
        (_Role.DestructiveRole, OverwritePolicy.OVERWRITE_ALL),  # "Overwrite all"
        (_Role.RejectRole, None),  # Cancel
    ],
)
def test_overwrite_prompt_is_freed_once_answered(window, role, policy):
    existing = ExistingMasks(manual=frozenset({"a"}), model=frozenset({"b"}))
    answered = []
    _answer_when_shown(role, answered)
    assert mgh.ask_overwrite_policy(window, existing, total=5) is policy
    _flush_deferred_deletes()
    [box] = answered
    assert sip.isdeleted(box)  # freed although a reference to it is still held
    assert _boxes(window) == []


# --------------------------------------------------------------------------- Measure Retry prompt
class _IdleGeneration:
    def is_busy(self) -> bool:
        return False


def _measure_handler(window, monkeypatch):
    window.image_manager = SimpleNamespace(original_folder=None, images={})
    window.feedback = RecordingFeedback()
    window.jobs = JobRegistry()
    handler = SkeletonHandler(window, generation=_IdleGeneration())
    retried = []
    monkeypatch.setattr(handler, "retry_measure", retried.append)
    return handler, retried


@pytest.mark.parametrize("answer", [_Button.Retry, _Button.Cancel])
def test_retry_prompt_is_freed_once_answered(window, monkeypatch, answer):
    handler, retried = _measure_handler(window, monkeypatch)
    handler._retry_jobs["length"] = object()
    handler._offer_retry("length", "root_lengths.csv is open in another program")
    box = handler._retry_box
    assert _boxes(window) == [box]
    box.button(answer).click()
    _flush_deferred_deletes()
    assert sip.isdeleted(box)
    assert _boxes(window) == []
    assert handler._retry_box is None
    if answer == _Button.Retry:
        assert retried == ["length"]
    else:
        assert retried == [] and "length" not in handler._retry_jobs


def test_answering_an_older_retry_prompt_keeps_the_newer_one(window, monkeypatch):
    handler, retried = _measure_handler(window, monkeypatch)
    handler._retry_jobs.update(length=object(), area=object())
    handler._offer_retry("length", "root_lengths.csv is open in another program")
    first = handler._retry_box
    handler._offer_retry("area", "root_areas.csv is open in another program")
    second = handler._retry_box

    first.button(_Button.Cancel).click()  # the older prompt is answered first
    _flush_deferred_deletes()
    assert sip.isdeleted(first)
    assert handler._retry_box is second  # still the open prompt, never the freed one
    assert _boxes(window) == [second]

    second.button(_Button.Retry).click()
    _flush_deferred_deletes()
    assert handler._retry_box is None
    assert _boxes(window) == []
    assert retried == ["area"] and "length" not in handler._retry_jobs

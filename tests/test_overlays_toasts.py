"""Toasts (UXS-07): a capped stack, in-place updates, wrapped text, sticky errors.

Before: 14 'GT saved' toasts (PgDn autosave) stacked up the whole window, a long danger
toast was 1596 px wide at x=-332 in a 1280 px window, and it vanished after 3.2 s like a
success.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from app.gui.widgets import ToastManager
from tests.qt_teardown import flush_deleted

LONG_ERROR = (
    "Cannot export: gt/classes.json is invalid ("
    + "class 'root_tip' colour #ffd166 duplicates class 'nodule'; " * 4
    + ")"
)


@pytest.fixture
def host(app):
    window = QWidget()
    window.resize(1280, 800)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    window.close()
    window.deleteLater()
    del window
    flush_deleted()


def _text(toast) -> str:
    return toast.findChild(QLabel, "toastText").text()


def test_a_burst_keeps_only_the_three_newest_toasts(host):
    manager = ToastManager(host)
    shown = [manager.show(f"GT saved for T001_L0{40 + i}.jpg") for i in range(14)]
    QApplication.processEvents()
    assert manager.toasts() == shown[-3:]


def test_a_danger_toast_outlives_newer_successes_when_the_stack_is_full(host):
    manager = ToastManager(host)
    danger = manager.show("Could not save GT: disk full", "danger")
    successes = [manager.show(f"Mask saved for {i}.jpg") for i in range(3)]
    assert manager.toasts() == [danger, *successes[1:]]


def test_toasts_with_the_same_key_update_in_place(host):
    manager = ToastManager(host)
    first = manager.show("GT saved for a.jpg", key="gt-saved")
    second = manager.show("GT saved for b.jpg", key="gt-saved")
    assert second is first
    assert manager.toasts() == [first]
    assert _text(first) == "GT saved for b.jpg"


def test_long_messages_wrap_inside_the_window(host):
    manager = ToastManager(host)
    toast = manager.show(LONG_ERROR, "danger")
    QApplication.processEvents()
    assert host.rect().contains(toast.geometry())
    assert toast.width() <= 460
    assert toast.accessibleName() == LONG_ERROR


def test_unbreakable_paths_are_elided_with_the_full_text_in_the_tooltip(host):
    manager = ToastManager(host)
    path = "D:/" + "/".join(["a_very_long_folder_name"] * 12) + "/mask/T001_L044.png"
    message = f"Could not save mask to {path}"
    toast = manager.show(message, "danger")
    QApplication.processEvents()
    assert host.rect().contains(toast.geometry())
    assert "…" in _text(toast)
    assert toast.toolTip() == message


def test_errors_stay_until_closed_while_successes_expire(host):
    manager = ToastManager(host)
    danger = manager.show("Could not save GT: disk full", "danger", timeout=50)
    success = manager.show("Mask saved", "success", timeout=50)
    QTest.qWait(600)
    assert danger in manager.toasts() and success not in manager.toasts()

    assert danger.close_button is not None and danger.close_button.isVisible()
    QTest.mouseClick(danger.close_button, Qt.MouseButton.LeftButton)
    assert danger not in manager.toasts()


def test_warnings_last_longer_and_can_be_closed(host):
    manager = ToastManager(host)
    warning = manager.show("GPU unavailable — using CPU", "warn", timeout=50)
    QTest.qWait(600)
    assert warning in manager.toasts()
    assert warning.close_button is not None


def test_success_toasts_have_no_close_button_but_a_click_dismisses_them(host):
    manager = ToastManager(host)
    toast = manager.show("Loaded 40 images")
    assert toast.close_button is None
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    assert toast not in manager.toasts()


def test_toasts_stay_above_the_bottom_offset(host):
    manager = ToastManager(host)
    manager.set_bottom_offset(74)  # e.g. the status bands of the main window
    toast = manager.show("Loaded 40 images")
    assert toast.geometry().bottom() < host.height() - 74

    offset = [200]
    manager.set_bottom_offset(lambda: offset[0])  # e.g. above the current editor's dock
    assert toast.geometry().bottom() < host.height() - 200

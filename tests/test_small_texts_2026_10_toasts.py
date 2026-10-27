"""A save toast with a long file name stays on one line (KNOWN_ISSUES #7).

"Saved mask · <file name>" wrapped onto two lines when the name was long: the name fitted
on a line of its own, so it was not shortened, but the whole line did not fit. Now the
name of a "<what> · <name>" toast is elided in the middle (its start and its extension
stay) when the line would wrap; the full text stays in the tooltip. Other messages wrap
as before.
"""

from __future__ import annotations

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from app.gui.widgets import ToastManager
from tests.qt_teardown import delete_widgets

NAME = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"


@pytest.fixture
def host(app):
    made = [QWidget()]
    made[0].resize(1024, 640)
    made[0].show()
    QTest.qWaitForWindowExposed(made[0])
    yield made[0]
    delete_widgets(made)


def _label(toast) -> QLabel:
    return toast.findChild(QLabel, "toastText")


# "mask and skeleton" is left out: the offscreen platform's wide font leaves its name
# under MIN_NAME_PX (the app's font leaves it about 230 px).
@pytest.mark.parametrize("what", ["mask", "GT"])
def test_a_long_file_name_is_elided_in_the_middle_on_one_line(host, what):
    message = f"Saved {what} · {NAME}"
    toast = ToastManager(host).show(message)
    QApplication.processEvents()

    label = _label(toast)
    assert not label.wordWrap()
    assert "\n" not in label.text()
    assert label.text().startswith(f"Saved {what} · 24EF")
    assert label.text().endswith(".jpg")
    assert "…" in label.text()
    assert toast.width() <= 460
    assert host.rect().contains(toast.geometry())
    assert toast.toolTip() == message
    assert toast.accessibleName() == message


def test_a_short_name_is_left_whole(host):
    toast = ToastManager(host).show("Saved mask · L44.jpg")
    QApplication.processEvents()

    assert _label(toast).text() == "Saved mask · L44.jpg"
    assert not _label(toast).wordWrap()


def test_a_name_with_no_room_left_beside_its_lead_wraps(host):
    message = f"Saved the mask, the skeleton and the ground truth tracing of this image · {NAME}"
    toast = ToastManager(host).show(message)
    QApplication.processEvents()

    assert _label(toast).wordWrap()
    assert _label(toast).text().startswith("Saved the mask, the skeleton")


def test_one_line_needs_a_name_room_of_min_name_px(app):
    """The fitting rule on its own, with the application's font."""
    from PyQt6.QtGui import QFontMetrics

    from app.gui.widgets.toasts import MIN_NAME_PX, name_on_one_line

    metrics = QFontMetrics(QApplication.font())
    lead = "Saved mask · "
    room = metrics.horizontalAdvance(lead) + MIN_NAME_PX + 40
    line = name_on_one_line(lead + NAME, metrics, room)
    assert line is not None and line.startswith(lead + "2") and line.endswith("jpg")
    assert metrics.horizontalAdvance(line) <= room
    assert name_on_one_line(lead + NAME, metrics, room - 50) is None  # under MIN_NAME_PX
    assert name_on_one_line(lead + "L44.jpg", metrics, room) is None  # fits as it is
    assert name_on_one_line(f"Could not save {NAME} here", metrics, room) is None


def test_a_sentence_still_wraps(host):
    message = f"Could not save mask for {NAME}: disk full"
    toast = ToastManager(host).show(message, "danger")
    QApplication.processEvents()

    assert _label(toast).wordWrap()
    assert _label(toast).text().endswith(": disk full")


def test_several_words_after_the_dot_still_wrap(host):
    message = "Branch deleted · " + "press Ctrl+Z to undo the deletion " * 3
    toast = ToastManager(host).show(message.strip(), "info")
    QApplication.processEvents()

    assert _label(toast).wordWrap()
    assert "…" not in _label(toast).text()


def test_an_update_in_place_refits_the_name(host):
    manager = ToastManager(host)
    toast = manager.show("Saved mask · L44.jpg", key="saved")
    QApplication.processEvents()
    assert manager.show(f"Saved mask · {NAME}", key="saved") is toast
    QApplication.processEvents()

    assert not _label(toast).wordWrap()
    assert _label(toast).text().endswith(".jpg") and "…" in _label(toast).text()

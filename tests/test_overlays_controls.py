"""Keyboard and screen-reader access to the SPROUTS controls (UXS-08, controls side).

- Icon-only buttons and dock sliders were announced as just "button" / "slider": their
  names now come from the tooltip ("Brush (B) · hold B ..." -> "Brush").
- Their widget-level stylesheets hid the app's focus ring: a Tab-focused IconButton,
  segment or dock slider changed 0 pixels.
- Arrow keys move the SegmentedControl selection (Left/Up, Right/Down, wrapping, Home/End).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

from app.gui.widgets import DockSlider, IconButton, SegmentedControl  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def window(app):
    """An active window with a focus sink above the control under test."""
    win = QWidget()
    win.resize(420, 260)
    layout = QVBoxLayout(win)
    sink = QLineEdit("sink")
    layout.addWidget(sink)
    win.show()
    win.activateWindow()
    assert QTest.qWaitForWindowActive(win, 3000)
    yield win, layout, sink
    win.close()
    win.deleteLater()


def _grab(widget: QWidget) -> QImage:
    QApplication.processEvents()
    return widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _changed_pixels(before: QImage, after: QImage) -> int:
    assert before.size() == after.size()
    return sum(
        before.pixel(x, y) != after.pixel(x, y)
        for y in range(before.height())
        for x in range(before.width())
    )


def _focus_repaint(window, widget: QWidget, focus_target: QWidget | None = None) -> int:
    _win, layout, sink = window
    layout.addWidget(widget)
    widget.show()
    sink.setFocus(Qt.FocusReason.OtherFocusReason)
    before = _grab(widget)
    (focus_target or widget).setFocus(Qt.FocusReason.TabFocusReason)
    assert (focus_target or widget).hasFocus()
    return _changed_pixels(before, _grab(widget))


# --------------------------------------------------------------------------- #
#  Accessible names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tooltip,name",
    [
        ("Brush (B) · hold B + scroll to resize", "Brush"),
        ("Fit to window (Ctrl+0)", "Fit to window"),
        ("Pan (hold Space, or middle-drag)", "Pan"),
        ("Edit classes", "Edit classes"),
    ],
)
def test_icon_button_is_named_after_its_tooltip(app, tooltip, name):
    assert IconButton("brush", tooltip).accessibleName() == name


def test_icon_button_name_follows_a_changed_tooltip(app):
    button = IconButton("trash", "Clear mask (undoable)")
    button.setToolTip("Clear tracing (undoable)")  # the GT editor retitles its controls
    assert button.accessibleName() == "Clear tracing"


def test_icon_button_keeps_an_explicit_name(app):
    button = IconButton("trash", "Clear mask (undoable)")
    button.setAccessibleName("Delete everything")
    button.setToolTip("Clear tracing (undoable)")
    assert button.accessibleName() == "Delete everything"


def test_dock_slider_is_named_after_its_tooltip(app):
    control = DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px")
    assert control.slider.accessibleName() == "Brush size"
    control.setToolTip("Tracing opacity")  # the GT editor retitles the opacity slider
    assert control.slider.accessibleName() == "Tracing opacity"


def test_icon_only_segments_are_named_after_their_value(app):
    seg = SegmentedControl([("single", "", "single"), ("overlay", "Overlay", "overlay")])
    assert seg.button("single").accessibleName() == "Single"
    assert seg.button("overlay").accessibleName() == "Overlay"


# --------------------------------------------------------------------------- #
#  Visible keyboard focus
# --------------------------------------------------------------------------- #
def test_icon_button_shows_keyboard_focus(window):
    assert _focus_repaint(window, IconButton("brush", "Brush (B)")) > 0


def test_segment_shows_keyboard_focus(window):
    seg = SegmentedControl([("single", "Single"), ("overlay", "Overlay")], value="single")
    assert _focus_repaint(window, seg, seg.button("overlay")) > 0


def test_dock_slider_shows_keyboard_focus(window):
    control = DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px")
    assert _focus_repaint(window, control, control.slider) > 0


# --------------------------------------------------------------------------- #
#  Arrow keys on the segmented control
# --------------------------------------------------------------------------- #
def test_arrow_keys_move_the_segmented_selection(window):
    _win, layout, _sink = window
    seg = SegmentedControl(
        [("single", "Single"), ("overlay", "Overlay"), ("split", "Side by side")], value="single"
    )
    layout.addWidget(seg)
    seg.show()
    got = []
    seg.valueChanged.connect(got.append)
    seg.button("single").setFocus(Qt.FocusReason.TabFocusReason)

    for key, expected in [
        (Qt.Key.Key_Right, "overlay"),
        (Qt.Key.Key_Down, "split"),
        (Qt.Key.Key_Right, "single"),  # wraps around
        (Qt.Key.Key_Left, "split"),  # and back
        (Qt.Key.Key_Home, "single"),
        (Qt.Key.Key_End, "split"),
        (Qt.Key.Key_Up, "overlay"),
    ]:
        QTest.keyClick(QApplication.focusWidget(), key)
        assert seg.value() == expected, key
        assert QApplication.focusWidget() is seg.button(expected)
    assert got == ["overlay", "split", "single", "split", "single", "split", "overlay"]

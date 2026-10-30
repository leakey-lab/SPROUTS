"""Overlay controls never take keyboard focus from the canvas on click (UXE-03).

Clicking Save, a dock slider or a rail tool used to move focus off the canvas, so the next
Space press clicked Save again instead of panning and a B tap no longer selected the brush.
Controls placed in the floating overlays are now ``TabFocus``: a click leaves focus where it
was, Tab still reaches them.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets import DockSlider, FloatingDock, ToolRail, keep_canvas_focus


def _panel_with_controls():
    container = QWidget()
    box = QVBoxLayout(container)
    button, slider, label, combo = QPushButton("Save"), QSlider(), QLabel("Size"), QComboBox()
    for w in (button, slider, label, combo):
        box.addWidget(w)
    return container, button, slider, label, combo


def test_keep_canvas_focus_turns_click_focus_into_tab_focus(app):
    container, button, slider, label, combo = _panel_with_controls()
    keep_canvas_focus(container)
    for w in (button, slider, combo):
        assert w.focusPolicy() == Qt.FocusPolicy.TabFocus, w
    assert label.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_keep_canvas_focus_can_refuse_focus_entirely(app):
    container, button, slider, _label, combo = _panel_with_controls()
    tool = QToolButton(container)  # TabFocus by default: must also become NoFocus
    keep_canvas_focus(container, tab_reachable=False)
    for w in (container, button, slider, combo, tool):
        assert w.focusPolicy() == Qt.FocusPolicy.NoFocus, w


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def _editor_like():
    """A host with a focusable canvas, a dock (Save + slider) and a rail (tool button)."""
    host = QWidget()
    host.resize(900, 600)
    canvas = _Canvas(host)
    canvas.setGeometry(0, 0, 900, 600)
    dock = FloatingDock(host)
    save = QPushButton("Save mask")
    size = DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px")
    dock.add_widget(size)
    dock.add_widget(save)
    rail = ToolRail(host)
    tool = QPushButton("Select")
    tool.setCheckable(True)
    rail.add_widget(tool)
    host.show()
    host.activateWindow()
    assert QTest.qWaitForWindowActive(host, 3000)
    dock.reposition()
    rail.reposition()
    canvas.setFocus()
    return host, canvas, save, size, tool


@pytest.mark.parametrize("target", ["save", "slider", "rail tool"])
def test_clicking_an_overlay_control_leaves_focus_on_the_canvas(app, target):
    host, canvas, save, size, tool = _editor_like()
    try:
        widget = {"save": save, "slider": size.slider, "rail tool": tool}[target]
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        assert QApplication.focusWidget() is canvas
    finally:
        host.close()


def test_space_after_clicking_save_does_not_click_it_again(app):
    host, canvas, save, _size, _tool = _editor_like()
    try:
        clicks = []
        save.clicked.connect(lambda: clicks.append(1))
        QTest.mouseClick(save, Qt.MouseButton.LeftButton)
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
        assert clicks == [1]
    finally:
        host.close()


def test_tab_still_reaches_the_dock_controls(app):
    host, canvas, _save, size, _tool = _editor_like()
    try:
        QTest.keyClick(canvas, Qt.Key.Key_Tab)
        assert QApplication.focusWidget() is size.slider
    finally:
        host.close()

"""The library tree's key relay ends a canvas's holds through ``end_holds()``.

A key relayed from the tree (``app/gui/canvas_key_relay.py``) whose release will not come
through it (the tree lost the keyboard to another widget, or the canvas's page left the
screen) ends the canvas's holds as a lost keyboard does: the sink's (B), then Space. No focus
event is made up for that: a synthetic ``FocusOut`` also repainted the canvas.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QLineEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui import shortcuts
from app.gui.canvas_key_relay import CanvasKeyRelay
from app.gui.editor_canvas import EditorCanvasView
from tests.qt_teardown import delete_widgets

B, SPACE = Qt.Key.Key_B, Qt.Key.Key_Space


class _Sink:
    """An InputSink that consumes B (as the Correct router does) and records its keys and
    the end of its holds."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def canvas_size(self) -> QSize:
        return QSize(400, 300)

    def canvas_press(self, pos, event) -> bool:
        return False

    def canvas_move(self, pos, event, dragging) -> None:
        pass

    def canvas_release(self, pos, event) -> None:
        pass

    def canvas_double_click(self, pos, event) -> None:
        pass

    def canvas_wheel(self, event) -> bool:
        return False

    def canvas_key_press(self, event) -> bool:
        self.calls.append(("key", event.key()))
        return event.key() == B

    def canvas_key_release(self, event) -> bool:
        self.calls.append(("key_release", event.key()))
        return event.key() == B

    def canvas_hover_left(self) -> None:
        pass

    def canvas_pan_changed(self, panning) -> None:
        pass

    def canvas_focus_lost(self) -> None:
        self.calls.append(("focus_lost",))


class _FocusOuts(QObject):
    """Counts the FocusOut events that reach ``view``."""

    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self.count = 0
        view.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.FocusOut:
            self.count += 1
        return False


@pytest.fixture
def rig(app, gl_off):
    """A shown window: the tree (with the keyboard), the Correct canvas, a text field."""
    host = QWidget()
    layout = QVBoxLayout(host)
    tree, sink = QTreeWidget(), _Sink()
    view = EditorCanvasView(sink)
    scene = QGraphicsScene(view)
    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor(90, 60, 30))
    scene.addItem(QGraphicsPixmapItem(pixmap))
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, 400, 300))
    other = QLineEdit()
    for widget in (tree, view, other):
        layout.addWidget(widget)
    CanvasKeyRelay(tree, lambda: (view, shortcuts.CORRECT))
    host.resize(500, 700)
    host.show()
    host.activateWindow()
    assert QTest.qWaitForWindowActive(host, 3000)
    tree.setFocus(Qt.FocusReason.MouseFocusReason)
    QApplication.processEvents()
    assert tree.hasFocus()
    yield SimpleNamespace(tree=tree, view=view, sink=sink, other=other, outs=_FocusOuts(view))
    delete_widgets([host])


def test_holds_end_when_the_tree_loses_the_keyboard_elsewhere(rig):
    QTest.keyPress(rig.tree, SPACE)
    QTest.keyPress(rig.tree, B)
    assert rig.view.space_pan and ("key", B) in rig.sink.calls

    rig.other.setFocus(Qt.FocusReason.MouseFocusReason)  # the releases will not reach it
    QApplication.processEvents()

    assert not rig.view.space_pan
    assert rig.sink.calls.count(("focus_lost",)) == 1
    assert ("key_release", B) not in rig.sink.calls  # a lost hold is not a B tap
    assert rig.outs.count == 0  # no made-up FocusOut reached the canvas


def test_a_release_while_the_canvas_is_hidden_ends_its_holds(rig):
    QTest.keyPress(rig.tree, B)
    QTest.keyPress(rig.tree, SPACE)
    rig.view.hide()  # its page left the screen; the tree keeps the keyboard
    rig.sink.calls.clear()

    QTest.keyRelease(rig.tree, B)

    assert rig.sink.calls == [("focus_lost",)]  # no B release reached it: no tap
    assert not rig.view.space_pan
    assert rig.outs.count == 0


def test_holds_stay_when_the_canvas_takes_the_keyboard(rig):
    QTest.keyPress(rig.tree, SPACE)
    rig.view.setFocus(Qt.FocusReason.MouseFocusReason)  # a press on it mid-pan
    QApplication.processEvents()
    assert rig.view.space_pan and ("focus_lost",) not in rig.sink.calls

    QTest.keyRelease(rig.view, SPACE)  # the release comes to the canvas itself

    assert not rig.view.space_pan

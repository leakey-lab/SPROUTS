"""A held Space ends when the canvas loses the keyboard (app/gui/canvas_view.py).

Its release then goes elsewhere (another widget, a popup, another window), so the canvas
never sees it. Only the Correct page ended the hold on a focus loss (its event filter):
the Library display kept its Space pan after the keyboard left it. Every canvas now ends
its holds in ``CanvasViewMixin.focusOutEvent``; ``end_holds()`` does the same on request
(the library tree's key relay).
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QRectF, Qt
from PyQt6.QtGui import QColor, QFocusEvent, QKeyEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.gui.display_controller import MagnifyingGraphicsView
from app.gui.editor_canvas import EditorCanvasView
from tests.qt_teardown import delete_widgets

VIEWS = [MagnifyingGraphicsView, EditorCanvasView]
NO_MOD = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def window(app, gl_off):
    """A shown, active window holding a canvas of ``view_cls`` and a text field."""
    made = []

    def make(view_cls):
        host = QWidget()
        layout = QVBoxLayout(host)
        view = view_cls(None)
        scene = QGraphicsScene(view)
        pixmap = QPixmap(400, 300)
        pixmap.fill(QColor(90, 60, 30))
        scene.addItem(QGraphicsPixmapItem(pixmap))
        view.setScene(scene)
        view.set_content_rect(QRectF(0, 0, 400, 300))
        other = QLineEdit()
        layout.addWidget(view)
        layout.addWidget(other)
        host.resize(500, 420)
        host.show()
        host.activateWindow()
        assert QTest.qWaitForWindowActive(host, 3000)
        view.setFocus(Qt.FocusReason.MouseFocusReason)
        QApplication.processEvents()
        assert view.hasFocus()
        made.append(host)
        return view, other

    yield make
    delete_widgets(made)


def _press_space(view) -> None:
    QApplication.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, NO_MOD))


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_a_space_pan_ends_when_the_keyboard_moves_to_another_widget(window, view_cls):
    view, other = window(view_cls)
    panning_before = view.is_panning_mode()
    _press_space(view)
    assert view.space_pan

    other.setFocus(Qt.FocusReason.MouseFocusReason)  # a click elsewhere, Space still down
    QApplication.processEvents()
    QApplication.sendEvent(other, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, NO_MOD))

    assert not view.hasFocus()
    assert not view.space_pan
    assert view.is_panning_mode() == panning_before  # sticky pan mode stays as it was


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
@pytest.mark.parametrize(
    "reason",
    [Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason],
    ids=["window", "popup"],
)
def test_a_space_pan_ends_on_any_focus_loss(window, view_cls, reason):
    view, _other = window(view_cls)
    cursor = view.viewport().cursor().shape()
    _press_space(view)
    QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusOut, reason))
    assert not view.space_pan
    assert view.viewport().cursor().shape() == cursor  # the cursor of no pan, as before


@pytest.mark.parametrize("view_cls", VIEWS, ids=lambda c: c.__name__)
def test_end_holds_ends_a_space_pan_on_request(window, view_cls):
    view, _other = window(view_cls)
    view.end_holds()  # nothing held: nothing happens
    assert not view.space_pan
    _press_space(view)
    view.end_holds()
    assert not view.space_pan
    _press_space(view)  # the next Space starts a pan again
    assert view.space_pan

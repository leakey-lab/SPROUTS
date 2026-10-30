"""Toasts must not nest graphics effects (review finding UI-F6).

The toast fades through a QGraphicsOpacityEffect on the Toast widget. When its inner frame
also carried a drop shadow, Qt logged seven QPainter warnings on every partial repaint of a
toast that sat over another widget. Qt does not support nested graphics effects.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QRect, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from app.gui.widgets.toasts import Toast, ToastManager


def _pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def test_toast_frame_has_no_effect_under_the_fade():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    toast = Toast(host, "Mask saved", "success")
    effects = [w.graphicsEffect() for w in toast.findChildren(QWidget) if w.graphicsEffect()]
    assert effects == [], "a child effect under the toast's opacity effect breaks painting"
    assert toast.graphicsEffect() is not None  # the fade itself is kept
    host.deleteLater()
    app.processEvents()


def test_partial_repaint_of_a_toast_over_a_sibling_logs_no_painter_warnings():
    app = QApplication.instance() or QApplication([])
    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))
    try:
        host = QWidget()
        host.resize(800, 600)
        under = QLabel("x" * 200, host)
        under.setGeometry(0, 0, 800, 600)
        host.show()
        _pump(app, 0.2)
        toast = ToastManager(host).show("hello", kind="info", timeout=5000)
        _pump(app, 0.4)
        g = toast.geometry()
        under.update(QRect(g.x() + 5, g.y() + 5, 20, 10))
        toast.update()
        _pump(app, 0.3)
        painter_msgs = [m for m in messages if "ainter" in m]
        assert painter_msgs == []
        host.close()
        host.deleteLater()
        app.processEvents()
    finally:
        qInstallMessageHandler(previous)

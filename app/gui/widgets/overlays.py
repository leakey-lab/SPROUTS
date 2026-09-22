"""In-window floating overlays for the SPROUTS editors and feedback layer.

These are parented to a host widget (the interface/canvas), not placed in a
layout. The host must call ``reposition()`` from its ``resizeEvent`` and
``raise_()`` them above the QGraphicsView. QSS has no backdrop-filter, so the
translucent design panels are approximated with a near-opaque ``--bg-1`` fill
plus a QGraphicsDropShadowEffect.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .effects import pop_shadow
from .icons import load_pixmap


# --------------------------------------------------------------------------- #
#  Floating tool rail / dock
# --------------------------------------------------------------------------- #
class _FloatingPanel(QFrame):
    """Rounded near-opaque panel with a pop shadow, for in-window overlays."""

    def __init__(self, parent=None, *, horizontal=False):
        super().__init__(parent)
        self.setObjectName("floatPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame#floatPanel {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-radius: 13px;
            }}
        """)
        box = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(3)
        self._box = box
        pop_shadow(self)

    def add_widget(self, w: QWidget):
        self._box.addWidget(w)

    def add_separator(self):
        sep = QFrame(self)
        if isinstance(self._box, QVBoxLayout):
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFixedHeight(1)
        else:
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {tokens.BORDER}; border: none;")
        self._box.addWidget(sep)


class ToolRail(_FloatingPanel):
    """Vertical floating rail, vertically centred at the host's left edge."""

    def __init__(self, parent=None, *, margin: int = 14):
        super().__init__(parent, horizontal=False)
        self._margin = margin

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        ph = self.parent().height()
        y = max(self._margin, (ph - self.height()) // 2)
        self.move(self._margin, y)


class FloatingDock(_FloatingPanel):
    """Horizontal floating dock, bottom-centred over the host."""

    def __init__(self, parent=None, *, margin: int = 18):
        super().__init__(parent, horizontal=True)
        self._margin = margin

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw, ph = self.parent().width(), self.parent().height()
        x = max(self._margin, (pw - self.width()) // 2)
        y = ph - self.height() - self._margin
        self.move(x, y)


class CornerPanel(_FloatingPanel):
    """Small horizontal floating panel pinned to a corner of the host.

    ``corner`` is one of ``"tl"``, ``"tr"``, ``"bl"``, ``"br"``. Used for the
    canvas filename/status chip and the zoom cluster.
    """

    def __init__(self, parent=None, *, corner: str = "br", margin: int = 14, v_margin=None):
        super().__init__(parent, horizontal=True)
        self._corner = corner
        self._margin = margin
        self._v_margin = margin if v_margin is None else v_margin
        self._box.setContentsMargins(5, 4, 5, 4)

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw, ph = self.parent().width(), self.parent().height()
        x = self._margin if self._corner[1] == "l" else pw - self.width() - self._margin
        y = self._v_margin if self._corner[0] == "t" else ph - self.height() - self._v_margin
        self.move(x, y)


class EnhancePopover(_FloatingPanel):
    """Top-right popover that hosts an arbitrary content widget."""

    def __init__(self, parent=None, *, margin: int = 14, width: int = 264):
        super().__init__(parent, horizontal=False)
        self._margin = margin
        self.setFixedWidth(width)
        self.hide()

    def set_content(self, w: QWidget):
        self._box.addWidget(w)

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw = self.parent().width()
        self.move(pw - self.width() - self._margin, self._margin)

    def toggle(self):
        # isHidden() tracks explicit show/hide state even when the host widget
        # is not itself on-screen (isVisible() would always be False then).
        if not self.isHidden():
            self.hide()
        else:
            self.reposition()
            self.raise_()
            self.show()


# --------------------------------------------------------------------------- #
#  Toast
# --------------------------------------------------------------------------- #
_KIND_COLOR = {
    "success": tokens.OK,
    "info": tokens.INFO,
    "warn": tokens.WARN,
    "danger": tokens.DANGER,
}


class Toast(QWidget):
    """Transient bottom-right toast. Fades via a QGraphicsOpacityEffect
    (windowOpacity is unreliable on some Qt platforms) and auto-dismisses."""

    closed = pyqtSignal()

    def __init__(
        self, parent, message: str, kind: str = "success", timeout: int = 3200, *, margin: int = 16
    ):
        super().__init__(parent)
        self._margin = margin
        color = _KIND_COLOR.get(kind, tokens.OK)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        frame = QFrame(self)
        frame.setObjectName("toastFrame")
        frame.setStyleSheet(f"""
            QFrame#toastFrame {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-left: 3px solid {color};
                border-radius: 10px;
            }}
            QLabel {{ color: {tokens.TEXT}; background: transparent; font-size: 12.5px; }}
        """)
        pop_shadow(frame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(13, 10, 14, 10)
        row.setSpacing(9)
        dot = QLabel()
        pm = load_pixmap("check" if kind == "success" else "info", color, 15)
        dot.setPixmap(pm)
        row.addWidget(dot)
        row.addWidget(QLabel(message))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timeout = timeout

    def show_toast(self):
        self.adjustSize()
        self.reposition()
        self.raise_()
        self.show()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(self._timeout)

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw, ph = self.parent().width(), self.parent().height()
        self.move(pw - self.width() - self._margin, ph - self.height() - self._margin)

    def dismiss(self):
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish)
        self._fade.start()

    def _finish(self):
        self.closed.emit()
        self.deleteLater()


class ToastManager:
    """Owns a stack of toasts anchored bottom-right of a host widget.

    Wire it on ``MainWindow`` and route transient successes (the existing
    ``status_bar.showMessage(...)`` calls) through :meth:`show`; hard errors keep
    using ``QMessageBox.critical``.
    """

    def __init__(self, host: QWidget, *, gap: int = 10, margin: int = 16):
        self._host = host
        self._gap = gap
        self._margin = margin
        self._toasts: list[Toast] = []

    def show(self, message: str, kind: str = "success", timeout: int = 3200) -> Toast:
        toast = Toast(self._host, message, kind=kind, timeout=timeout, margin=self._margin)
        toast.closed.connect(lambda t=toast: self._remove(t))
        self._toasts.append(toast)
        toast.show_toast()
        self.reposition()
        return toast

    def _remove(self, toast: Toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        self.reposition()

    def reposition(self):
        ph = self._host.height()
        pw = self._host.width()
        y = ph - self._margin
        for toast in reversed(self._toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(pw - toast.width() - self._margin, y)
            y -= self._gap


# --------------------------------------------------------------------------- #
#  Progress overlay (generate / loading feedback)
# --------------------------------------------------------------------------- #
class ProgressOverlay(QWidget):
    """Floating top-centre progress card: live dot + label + percent + bar."""

    def __init__(self, parent=None, *, margin: int = 18, width: int = 340):
        super().__init__(parent)
        self._margin = margin
        self.setFixedWidth(width)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        frame = QFrame(self)
        frame.setObjectName("progFrame")
        frame.setStyleSheet(f"""
            QFrame#progFrame {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-radius: 12px;
            }}
            QLabel#progLabel {{ color: {tokens.TEXT}; background: transparent;
                font-size: 12.5px; font-weight: 600; }}
            QLabel#progPct {{ color: {tokens.ACCENT}; background: transparent;
                font-family: {tokens.MONO}; font-size: 12px; }}
            QProgressBar {{ background-color: {tokens.BG_3}; border: none;
                border-radius: 2px; height: 4px; }}
            QProgressBar::chunk {{ background-color: {tokens.ACCENT}; border-radius: 2px; }}
        """)
        pop_shadow(frame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 12, 16, 14)
        col.setSpacing(9)
        top = QHBoxLayout()
        top.setSpacing(9)
        self._dot = QLabel()
        self._dot.setPixmap(load_pixmap("info", tokens.ACCENT, 8))
        self._dot.setFixedWidth(10)
        self._label = QLabel("Working…")
        self._label.setObjectName("progLabel")
        self._pct = QLabel("0%")
        self._pct.setObjectName("progPct")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self._dot)
        top.addWidget(self._label, 1)
        top.addWidget(self._pct)
        col.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        col.addWidget(self._bar)
        self.hide()

    def set_label(self, text: str):
        self._label.setText(text)

    def set_progress(self, pct: int):
        pct = max(0, min(100, int(pct)))
        self._bar.setValue(pct)
        self._pct.setText(f"{pct}%")

    def start(self, label: str = "Working…"):
        self.set_label(label)
        self.set_progress(0)
        self.reposition()
        self.raise_()
        self.show()

    def finish(self):
        self.set_progress(100)
        self.hide()

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw = self.parent().width()
        x = max(self._margin, (pw - self.width()) // 2)
        self.move(x, self._margin)

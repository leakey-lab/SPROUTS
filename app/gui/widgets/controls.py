"""Reusable SPROUTS controls: SegmentedControl, IconButton.

Pure presentation — these wrap standard Qt widgets and expose plain signals so
callers can forward to existing handlers without behaviour changes.
"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QWidget,
)

from . import tokens
from .icons import load_icon

_SEG_QSS = f"""
QWidget#seg {{
    background-color: {tokens.BG_2};
    border: 1px solid {tokens.BORDER};
    border-radius: 8px;
}}
QPushButton#segBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    color: {tokens.TEXT_MUTED};
    font-size: 12.5px;
    font-weight: 500;
    padding: 5px 12px;
}}
QPushButton#segBtn:hover {{ color: {tokens.TEXT}; }}
QPushButton#segBtn:checked {{
    background-color: {tokens.BG_ACTIVE};
    color: {tokens.TEXT};
}}
"""


class SegmentedControl(QWidget):
    """Exclusive pill segmented control. Emits ``valueChanged(str)``.

    ``options`` is an iterable of either ``value`` strings or ``(value, label)``
    / ``(value, label, icon_name)`` tuples.
    """

    valueChanged = pyqtSignal(str)

    def __init__(self, options: Iterable, value: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        self.setStyleSheet(_SEG_QSS)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        first = None
        for opt in options:
            if isinstance(opt, (tuple, list)):
                val = opt[0]
                label = opt[1] if len(opt) > 1 else opt[0]
                icon = opt[2] if len(opt) > 2 else None
            else:
                val, label, icon = opt, opt, None
            btn = QPushButton(label)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon:
                btn.setIcon(load_icon(icon, tokens.TEXT_MUTED, 15))
                btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _=False, v=val: self._on_click(v))
            self._group.addButton(btn)
            lay.addWidget(btn)
            self._buttons[val] = btn
            if first is None:
                first = val

        self._value = None
        if value is None:
            value = first
        if value is not None:
            self.setValue(value, emit=False)

    def _on_click(self, value: str):
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)

    def setValue(self, value: str, emit: bool = True):
        btn = self._buttons.get(value)
        if btn is None:
            return
        btn.setChecked(True)
        self._value = value
        if emit:
            self.valueChanged.emit(value)

    def value(self) -> str | None:
        return self._value


class IconButton(QToolButton):
    """Bare icon button. ``.on`` (checked) state = accent-soft pill.

    The active/inactive colours are baked into the rendered icon so it tracks
    the checked state without relying on QSS ``currentColor`` (unsupported).
    """

    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        *,
        size: int = 30,
        icon_px: int = 17,
        checkable: bool = False,
        color: str = tokens.TEXT_MUTED,
        on_color: str = tokens.ACCENT,
        parent=None,
    ):
        super().__init__(parent)
        self._icon_name = icon_name
        self._color = color
        self._on_color = on_color
        self._icon_px = icon_px
        self.setObjectName("iconBtn")
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_px, icon_px))
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet(self._qss())
        self._refresh_icon()
        self.toggled.connect(lambda _=False: self._refresh_icon())

    def _qss(self) -> str:
        return f"""
        QToolButton#iconBtn {{
            background: transparent;
            border: 1px solid transparent;
            border-radius: 7px;
            color: {tokens.TEXT_MUTED};
        }}
        QToolButton#iconBtn:hover {{ background-color: {tokens.BG_3}; }}
        QToolButton#iconBtn:checked {{ background-color: {tokens.rgba(self._on_color, 0.14)}; }}
        QToolButton#iconBtn:disabled {{ background: transparent; }}
        """

    def _refresh_icon(self):
        col = self._on_color if self.isChecked() else self._color
        self.setIcon(load_icon(self._icon_name, col, self._icon_px))

    def set_icon_name(self, name: str):
        self._icon_name = name
        self._refresh_icon()

    def set_colors(self, color: str | None = None, on_color: str | None = None):
        if color is not None:
            self._color = color
        if on_color is not None:
            self._on_color = on_color
            self.setStyleSheet(self._qss())
        self._refresh_icon()


_DOCK_SLIDER_QSS = f"""
QSlider {{ background: transparent; min-height: 18px; max-height: 18px; }}
QSlider::groove:horizontal {{
    border: none; height: 4px; background: {tokens.BG_3}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {tokens.ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {tokens.TEXT}; border: none; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QLabel#dockSliderValue {{
    color: {tokens.TEXT}; background: transparent;
    font-family: {tokens.MONO}; font-size: 12px;
}}
"""


class DockSlider(QWidget):
    """Icon + filled horizontal slider + mono value readout, for floating docks.

    ``fmt`` formats the readout from the integer value (e.g. ``"{} px"``).
    The inner :class:`QSlider` is exposed as ``.slider`` so callers keep using
    the standard ``valueChanged`` / ``setValue`` API.
    """

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        minimum: int,
        maximum: int,
        value: int,
        *,
        fmt: str = "{}",
        width: int = 110,
        parent=None,
    ):
        super().__init__(parent)
        self._fmt = fmt
        self.setStyleSheet(_DOCK_SLIDER_QSS)
        self.setToolTip(tooltip)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(load_icon(icon_name, tokens.TEXT_MUTED, 16).pixmap(16, 16))
        row.addWidget(icon)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.setFixedWidth(width)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.slider)

        self.value_label = QLabel()
        self.value_label.setObjectName("dockSliderValue")
        self.value_label.setMinimumWidth(44)
        row.addWidget(self.value_label)

        self.slider.valueChanged.connect(self._sync_label)
        self._sync_label(value)

    def _sync_label(self, value: int):
        self.value_label.setText(self._fmt.format(value))

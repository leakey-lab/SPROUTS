"""Reusable SPROUTS controls: SegmentedControl, IconButton, DockSlider.

Pure presentation — these wrap standard Qt widgets and expose plain signals so
callers can forward to existing handlers without behaviour changes.

Accessibility (audit UXS-08): icon-only buttons and dock sliders take their
accessible name from their tooltip, every control shows a keyboard-focus ring
(its own stylesheet would otherwise hide the app's), and arrow keys move the
SegmentedControl selection.
"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
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


def accessible_label(tooltip: str) -> str:
    """Short control name from a tooltip: ``"Brush (B) · hold B ..."`` -> ``"Brush"``."""
    return tooltip.split(" (")[0].split(" ·")[0].strip()


_SEG_QSS = f"""
QWidget#seg {{
    background-color: {tokens.BG_2};
    border: 1px solid {tokens.BORDER};
    border-radius: 8px;
}}
QPushButton#segBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {tokens.TEXT_MUTED};
    font-size: 12.5px;
    font-weight: 500;
    padding: 4px 11px;
}}
QPushButton#segBtn:hover {{ color: {tokens.TEXT}; }}
QPushButton#segBtn:checked {{
    background-color: {tokens.BG_ACTIVE};
    color: {tokens.TEXT};
}}
{tokens.focus_rule("QPushButton#segBtn")}
"""


class SegmentedControl(QWidget):
    """Exclusive pill segmented control. Emits ``valueChanged(str)``.

    ``options`` is an iterable of either ``value`` strings or ``(value, label)``
    / ``(value, label, icon_name)`` tuples. With a segment focused, Left/Up and
    Right/Down select the previous / next segment (wrapping), Home / End the
    first / last, like a radio group.
    """

    valueChanged = pyqtSignal(str)
    #: Every click or keyboard selection, also of the already-selected segment
    #: (what a "show this view" selector needs; ``valueChanged`` fires on change only).
    activated = pyqtSignal(str)
    _STEP_KEYS = {
        Qt.Key.Key_Left: -1,
        Qt.Key.Key_Up: -1,
        Qt.Key.Key_Right: 1,
        Qt.Key.Key_Down: 1,
    }

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
            btn.setAccessibleName(label or str(val).replace("_", " ").capitalize())
            if icon:
                btn.setIcon(load_icon(icon, tokens.TEXT_MUTED, 15))
                btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _=False, v=val: self._on_click(v))
            btn.installEventFilter(self)
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

    def button(self, value: str) -> QPushButton | None:
        """The segment button for ``value`` (``None`` if there is no such segment)."""
        return self._buttons.get(value)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.KeyPress and obj in self._buttons.values():
            target = self._keyboard_target(obj, event.key())
            if target is not None:
                self._buttons[target].setChecked(True)
                self._on_click(target)
                self._buttons[target].setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(obj, event)

    def _keyboard_target(self, focused: QPushButton, key) -> str | None:
        """Segment an arrow / Home / End key press on ``focused`` selects, if any."""
        values = [v for v, b in self._buttons.items() if b.isEnabled() and not b.isHidden()]
        if not values:
            return None
        if key == Qt.Key.Key_Home:
            return values[0]
        if key == Qt.Key.Key_End:
            return values[-1]
        step = self._STEP_KEYS.get(key)
        current = next((v for v, b in self._buttons.items() if b is focused), None)
        if step is None or current not in values:
            return None
        return values[(values.index(current) + step) % len(values)]

    def _on_click(self, value: str):
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)
        self.activated.emit(value)

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
    Its accessible name follows the tooltip (see :func:`accessible_label`)
    unless a caller sets one explicitly.
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
        self._derived_name = ""
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

    def event(self, event):
        if event.type() == QEvent.Type.ToolTipChange:
            self._sync_accessible_name()
        return super().event(event)

    def _sync_accessible_name(self) -> None:
        """Name the button after its tooltip, never over a name set by a caller."""
        name = accessible_label(self.toolTip())
        if self.accessibleName() in ("", self._derived_name):
            self.setAccessibleName(name)
        self._derived_name = name

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
        {tokens.focus_rule("QToolButton#iconBtn")}
        """

    def _refresh_icon(self):
        col = self._on_color if self.isChecked() else self._color
        self.setIcon(load_icon(self._icon_name, col, self._icon_px))

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
    background: {tokens.TEXT}; border: 2px solid {tokens.BG_1}; width: 10px; height: 10px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {tokens.TEXT}; border-color: {tokens.BG_3}; }}
QSlider::handle:horizontal:focus {{ border-color: {tokens.FOCUS}; }}
QLabel#dockSliderValue {{
    color: {tokens.TEXT}; background: transparent;
    font-family: {tokens.MONO}; font-size: 12px;
}}
"""


class DockSlider(QWidget):
    """Icon + filled horizontal slider + mono value readout, for floating docks.

    ``fmt`` formats the readout from the integer value (e.g. ``"{} px"``).
    The inner :class:`QSlider` is exposed as ``.slider`` so callers keep using
    the standard ``valueChanged`` / ``setValue`` API; its accessible name follows
    this control's tooltip. In compact mode (a narrow canvas, see
    :meth:`set_compact`) the slider is shorter and the readout hidden.
    """

    COMPACT_SLIDER_WIDTH = 64

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
        self._width = width
        self._compact_width = min(width, self.COMPACT_SLIDER_WIDTH)
        self._compact = False
        self.setStyleSheet(_DOCK_SLIDER_QSS)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(8)
        self._row = row

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
        self.setToolTip(tooltip)  # after the slider exists: it names the slider

    def event(self, event):
        if event.type() == QEvent.Type.ToolTipChange:
            self.slider.setAccessibleName(accessible_label(self.toolTip()))
            self.slider.setAccessibleDescription(self.toolTip())
        return super().event(event)

    def _sync_label(self, value: int):
        self.value_label.setText(self._fmt.format(value))

    def set_compact(self, compact: bool) -> None:
        """Shorter slider without the value readout (``True``), or the full control."""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.slider.setFixedWidth(self._compact_width if compact else self._width)
        self.value_label.setVisible(not compact)
        self.updateGeometry()  # re-measure the dock that holds this control now

    def is_compact(self) -> bool:
        return self._compact

    def compact_savings(self) -> int:
        """Pixels of width this control gives up in compact mode."""
        label = self.value_label
        readout = label.sizeHint().expandedTo(label.minimumSize()).width()
        return self._width - self._compact_width + self._row.spacing() + readout

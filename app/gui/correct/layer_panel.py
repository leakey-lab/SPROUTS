"""The Layers panel of the Correct page: :class:`LayerPanel` (plan contract C20).

One row per edit layer, the mask on top (spec section 3.2): the layer's key (1 / 2), a
swatch in its colour, its name, an eye for its visibility and, below the name, its save
state as a dot and a label in the status chip's colours ("Saved", "Unsaved", "Not saved",
or the layer's own label such as "No mask", "Prediction" or "Loaded file"). The active row
has a raised fill and a 2 px bar in its layer's colour; a row can flash briefly (the editor
flashes the other layer's row when Ctrl+Z undoes one of that layer's actions).

A pure view: a click on a row asks for that layer (:attr:`LayerPanel.activate_requested`)
and a click on an eye for the other visibility (:attr:`LayerPanel.visibility_toggled`); the
editor answers with :meth:`LayerPanel.set_active` and :meth:`LayerPanel.set_visible_state`.
A click never takes the keyboard focus, so the canvas keeps its hold keys (Space, B); Tab
reaches each row and then its eye (W18), the focused one drawn with the focus ring: Space or
Enter on a row asks for its layer, Space on an eye toggles it, the page's keys still work.
The editor's ``OverlayLayout`` places it in the ``side_panel`` role (right edge) through
:meth:`LayerPanel.preferred_height` and :meth:`LayerPanel.fit_height`. It follows the GT
class panel's look (``app.gui.class_panel``).
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QWidget

from app.gui import shortcuts
from app.gui.correct.layer import LAYER_IDS, EditLayer, LayerStatus
from app.gui.widgets import IconButton, keep_canvas_focus, tokens
from app.gui.widgets.icons import load_icon
from app.gui.widgets.overlays import StatusChip, _FloatingPanel

__all__ = ["LayerPanel", "LayerRow"]

_MARGIN = 14  # standalone placement (no OverlayLayout): distance from the host's right edge
_EYE_PX = 15
_DOT = "●"
_BAR = 2.0  # the active row's bar, px
_RADIUS = 4.0
_FLASH_ALPHA = 0.22
# The row's own label for a state, when the layer gives none (the chip's colours).
_STATE_LABELS = {"clean": "Saved", "dirty": "Unsaved", "error": "Not saved", "none": ""}
_EYE_QSS = f"""
QToolButton#iconBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QToolButton#iconBtn:hover {{ background-color: {tokens.BG_3}; }}
{tokens.focus_rule("QToolButton#iconBtn")}
"""
# Keys that ask for the focused row's layer, as a click does (a button's Space / Enter).
_ACTIVATE_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Select)


@lru_cache(maxsize=4)
def _struck_eye(color: str) -> QIcon:
    """The eye in ``color`` struck through (the layer is hidden), sharp at every scale."""
    base = load_icon("eye", color, _EYE_PX)
    scales = {1.0, 2.0} | {screen.devicePixelRatio() for screen in QGuiApplication.screens()}
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    icon = QIcon()
    for scale in sorted(scales):
        pixmap = base.pixmap(QSize(_EYE_PX, _EYE_PX), scale)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.drawLine(QPointF(2.0, 2.0), QPointF(_EYE_PX - 2.0, _EYE_PX - 2.0))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class _EyeButton(IconButton):
    """A row's visibility toggle: checked = shown (the eye), unchecked = hidden (struck)."""

    def __init__(self, noun: str) -> None:
        super().__init__("eye", size=22, icon_px=_EYE_PX, checkable=True)
        self._noun = noun
        self.toggled.connect(self._retip)
        self.setChecked(True)  # shown; toggled draws the eye and names the action

    def _qss(self) -> str:  # IconButton hook: no checked pill, the icon says shown / hidden
        return _EYE_QSS

    def _refresh_icon(self) -> None:  # IconButton hook: the icon of the checked state
        shown = self.isChecked()
        self.setIcon(
            load_icon("eye", tokens.TEXT_MUTED, _EYE_PX)
            if shown
            else _struck_eye(tokens.TEXT_FAINT)
        )

    def _retip(self, shown: bool) -> None:
        keys = [
            " / ".join(shortcuts.keys_for(action, shortcuts.CORRECT))
            for action in ("toggle_layers", "toggle_reference")
        ]
        self.setToolTip(
            f"{'Hide' if shown else 'Show'} the {self._noun} "
            f"({keys[0]}: both layers, {keys[1]}: the other one)"
        )


def _label(text: str, css: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(f"{css} background: transparent; border: none;")
    return label


class LayerRow(QFrame):
    """One row of the Layers panel: key, swatch, name and eye; the save state below the name.

    ``key_label``, ``swatch``, ``name_label``, ``eye_button``, ``state_dot`` and
    ``state_label`` are its parts. The active fill, its bar, the flash and the focus ring are
    painted rather than styled, so a layer switch or a flash re-polishes nothing. Tab (never a
    click) focuses the row; Space or Enter then asks for its layer.
    """

    FLASH_MS = 300
    activated = pyqtSignal(str)  # the row's layer id: a click anywhere but on the eye
    visibility_toggled = pyqtSignal(str, bool)  # (layer id, shown) after an eye click

    def __init__(self, layer: EditLayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layer_id = layer.layer_id
        self._fill = QColor(tokens.BG_2)
        self._bar = QColor(layer.color)
        self._flash = QColor(layer.color)
        self._flash.setAlphaF(_FLASH_ALPHA)
        self._active = self._flashing = False
        self._state_colour = ""
        self._ring = QColor(tokens.FOCUS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # Tab only: a click keeps the canvas keys
        action = f"{layer.layer_id}_layer"  # the registry's layer keys: 1 = mask, 2 = skeleton
        self.setToolTip(shortcuts.tooltip(f"Edit the {layer.noun}", action, shortcuts.CORRECT))

        keys = shortcuts.keys_for(action, shortcuts.CORRECT)
        mono = f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; font-size: 11px;"
        self.key_label = _label(keys[0] if keys else "", mono)
        self.key_label.setFixedWidth(12)
        self.swatch = QLabel()
        self.swatch.setFixedSize(12, 12)
        self.swatch.setStyleSheet(
            f"background-color: {layer.color}; border-radius: 2px; border: none;"
        )
        name = f"{layer.noun[:1].upper()}{layer.noun[1:]}"
        self.setAccessibleName(f"{name} layer")
        self.name_label = _label(name, f"color: {tokens.TEXT}; font-size: 12.5px;")
        self.eye_button = _EyeButton(layer.noun)
        self.eye_button.clicked.connect(self._eye_clicked)
        self.state_dot = _label("", "")
        self.state_label = _label("", "")

        grid = QGridLayout(self)
        grid.setContentsMargins(8, 4, 6, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)
        grid.addWidget(self.key_label, 0, 0)
        grid.addWidget(self.swatch, 0, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.name_label, 0, 2)
        grid.addWidget(self.eye_button, 0, 3)
        grid.addWidget(self.state_dot, 1, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.state_label, 1, 2, 1, 2)  # runs on under the eye
        grid.setColumnStretch(2, 1)

        self._flash_timer = QTimer(self, singleShot=True, interval=self.FLASH_MS)
        self._flash_timer.timeout.connect(self._end_flash)
        self.set_status(LayerStatus("none"))
        self.state_label.ensurePolished()  # the line keeps its height while it is empty
        self.state_label.setMinimumHeight(self.state_label.fontMetrics().height())

    def set_status(self, status: LayerStatus) -> bool:
        """Show ``status``: its label, else the state's ("Saved", "Unsaved", "Not saved" or
        nothing), dot and label in the status chip's colour of the state, ``status.tooltip``
        as their tooltip. True when the text changed (the row may need another width)."""
        try:
            colour, chip_label = StatusChip.STATES[status.state]
        except KeyError:
            raise ValueError(f"unknown layer state {status.state!r}") from None
        text = (
            status.label
            if status.label is not None
            else _STATE_LABELS.get(status.state, chip_label)
        )
        if colour != self._state_colour:  # restyle only when the colour changes
            self._state_colour = colour
            css = f"color: {colour}; font-size: 11px; background: transparent; border: none;"
            self.state_dot.setStyleSheet(css)
            self.state_label.setStyleSheet(css)
        changed = text != self.state_label.text()
        if changed:
            self.state_label.setText(text)
            self.state_dot.setText(_DOT if text else "")
            self.setAccessibleDescription(text)  # what a screen reader adds to the row's name
            # The label only invalidates this row's layout; the panel's would read the row's
            # old size hint until a posted LayoutRequest. Tell it now, so it can re-measure.
            self.updateGeometry()
        if status.tooltip != self.state_label.toolTip():
            self.state_label.setToolTip(status.tooltip)
            self.state_dot.setToolTip(status.tooltip)
        return changed

    def set_active(self, active: bool) -> None:
        """The edited layer's row (the fill and the bar) or a reference row (plain)."""
        if active != self._active:
            self._active = active
            self.update()

    def is_active(self) -> bool:
        """True while the row is drawn as the edited layer's."""
        return self._active

    def set_visible_state(self, visible: bool) -> None:
        """Show the eye (``visible``) or the struck-through eye; emits nothing."""
        self.eye_button.setChecked(visible)

    def flash(self) -> None:
        """Tint the row in its layer's colour for ``FLASH_MS`` (a new flash restarts it)."""
        self._flashing = True
        self._flash_timer.start()
        self.update()

    def is_flashing(self) -> bool:
        """True while a flash is showing."""
        return self._flashing

    def _end_flash(self) -> None:
        self._flashing = False
        self.update()

    def _eye_clicked(self, shown: bool) -> None:
        self.visibility_toggled.emit(self.layer_id, shown)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        """The active fill and bar, the flash tint and the focus ring, under the row's labels
        (Qt repaints the row when it takes or loses the focus)."""
        focused = self.hasFocus()
        if not (self._active or self._flashing or focused):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), _RADIUS, _RADIUS)
        if self._active:
            painter.fillPath(shape, self._fill)
        if self._flashing:
            painter.fillPath(shape, self._flash)
        if self._active:
            painter.save()
            painter.setClipPath(shape)
            painter.fillRect(QRectF(0.0, 0.0, _BAR, float(self.height())), self._bar)
            painter.restore()
        if focused:  # 1 px inside the edge, as the focus_rule borders of the other controls
            painter.setPen(QPen(self._ring, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS
            )
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """A left click anywhere on the row (the eye excepted) asks for its layer."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.layer_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Space or Enter on the focused row asks for its layer, as a click does."""
        if event.key() in _ACTIVATE_KEYS:
            self.activated.emit(self.layer_id)
            event.accept()
            return
        super().keyPressEvent(event)


def _panel_order(layer: EditLayer) -> int:
    """Rows follow LAYER_IDS (mask on top); other layers come after, in the given order."""
    return LAYER_IDS.index(layer.layer_id) if layer.layer_id in LAYER_IDS else len(LAYER_IDS)


class LayerPanel(_FloatingPanel):
    """The Layers panel (OverlayLayout role side_panel): one row per layer, mask on top.
    A click never takes the focus; Tab reaches each row, then its eye.

    ``rows`` maps each layer id to its :class:`LayerRow`, top to bottom. The panel is at
    least ``MIN_WIDTH`` px wide and grows when a state label needs more room.
    """

    MIN_WIDTH = 155
    activate_requested = pyqtSignal(str)  # a row was clicked
    visibility_toggled = pyqtSignal(str, bool)  # an eye was clicked: (layer_id, visible)

    def __init__(self, layers: Sequence[EditLayer], parent: QWidget | None = None) -> None:
        super().__init__(parent, horizontal=False)
        self.setObjectName("layerPanel")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.rows: dict[str, LayerRow] = {}
        for layer in sorted(layers, key=_panel_order):
            row = LayerRow(layer)
            row.activated.connect(self.activate_requested)
            row.visibility_toggled.connect(self.visibility_toggled)
            self.add_widget(row)
            self.rows[layer.layer_id] = row
        keep_canvas_focus(self)  # rows, eyes: Tab reaches them, a click keeps the canvas keys
        if self.rows:
            self.set_active(next(iter(self.rows)))

    def set_status(self, layer_id: str, status: LayerStatus) -> None:
        """State dot + label + tooltip of ``layer_id``'s row (see :meth:`LayerRow.set_status`)."""
        if self.rows[layer_id].set_status(status) and self.isVisible():
            wanted = max(self.minimumWidth(), self.sizeHint().width())
            if wanted != self.width():
                self.reposition()  # the layout re-measures and places the panel

    def set_active(self, layer_id: str) -> None:
        """Mark ``layer_id``'s row as the edited layer's: BG_2 fill + 2 px bar in its colour."""
        self.rows[layer_id]  # an unknown id is an error, not "no active row"
        for row_id, row in self.rows.items():
            row.set_active(row_id == layer_id)

    def set_visible_state(self, layer_id: str, visible: bool) -> None:
        """Show ``layer_id``'s eye open (``visible``) or struck through; emits nothing."""
        self.rows[layer_id].set_visible_state(visible)

    def flash(self, layer_id: str) -> None:
        """Highlight ``layer_id``'s row for about 300 ms (an undo of the other layer)."""
        self.rows[layer_id].flash()

    def preferred_height(self, host_height: int) -> int:
        """The height that shows every row (OverlayLayout hook); two rows always fit."""
        return self.sizeHint().height()

    def fit_height(self, height: int) -> None:
        """Take ``height`` px (OverlayLayout hook), never more than the rows need."""
        self.setFixedHeight(max(0, min(int(height), self.sizeHint().height())))

    def _place(self) -> None:
        """Standalone placement (no OverlayLayout): right edge, vertically centred."""
        self.adjustSize()
        host = self.parentWidget()
        x = host.width() - self.width() - _MARGIN
        self.move(x, max(0, (host.height() - self.height()) // 2))

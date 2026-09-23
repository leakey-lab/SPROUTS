"""Floating class panel overlay for GT tracing (spec Sec 3.3).

A vertical floating panel, pinned to the right edge of the host canvas and
vertically centred, showing one row per active class in registry order:
a number-key hint, a colour swatch, the class name and (for every class but
Ignore) a "reviewed" checkbox. Clicking a row selects it as the active paint
class; the gear button opens the class editor dialog.

No part of the panel takes keyboard focus: a click on a row, checkbox or
the gear leaves focus on the canvas, so Space still pans and can never toggle
a "checked" box (spec §3.4: a flipped review flag silently changes the
dataset).

This module never touches ``app/gui/widgets/*`` — it only consumes the
existing ``_FloatingPanel``/``IconButton`` primitives.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gt.registry import ClassDef, Registry
from app.gui.widgets import tokens
from app.gui.widgets.controls import IconButton
from app.gui.widgets.overlays import _FloatingPanel

_MARGIN = 14
_MAX_HEIGHT_FRACTION = 0.6
_REVIEWED_TOOLTIP = "Checked: this class was looked for on this image"


def _no_focus(*widgets: QWidget) -> None:
    for w in widgets:
        w.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class _ClassRow(QFrame):
    """One row of the class panel.

    Clicking anywhere in the row except the checkbox emits ``activated``.
    The checkbox emits ``reviewed_toggled`` only for user-driven toggles
    (``set_reviewed`` updates it silently).
    """

    activated = pyqtSignal()
    reviewed_toggled = pyqtSignal(bool)

    def __init__(self, class_def: ClassDef, number: int | None, parent=None):
        super().__init__(parent)
        self.key = class_def.key
        self.setObjectName("classRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        _no_focus(self)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)

        hint = "0" if class_def.is_ignore else ("" if number is None else str(number))
        self._number_label = QLabel(hint)
        self._number_label.setFixedWidth(12)
        self._number_label.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; "
            "font-size: 11px; background: transparent; border: none;"
        )
        row.addWidget(self._number_label)

        self._swatch = QLabel()
        self._swatch.setFixedSize(12, 12)
        row.addWidget(self._swatch)

        self._name_label = QLabel()
        self._name_label.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 12.5px; background: transparent; border: none;"
        )
        row.addWidget(self._name_label, 1)

        self.checkbox: QCheckBox | None = None
        if not class_def.is_ignore:
            self.checkbox = QCheckBox()
            self.checkbox.setToolTip(_REVIEWED_TOOLTIP)
            _no_focus(self.checkbox)
            self.checkbox.toggled.connect(self._on_checkbox_toggled)
            row.addWidget(self.checkbox)

        self._suppress_toggle = False
        self._active = False
        self.update_class(class_def)
        self._apply_style()

    def update_class(self, class_def: ClassDef) -> None:
        self._name_label.setText(class_def.name)
        self._swatch.setStyleSheet(
            f"background-color: {class_def.color}; border-radius: 2px; border: none;"
        )

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        self._apply_style()

    def set_reviewed(self, checked: bool, locked: bool) -> None:
        if self.checkbox is None:
            return
        self._suppress_toggle = True
        self.checkbox.setChecked(checked)
        self.checkbox.setEnabled(not locked)
        self._suppress_toggle = False

    def _apply_style(self) -> None:
        if self._active:
            self.setStyleSheet(
                f"QFrame#classRow {{ background-color: {tokens.SEL_SOFT}; "
                f"border-left: 2px solid {tokens.SEL}; border-radius: 4px; }}"
            )
        else:
            self.setStyleSheet(
                "QFrame#classRow { background: transparent; "
                "border-left: 2px solid transparent; border-radius: 4px; }"
            )

    def _on_checkbox_toggled(self, checked: bool) -> None:
        if self._suppress_toggle:
            return
        self.reviewed_toggled.emit(checked)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)


class ClassPanel(_FloatingPanel):
    """Floating class panel, right edge of the host, vertically centred."""

    active_changed = pyqtSignal(str)
    reviewed_changed = pyqtSignal(str, bool)
    edit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, horizontal=False)
        self.setObjectName("classPanel")

        header = QWidget(self)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(6, 2, 2, 2)
        header_row.setSpacing(6)
        title = QLabel("Classes")
        title.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: 11.5px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        header_row.addWidget(title, 1)
        self._gear = IconButton("settings", "Edit classes")
        self._gear.clicked.connect(self.edit_requested.emit)
        header_row.addWidget(self._gear)
        self.add_widget(header)
        self._header = header

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self.add_widget(self._scroll)

        self._rows_container = QWidget()
        self._rows_container.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_container)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        # A click falls through to the nearest focusable ancestor, so every
        # layer (not only the checkbox) must refuse focus.
        _no_focus(self, header, self._gear, self._scroll, self._scroll.viewport())
        _no_focus(self._rows_container)

        self._registry: Registry | None = None
        self._rows: dict[str, _ClassRow] = {}
        self._active_key: str | None = None

    # ------------------------------------------------------------------ #
    def set_registry(self, registry: Registry) -> None:
        """Rebuild rows from ``registry.active()``, in registry order."""
        self._registry = registry

        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # Off-screen and out of the tree now: deleteLater alone leaves
                # the old row visible (and findable) until the event loop runs.
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()

        active_classes = registry.active()
        labelled_keys = [c.key for c in active_classes if not c.is_ignore]
        number_for_key = {key: i + 1 for i, key in enumerate(labelled_keys[:9])}

        for class_def in active_classes:
            number = number_for_key.get(class_def.key)
            row = _ClassRow(class_def, number)
            row.activated.connect(lambda k=class_def.key: self._handle_row_activated(k))
            row.reviewed_toggled.connect(
                lambda checked, k=class_def.key: self.reviewed_changed.emit(k, checked)
            )
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows[class_def.key] = row

        if self._active_key is not None and self._active_key in self._rows:
            self._rows[self._active_key].set_active(True)

    def set_active(self, key: str) -> None:
        """Highlight ``key``'s row. Never emits ``active_changed``."""
        if self._active_key == key:
            return
        if self._active_key is not None and self._active_key in self._rows:
            self._rows[self._active_key].set_active(False)
        self._active_key = key
        if key in self._rows:
            self._rows[key].set_active(True)

    def active_key(self) -> str:
        return self._active_key

    def set_reviewed(self, reviewed: set[str], locked: set[str]) -> None:
        """Checkbox checked for keys in ``reviewed|locked``; disabled for ``locked``."""
        for key, row in self._rows.items():
            row.set_reviewed(key in reviewed or key in locked, key in locked)

    def key_for_shortcut(self, n: int) -> str | None:
        """``n=1..9`` -> n-th labelled class key; ``0`` -> ``"ignore"``."""
        if self._registry is None:
            return None
        if n == 0:
            return "ignore"
        labelled = self._registry.labelled()
        if 1 <= n <= len(labelled):
            return labelled[n - 1].key
        return None

    def reposition(self) -> None:
        if self.parent() is None:
            return
        # NOTE: QScrollArea.sizeHint() caches the contained widget's size the
        # first time it is computed and does not reliably invalidate that
        # cache without a real show/resize event cycle (which this in-window
        # overlay never gets while offscreen/headless). Measuring
        # ``_rows_container``'s own sizeHint() directly sidesteps that stale
        # cache so the panel shrinks correctly when the registry shrinks
        # (e.g. after retiring classes), not just when it grows.
        self.adjustSize()  # settles the natural width from the header row
        parent_width = self.parent().width()
        parent_height = self.parent().height()
        max_height = int(parent_height * _MAX_HEIGHT_FRACTION)

        margins = self._box.contentsMargins()
        chrome_height = margins.top() + margins.bottom()
        chrome_height += self._box.spacing()
        chrome_height += self._header.sizeHint().height()
        content_height = self._rows_container.sizeHint().height()
        scroll_height = min(content_height, max(0, max_height - chrome_height))
        self._scroll.setFixedHeight(scroll_height)
        self.resize(self.width(), chrome_height + scroll_height)

        x = parent_width - self.width() - _MARGIN
        y = max(0, (parent_height - self.height()) // 2)
        self.move(x, y)

    def _handle_row_activated(self, key: str) -> None:
        self.set_active(key)
        self.active_changed.emit(key)

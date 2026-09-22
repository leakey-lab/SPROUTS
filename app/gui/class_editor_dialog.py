"""Class editor dialog for GT tracing (spec Sec 3.6).

Modal dialog over a :class:`~app.gt.registry.Registry`: add (name, key
auto-slugged from the name and editable, colour picker), rename, recolour and
retire (confirmed) a class. Id and key are read-only for existing classes;
Ignore has no retire button. Every edit is validated by rebuilding a
candidate ``Registry`` via ``add``/``update``/``retire``; a ``RegistryError``
is shown inline and disables OK. :meth:`ClassEditorDialog.result_registry`
returns the accepted registry, or ``None`` if the dialog was cancelled or the
registry is unchanged from the one it was constructed with.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gt.registry import ClassDef, Registry, RegistryError, slugify_key
from app.gui.widgets import tokens

_DEFAULT_ADD_COLOR = "#4c9bd6"


class _EditorRow(QWidget):
    """One editable row: read-only id/key, name field, swatch, retire button."""

    def __init__(self, class_def: ClassDef, parent=None):
        super().__init__(parent)
        self.key = class_def.key

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        id_label = QLabel(str(class_def.id))
        id_label.setFixedWidth(28)
        id_label.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; "
            "font-size: 11px; background: transparent;"
        )
        row.addWidget(id_label)

        key_label = QLabel(class_def.key)
        key_label.setFixedWidth(90)
        key_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-family: {tokens.MONO}; "
            "font-size: 11px; background: transparent;"
        )
        row.addWidget(key_label)

        self.swatch_button = QPushButton()
        self.swatch_button.setFixedSize(20, 20)
        self.swatch_button.setToolTip("Change colour")
        self.set_swatch_color(class_def.color)
        row.addWidget(self.swatch_button)

        self.name_edit = QLineEdit(class_def.name)
        row.addWidget(self.name_edit, 1)

        self.retire_button: QPushButton | None = None
        if not class_def.is_ignore:
            self.retire_button = QPushButton("Retire")
            row.addWidget(self.retire_button)

    def set_swatch_color(self, hex_color: str) -> None:
        self.swatch_button.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid {tokens.BORDER_STRONG}; "
            "border-radius: 4px;"
        )


class ClassEditorDialog(QDialog):
    """Modal editor over a class ``Registry``."""

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit classes")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background-color: {tokens.BG_1}; }}")

        self._original = registry
        self._candidate = registry
        self._accepted_registry: Registry | None = None
        self._error: str | None = None
        self._rows: dict[str, _EditorRow] = {}
        self._key_auto = True
        self._add_color = _DEFAULT_ADD_COLOR

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        heading = QLabel("Classes")
        heading.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        outer.addWidget(heading)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        outer.addWidget(self._rows_container)

        outer.addWidget(self._build_add_form())

        self._error_label = QLabel("")
        self._error_label.setStyleSheet(
            f"color: {tokens.DANGER}; font-size: 11.5px; background: transparent;"
        )
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        outer.addWidget(self._button_box)

        self._rebuild_rows()

    # ------------------------------------------------------------------ #
    #  Add form
    # ------------------------------------------------------------------ #
    def _build_add_form(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("addForm")
        frame.setStyleSheet(
            f"QFrame#addForm {{ background-color: {tokens.BG_2}; "
            f"border: 1px solid {tokens.BORDER}; border-radius: 8px; }}"
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        self._add_name_edit = QLineEdit()
        self._add_name_edit.setPlaceholderText("New class name")
        self._add_name_edit.textChanged.connect(self._on_add_name_changed)
        row.addWidget(self._add_name_edit, 2)

        self._add_key_edit = QLineEdit()
        self._add_key_edit.setPlaceholderText("key")
        self._add_key_edit.textEdited.connect(self._on_add_key_edited)
        row.addWidget(self._add_key_edit, 1)

        self._add_swatch = QPushButton()
        self._add_swatch.setFixedSize(22, 22)
        self._add_swatch.setToolTip("Choose colour")
        self._add_swatch.clicked.connect(self._on_add_swatch_clicked)
        row.addWidget(self._add_swatch)

        self._add_button = QPushButton("Add")
        self._add_button.clicked.connect(self._on_add_clicked)
        row.addWidget(self._add_button)

        self._set_add_color(self._add_color)
        return frame

    def _on_add_name_changed(self, text: str) -> None:
        if self._key_auto:
            self._add_key_edit.setText(slugify_key(text))

    def _on_add_key_edited(self, _text: str) -> None:
        self._key_auto = False

    def _set_add_color(self, hex_color: str) -> None:
        """Set the pending add-form colour without opening ``QColorDialog``."""
        self._add_color = hex_color
        self._add_swatch.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid {tokens.BORDER_STRONG}; "
            "border-radius: 4px;"
        )

    def _on_add_swatch_clicked(self) -> None:
        color = QColorDialog.getColor(QColor(self._add_color), self, "Choose colour")
        if not color.isValid():
            return
        self._set_add_color(color.name())

    def _on_add_clicked(self) -> None:
        name = self._add_name_edit.text().strip()
        key = self._add_key_edit.text().strip()
        try:
            new_registry = self._candidate.add(key=key, name=name, color=self._add_color)
        except RegistryError as exc:
            self._set_error(str(exc))
            return
        self._apply_candidate(new_registry)
        self._reset_add_form()

    def _reset_add_form(self) -> None:
        self._key_auto = True
        self._add_name_edit.blockSignals(True)
        self._add_name_edit.clear()
        self._add_name_edit.blockSignals(False)
        self._add_key_edit.blockSignals(True)
        self._add_key_edit.clear()
        self._add_key_edit.blockSignals(False)
        self._set_add_color(_DEFAULT_ADD_COLOR)

    # ------------------------------------------------------------------ #
    #  Existing-class rows
    # ------------------------------------------------------------------ #
    def _rebuild_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()

        for class_def in self._candidate.active():
            row = _EditorRow(class_def)
            row.name_edit.editingFinished.connect(
                lambda k=class_def.key: self._on_name_committed(k)
            )
            row.swatch_button.clicked.connect(
                lambda _checked=False, k=class_def.key: self._on_swatch_clicked(k)
            )
            if row.retire_button is not None:
                row.retire_button.clicked.connect(
                    lambda _checked=False, k=class_def.key: self._on_retire_clicked(k)
                )
            self._rows_layout.addWidget(row)
            self._rows[class_def.key] = row

    def _on_name_committed(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        new_name = row.name_edit.text().strip()
        if new_name == self._candidate.by_key(key).name:
            return
        try:
            new_registry = self._candidate.update(key=key, name=new_name)
        except RegistryError as exc:
            self._set_error(str(exc))
            return
        self._candidate = new_registry
        self._set_error(None)

    def _on_swatch_clicked(self, key: str) -> None:
        current = self._candidate.by_key(key).color
        color = QColorDialog.getColor(QColor(current), self, "Choose colour")
        if not color.isValid():
            return
        self._apply_color(key, color.name())

    def _apply_color(self, key: str, hex_color: str) -> None:
        """Apply ``hex_color`` to ``key`` without opening ``QColorDialog``."""
        try:
            new_registry = self._candidate.update(key=key, color=hex_color)
        except RegistryError as exc:
            self._set_error(str(exc))
            return
        self._candidate = new_registry
        self._set_error(None)
        row = self._rows.get(key)
        if row is not None:
            row.set_swatch_color(hex_color)

    def _on_retire_clicked(self, key: str) -> None:
        class_def = self._candidate.by_key(key)
        reply = QMessageBox.question(
            self,
            "Retire class",
            f'Retire "{class_def.name}"? Existing pixels keep their id; the class is '
            "hidden here and exported as Ignore.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            new_registry = self._candidate.retire(key)
        except RegistryError as exc:
            self._set_error(str(exc))
            return
        self._apply_candidate(new_registry)

    # ------------------------------------------------------------------ #
    def _apply_candidate(self, new_registry: Registry) -> None:
        self._candidate = new_registry
        self._set_error(None)
        self._rebuild_rows()

    def _set_error(self, message: str | None) -> None:
        self._error = message
        self._error_label.setText(message or "")
        self._error_label.setVisible(bool(message))
        ok_button = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(message is None)

    def _on_accept(self) -> None:
        if self._error is not None:
            return
        if self._candidate != self._original:
            self._accepted_registry = self._candidate
        else:
            self._accepted_registry = None
        self.accept()

    def result_registry(self) -> Registry | None:
        """The accepted registry, or ``None`` if cancelled or unchanged."""
        return self._accepted_registry

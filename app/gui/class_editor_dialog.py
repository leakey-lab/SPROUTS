"""Class editor dialog for GT tracing (spec Sec 3.6).

Modal dialog over a :class:`~app.gt.registry.Registry`: add (name, key
auto-slugged from the name and editable, colour picker), rename, recolour and
retire (confirmed) a class. Id and key are read-only for existing classes;
Ignore has no retire button. Every edit is validated by rebuilding a
candidate ``Registry`` via ``add``/``update``/``retire``; a ``RegistryError``
is shown inline. While a field still holds invalid input (a class name that
failed to apply, or an Add that failed) OK is disabled; the error clears as
soon as that input is fixed, reverted or abandoned. The class rows scroll
once there are many of them. :meth:`ClassEditorDialog.result_registry`
returns the accepted registry, or ``None`` if the dialog was cancelled or the
registry is unchanged from the one it was constructed with.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gt.registry import ClassDef, Registry, RegistryError, slugify_key
from app.gui.widgets import tokens

# Default colours offered for a new class, in order; the first one no class
# uses (retired included) is picked, so Add works without choosing a colour.
_ADD_PALETTE = (
    "#4c9bd6",
    "#f4a261",
    "#9b5de5",
    "#2ec4b6",
    "#e76f51",
    "#90be6d",
    "#f15bb5",
    "#00bbf9",
    "#c9a227",
    "#8d99ae",
)
# Class rows scroll beyond this height, so the dialog fits on screen.
_ROWS_MAX_HEIGHT = 360
# Side of a class row's colour swatch and of the add form's (slightly larger) one.
_SWATCH_PX = 20
_ADD_SWATCH_PX = 22
# Error sources for a refused one-off action: nothing stays pending, so they
# never block OK and are cleared by the next successful edit.
_TRANSIENT_SOURCES = ("color", "retire")


def _swatch_qss(hex_color: str) -> str:
    """Colour chip for a swatch button.

    Zero min-size and padding keep ``setFixedSize`` square: the app QSS gives every
    QPushButton ``padding: 9px 14px; min-height: 22px``, which stretched swatches to
    20x42. The focus ring is the text colour so it shows on any class colour.
    """
    return (
        f"QPushButton {{ background-color: {hex_color}; "
        f"border: 1px solid {tokens.BORDER_STRONG}; border-radius: 4px; "
        "min-height: 0; min-width: 0; padding: 0; } "
        + tokens.focus_rule("QPushButton", tokens.FOCUS_ON_FILL)
    )


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
        self.swatch_button.setFixedSize(_SWATCH_PX, _SWATCH_PX)
        self.swatch_button.setToolTip("Change colour")
        # The key is read-only, so this name never goes stale after a rename.
        self.swatch_button.setAccessibleName(f"Colour of class {class_def.key}")
        self.set_swatch_color(class_def.color)
        row.addWidget(self.swatch_button)

        self.name_edit = QLineEdit(class_def.name)
        row.addWidget(self.name_edit, 1)

        self.retire_button: QPushButton | None = None
        if not class_def.is_ignore:
            self.retire_button = QPushButton("Retire")
            row.addWidget(self.retire_button)

    def set_swatch_color(self, hex_color: str) -> None:
        self.swatch_button.setStyleSheet(_swatch_qss(hex_color))


class ClassEditorDialog(QDialog):
    """Modal editor over a class ``Registry``."""

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit classes")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._original = registry
        self._candidate = registry
        self._accepted_registry: Registry | None = None
        # Validation errors by source ("name:<key>", "add", "color", "retire");
        # ``_error`` is the newest one that blocks OK (None = OK enabled).
        self._errors: dict[str, str] = {}
        self._error: str | None = None
        self._rows: dict[str, _EditorRow] = {}
        self._key_auto = True
        self._add_color_auto = True  # False once the user picks a colour
        self._add_color = self._free_add_color()

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
        self._rows_layout.addStretch(1)
        self._rows_scroll = QScrollArea()
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_scroll.setMaximumHeight(_ROWS_MAX_HEIGHT)
        self._rows_scroll.setWidget(self._rows_container)
        # Let the dialog background show through (setWidget turns filling on).
        self._rows_container.setAutoFillBackground(False)
        self._rows_scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self._rows_scroll)

        # Created before the add form, whose handlers report into them.
        self._error_label = QLabel("")
        self._error_label.setStyleSheet(
            f"color: {tokens.DANGER}; font-size: 11.5px; background: transparent;"
        )
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)

        outer.addWidget(self._build_add_form())
        outer.addWidget(self._error_label)
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
        self._add_swatch.setFixedSize(_ADD_SWATCH_PX, _ADD_SWATCH_PX)
        self._add_swatch.setToolTip("Choose colour")
        self._add_swatch.setAccessibleName("Colour of the new class")
        self._add_swatch.clicked.connect(self._on_add_swatch_clicked)
        row.addWidget(self._add_swatch)

        self._add_button = QPushButton("Add")
        self._add_button.clicked.connect(self._on_add_clicked)
        row.addWidget(self._add_button)

        self._show_add_color(self._add_color)
        return frame

    def _free_add_color(self) -> str:
        """A colour no class uses (retired included), for the add form's default."""
        used = {c.color.lower() for c in self._candidate.classes}
        for color in _ADD_PALETTE:
            if color not in used:
                return color
        # Palette exhausted: sweep hues (47 is coprime with 360, so 360 distinct).
        for i in range(360 * 3):
            color = QColor.fromHsv((i * 47) % 360, 170 - 40 * (i // 360), 220).name()
            if color not in used:
                return color
        return _ADD_PALETTE[0]  # unreachable: at most 255 classes exist

    def _on_add_name_changed(self, text: str) -> None:
        if self._key_auto:
            self._add_key_edit.setText(slugify_key(text))
        self._set_error("add", None)  # the failed Add's input is being changed

    def _on_add_key_edited(self, _text: str) -> None:
        self._key_auto = False
        self._set_error("add", None)

    def _show_add_color(self, hex_color: str) -> None:
        self._add_color = hex_color
        self._add_swatch.setStyleSheet(_swatch_qss(hex_color))

    def _set_add_color(self, hex_color: str) -> None:
        """Set the pending add-form colour (as if picked) without opening ``QColorDialog``."""
        self._add_color_auto = False
        self._show_add_color(hex_color)
        self._set_error("add", None)

    def _refresh_auto_add_color(self) -> None:
        """Keep an unpicked default colour off every colour the classes use."""
        if self._add_color_auto:
            self._show_add_color(self._free_add_color())

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
            self._set_error("add", str(exc))
            return
        self._errors.pop("add", None)
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
        self._add_color_auto = True
        self._refresh_auto_add_color()

    # ------------------------------------------------------------------ #
    #  Existing-class rows
    # ------------------------------------------------------------------ #
    def _rebuild_rows(self) -> None:
        while self._rows_layout.count() > 1:  # keep the trailing stretch
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # Hidden now, deleted later: this can run inside a row's own
                # Retire click, so the row must outlive the signal.
                w.hide()
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
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows[class_def.key] = row

    def _on_name_committed(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        source = f"name:{key}"
        new_name = row.name_edit.text().strip()
        if new_name == self._candidate.by_key(key).name:
            self._set_error(source, None)  # reverted to the current name
            return
        try:
            new_registry = self._candidate.update(key=key, name=new_name)
        except RegistryError as exc:
            self._set_error(source, str(exc))
            return
        self._candidate = new_registry
        self._edit_succeeded(source)

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
            self._set_error("color", str(exc))
            return
        self._candidate = new_registry
        self._edit_succeeded()
        row = self._rows.get(key)
        if row is not None:
            row.set_swatch_color(hex_color)
        self._refresh_auto_add_color()

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
            self._set_error("retire", str(exc))
            return
        self._apply_candidate(new_registry)

    # ------------------------------------------------------------------ #
    def _apply_candidate(self, new_registry: Registry) -> None:
        """Adopt ``new_registry`` and rebuild the rows from it.

        The rebuilt name fields hold the candidate's names again, so their
        errors go; a failed Add's error stays while its input is in the form.
        """
        self._candidate = new_registry
        for source in [s for s in self._errors if s.startswith("name:")]:
            del self._errors[source]
        self._edit_succeeded()
        self._rebuild_rows()
        self._refresh_auto_add_color()

    def _set_error(self, source: str, message: str | None) -> None:
        """Record (``message``) or clear (``None``) the validation error of ``source``."""
        self._errors.pop(source, None)
        if message:
            self._errors[source] = message  # newest last: it is the one shown
        self._show_errors()

    def _edit_succeeded(self, source: str | None = None) -> None:
        """An edit applied: clear its own error and any one-off action's message."""
        for s in (*_TRANSIENT_SOURCES, source):
            self._errors.pop(s, None)
        self._show_errors()

    def _show_errors(self) -> None:
        blocking = [m for s, m in self._errors.items() if s not in _TRANSIENT_SOURCES]
        self._error = blocking[-1] if blocking else None
        message = next(reversed(self._errors.values()), "")
        self._error_label.setText(message)
        self._error_label.setVisible(bool(message))
        ok_button = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(self._error is None)

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

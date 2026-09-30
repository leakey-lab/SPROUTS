"""
Discoverability widgets: an empty-state placeholder and the keyboard-shortcut
sheet.

Styled from :mod:`app.gui.widgets.tokens` (no private colour copies).

Public API
----------
- ``EmptyStateWidget(on_load, parent=None)``: a centered placeholder shown in
  the index-0 display page when no images are loaded. ``on_load`` is a 0-arg
  callable invoked when the user clicks the primary "Load Images" button
  (``MainWindow`` passes ``main_window.load_images``). ``set_loading(name)`` says
  "Loading images…" (no button) while the first folder loads; ``None`` restores it.
- ``ShortcutsDialog(parent=None, *, current_scope=None, on_about=None)``: the
  F1 sheet, generated from :data:`app.gui.shortcuts.REGISTRY` with one section
  per scope (the current stage's section first). ``on_about`` adds an
  "About SPROUTS…" button. The dialog deletes itself when closed, so
  ``ShortcutsDialog(self).exec()`` on every F1 no longer leaks a child.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.gui import shortcuts
from app.gui.widgets import tokens
from app.gui.widgets.buttons import ghost_button, primary_button
from app.gui.widgets.icons import load_pixmap

_KEYCAP_QSS = (
    f"QLabel#shortcutKeys {{ background-color: {tokens.BG_3}; color: {tokens.INFO}; "
    f"border: 1px solid {tokens.BORDER_STRONG}; border-radius: 4px; padding: 2px 8px; "
    f"font-family: {tokens.MONO}; font-weight: 600; font-size: 9pt; }}"
)
_SECTION_QSS = (
    f"QFrame#shortcutSection {{ background-color: {tokens.BG_2}; "
    f"border: 1px solid {tokens.BORDER}; border-radius: {tokens.RADIUS_SM}px; }}"
)
_KIND_HINT = {shortcuts.GESTURE: "mouse", shortcuts.KEY: ""}


class EmptyStateWidget(QWidget):
    """Centered placeholder shown when no images are loaded.

    While the first folder loads it says so (:meth:`set_loading`) instead of inviting a
    load under the loading card.
    """

    TITLE = "No images loaded"
    SUBTITLE = "Load a folder of root images to begin."
    LOADING_TITLE = "Loading images…"

    def __init__(self, on_load: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self._on_load = on_load
        self.setObjectName("emptyState")
        self.setStyleSheet(f"QWidget#emptyState {{ background-color: {tokens.BG_0}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addStretch(1)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(load_pixmap("sprouts_logo", tokens.ACCENT, 72))
        outer.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_label = title = QLabel(self.TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 12pt; font-weight: bold; margin-top: 12px;"
        )
        outer.addWidget(title)

        self.subtitle_label = subtitle = QLabel(self.SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 9pt; margin-top: 4px;")
        outer.addWidget(subtitle)

        self.load_button = primary_button(
            "Load Images",
            "load",
            tooltip=shortcuts.tooltip("Open a folder of root images", "open_folder", "Global"),
        )
        self.load_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.load_button.clicked.connect(self._handle_load)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 12, 0, 0)
        btn_row.addStretch(1)
        btn_row.addWidget(self.load_button)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        hint = QLabel("Press F1 or ? for keyboard shortcuts")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 8pt; margin-top: 8px;")
        outer.addWidget(hint)

        outer.addStretch(1)

    def set_loading(self, folder: str | None) -> None:
        """Say that ``folder`` (its name) is loading, without the Load button; ``None``
        goes back to the invitation to load a folder."""
        loading = bool(folder)
        self.title_label.setText(self.LOADING_TITLE if loading else self.TITLE)
        self.subtitle_label.setText(folder if loading else self.SUBTITLE)
        self.load_button.setVisible(not loading)

    def _handle_load(self) -> None:
        """Invoke the supplied 0-arg load callback, ignoring the clicked(bool) arg."""
        if callable(self._on_load):
            self._on_load()


class ShortcutsDialog(QDialog):
    """The F1 sheet: every registered binding, one section per scope."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_scope: str | None = None,
        on_about: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(560, 620)
        self._section_titles: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("Keyboard shortcuts")
        heading.setStyleSheet(f"color: {tokens.ACCENT}; font-size: 12pt; font-weight: bold;")
        layout.addWidget(heading)

        intro = QLabel("Global keys work everywhere; editor keys work while that editor is open.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 9pt;")
        layout.addWidget(intro)

        body = QWidget()
        body_col = QVBoxLayout(body)
        body_col.setContentsMargins(0, 0, 4, 0)
        body_col.setSpacing(12)
        for scope in self._ordered_scopes(current_scope):
            body_col.addWidget(self._build_section(scope, scope == current_scope))
        body_col.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("shortcutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        if on_about is not None:
            about = ghost_button("About SPROUTS…", "info", compact=True)
            about.clicked.connect(lambda _=False: on_about())
            footer.addWidget(about)
        footer.addStretch(1)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        button_box.rejected.connect(self.reject)
        footer.addWidget(button_box)
        layout.addLayout(footer)

    @staticmethod
    def _ordered_scopes(current_scope: str | None) -> list[str]:
        scopes = list(shortcuts.SCOPES)
        if current_scope in scopes:
            scopes.remove(current_scope)
            scopes.insert(0, current_scope)
        return scopes

    def section_titles(self) -> list[str]:
        """Section headings in display order."""
        return list(self._section_titles)

    def _build_section(self, scope: str, current: bool) -> QWidget:
        self._section_titles.append(scope)
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        title = QLabel(f"{scope}  ·  current" if current else scope)
        title.setAccessibleName(scope)
        title.setStyleSheet(
            f"color: {tokens.ACCENT if current else tokens.TEXT}; "
            f"font-size: 10pt; font-weight: 600;"
        )
        col.addWidget(title)

        frame = QFrame()
        frame.setObjectName("shortcutSection")
        frame.setStyleSheet(_SECTION_QSS + _KEYCAP_QSS)
        grid = QGridLayout(frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)
        for row, spec in enumerate(shortcuts.specs_for(scope)):
            keys = QLabel(spec.label)
            keys.setObjectName("shortcutKeys")
            keys.setAlignment(Qt.AlignmentFlag.AlignCenter)
            keys.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            hint = _KIND_HINT.get(spec.kind, "")
            desc = QLabel(f"{spec.description}  ({hint})" if hint else spec.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {tokens.TEXT}; font-size: 9pt; border: none;")
            grid.addWidget(keys, row, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(desc, row, 1)
        col.addWidget(frame)
        return wrap

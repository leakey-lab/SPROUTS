"""
Discoverability widgets: an empty-state placeholder and a shortcuts reference
dialog.

Self-contained — styled with inline QSS matching the app's Dracula palette (no
external theme dependency).

Public API
----------
- ``EmptyStateWidget(on_load, parent=None)``: a centered placeholder shown in
  the index-0 display page when no images are loaded. ``on_load`` is a 0-arg
  callable invoked when the user clicks the primary "Load Images" button
  (``MainWindow`` passes ``main_window.load_images``).
- ``ShortcutsDialog(parent=None)``: a modal dialog listing the real editor
  keyboard shortcuts. Open it from a ``QShortcut`` bound to ``F1``/``?``.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# SPROUTS refined-dark tokens (mirrors app.gui.widgets.tokens / dark_theme.qss).
_BG = "#15161c"  # --bg-0
_BG_ALT = "#21232e"  # --bg-2
_SURFACE = "#282a37"  # --bg-3
_SURFACE_HI = "#373a4c"  # --border-strong
_BORDER = "#2a2c39"  # --border
_TEXT = "#eceef5"  # --text
_TEXT_MUTED = "#9498ad"  # --text-muted
_ACCENT = "#c39af6"  # --accent (purple)
_ACCENT_PRESS = "#ab87d8"
_ACCENT_INK = "#1a1322"
_ACCENT_CYAN = "#79c0e8"  # --info (key-cap text)

_PRIMARY_BUTTON_QSS = f"""
QPushButton {{
    background-color: {_ACCENT};
    color: {_ACCENT_INK};
    border: none;
    border-radius: 7px;
    padding: 10px 24px;
    font-weight: 600;
    font-size: 10pt;
}}
QPushButton:hover {{
    background-color: {_ACCENT_PRESS};
}}
QPushButton:pressed {{
    background-color: #9a78c6;
}}
"""

_TEXT_BUTTON_QSS = f"""
QPushButton {{
    background-color: {_SURFACE};
    color: {_TEXT};
    border: 1px solid {_SURFACE_HI};
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 9pt;
}}
QPushButton:hover {{
    background-color: {_BORDER};
    border: 1px solid {_ACCENT};
}}
"""


def _icon_path(icon_name: str) -> str | None:
    """Return an absolute path to a resources/icons/<icon_name> file if present."""
    base_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    candidate = os.path.join(base_path, "resources", "icons", icon_name)
    if os.path.exists(candidate):
        return candidate
    return None


# The real editor shortcuts confirmed from mask_tracing_interface.py and
# skeleton_correction_interface.py. Format: (keys, description).
_SHORTCUTS = [
    ("B / E / F", "Brush, eraser, fill (mask tracing)"),
    ("B + wheel", "Hold B and scroll to resize the brush; [ and ] also work"),
    ("Space", "Hold to pan the canvas (middle-drag also pans)"),
    ("H", "Show or hide the mask overlay"),
    ("Ctrl+wheel", "Zoom under the cursor; Ctrl+0 fits, Ctrl+1 is 100%"),
    ("Ctrl+S", "Save the mask"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
]


class EmptyStateWidget(QWidget):
    """Centered placeholder shown when no images are loaded."""

    def __init__(self, on_load: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self._on_load = on_load
        self.setObjectName("emptyState")
        self.setStyleSheet(f"QWidget#emptyState {{ background-color: {_BG}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addStretch(1)

        # --- Icon (SPROUTS leaf logo) ---
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from app.gui.widgets import load_pixmap

            icon_label.setPixmap(load_pixmap("sprouts_logo", _ACCENT, 72))
        except Exception:
            icon_label.setText("\U0001f331")
            icon_label.setStyleSheet(f"color: {_ACCENT}; font-size: 56pt;")
        outer.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # --- Title ---
        title = QLabel("No images loaded")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {_TEXT}; font-size: 12pt; font-weight: bold; margin-top: 12px;"
        )
        outer.addWidget(title)

        # --- Subtitle ---
        subtitle = QLabel("Load a folder of root images to begin.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 9pt; margin-top: 4px;")
        outer.addWidget(subtitle)

        # --- Primary action button ---
        self.load_button = QPushButton("  Load Images")
        self.load_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_button.setStyleSheet(_PRIMARY_BUTTON_QSS)
        self.load_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        try:
            from app.gui.widgets import load_icon

            self.load_button.setIcon(load_icon("load", _ACCENT_INK, 17))
        except Exception:
            pass
        self.load_button.clicked.connect(self._handle_load)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 12, 0, 0)
        btn_row.addStretch(1)
        btn_row.addWidget(self.load_button)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        # --- Shortcut hint line ---
        hint = QLabel("Press F1 or ? for keyboard shortcuts")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 8pt; margin-top: 8px;")
        outer.addWidget(hint)

        outer.addStretch(1)

    def _handle_load(self) -> None:
        """Invoke the supplied 0-arg load callback, ignoring the clicked(bool) arg."""
        if callable(self._on_load):
            self._on_load()


class ShortcutsDialog(QDialog):
    """Modal dialog listing the real editor keyboard shortcuts."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setModal(True)
        self.setStyleSheet(f"background-color: {_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        heading = QLabel("Editor Shortcuts")
        heading.setStyleSheet(f"color: {_ACCENT}; font-size: 12pt; font-weight: bold;")
        layout.addWidget(heading)

        intro = QLabel("These apply in the Mask Tracing and Skeleton Correction editors.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 9pt;")
        layout.addWidget(intro)

        grid_frame = QFrame()
        grid_frame.setStyleSheet(
            f"QFrame {{ background-color: {_BG_ALT}; "
            f"border: 1px solid {_BORDER}; border-radius: 6px; }}"
        )
        grid = QGridLayout(grid_frame)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        for row, (keys, desc) in enumerate(_SHORTCUTS):
            key_label = QLabel(keys)
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            key_label.setStyleSheet(
                f"QLabel {{ background-color: {_SURFACE}; color: {_ACCENT_CYAN}; "
                f"border: 1px solid {_SURFACE_HI}; border-radius: 4px; "
                f"padding: 2px 8px; font-family: Consolas, 'Courier New', monospace; "
                f"font-weight: bold; font-size: 9pt; }}"
            )
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"color: {_TEXT}; font-size: 9pt; border: none;")
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(desc_label, row, 1)

        layout.addWidget(grid_frame)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setStyleSheet(_TEXT_BUTTON_QSS)
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

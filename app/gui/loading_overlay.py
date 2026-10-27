"""Loading card shown while a directory is being scanned / loaded.

Thin subclass of the design-system :class:`ProgressOverlay` that adds optional
``set_filename`` / ``set_count`` detail lines (hidden while empty, so the card has no
blank area; a long file name is elided in the middle) and keeps the card
``top_offset()`` px below its parent's top edge (the main window passes its top bar's
height, so the card never covers the stages). ``start`` / ``set_progress`` / ``hide``
are inherited.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.gui.widgets import tokens
from app.gui.widgets.overlays import ProgressOverlay
from app.gui.widgets.text_fit import ElidedLabel


class LoadingOverlay(ProgressOverlay):
    """Loading progress card with filename + count detail lines."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        top_offset: Callable[[], int] | None = None,
    ):
        super().__init__(parent, width=360)
        self._top_offset = top_offset

        # Append two small detail labels under the inherited progress bar.
        detail = QVBoxLayout()
        detail.setContentsMargins(16, 0, 16, 12)
        detail.setSpacing(2)
        # A long folder name is elided in the middle (the card is 360 px wide); the
        # caller's tooltip keeps the full path.
        self._filename_label = ElidedLabel("")
        self._filename_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; background: transparent; font-size: 11px;"
        )
        self._filename_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; background: transparent; "
            f"font-family: {tokens.MONO}; font-size: 10.5px;"
        )
        detail.addWidget(self._filename_label)
        detail.addWidget(self._count_label)
        self._filename_label.hide()
        self._count_label.hide()
        self._detail = detail
        self._column.addLayout(detail)
        self._sync_details()

    def set_filename(self, name: str, tooltip: str = "") -> None:
        """The detail line (e.g. the folder being loaded; ``tooltip`` its full path)."""
        self._filename_label.setText(str(name) if name else "")
        self._filename_label.setToolTip(tooltip)
        self._sync_details()

    def set_count(self, scanned: int, total: int) -> None:
        if total:
            self._count_label.setText(f"{scanned:,} / {total:,}")
        else:
            self._count_label.setText("")
        self._sync_details()

    def reposition(self) -> None:
        """Top centre of the parent, ``top_offset()`` px further down (below the top bar)."""
        super().reposition()
        offset = self._top_offset() if self._top_offset is not None else 0
        if offset > 0:
            self.move(self.x(), self.y() + int(offset))

    def _sync_details(self) -> None:
        """Show only the detail lines that have text (the card then has no blank area)."""
        shown = False
        for label in (self._filename_label, self._count_label):
            label.setVisible(bool(label.text()))
            shown = shown or bool(label.text())
        self._detail.setContentsMargins(16, 0, 16, 12 if shown else 0)
        for layout in (self._column, self.layout()):
            layout.activate()  # the card's size follows the lines at once
        if self.isVisible():
            self.reposition()

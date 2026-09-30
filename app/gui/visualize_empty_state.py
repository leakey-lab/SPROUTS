"""The Visualize stage's page while its dashboard cannot open yet (W5).

Entering Visualize opens the highlighted dashboard. When that dashboard cannot open yet,
this page says why in place of a message box: no CSV (nothing was measured; it names the
file it looks for and offers the next step, "Go to Measure", or "Go to Generate" when
there is nothing to measure yet), or a run that rewrites its data is going (the window
opens the dashboard when the run ends). While a dashboard is being built it only says it
is opening.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.gui.widgets import tokens
from app.gui.widgets.buttons import primary_button
from app.gui.widgets.icons import load_icon, load_pixmap

__all__ = ["VisualizeEmptyState"]

#: The action button's icon per stage (the topbar's stage icons).
_STAGE_ICONS = {"Measure": "ruler", "Generate": "cpu"}


class VisualizeEmptyState(QWidget):
    """Centered title, the next step, where the CSV is expected, and a button to that step.

    Signals:
        stage_requested(str): the action button was clicked (the stage key to go to).
    """

    stage_requested = pyqtSignal(str)
    OPENING = "Opening the dashboard…"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stage: str | None = None
        self._blocked = False
        self.setObjectName("visualizeEmptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#visualizeEmptyState {{ background-color: {tokens.BG_0}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setPixmap(load_pixmap("chart", tokens.TEXT_FAINT, 48))
        outer.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 12pt; font-weight: bold; margin-top: 10px;"
        )
        outer.addWidget(self.title_label)

        self.body_label = QLabel()
        self.body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 9.5pt;")
        outer.addWidget(self.body_label)

        self.detail_label = QLabel()
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; font-size: 8pt;"
            " margin-top: 6px;"
        )
        outer.addWidget(self.detail_label)

        self.action_button = primary_button("", "ruler")
        self.action_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.action_button.clicked.connect(self._on_action)
        row = QHBoxLayout()
        row.setContentsMargins(0, 12, 0, 0)
        row.addStretch(1)
        row.addWidget(self.action_button)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.show_opening()

    def show_opening(self) -> None:
        """While a dashboard is built: one quiet line, no action."""
        self._stage = None
        self._blocked = False
        self.icon_label.setVisible(False)
        self.title_label.setText("")
        self.body_label.setText(self.OPENING)
        self.detail_label.setText("")
        self.detail_label.setVisible(False)
        self.action_button.setVisible(False)

    def show_missing(
        self, title: str, body: str, detail: str = "", *, stage: str | None, action: str = ""
    ) -> None:
        """No CSV for the chosen dashboard: ``title``, the next step ``body``, ``detail``
        (where the CSV is expected) and a button ``action`` that asks for ``stage``."""
        self._stage = stage
        self._blocked = True
        self.icon_label.setVisible(True)
        self.title_label.setText(title)
        self.body_label.setText(body)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.action_button.setText(action)
        if stage in _STAGE_ICONS:
            self.action_button.setIcon(load_icon(_STAGE_ICONS[stage], tokens.ACCENT_INK, 16))
        self.action_button.setVisible(bool(stage and action))

    def next_stage(self) -> str | None:
        """The stage the action button goes to (None while it is hidden)."""
        return self._stage

    def is_blocked(self) -> bool:
        """True while the page says why the dashboard cannot open (not while opening)."""
        return self._blocked

    def _on_action(self) -> None:
        if self._stage:
            self.stage_requested.emit(self._stage)

"""SPROUTS welcome / get-started screen.

Presentation-only landing page shown at ``app_stack`` index 0.

* ``on_get_started`` (0-arg, wired by ``MainWindow`` to ``load_images``) opens
  the folder dialog; the Browse button calls it.
* ``on_open_recent(path)`` (wired to ``MainWindow.open_path``, dialog-free)
  opens a folder directly: recent-project rows and the dropzone call it. A
  dropped folder opens *that* folder; a dropped file opens its folder.
* Recent projects come from :class:`app.gui.recent_projects.RecentProjectsStore`
  (the store ``MainWindow`` records into). They sit in a scroll area showing at
  most four rows, so eight entries no longer raise the window's minimum height.
  Folders that no longer exist are checked off the GUI thread (network paths
  can block), dimmed with "Folder not found" and can be removed.
* Stage chips are the ribbon's stages (``app.gui.shell_chrome._STAGES``) and
  the footer shows ``app.__version__`` and the model files — no hardcoded
  device or version claims.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, __version__
from app.gui import shortcuts
from app.gui.recent_projects import RecentProject, RecentProjectsStore
from app.gui.widgets import tokens
from app.gui.widgets.buttons import ghost_button, primary_button
from app.gui.widgets.icons import load_icon, load_pixmap

logger = logging.getLogger(__name__)

#: Recent rows visible before the list scrolls.
VISIBLE_RECENT_ROWS = 4


def _ribbon_stage_labels() -> list[str]:
    """The 7 ribbon stage labels, in order (single source: shell_chrome)."""
    from app.gui import shell_chrome

    return [stage[1] for stage in shell_chrome._STAGES]


def _model_files() -> list[str]:
    """Checkpoint file names (no hashing, no torch)."""
    from app.models import MODELS

    return [os.path.basename(spec.relpath) for spec in MODELS.values()]


def folder_from_urls(urls: Sequence[QUrl]) -> str | None:
    """First local folder among dropped ``urls`` (a file gives its folder)."""
    for url in urls:
        if not url.isLocalFile():
            continue
        path = os.path.normpath(url.toLocalFile())
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.isdir(path):
            return path
    return None


class _Dropzone(QFrame):
    """Dashed drag-and-drop target; ``on_drop_folder(path)`` gets the dropped folder."""

    def __init__(self, on_drop_folder: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._on_drop_folder = on_drop_folder
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(130)
        self.setStyleSheet(f"""
            QFrame#dropzone {{
                background-color: {tokens.BG_2};
                border: 1.5px dashed {tokens.BORDER_STRONG};
                border-radius: {tokens.RADIUS_LG}px;
            }}
        """)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        folder = folder_from_urls(event.mimeData().urls()) if event.mimeData().hasUrls() else None
        if folder is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self._on_drop_folder(folder)


class _ElidedLabel(QLabel):
    """Single-line label that elides in the middle instead of being clipped (long paths)."""

    def minimumSizeHint(self):  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, rect.width()
        )
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, int(self.alignment()), text)


class RecentRow(QFrame):
    """One recent project: an open button plus a remove (×) button."""

    def __init__(
        self,
        entry: RecentProject,
        on_open: Callable[[str], None],
        on_remove: Callable[[str], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.entry = entry
        self._missing = False
        self.setObjectName("recentRow")
        self.setStyleSheet(
            f"QFrame#recentRow {{ background-color: {tokens.BG_2}; "
            f"border: 1px solid {tokens.BORDER}; border-radius: {tokens.RADIUS_SM}px; }}"
            f"QFrame#recentRow:hover {{ border: 1px solid {tokens.BORDER_STRONG}; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)

        self.open_button = QPushButton()
        self.open_button.setObjectName("recentOpen")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setIcon(load_icon("folder", tokens.TEXT_MUTED, 15))
        self.open_button.setAccessibleName(f"Open recent project {entry.name}")
        self.open_button.setToolTip(entry.path)
        self.open_button.setStyleSheet(
            "QPushButton#recentOpen { background: transparent; border: 1px solid transparent;"
            " text-align: left; padding: 6px 8px; }"
            f"QPushButton#recentOpen:hover {{ background-color: {tokens.BG_3}; }}"
            + tokens.focus_rule("QPushButton#recentOpen")
        )
        self.open_button.clicked.connect(lambda _=False: on_open(entry.path))

        inner = QHBoxLayout(self.open_button)
        inner.setContentsMargins(34, 4, 10, 4)
        inner.setSpacing(10)
        meta = QVBoxLayout()
        meta.setSpacing(1)
        self._name_label = self._label(entry.name, tokens.TEXT, "12.5px", bold=True)
        self._path_label = self._label(
            entry.path, tokens.TEXT_FAINT, "10.5px", mono=True, cls=_ElidedLabel
        )
        self._path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta.addWidget(self._name_label)
        meta.addWidget(self._path_label)
        inner.addLayout(meta, 1)
        self._info_label = self._label(self._info_text(entry), tokens.TEXT_MUTED, "11px")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(self._info_label)
        row.addWidget(self.open_button, 1)

        self.remove_button = QToolButton()
        self.remove_button.setObjectName("recentRemove")
        self.remove_button.setIcon(load_icon("xmark", tokens.TEXT_MUTED, 12))
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button.setToolTip("Remove from recent projects")
        self.remove_button.setAccessibleName(f"Remove {entry.name} from recent projects")
        self.remove_button.clicked.connect(lambda _=False: on_remove(entry.path))
        row.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _label(
        text: str, color: str, size: str, *, bold=False, mono=False, cls: type = QLabel
    ) -> QLabel:
        label = cls(text)
        family = f"font-family: {tokens.MONO}; " if mono else ""
        weight = "font-weight: 600; " if bold else ""
        label.setStyleSheet(
            f"color: {color}; {family}{weight}font-size: {size}; "
            "background: transparent; border: none;"
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return label

    @staticmethod
    def _info_text(entry: RecentProject) -> str:
        bits = []
        if entry.count is not None:
            bits.append(f"{entry.count} images")
        rel = WelcomeWidget._relative_date(entry.ts)
        if rel:
            bits.append(rel)
        return "  ·  ".join(bits)

    def set_missing(self, missing: bool) -> None:
        """Dim the row and say "Folder not found" when the folder is gone."""
        self._missing = bool(missing)
        self.open_button.setEnabled(not missing)
        if missing:
            self._info_label.setText("Folder not found")
            self._info_label.setStyleSheet(
                f"color: {tokens.WARN}; font-size: 11px; background: transparent; border: none;"
            )
            self.open_button.setToolTip(f"Folder not found: {self.entry.path}")
            self.remove_button.setIcon(load_icon("xmark", tokens.TEXT, 12))

    def is_missing(self) -> bool:
        return self._missing


class _ExistenceProbe(QObject):
    """Checks recent folders on a worker thread (network paths can block for seconds)."""

    checked = pyqtSignal(str, bool)

    def start(self, paths: Sequence[str], path_exists: Callable[[str], bool]) -> None:
        def run() -> None:
            for path in paths:
                try:
                    ok = bool(path_exists(path))
                except OSError:
                    ok = False
                try:
                    self.checked.emit(path, ok)
                except RuntimeError:  # the widget was deleted meanwhile
                    return

        threading.Thread(target=run, name="recent-projects-probe", daemon=True).start()


class WelcomeWidget(QWidget):
    """Get-started landing page."""

    def __init__(
        self,
        on_get_started: Callable[[], None],
        on_open_recent: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
        *,
        recent_store: RecentProjectsStore | None = None,
        stage_labels: Sequence[str] | None = None,
        on_about: Callable[[], None] | None = None,
        path_exists: Callable[[str], bool] = os.path.isdir,
        probe_async: bool = True,
    ):
        super().__init__(parent)
        self._on_get_started = on_get_started
        self._on_open_recent = on_open_recent
        self._store = recent_store if recent_store is not None else RecentProjectsStore()
        self._stage_labels = list(stage_labels or _ribbon_stage_labels())
        self._path_exists = path_exists
        self._probe_async = probe_async
        self._rows: list[RecentRow] = []
        self._probe = _ExistenceProbe()  # unparented: outlives the widget if a probe is running
        self._probe.checked.connect(self._on_exists_checked)
        self.setObjectName("welcome")
        self.setStyleSheet(f"QWidget#welcome {{ background-color: {tokens.BG_0}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 20)
        outer.setSpacing(12)
        outer.addStretch(1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(load_pixmap("sprouts_logo", tokens.ACCENT, 58))
        outer.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)

        title = QLabel(
            f'<span style="color:{tokens.TEXT}">SPR</span>'
            f'<span style="color:{tokens.ACCENT}">OU</span>'
            f'<span style="color:{tokens.TEXT}">TS</span>'
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAccessibleName(APP_NAME)
        title.setStyleSheet("font-size: 30px; font-weight: 800; letter-spacing: 2px;")
        outer.addWidget(title)

        tagline = QLabel("Plant-root phenotyping from minirhizotron images")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 13px;")
        outer.addWidget(tagline)

        outer.addWidget(self._build_dropzone())

        # Recent projects (hidden when empty).
        self.recent_section = self._build_recent_section()
        outer.addWidget(self.recent_section)

        outer.addWidget(self._build_stage_chips(), 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        outer.addLayout(self._build_footer(on_about))

        self._populate_recents()

    # ------------------------------------------------------------------ parts
    def _build_dropzone(self) -> QFrame:
        self.dropzone = _Dropzone(self._handle_drop)
        dz_box = QVBoxLayout(self.dropzone)
        dz_box.setContentsMargins(20, 16, 20, 16)
        dz_box.setSpacing(12)
        dz_box.addStretch(1)

        dz_hint = QLabel("Drop a folder of root images here")
        dz_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dz_hint.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: 12.5px; border: none; background: transparent;"
        )
        dz_box.addWidget(dz_hint)

        self.browse_button = primary_button(
            "Browse…",
            "folderOpen",
            tooltip=shortcuts.tooltip("Choose a folder of root images", "open_folder", "Global"),
        )
        self.browse_button.clicked.connect(self._handle_get_started)
        browse_row = QHBoxLayout()
        browse_row.addStretch(1)
        browse_row.addWidget(self.browse_button)
        browse_row.addStretch(1)
        dz_box.addLayout(browse_row)
        dz_box.addStretch(1)
        return self.dropzone

    def _build_recent_section(self) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        heading = QLabel("Recent projects")
        heading.setStyleSheet(f"color: {tokens.TEXT_FAINT}; font-size: 11px;")
        col.addWidget(heading)

        self._recent_list = QWidget()
        self._recent_col = QVBoxLayout(self._recent_list)
        self._recent_col.setContentsMargins(0, 0, 0, 0)
        self._recent_col.setSpacing(4)
        self._recent_col.addStretch(1)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setObjectName("recentScroll")
        self._recent_scroll.setWidgetResizable(True)
        self._recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._recent_scroll.setWidget(self._recent_list)
        col.addWidget(self._recent_scroll)
        return wrap

    def _build_stage_chips(self) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for stage in self._stage_labels:
            chip = QLabel(stage)
            chip.setObjectName("stageChip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setStyleSheet(f"""
                QLabel {{
                    background-color: {tokens.BG_2};
                    color: {tokens.TEXT_MUTED};
                    border: 1px solid {tokens.BORDER};
                    border-radius: 12px;
                    padding: 4px 12px;
                    font-size: 11.5px;
                }}
            """)
            row.addWidget(chip)
        return wrap

    def _build_footer(self, on_about: Callable[[], None] | None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        footer = QLabel(" · ".join([f"{APP_NAME} v{__version__}", *_model_files()]))
        footer.setObjectName("welcomeFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; font-size: 11px;"
        )
        row.addWidget(footer)
        if on_about is not None:
            about = ghost_button("About", "info", compact=True, tooltip="About SPROUTS")
            about.clicked.connect(lambda _=False: on_about())
            row.addWidget(about)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ recents
    def recent_rows(self) -> list[RecentRow]:
        """The recent-project rows currently shown (most recent first)."""
        return list(self._rows)

    def stage_labels(self) -> list[str]:
        """The stage chip labels, in order."""
        return list(self._stage_labels)

    def refresh_recents(self) -> None:
        """Re-read the store (e.g. after MainWindow recorded a project)."""
        self._populate_recents()

    def _populate_recents(self) -> None:
        for row in self._rows:
            self._recent_col.removeWidget(row)
            row.deleteLater()
        entries = self._store.entries()
        self._rows = [
            RecentRow(entry, self._handle_open_recent, self._handle_remove_recent)
            for entry in entries
        ]
        for i, row in enumerate(self._rows):
            self._recent_col.insertWidget(i, row)
        self.recent_section.setVisible(bool(self._rows))
        self._fit_recent_scroll()
        paths = [e.path for e in entries]
        if not paths:
            return
        if self._probe_async:
            self._probe.start(paths, self._path_exists)
        else:
            for path in paths:
                self._on_exists_checked(path, bool(self._path_exists(path)))

    def _fit_recent_scroll(self) -> None:
        """Show up to VISIBLE_RECENT_ROWS rows; the scroll may shrink to one row."""
        if not self._rows:
            return
        row_h = self._rows[0].sizeHint().height()
        spacing = self._recent_col.spacing()
        shown = min(len(self._rows), VISIBLE_RECENT_ROWS)
        self._recent_scroll.setMaximumHeight(shown * row_h + (shown - 1) * spacing + 2)
        self._recent_scroll.setMinimumHeight(row_h + 2)

    @pyqtSlot(str, bool)
    def _on_exists_checked(self, path: str, exists: bool) -> None:
        for row in self._rows:
            if row.entry.path == path:
                row.set_missing(not exists)

    def _handle_open_recent(self, path: str) -> None:
        if path and callable(self._on_open_recent):
            self._on_open_recent(path)

    def _handle_remove_recent(self, path: str) -> None:
        self._store.remove(path)
        self._populate_recents()

    def _handle_drop(self, folder: str) -> None:
        if callable(self._on_open_recent):
            self._on_open_recent(folder)
        else:  # no dialog-free opener wired: fall back to the picker
            self._handle_get_started()

    @staticmethod
    def _relative_date(ts: str) -> str:
        """ISO timestamp -> short relative-ish label (best effort)."""
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return ""
        secs = (datetime.now() - dt).total_seconds()
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)}m ago"
        if secs < 86400:
            return f"{int(secs // 3600)}h ago"
        if secs < 7 * 86400:
            return f"{int(secs // 86400)}d ago"
        return dt.strftime("%Y-%m-%d")

    def _handle_get_started(self, *_args) -> None:
        if callable(self._on_get_started):
            self._on_get_started()

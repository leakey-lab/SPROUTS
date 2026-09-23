"""
"Export dataset…" dialog (spec §4 / Track X).

Collects the export options (output folder, validation share, tube grouping,
completeness filter, seed), runs ``app.gt.export.export_dataset`` on a
``QThread`` with progress, and shows a summary (exported / train / val /
skipped / errors, with the first failed images and why; the full list is in
``<out>/errors.txt``) when it finishes. Constructed by ``MainWindow``'s
"Export dataset…" action bar button (Track E / Task 4); the constructor
signature is frozen so that caller can rely on it.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.gt.export import ExportError, ExportOptions, ExportResult, export_dataset
from app.gt.registry import Registry
from app.gui.widgets import tokens

# Failed images listed in the summary; the rest are counted ("…and N more").
_MAX_LISTED_ERRORS = 10


class ExportThread(QThread):
    """Runs ``export_dataset`` off the GUI thread."""

    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(object)  # ExportResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        project_dir: str,
        images: dict[str, str],
        registry: Registry,
        options: ExportOptions,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_dir = project_dir
        self._images = images
        self._registry = registry
        self._options = options

    def run(self) -> None:
        try:
            result = export_dataset(
                self._project_dir,
                self._images,
                self._registry,
                self._options,
                progress_cb=self.progress.emit,
            )
        except ExportError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # thread boundary: surface anything, never crash the app
            self.failed.emit(f"Export failed: {exc}")
        else:
            self.finished_ok.emit(result)


class ExportDatasetDialog(QDialog):
    """Collects export options and runs the export on a background thread."""

    def __init__(
        self,
        project_dir: str,
        images: dict[str, str],
        registry: Registry,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.project_dir = project_dir
        self.images = images
        self.registry = registry
        self.result_summary: ExportResult | None = None
        self.export_thread: ExportThread | None = None

        self.setWindowTitle("Export dataset")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {tokens.BG_1}; }}")
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("Export dataset")
        heading.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 12pt; font-weight: 600; border: none;"
        )
        layout.addWidget(heading)

        subtitle = QLabel(
            "Copies GT-traced images and remapped class masks into a training-ready layout."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 9pt; border: none;")
        layout.addWidget(subtitle)

        # --- Output folder ---
        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose an output folder…")
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setObjectName("ghostButton")
        self.browse_button.clicked.connect(self._on_browse)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.browse_button)
        layout.addLayout(_labeled("Output folder", out_row))

        # --- Validation share ---
        self.val_spin = QSpinBox()
        self.val_spin.setRange(0, 90)
        self.val_spin.setValue(20)
        self.val_spin.setSuffix("%")
        layout.addLayout(_labeled("Validation share", self.val_spin))

        # --- Options ---
        self.group_by_tube_check = QCheckBox("Keep each tube in one split")
        self.group_by_tube_check.setChecked(True)
        layout.addWidget(self.group_by_tube_check)

        self.only_complete_check = QCheckBox("Only fully checked images")
        self.only_complete_check.setChecked(True)
        layout.addWidget(self.only_complete_check)

        # --- Seed ---
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 1_000_000)
        self.seed_spin.setValue(0)
        layout.addLayout(_labeled("Random seed", self.seed_spin))

        # --- Progress + status ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: 9pt; border: none;"
        )
        layout.addWidget(self.status_label)

        # --- Buttons ---
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("ghostButton")
        self.cancel_button.clicked.connect(self.reject)
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("primaryButton")
        self.export_button.clicked.connect(self._on_export_button_clicked)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.export_button)
        layout.addLayout(button_row)

    # --------------------------------------------------------------- logic

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_edit.setText(path)

    def _on_export_button_clicked(self) -> None:
        if self.export_button.text() == "Close":
            self.accept()
            return
        self.start_export()

    def start_export(self) -> None:
        """Validate options and start the export ``QThread``.

        Public so callers (and tests) can drive the export without going
        through a simulated button click.
        """
        if self._is_running():
            return

        out_dir = self.output_edit.text().strip()
        if not out_dir:
            self.status_label.setText("Choose an output folder")
            return

        options = ExportOptions(
            out_dir=out_dir,
            val_fraction=self.val_spin.value() / 100.0,
            group_by_tube=self.group_by_tube_check.isChecked(),
            only_complete=self.only_complete_check.isChecked(),
            seed=self.seed_spin.value(),
        )

        self.result_summary = None
        self.progress_bar.setValue(0)
        self.status_label.setText("Exporting…")
        self._set_controls_enabled(False)

        self.export_thread = ExportThread(self.project_dir, self.images, self.registry, options)
        self.export_thread.progress.connect(self.progress_bar.setValue)
        self.export_thread.finished_ok.connect(self._on_finished_ok)
        self.export_thread.failed.connect(self._on_failed)
        self.export_thread.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.output_edit,
            self.browse_button,
            self.val_spin,
            self.group_by_tube_check,
            self.only_complete_check,
            self.seed_spin,
            self.cancel_button,
            self.export_button,
        ):
            widget.setEnabled(enabled)

    def _is_running(self) -> bool:
        return self.export_thread is not None and self.export_thread.isRunning()

    def _join_worker(self) -> None:
        """The worker emitted its last signal: wait the few instructions until it
        has really finished, so the dialog (and the thread object it owns) can
        be closed and deleted right away."""
        if self.export_thread is not None:
            self.export_thread.wait()

    def _on_finished_ok(self, result: ExportResult) -> None:
        self._join_worker()
        self.result_summary = result
        self.progress_bar.setValue(100)
        parts = [f"Exported {result.exported} image(s) ({result.train} train / {result.val} val)"]
        if result.skipped_incomplete:
            parts.append(f"{result.skipped_incomplete} skipped (incomplete)")
        if result.errors:
            parts.append(f"{len(result.errors)} error(s)")
        text = " — ".join(parts)
        if result.errors:
            lines = [f"{stem}: {message}" for stem, message in result.errors[:_MAX_LISTED_ERRORS]]
            more = len(result.errors) - _MAX_LISTED_ERRORS
            if more > 0:
                lines.append(f"…and {more} more")
            text += "\n\nNot exported (full list in errors.txt):\n" + "\n".join(lines)
        self.status_label.setText(text)
        self.export_button.setText("Close")
        self._set_controls_enabled(True)

    def _on_failed(self, message: str) -> None:
        self._join_worker()
        self.result_summary = None
        self.progress_bar.setValue(0)
        self.status_label.setText(message)
        self._set_controls_enabled(True)

    # ---------------------------------------------------------- lifecycle

    def reject(self) -> None:
        """Ignore Cancel / Esc while the export thread is running.

        Letting the default ``QDialog.reject()`` proceed would hide (and,
        once the caller drops its reference after ``.exec()`` returns, let
        Python garbage-collect) the dialog while ``ExportThread`` is still
        running on its own OS thread — destroying a running ``QThread`` is a
        fatal Qt error. Refuse instead; the run always finishes on its own
        and re-enables the controls.
        """
        if self._is_running():
            self.status_label.setText("Export in progress — please wait…")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Block the title-bar close / Alt+F4 while an export is running (see ``reject``)."""
        if self._is_running():
            self.status_label.setText("Export in progress — please wait…")
            event.ignore()
            return
        super().closeEvent(event)


def _labeled(text: str, content) -> QVBoxLayout:
    """A small label stacked above a widget or layout."""
    box = QVBoxLayout()
    box.setSpacing(4)
    label = QLabel(text)
    label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 9pt; border: none;")
    box.addWidget(label)
    if isinstance(content, QWidget):
        box.addWidget(content)
    else:
        box.addLayout(content)
    return box

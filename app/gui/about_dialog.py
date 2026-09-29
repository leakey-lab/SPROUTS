"""About SPROUTS: version, commit, model identity, calibration, runtime and citation.

FAIR4RS F1.2 / R1.2 (UXS-15 / FR-09): the running version and the weights that
produce every mask and skeleton are visible in the app and can be copied into a
methods section. Model verification hashes ~390 MB on first use, so it runs on
a worker thread (``app.models.verify_all`` caches by path/size/mtime); the
dialog shows "checking…" until the result arrives and never blocks.

The text builders (:func:`citation_text`, :func:`details_text`) are Qt-free.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import threading
from collections.abc import Callable, Mapping

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app import APP_FULL_NAME, __version__
from app.config import PROJECT_ROOT
from app.gui.widgets import tokens
from app.gui.widgets.buttons import ghost_button
from app.models import MODELS, ModelStatus

logger = logging.getLogger(__name__)

__all__ = ["AboutDialog", "CITATION_PATH", "citation_text", "details_text"]

CITATION_PATH = os.path.join(PROJECT_ROOT, "CITATION.cff")
_CHECKING = "checking…"
_STATE_TEXT = {"ok": "verified", "mismatch": "DIFFERENT weights", "missing": "missing"}


# ------------------------------------------------------------------ Qt-free text builders
def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_cff(text: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Top-level scalars and the ``authors`` list of a CITATION.cff (no YAML dependency)."""
    fields: dict[str, str] = {}
    authors: list[dict[str, str]] = []
    section = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if top:
            section = top.group(1)
            if top.group(2) and not top.group(2).startswith(">"):
                fields[section] = _scalar(top.group(2))
            continue
        if section != "authors":
            continue
        item = re.match(r"^\s*(-\s+)?([\w-]+):\s*(.*)$", line)
        if item:
            if item.group(1) or not authors:
                authors.append({})
            authors[-1][item.group(2)] = _scalar(item.group(3))
    return fields, authors


def _apa_author(author: Mapping[str, str]) -> str:
    family = author.get("family-names") or author.get("name") or ""
    given = author.get("given-names", "")
    initials = " ".join(f"{part[0]}." for part in given.replace(".", " ").split() if part)
    return f"{family}, {initials}" if initials else family


def citation_text(path: str = CITATION_PATH) -> str | None:
    """APA-style software citation built from CITATION.cff; ``None`` when absent."""
    try:
        with open(path, encoding="utf-8") as fh:
            fields, authors = _parse_cff(fh.read())
    except OSError:
        return None
    names = [_apa_author(a) for a in authors if a]
    if len(names) > 1:
        who = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        who = names[0] if names else APP_FULL_NAME
    year = fields.get("date-released", "")[:4] or "n.d."
    version = fields.get("version", __version__)
    title = fields.get("title", APP_FULL_NAME)
    doi = fields.get("doi")
    link = f"https://doi.org/{doi}" if doi else fields.get("url", "")
    text = f"{who} ({year}). {title} (Version {version}) [Computer software]."
    return f"{text} {link}" if link else text


def _fov() -> str:
    from app.gui.metrics_bar import fov_text

    return fov_text()


def _runtime_text() -> str:
    """Device summary without importing torch (``unknown`` until a job resolved it)."""
    try:
        from app.inference.runtime import describe_runtime

        return describe_runtime(resolve=False).text
    except Exception:  # never let the About box fail on a runtime probe
        logger.exception("Runtime summary unavailable")
        return "unavailable"


def _commit() -> str:
    from app.provenance import git_commit

    commit = git_commit()
    return commit[:12] if commit else "unknown (not a git checkout)"


def _model_line(key: str, status: ModelStatus | None) -> str:
    spec = MODELS[key]
    name = os.path.basename(spec.relpath)
    if status is None:
        return f"{name} · {_CHECKING}"
    digest = (status.sha256 or spec.sha256)[:12]
    return f"{name} · sha256 {digest} · {_STATE_TEXT.get(status.state, status.state)}"


def details_text(statuses: Mapping[str, ModelStatus] | None = None) -> str:
    """Plain-text provenance block for a methods section or lab notebook."""
    statuses = statuses or {}
    lines = [
        f"{APP_FULL_NAME} v{__version__}",
        f"Commit: {_commit()}",
        f"Calibration FOV: {_fov()}",
        f"Runtime: {_runtime_text()}",
        f"Python {platform.python_version()} · {platform.system()} {platform.release()}",
    ]
    lines += [f"Model {key}: {_model_line(key, statuses.get(key))}" for key in MODELS]
    return "\n".join(lines)


def _verify_models() -> dict[str, ModelStatus]:
    from app.models import verify_all

    return verify_all()


# ------------------------------------------------------------------ Qt dialog
class _ModelVerifier(QObject):
    """Runs :func:`_verify_models` on a worker thread and reports back."""

    finished = pyqtSignal(object)

    def start(self, verify: Callable[[], Mapping[str, ModelStatus]]) -> None:
        def run() -> None:
            try:
                result = dict(verify())
            except Exception:
                logger.exception("Model verification failed")
                result = {}
            try:
                self.finished.emit(result)
            except RuntimeError:  # dialog closed meanwhile
                pass

        threading.Thread(target=run, name="about-verify-models", daemon=True).start()


class AboutDialog(QDialog):
    """Name, version, commit, models (+ verification), FOV, runtime, citation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("About SPROUTS")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(540)
        self._statuses: dict[str, ModelStatus] = {}
        self._citation = citation_text(CITATION_PATH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"{APP_FULL_NAME}")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {tokens.ACCENT}; font-size: 13pt; font-weight: 700;")
        layout.addWidget(title)
        version = QLabel(f"v{__version__}")
        version.setObjectName("aboutVersion")
        version.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-family: {tokens.MONO};")
        layout.addWidget(version)

        grid_frame = QFrame()
        grid_frame.setObjectName("aboutFacts")
        grid_frame.setStyleSheet(
            f"QFrame#aboutFacts {{ background-color: {tokens.BG_2}; "
            f"border: 1px solid {tokens.BORDER}; border-radius: {tokens.RADIUS_SM}px; }}"
        )
        self._grid = QGridLayout(grid_frame)
        self._grid.setContentsMargins(12, 10, 12, 10)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(1, 1)
        self._add_fact("Commit", _commit())
        self._add_fact("Calibration", f"FOV {_fov()}")
        self._add_fact("Runtime", _runtime_text())
        self._model_labels = {
            key: self._add_fact(f"Model · {key}", _model_line(key, None)) for key in MODELS
        }
        layout.addWidget(grid_frame)

        if self._citation:
            cite_head = QLabel("How to cite")
            cite_head.setStyleSheet(f"color: {tokens.TEXT}; font-weight: 600;")
            layout.addWidget(cite_head)
            cite = QLabel(self._citation)
            cite.setObjectName("aboutCitation")
            cite.setWordWrap(True)
            cite.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cite.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            layout.addWidget(cite)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        copy_details = ghost_button("Copy details", compact=True)
        copy_details.clicked.connect(self.copy_details)
        buttons.addWidget(copy_details)
        if self._citation:
            copy_cite = ghost_button("Copy citation", compact=True)
            copy_cite.clicked.connect(self.copy_citation)
            buttons.addWidget(copy_cite)
        open_logs = ghost_button("Open log folder", "folderOpen", compact=True)
        open_logs.clicked.connect(self._open_log_folder)
        buttons.addWidget(open_logs)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

        self._verifier = _ModelVerifier()  # unparented: outlives a closed dialog
        self._verifier.finished.connect(self._on_models_verified)
        self._verifier.start(_verify_models)

    def _add_fact(self, name: str, value: str) -> QLabel:
        row = self._grid.rowCount()
        key = QLabel(name)
        key.setStyleSheet(f"color: {tokens.TEXT_FAINT}; border: none;")
        val = QLabel(value)
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setStyleSheet(f"color: {tokens.TEXT}; font-family: {tokens.MONO}; border: none;")
        self._grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(val, row, 1)
        return val

    @pyqtSlot(object)
    def _on_models_verified(self, statuses: dict) -> None:
        self._statuses = dict(statuses)
        for key, label in self._model_labels.items():
            status = self._statuses.get(key)
            label.setText(_model_line(key, status) if status else f"{key}: unavailable")
            if status is not None and not status.ok:
                label.setStyleSheet(
                    f"color: {tokens.WARN}; font-family: {tokens.MONO}; border: none;"
                )

    def copy_details(self) -> None:
        """Copy the provenance block (version, commit, FOV, runtime, model digests)."""
        QGuiApplication.clipboard().setText(details_text(self._statuses))

    def copy_citation(self) -> None:
        if self._citation:
            QGuiApplication.clipboard().setText(self._citation)

    @staticmethod
    def _open_log_folder() -> None:
        try:
            from app.logging_config import default_log_dir

            folder = str(default_log_dir())
        except Exception:
            logger.exception("Log folder unavailable")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

"""About SPROUTS: version, commit, model identity, calibration, runtime and citation.

FAIR4RS F1.2 / R1.2 (UXS-15 / FR-09): the running version and the weights that
produce every mask and skeleton are visible in the app and can be copied into a
methods section. Model verification hashes ~390 MB on first use, so it runs on
a worker thread (``app.models.verify_all`` caches by path/size/mtime); the
dialog shows "checking…" until the result arrives and never blocks.

The Runtime line is exact about its source. Once PyTorch has resolved the runtime it
is that runtime (device, CUDA, precision). PyTorch loads only when the user heads for
Generate, so before that the dialog asks the NVIDIA driver on a worker thread
(:func:`app.inference.device_probe.probe_device`, about 20 ms, no torch) and names the
device "from the NVIDIA driver; PyTorch not loaded yet".

The text builders (:func:`citation_text`, :func:`details_text`, :func:`runtime_text`)
are Qt-free.
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

__all__ = ["AboutDialog", "CITATION_PATH", "citation_text", "details_text", "runtime_text"]

CITATION_PATH = os.path.join(PROJECT_ROOT, "CITATION.cff")
_CHECKING = "checking…"
_STATE_TEXT = {"ok": "verified", "mismatch": "DIFFERENT weights", "missing": "missing"}
# Where a device named before PyTorch has loaded comes from (the Runtime line).
_FROM_DRIVER = "from the NVIDIA driver; PyTorch not loaded yet"
_NOT_LOADED = "PyTorch not loaded yet"
_NOT_DETECTED = "Device not detected yet"


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


def _runtime_summary():
    """The runtime summary without importing torch (device ``unknown`` until PyTorch has
    resolved it), or None when it is unavailable."""
    try:
        from app.inference.runtime import describe_runtime

        return describe_runtime(resolve=False)
    except Exception:  # never let the About box fail on a runtime probe
        logger.exception("Runtime summary unavailable")
        return None


def _clause(sentence: str) -> str:
    """A guess's reason as one part of the Runtime line (no final full stop)."""
    return (sentence or "").strip().rstrip(".")


def _probe_device():
    """The device the NVIDIA driver and the installed PyTorch build name (a worker-thread
    job: about 20 ms; never imports torch, never raises)."""
    from app.inference.device_probe import probe_device

    return probe_device()


def runtime_text(guess=None) -> str:
    """The Runtime line; never imports torch.

    Once PyTorch has resolved the runtime: that runtime (``"GPU · <name> · CUDA 12.8 ·
    TF32"``), whatever ``guess`` says. Before that, from ``guess`` (a
    :class:`~app.inference.device_probe.DeviceGuess`): the device the NVIDIA driver named,
    "(from the NVIDIA driver; PyTorch not loaded yet)"; for a CPU or unknown device the
    guess's reason follows. Without a guess: "Device not detected yet". Missing weights are
    named last, as on the status line.
    """
    summary = _runtime_summary()
    if summary is None:
        return "unavailable"
    if summary.device != "unknown" or guess is None:
        return summary.text
    if guess.device == "GPU":
        parts = [f"{guess.name or 'GPU'} ({_FROM_DRIVER})"]
    elif guess.problem:  # PyTorch is not installed: nothing will load
        parts = [_NOT_DETECTED, _clause(guess.problem)]
    else:
        device = "CPU" if guess.device == "CPU" else _NOT_DETECTED
        parts = [f"{device} ({_NOT_LOADED})", _clause(guess.note)]
    parts = [part for part in parts if part]
    parts += [
        f"{model} model missing"
        for model, present in (
            ("mask", summary.mask_weights),
            ("skeleton", summary.skeleton_weights),
        )
        if not present
    ]
    return " · ".join(parts)


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


def details_text(
    statuses: Mapping[str, ModelStatus] | None = None, runtime: str | None = None
) -> str:
    """Plain-text provenance block for a methods section or lab notebook; ``runtime`` is
    the Runtime line (:func:`runtime_text` without a device guess when None)."""
    statuses = statuses or {}
    lines = [
        f"{APP_FULL_NAME} v{__version__}",
        f"Commit: {_commit()}",
        f"Calibration FOV: {_fov()}",
        f"Runtime: {runtime_text() if runtime is None else runtime}",
        f"Python {platform.python_version()} · {platform.system()} {platform.release()}",
    ]
    lines += [f"Model {key}: {_model_line(key, statuses.get(key))}" for key in MODELS]
    return "\n".join(lines)


def _verify_models() -> dict[str, ModelStatus]:
    from app.models import verify_all

    return verify_all()


# ------------------------------------------------------------------ Qt dialog
class _ModelVerifier(QObject):
    """Runs a job (:func:`_verify_models`, :func:`_probe_device`) on a worker thread and
    reports its result back (``failed`` when it raised)."""

    finished = pyqtSignal(object)

    def start(self, job: Callable[[], object], *, name: str = "about-verify-models", failed=None):
        def run() -> None:
            try:
                result = job()
            except Exception:
                logger.exception("About dialog job %s failed", name)
                result = failed
            try:
                self.finished.emit(result)
            except RuntimeError:  # dialog closed meanwhile
                pass

        threading.Thread(target=run, name=name, daemon=True).start()


class AboutDialog(QDialog):
    """Name, version, commit, models (+ verification), FOV, runtime, citation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("About SPROUTS")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(540)
        self._statuses: dict[str, ModelStatus] = {}
        self._guess = None  # the driver's device guess, once asked (PyTorch not loaded)
        self._citation = citation_text(CITATION_PATH)
        summary = _runtime_summary()
        # PyTorch has not resolved the runtime yet: the driver names the device meanwhile.
        ask_driver = summary is not None and summary.device == "unknown"

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
        self._runtime_label = self._add_fact("Runtime", _CHECKING if ask_driver else runtime_text())
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
        verify = _verify_models
        self._verifier.start(lambda: dict(verify()), failed={})
        self._prober = None
        if ask_driver:  # its own thread: the model hashing can take seconds
            self._prober = _ModelVerifier()
            self._prober.finished.connect(self._on_device_probed)
            self._prober.start(_probe_device, name="about-probe-device")

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

    @pyqtSlot(object)
    def _on_device_probed(self, guess) -> None:
        """The driver named the device (or said why it could not): the Runtime line."""
        self._guess = guess
        self._runtime_label.setText(runtime_text(guess))

    def copy_details(self) -> None:
        """Copy the provenance block (version, commit, FOV, runtime, model digests). The
        Runtime line is read again: PyTorch may have resolved the runtime meanwhile."""
        runtime = runtime_text(self._guess)
        if self._runtime_label.text() not in (runtime, _CHECKING):  # not while asking the driver
            self._runtime_label.setText(runtime)
        QGuiApplication.clipboard().setText(details_text(self._statuses, runtime))

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

"""
Tests for the "Export dataset…" dialog (``app/gui/export_dataset_dialog.py``).

Offscreen Qt: constructs the dialog against a small tmp project, drives the
export via its start method (not a real click, to avoid relying on event-loop
timing), and waits for the worker ``QThread`` to finish before asserting on
the summary.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from app.gt.export import ExportResult
from app.gt.registry import default_registry
from app.gt.store import Tracing, gt_path, write_tracing
from app.gui.export_dataset_dialog import ExportDatasetDialog

_STEMS = [
    "plot_T1_L1_2024.01.01_120000",
    "plot_T1_L2_2024.01.01_120000",
    "plot_T2_L1_2024.01.01_120000",
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_ids(shape, values):
    h, w = shape
    flat = np.array([values[i % len(values)] for i in range(h * w)], dtype=np.uint8)
    return flat.reshape(h, w)


def _build_project(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    registry = default_registry()
    images = {}
    reviewed = frozenset({"root", "nodule", "root_tip"})
    for stem in _STEMS:
        image_path = project_dir / f"{stem}.png"
        Image.new("RGB", (20, 20), color=(5, 5, 5)).save(image_path, format="PNG")
        images[stem] = str(image_path)
        ids = _make_ids((20, 20), [0, 1, 2, 3])
        write_tracing(gt_path(str(image_path)), Tracing(ids=ids, reviewed=reviewed), registry)
    return str(project_dir), images, registry


def _wait_for_thread(dialog, timeout_ms=10000):
    assert dialog.export_thread is not None
    dialog.export_thread.wait(timeout_ms)
    QApplication.processEvents()


def test_export_dialog_runs_and_shows_summary(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)

    out_dir = tmp_path / "export_out"
    dialog.output_edit.setText(str(out_dir))
    dialog.val_spin.setValue(50)

    dialog.start_export()
    _wait_for_thread(dialog)

    assert dialog.result_summary is not None
    assert isinstance(dialog.result_summary, ExportResult)
    assert dialog.result_summary.exported > 0
    assert str(dialog.result_summary.exported) in dialog.status_label.text()
    assert dialog.export_button.text() == "Close"


def test_export_dialog_non_empty_out_dir_shows_error(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)

    out_dir = tmp_path / "export_out_taken"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("x")
    dialog.output_edit.setText(str(out_dir))

    dialog.start_export()
    _wait_for_thread(dialog)

    assert dialog.result_summary is None
    assert "not empty" in dialog.status_label.text().lower()
    assert dialog.export_button.text() == "Export"


def test_export_dialog_empty_output_path_shows_message(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)

    dialog.output_edit.setText("")
    dialog.start_export()

    assert dialog.export_thread is None
    assert dialog.status_label.text() == "Choose an output folder"

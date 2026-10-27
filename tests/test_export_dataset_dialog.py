"""
Tests for the "Export dataset…" dialog (``app/gui/export_dataset_dialog.py``).

Offscreen Qt: constructs the dialog against a small tmp project, drives the
export via its start method (not a real click, to avoid relying on event-loop
timing), and waits for the worker ``QThread`` to finish before asserting on
the summary. Tests that act while the export runs hold the worker inside
``export_dataset`` on a ``threading.Event`` (``blocked_export``), so they
never race it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
import threading

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


@pytest.fixture
def blocked_export(monkeypatch):
    """Hold the worker inside ``export_dataset`` until the test releases it.

    Returns ``(started, release)`` events: ``started`` is set once the worker
    thread is inside the export, and the export proceeds only after
    ``release`` is set (released on teardown too, so a failing test never
    leaves the thread blocked).
    """
    import app.gui.export_dataset_dialog as mod

    started, release = threading.Event(), threading.Event()
    real_export = mod.export_dataset

    def _blocking_export(*args, **kwargs):
        started.set()
        release.wait(30)
        return real_export(*args, **kwargs)

    monkeypatch.setattr(mod, "export_dataset", _blocking_export)
    yield started, release
    release.set()


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


def test_export_button_disabled_while_running(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_disable"))

    dialog.start_export()
    assert not dialog.export_button.isEnabled()

    _wait_for_thread(dialog)
    assert dialog.export_button.isEnabled()
    assert dialog.export_button.text() == "Close"


def test_reject_during_run_is_ignored_and_thread_finishes_cleanly(app, tmp_path, blocked_export):
    """Cancel/Esc while exporting must not tear down the running QThread.

    Destroying a running QThread is a fatal Qt error; ``reject()`` must be a
    no-op while ``export_thread`` is running (verified by the ``rejected``
    signal not firing), and the export must still complete normally once it
    is left alone. The worker is held inside ``export_dataset`` until the
    assertions are done, so the test never races it.
    """
    started, release = blocked_export
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_reject"))

    dialog.start_export()
    thread = dialog.export_thread
    assert thread is not None
    assert started.wait(10), "worker never entered export_dataset"
    assert thread.isRunning()

    rejected_calls = []
    dialog.rejected.connect(lambda: rejected_calls.append(1))

    dialog.reject()  # simulate Cancel / Esc while exporting

    assert rejected_calls == []  # reject() is ignored while the thread runs
    assert dialog.export_thread is thread  # the worker thread was not torn down
    assert "in progress" in dialog.status_label.text()

    release.set()
    _wait_for_thread(dialog)
    assert dialog.result_summary is not None
    assert dialog.result_summary.exported > 0

    # Once idle, reject() behaves normally again.
    dialog.reject()
    assert rejected_calls == [1]


def test_close_event_during_run_is_ignored(app, tmp_path, blocked_export):
    from PyQt6.QtGui import QCloseEvent

    started, release = blocked_export
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_close"))

    dialog.start_export()
    assert started.wait(10), "worker never entered export_dataset"
    assert dialog.export_thread.isRunning()
    event = QCloseEvent()
    dialog.closeEvent(event)

    assert not event.isAccepted()
    assert dialog.export_thread is not None

    release.set()
    _wait_for_thread(dialog)
    assert dialog.result_summary is not None
    assert dialog.result_summary.exported > 0
    assert not dialog.export_thread.isRunning()  # finished before Close is offered


def _failing_export(monkeypatch, exc: BaseException) -> None:
    import app.gui.export_dataset_dialog as mod

    def _raise(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(mod, "export_dataset", _raise)


def _logged(caplog) -> list[tuple[int, bool]]:
    """``(level, with a traceback)`` of each record the dialog's module logged."""
    return [
        (record.levelno, record.exc_info is not None)
        for record in caplog.records
        if record.name == "app.gui.export_dataset_dialog"
    ]


def _run_failing_export(tmp_path) -> ExportDatasetDialog:
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_failing"))
    dialog.start_export()
    _wait_for_thread(dialog)
    return dialog


def test_unexpected_worker_exception_is_reported_not_fatal(app, tmp_path, monkeypatch, caplog):
    _failing_export(monkeypatch, RuntimeError("unexpected boom"))

    dialog = _run_failing_export(tmp_path)

    assert dialog.result_summary is None
    assert dialog.status_label.text() == "Export failed: unexpected boom"
    assert dialog.export_button.text() == "Export" and dialog.export_button.isEnabled()
    assert _logged(caplog) == [(logging.ERROR, True)]  # a bug: its traceback is in the log


class _Odd(BaseException):
    """Neither an Exception nor an interrupt, as a library may raise."""


@pytest.mark.parametrize(
    "exc", [SystemExit(3), KeyboardInterrupt(), _Odd("odd")], ids=["exit", "interrupt", "other"]
)
def test_any_base_exception_is_reported_and_the_controls_come_back(
    app, tmp_path, monkeypatch, caplog, exc
):
    """Escaping the thread it left the dialog at "Exporting…" with every control off (and
    SystemExit kept the thread from ending, so the dialog could not be closed)."""
    _failing_export(monkeypatch, exc)

    dialog = _run_failing_export(tmp_path)

    assert not dialog.export_thread.isRunning()
    assert dialog.status_label.text() == f"Export failed: {exc}"
    assert dialog.export_button.isEnabled() and dialog.cancel_button.isEnabled()
    assert _logged(caplog) == [(logging.ERROR, True)]


def test_an_error_whose_text_fails_is_reported_by_its_type(app, tmp_path, monkeypatch):
    class _NoText(Exception):
        def __str__(self):
            raise RuntimeError("no text")

    _failing_export(monkeypatch, _NoText())

    dialog = _run_failing_export(tmp_path)

    assert dialog.status_label.text() == "Export failed: _NoText"
    assert dialog.export_button.isEnabled()


def test_a_file_held_open_elsewhere_is_reported_without_a_traceback(
    app, tmp_path, monkeypatch, caplog
):
    from app.io_utils import FileLockedError

    exc = FileLockedError(str(tmp_path / "manifest.csv"))
    _failing_export(monkeypatch, exc)

    dialog = _run_failing_export(tmp_path)

    assert dialog.status_label.text() == f"Export failed: {exc}"
    assert _logged(caplog) == [(logging.WARNING, False)]  # a user condition, not a bug


def test_a_refused_request_keeps_its_message_and_logs_nothing(app, tmp_path, monkeypatch, caplog):
    from app.gt.export import ExportError

    _failing_export(monkeypatch, ExportError("Output directory is not empty: X"))

    dialog = _run_failing_export(tmp_path)

    assert dialog.status_label.text() == "Output directory is not empty: X"
    assert _logged(caplog) == []


def test_summary_lists_failed_images(app, tmp_path, monkeypatch):
    import app.gui.export_dataset_dialog as mod

    errors = [(f"img_{i:02d}", f"reason {i}") for i in range(12)]

    def _fake(project_dir, images, registry, options, progress_cb=None):
        return ExportResult(
            out_dir=options.out_dir, exported=3, train=2, val=1, skipped_incomplete=1, errors=errors
        )

    monkeypatch.setattr(mod, "export_dataset", _fake)
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_errors"))
    dialog.start_export()
    _wait_for_thread(dialog)

    text = dialog.status_label.text()
    assert "12 error(s)" in text
    for stem, reason in errors[:10]:
        assert f"{stem}: {reason}" in text
    for stem, _reason in errors[10:]:
        assert stem not in text
    assert "…and 2 more" in text
    assert "errors.txt" in text


def test_summary_without_errors_lists_none(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_ok"))
    dialog.start_export()
    _wait_for_thread(dialog)
    assert "error" not in dialog.status_label.text().lower()


def test_progress_bar_hidden_until_an_export_starts(app, tmp_path):
    """No '0%' bar before anything runs (audit UXS-13); it shows the run, then 100%."""
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    assert dialog.progress_bar.isHidden()

    dialog.output_edit.setText(str(tmp_path / "export_out_progress"))
    dialog.start_export()
    assert not dialog.progress_bar.isHidden()
    _wait_for_thread(dialog)
    assert not dialog.progress_bar.isHidden()
    assert dialog.progress_bar.value() == 100


def test_failed_export_hides_the_progress_bar_again(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    taken = tmp_path / "export_out_taken_again"
    taken.mkdir()
    (taken / "existing.txt").write_text("x")
    dialog.output_edit.setText(str(taken))

    dialog.start_export()
    _wait_for_thread(dialog)

    assert dialog.progress_bar.isHidden()
    assert dialog.export_button.isEnabled() and dialog.export_button.text() == "Export"


def test_export_button_needs_an_output_folder(app, tmp_path):
    """Export is only offered once there is somewhere to export to (audit UXS-13)."""
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    assert not dialog.export_button.isEnabled()

    dialog.output_edit.setText("   ")
    assert not dialog.export_button.isEnabled()
    dialog.output_edit.setText(str(tmp_path / "export_out_gate"))
    assert dialog.export_button.isEnabled()
    dialog.output_edit.setText("")
    assert not dialog.export_button.isEnabled()


def test_close_stays_available_after_a_finished_export(app, tmp_path):
    project_dir, images, registry = _build_project(tmp_path)
    dialog = ExportDatasetDialog(project_dir, images, registry)
    dialog.output_edit.setText(str(tmp_path / "export_out_close_mode"))
    dialog.start_export()
    _wait_for_thread(dialog)
    assert dialog.export_button.text() == "Close"

    dialog.output_edit.setText("")  # the output gate only applies to "Export"
    assert dialog.export_button.isEnabled()

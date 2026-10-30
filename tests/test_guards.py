"""The guard fixtures of conftest.py (tests/guards.py): no prompt, no file dialog, no delayed
temp sweep, no OpenGL, and no exception hidden in a Qt slot."""

import os
import sys

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.gui import canvas_gl, image_manager
from tests import guards

FAILED = pytest.fail.Exception
_REAL_BOXES = {name: getattr(QMessageBox, name) for name in ("exec", *guards.PROMPT_STATICS)}
_REAL_DIALOGS = {name: getattr(QFileDialog, name) for name in guards.FILE_DIALOGS}
_REAL_SWEEP = image_manager.ImageManager._start_temp_sweep
_REAL_HOOK = sys.excepthook


def _exc_info_from(filename: str):
    """``(kind, value, tb)`` of an error raised and caught in code of ``filename`` only; its
    message is ``filename``."""
    source = "def capture():\n    try:\n        raise ValueError(NAME)\n"
    source += "    except ValueError:\n        return sys.exc_info()\n"
    namespace = {"sys": sys, "NAME": filename}
    exec(compile(source, filename, "exec"), namespace)  # its frames name ``filename``
    return namespace["capture"]()


@pytest.fixture
def gl_requested(monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "1")


@pytest.fixture
def sources_in_module(monkeypatch):
    """This module names the files whose slot errors count, as a module would."""
    names = (os.path.join("pkg", "thing.py"),)
    monkeypatch.setattr(sys.modules[__name__], "SLOT_ERRORS_FROM", names, raising=False)


def test_no_prompts_refuses_exec_and_the_static_boxes(app, no_prompts):
    box = QMessageBox()
    box.setText("Save changes?")
    with pytest.raises(FAILED, match="prompt: Save changes"):
        box.exec()
    for name in guards.PROMPT_STATICS:
        with pytest.raises(FAILED, match=f"QMessageBox.{name}"):
            getattr(QMessageBox, name)(None, "Title", "Text")
    box.deleteLater()


def test_no_file_dialogs_refuses_every_getter(app, no_file_dialogs):
    for name in guards.FILE_DIALOGS:
        with pytest.raises(FAILED, match=f"QFileDialog.{name}"):
            getattr(QFileDialog, name)(None, "Pick")


def test_no_temp_sweep_turns_the_delayed_sweep_into_a_no_op(no_temp_sweep, tmp_path):
    sweep = image_manager.ImageManager._start_temp_sweep
    assert sweep is not _REAL_SWEEP
    assert sweep(str(tmp_path)) is None


def test_gl_off_wins_over_the_environment(gl_requested, gl_off):
    assert os.environ[canvas_gl.ENV_VAR] == "0"
    assert canvas_gl.opengl_viewport_requested() is False


def test_window_guards_combines_prompts_dialogs_sweep_and_gl(app, gl_requested, window_guards):
    with pytest.raises(FAILED):
        QMessageBox().exec()
    with pytest.raises(FAILED):
        QFileDialog.getExistingDirectory(None, "Pick")
    assert image_manager.ImageManager._start_temp_sweep is not _REAL_SWEEP
    assert os.environ[canvas_gl.ENV_VAR] == "0"


def test_slot_errors_records_an_exception_raised_in_a_qt_slot(app, slot_errors):
    def slot():
        raise RuntimeError("raised in a slot")

    QTimer.singleShot(0, slot)
    for _ in range(3):
        QApplication.processEvents()

    assert [str(error) for error in slot_errors] == ["raised in a slot"]
    slot_errors.clear()  # seen: the test passes


def test_a_recorded_slot_error_fails_the_test_at_teardown(monkeypatch):
    clean = guards.record_slot_errors(monkeypatch)
    next(clean)
    with pytest.raises(StopIteration):
        next(clean)  # nothing recorded: the teardown passes

    failing = guards.record_slot_errors(monkeypatch)
    errors = next(failing)
    sys.excepthook(*_exc_info_from("anywhere.py"))
    assert len(errors) == 1
    with pytest.raises(AssertionError):
        next(failing)


def test_sources_limit_the_recording_to_tracebacks_through_those_files(monkeypatch, capsys):
    recorder = guards.record_slot_errors(monkeypatch, (os.path.join("pkg", "thing.py"), "solo.py"))
    errors = next(recorder)
    recorded = [
        os.path.join("root", "pkg", "thing.py"),  # ends with the entry
        os.path.join("root", "solo.py"),  # the file-name entry
        os.path.join("pkg", "thing.py"),  # the entry itself
    ]
    printed = [
        os.path.join("root", "notpkg", "thing.py"),  # no separator before the entry
        os.path.join("root", "notsolo.py"),
        os.path.join("root", "pkg", "other.py"),
    ]
    for filename in recorded + printed:
        sys.excepthook(*_exc_info_from(filename))

    assert [str(error) for error in errors] == recorded
    err = capsys.readouterr().err
    assert all(f"ValueError: {name}" in err for name in printed)
    assert not any(f"ValueError: {name}\n" in err for name in recorded)
    errors.clear()
    with pytest.raises(StopIteration):
        next(recorder)


def test_slot_errors_reads_the_modules_sources(sources_in_module, slot_errors, capsys):
    sys.excepthook(*_exc_info_from(os.path.join("root", "pkg", "thing.py")))
    sys.excepthook(*_exc_info_from(os.path.join("root", "elsewhere.py")))

    assert [str(error) for error in slot_errors] == [os.path.join("root", "pkg", "thing.py")]
    assert "elsewhere.py" in capsys.readouterr().err
    slot_errors.clear()


def test_every_guard_ends_with_its_test():
    assert {name: getattr(QMessageBox, name) for name in _REAL_BOXES} == _REAL_BOXES
    assert {name: getattr(QFileDialog, name) for name in _REAL_DIALOGS} == _REAL_DIALOGS
    assert image_manager.ImageManager._start_temp_sweep is _REAL_SWEEP
    assert sys.excepthook is _REAL_HOOK
    assert not hasattr(sys.modules[__name__], "SLOT_ERRORS_FROM")

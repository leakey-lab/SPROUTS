"""Guards of the GUI tests. Each patches through the test's ``monkeypatch``, so it ends with
the test.

- ``refuse_prompts``: ``QMessageBox.exec`` and the static boxes (``information``,
  ``warning``, ``critical``, ``question``) fail the test. Offscreen, a modal box would wait
  forever.
- ``refuse_file_dialogs``: the ``QFileDialog`` getters fail the test.
- ``skip_temp_sweep``: a loaded project schedules its stale-temp sweep 2 s later (R8) on an
  untracked timer; here it is a no-op, so a window of one test never sweeps inside a later
  test.
- ``gl_off``: raster canvases (``ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=0``) even when the
  environment asks for OpenGL.
- ``record_slot_errors``: PyQt hands an exception raised in a Qt slot to ``sys.excepthook``,
  which only prints it under pytest, so the test passes. This hook records it and the test
  fails at teardown. With ``sources``, only an exception whose traceback passes through one
  of those files is recorded; any other goes to ``sys.__excepthook__`` (printed).

``conftest.py`` offers them as fixtures (``no_prompts``, ``no_file_dialogs``,
``no_temp_sweep``, ``gl_off``, ``slot_errors``) and as compositions (``window_guards``,
``correct_guards``). Qt and app modules are imported when a guard runs, not here.
"""

from __future__ import annotations

import os
import sys
import traceback

import pytest

#: The static ``QMessageBox`` helpers ``refuse_prompts`` refuses besides ``exec``.
PROMPT_STATICS = ("information", "warning", "critical", "question")
#: The ``QFileDialog`` getters ``refuse_file_dialogs`` refuses.
FILE_DIALOGS = ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory")


def _refuse_box(box) -> None:
    pytest.fail(f"prompt: {box.text()}")


def _refused_static(name: str):
    def refuse(*args, **_kwargs):
        pytest.fail(f"QMessageBox.{name}: {args[1:3]}")

    return staticmethod(refuse)


def _refused_dialog(name: str):
    def refuse(*_args, **_kwargs):
        pytest.fail(f"a file dialog was opened: QFileDialog.{name}")

    return staticmethod(refuse)


def refuse_prompts(monkeypatch) -> None:
    """Every message box fails the test: ``exec`` and the static helpers."""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", _refuse_box)
    for name in PROMPT_STATICS:
        monkeypatch.setattr(QMessageBox, name, _refused_static(name))


def refuse_file_dialogs(monkeypatch) -> None:
    """Every ``QFileDialog`` getter fails the test."""
    from PyQt6.QtWidgets import QFileDialog

    for name in FILE_DIALOGS:
        monkeypatch.setattr(QFileDialog, name, _refused_dialog(name))


def skip_temp_sweep(monkeypatch) -> None:
    """A loaded project's delayed stale-temp sweep does nothing."""
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


def gl_off(monkeypatch) -> None:
    """The canvases draw without OpenGL."""
    from app.gui import canvas_gl

    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")


def _passes_through(tb, suffixes: tuple[str, ...]) -> bool:
    """True when a frame of ``tb`` is in a file whose path ends with one of ``suffixes``
    (normcased, at a path-separator boundary)."""
    for frame in traceback.extract_tb(tb):
        name = os.path.normcase(frame.filename)
        if any(name == suffix or name.endswith(os.sep + suffix) for suffix in suffixes):
            return True
    return False


def record_slot_errors(monkeypatch, sources=None):
    """Generator for a fixture: replace ``sys.excepthook`` with a recorder, yield the list of
    recorded exceptions, and assert after the test that it is empty.

    ``sources`` is a path (``os.path.join("correct", "editor.py")``), a file name, or several
    of them; None records every exception the hook is given.
    """
    if sources is None:
        suffixes = None
    else:
        named = (sources,) if isinstance(sources, str) else tuple(sources)
        suffixes = tuple(os.path.normcase(source) for source in named)
    errors: list = []

    def hook(kind, value, tb):
        if suffixes is None or _passes_through(tb, suffixes):
            errors.append(value)
        else:
            sys.__excepthook__(kind, value, tb)

    monkeypatch.setattr(sys, "excepthook", hook)
    yield errors
    assert errors == []
